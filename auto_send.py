#!/usr/bin/env python3
"""自动发送调度 auto_send.py — 把已审核通过(approved)的消息按渠道自动发出。

与手动通道并存: ops.sh send(逐条手动) 和网页"通过并发送"按钮不受影响。
自动通道只消费 status='approved' 且未被手动发出的消息。

限速(可选保护, 默认不限量):
  - 最近 AUTO_SEND_WINDOW_HOURS 小时内, 真实渠道已发送数(不含 mock 演练)
    达到 AUTO_SEND_WINDOW_MAX 则本轮退出(0 = 不限量, 付费服务默认)
  - 发送节奏: 成功连发无间隔(官方通道), 单条失败停顿 5s 再继续(防连环限流)

失败处理:
  - 单条发送失败 -> send_fail_count+1
  - 连续失败 >= AUTO_SEND_FAIL_LIMIT -> 跳过, 不再自动尝试(保留 approved 等手动)
  - x 渠道 credits 耗尽(402) -> sender.py 已标 credit_exhausted=1, 不再自动尝试
  - 失败原因由 sender.py 写入 ops_log(send.fail), 可在网页"操作日志"查看

防骚扰: 同一 person 近 7 天已有 sent/approved 消息 -> 跳过(不重复打扰)

开关: .env AUTO_SEND_ENABLED=true 才运行(默认 false)
防重入: data/auto_send.lock (fcntl 非阻塞, cron 重叠直接退出)

用法:
  venv/bin/python auto_send.py            # 执行一轮
  venv/bin/python auto_send.py --status   # 只查看状态, 不发送

cron:
  */10 * * * * /opt/leads/venv/bin/python /opt/leads/auto_send.py >> /opt/leads/data/auto_send.log 2>&1
"""
import argparse
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/leads")
from dotenv import load_dotenv

from db import get_conn, log_op

BASE = Path("/opt/leads")
DATA = BASE / "data"
LOCK_FILE = DATA / "auto_send.lock"
LOG_FILE = DATA / "auto_send.log"
PY = str(BASE / "venv" / "bin" / "python")

load_dotenv(BASE / ".env")

ENABLED = os.getenv("AUTO_SEND_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
# 0 = 不限量(付费服务, 不做条数上限); 窗口/批次仅作为可选保护, 默认关闭
BATCH_MAX = int(os.getenv("AUTO_SEND_BATCH_MAX", "0"))
WINDOW_HOURS = int(os.getenv("AUTO_SEND_WINDOW_HOURS", "5"))
WINDOW_MAX = int(os.getenv("AUTO_SEND_WINDOW_MAX", "0"))
FAIL_LIMIT = int(os.getenv("AUTO_SEND_FAIL_LIMIT", "3"))
ALLOW_MOCK = os.getenv("AUTO_SEND_ALLOW_MOCK", "false").strip().lower() in ("1", "true", "yes", "on")

CHANNELS = ("x", "tg", "wa", "reddit") + (("mock",) if ALLOW_MOCK else ())

SENDER_TIMEOUT = 180


def log(line):
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n")
    except OSError:
        pass


def window_sent(conn):
    """滑动窗口内真实渠道已发送数(mock 演练不计入限速)。"""
    row = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE status='sent' AND channel != 'mock' "
        "AND datetime(sent_at) >= datetime('now', ?)",
        (f"-{WINDOW_HOURS} hours",),
    ).fetchone()
    return row[0]


def recently_contacted(conn, person_id, exclude_id):
    """同一 person 近 7 天是否已有 sent/approved 消息(排除候选自身)。"""
    row = conn.execute(
        "SELECT 1 FROM messages WHERE person_id=? AND id != ? "
        "AND status IN ('sent','approved') "
        "AND datetime(created_at) >= datetime('now','-7 days') LIMIT 1",
        (person_id, exclude_id),
    ).fetchone()
    return row is not None


def pick_candidates(conn, limit):
    ph = ",".join("?" * len(CHANNELS))
    rows = conn.execute(
        f"SELECT * FROM messages WHERE status='approved' "
        f"AND channel IN ({ph}) "
        "AND COALESCE(credit_exhausted,0)=0 "
        "AND COALESCE(send_fail_count,0) < ? "
        "AND EXISTS (SELECT 1 FROM accounts a WHERE a.person_id=messages.person_id "
        "    AND a.platform = CASE messages.channel "
        "        WHEN 'x' THEN 'x' WHEN 'tg' THEN 'telegram' "
        "        WHEN 'wa' THEN 'whatsapp' ELSE messages.channel END) "
        "ORDER BY id ASC LIMIT ?",
        (*CHANNELS, FAIL_LIMIT, limit * 4),
    ).fetchall()
    out = []
    for m in rows:
        if recently_contacted(conn, m["person_id"], m["id"]):
            log(f"  [skip] #{m['id']} person={m['person_id']} 近7天已联系过, 跳过")
            continue
        out.append(m)
        if len(out) >= limit:
            break
    return out


def send_one(conn, mid, channel):
    """子进程调 sender.py(复用其 approved 门禁与 sent 状态流转), 返回 (ok, detail)。"""
    try:
        r = subprocess.run(
            [PY, str(BASE / "sender.py"), "send", str(mid), "--channel", channel],
            capture_output=True, text=True, timeout=SENDER_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    detail = (r.stdout or r.stderr or "").strip()
    tail = detail.replace("\n", " | ")[-300:] if detail else f"exit={r.returncode}"
    return r.returncode == 0, tail


def run_once():
    if not ENABLED:
        log("[auto_send] 开关关闭 (AUTO_SEND_ENABLED=false), 本轮退出")
        return
    lock = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("[auto_send] 上一轮仍在运行(锁被占用), 本轮退出")
        return
    conn = get_conn()
    sent = window_sent(conn)
    # WINDOW_MAX<=0 表示不限量(付费服务默认); >0 时作为可选保护
    if WINDOW_MAX > 0:
        remaining = WINDOW_MAX - sent
        if remaining <= 0:
            log(f"[auto_send] 窗口内已发 {sent}/{WINDOW_MAX}, 已满, 本轮退出")
            conn.close()
            return
    else:
        remaining = None
    if BATCH_MAX > 0:
        batch = BATCH_MAX if remaining is None else min(BATCH_MAX, remaining)
    else:
        batch = remaining or 10 ** 6
    cands = pick_candidates(conn, batch)
    if not cands:
        tail = f"无待发 approved 消息" + (f"(窗口余量 {remaining})" if remaining is not None else "")
        log(f"[auto_send] {tail}, 本轮退出")
        conn.close()
        return
    quota = f"窗口已发 {sent}" if WINDOW_MAX <= 0 else f"窗口已发 {sent}/{WINDOW_MAX}"
    log(f"[auto_send] 本轮 {len(cands)} 条 ({quota})")
    for i, m in enumerate(cands):
        ok, detail = send_one(conn, m["id"], m["channel"])
        if ok:
            log(f"  [ok] #{m['id']} channel={m['channel']} person={m['person_id']} -> sent")
            log_op("auto_send", f"message_id={m['id']} channel={m['channel']}", "ok")
        else:
            fails = (m["send_fail_count"] or 0) + 1
            conn.execute(
                "UPDATE messages SET send_fail_count=? WHERE id=?",
                (fails, m["id"]),
            )
            conn.commit()
            log(f"  [fail] #{m['id']} channel={m['channel']} 第{fails}次失败: {detail}")
            log_op("auto_send.fail", f"message_id={m['id']} channel={m['channel']}", detail)
        if i < len(cands) - 1:
            if ok:
                log("  [next] 连发, 无间隔")
            else:
                log("  [wait] 上一条失败, 5s 后继续")
                time.sleep(5)
    conn.close()


def show_status():
    conn = get_conn()
    sent = window_sent(conn)
    rows = {}
    for s in ("pending", "approved", "sent"):
        rows[s] = conn.execute("SELECT COUNT(*) FROM messages WHERE status=?", (s,)).fetchone()[0]
    skipped = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE status='approved' "
        "AND (COALESCE(credit_exhausted,0)=1 OR COALESCE(send_fail_count,0)>=?)",
        (FAIL_LIMIT,),
    ).fetchone()[0]
    conn.close()
    print(f"开关(AUTO_SEND_ENABLED): {'开启' if ENABLED else '关闭'}")
    if WINDOW_MAX > 0:
        print(f"窗口限速(可选保护): {WINDOW_HOURS}小时内 {WINDOW_MAX}条 | 已发: {sent} | 余量: {WINDOW_MAX - sent}")
    else:
        print(f"窗口限速: 不限量(付费服务) | 近{WINDOW_HOURS}小时已发(真实渠道): {sent}")
    print(f"发送节奏: 连发(成功无间隔, 失败停顿5s) | 每轮上限: {'不限' if BATCH_MAX <= 0 else str(BATCH_MAX) + '条'} | 失败跳过: {FAIL_LIMIT}次")
    print(f"队列: pending={rows['pending']} approved={rows['approved']} sent={rows['sent']}")
    print(f"approved 中已熔断/超失败限制(需手动): {skipped}")


def main():
    p = argparse.ArgumentParser(description="自动发送调度")
    p.add_argument("--status", action="store_true", help="只查看状态, 不发送")
    args = p.parse_args()
    if args.status:
        show_status()
    else:
        run_once()


if __name__ == "__main__":
    main()
