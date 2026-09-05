#!/usr/bin/env python3
"""统一发送网关 sender.py — 所有通道发送的唯一入口。

用法:
  python sender.py send <message_id> --channel x|tg|wa|mock

规则(代码层强制):
  - 消息必须 status='approved' 才能发送, 否则拦截
  - 发送成功 -> status='sent' + sent_at, ops_log 留痕
  - channel 缺省 mock(不联网, 模拟发送)
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/opt/leads")
from db import get_conn, log_op

BASE = Path("/opt/leads")
PY = str(BASE / "venv" / "bin" / "python")


def ensure_recipient_col():
    conn = get_conn()
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(messages)")]
    if "recipient" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN recipient TEXT DEFAULT ''")
    if "send_fail_count" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN send_fail_count INTEGER DEFAULT 0")
    if "credit_exhausted" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN credit_exhausted INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


def get_recipient(conn, mid):
    """从 accounts 表反查该消息对应的对方地址(按 channel)。"""
    m = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    if not m:
        return None, None
    ch = m["channel"] or "mock"
    # mock 通道: 无真实地址
    if ch == "mock":
        return m, None
    platform_map = {"x": "x", "tg": "telegram", "wa": "whatsapp"}
    plat = platform_map.get(ch, ch)
    row = conn.execute(
        "SELECT username FROM accounts WHERE person_id=? AND platform=? ORDER BY id LIMIT 1",
        (m["person_id"], plat),
    ).fetchone()
    return m, (row["username"] if row else None)


def send(mid, channel):
    ensure_recipient_col()
    conn = get_conn()
    m = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    if not m:
        print(f"  [send] [{mid}] 消息不存在")
        sys.exit(1)
    if m["status"] != "approved":
        print(f"  [send] [{mid}] 状态={m['status']!r} 未通过审核, 已拦截(必须 approved 才能发送)")
        sys.exit(1)
    text = m["content"]
    if not text:
        print(f"  [send] [{mid}] 消息内容为空")
        sys.exit(1)

    if channel == "mock":
        from mock_client import do_send_mock
        ok, err = do_send_mock(mid), ""
    elif channel == "tg":
        # 先存 recipient 占位: 发送后由 tg 客户端回填 chat_id
        ok, err = run_tg_dm(conn, m, text)
    elif channel == "wa":
        ok, err = run_wa_dm(conn, m, text)
    elif channel == "x":
        ok, err = run_x_dm(conn, m, text)
    elif channel == "reddit":
        ok, err = run_reddit_dm(conn, m, text)
    else:
        print(f"  [send] 未知通道 {channel!r} (可选: x|tg|wa|mock|reddit)")
        sys.exit(1)

    if ok:
        conn.execute(
            "UPDATE messages SET status='sent', sent_at=datetime('now') WHERE id=?",
            (mid,),
        )
        conn.execute(
            "UPDATE persons SET n_msgs_sent = COALESCE(n_msgs_sent, 0) + 1 WHERE id=?",
            (m["person_id"],),
        )
        conn.commit()
        log_op("send", f"message_id={mid} channel={channel}", "ok")
        print(f"  [send] [{mid}] 已通过 {channel} 发送")
    else:
        log_op("send.fail", f"message_id={mid} channel={channel}", err or "failed")
        print(f"  [send] [{mid}] {channel} 发送失败: {err}")
        sys.exit(1)
    conn.close()


def run_tg_dm(conn, m, text):
    from tg_client import send_dm
    recip = conn.execute(
        "SELECT username FROM accounts WHERE person_id=? AND platform='telegram' ORDER BY id LIMIT 1",
        (m["person_id"],),
    ).fetchone()
    if not recip or not recip["username"]:
        err = f"person 没有 telegram 账号, 先补 accounts 或用 fetch 抓取"
        print(f"  [send] [{m['id']}] {err}")
        return False, err
    return send_dm(recip["username"], text)


def run_wa_dm(conn, m, text):
    from wa_client import send_wa
    recip = conn.execute(
        "SELECT username FROM accounts WHERE person_id=? AND platform='whatsapp' ORDER BY id LIMIT 1",
        (m["person_id"],),
    ).fetchone()
    if not recip or not recip["username"]:
        err = f"person 没有 whatsapp 账号"
        print(f"  [send] [{m['id']}] {err}")
        return False, err
    return send_wa(recip["username"], text)


def run_x_dm(conn, m, text):
    """X 私信(经 x_client.py dm, 内部会 username->user_id 解析)。
    402(credits 耗尽) -> 标记 credit_exhausted, 自动通道将熔断不再尝试。
    """
    recip = conn.execute(
        "SELECT username FROM accounts WHERE person_id=? AND platform='x' ORDER BY id LIMIT 1",
        (m["person_id"],),
    ).fetchone()
    if not recip or not recip["username"]:
        err = f"person 没有 x 账号, 先运行 ops.sh xleads 生成匹配"
        print(f"  [send] [{m['id']}] {err}")
        return False, err
    try:
        r = subprocess.run(
            [PY, str(BASE / "x_client.py"), "dm", recip["username"], text],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        err = "X API 调用超时(>120s)"
        print(f"  [send] [{m['id']}] {err}")
        return False, err
    out = (r.stdout or r.stderr or "").strip()
    print("  [x_client]", out[:300])
    if r.returncode == 0:
        return True, ""
    # 解析具体原因(状态码 + 响应片段), 402 = credits 耗尽
    detail = out.replace("\n", " | ")[-300:] or f"exit={r.returncode}"
    if "402" in out or "credits" in out.lower():
        conn.execute("UPDATE messages SET credit_exhausted=1 WHERE id=?", (m["id"],))
        conn.commit()
        detail = f"X credits 耗尽(402), 已标记熔断 | {detail}"
    elif "404" in out:
        detail = f"X 用户不存在(404) | {detail}"
    elif "403" in out:
        detail = f"X 对方拒收/无权发送(403) | {detail}"
    elif "429" in out:
        detail = f"X 限流(429) | {detail}"
    elif "401" in out:
        detail = f"X 凭据无效(401) | {detail}"
    return False, detail


def run_reddit_dm(conn, m, text):
    from reddit_client import send_dm
    recip = conn.execute(
        "SELECT username FROM accounts WHERE person_id=? AND platform='reddit' ORDER BY id LIMIT 1",
        (m["person_id"],),
    ).fetchone()
    if not recip or not recip["username"]:
        err = f"person 没有 reddit 账号"
        print(f"  [send] [{m['id']}] {err}")
        return False, err
    return send_dm(recip["username"], text)


def main():
    p = argparse.ArgumentParser(description="统一发送网关")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("send", help="发送消息")
    s.add_argument("mid", type=int)
    s.add_argument("--channel", default="mock", choices=["x", "tg", "wa", "mock", "reddit"])
    args = p.parse_args()
    if args.cmd == "send":
        send(args.mid, args.channel)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
