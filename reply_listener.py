#!/usr/bin/env python3
"""统一回复监听 reply_listener.py — 把各通道收到的回复写回 leads.db。

用法:
  python reply_listener.py tg    # Telegram 长轮询(官方 Bot API, 免费)
  python reply_listener.py wa    # 轮询 wa_inbox.jsonl(wa_worker 写入)

回复落库规则:
  - 找到对应已发送(sent)消息 -> 写 reply_content + status='replied' + ops_log
  - 找不到 -> ops_log 记 no_target, 等人工处理
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/leads")
from db import get_conn, log_op, record_reply

BASE = Path("/opt/leads")
WA_INBOX = BASE / "data" / "wa_inbox.jsonl"
PROCESSED = set()


def apply_reply(chat_key, text, channel, platform_hint=""):
    """chat_key: 对方唯一标识(chat_id / jid / username)"""
    conn = get_conn()
    m = conn.execute(
        "SELECT id FROM messages WHERE channel=? AND status='sent' AND recipient=? "
        "ORDER BY id DESC LIMIT 1",
        (channel, chat_key),
    ).fetchone()
    if not m:
        log_op("reply.unknown", f"channel={channel} key={chat_key}", "no_target")
        print(f"  [reply] {channel} 未知会话: {chat_key} -> {text[:50]}")
        conn.close()
        return False
    conn.execute(
        "UPDATE messages SET reply_content=?, status='replied' WHERE id=?",
        (text, m["id"]),
    )
    record_reply(conn, m["id"])
    conn.commit()
    conn.close()
    log_op("reply.received", f"message_id={m['id']} channel={channel}", "ok")
    print(f"  [reply] [{m['id']}] {channel} 回复: {text[:60]}")
    return True


def listen_wa():
    """轮询 wa_worker 写入的 inbox, 去重处理。"""
    print("  [wa] 轮询 wa_inbox (等待 WhatsApp 扫码接入)")
    seen = {}
    if WA_INBOX.exists():
        for line in WA_INBOX.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                e = json.loads(line)
                seen[e.get("id")] = e
            except Exception:
                pass
    while True:
        try:
            if WA_INBOX.exists():
                for line in WA_INBOX.read_text(encoding="utf-8", errors="ignore").splitlines():
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    eid = e.get("id")
                    if eid in seen:
                        continue
                    seen[eid] = e
                    apply_reply(e.get("jid", ""), e.get("text", ""), "wa")
        except Exception as ex:
            print(f"  [wa] 异常: {ex}")
        time.sleep(10)


def listen_x():
    """轮询 X DM(每 15 分钟), 用 dm_inbox 写回复到库。"""
    import subprocess
    print("  [x] 开始轮询 DM (每 15 分钟)")
    while True:
        try:
            r = subprocess.run(
                [sys.executable, str(BASE / "x_client.py"), "dm-inbox"],
                capture_output=True, text=True, timeout=120,
            )
            for line in (r.stdout or "").splitlines():
                print(f"  [x] {line}")
            if r.returncode != 0:
                print(f"  [x] 异常: {(r.stderr or '')[:200]}")
        except Exception as ex:
            print(f"  [x] 异常: {ex}")
        time.sleep(15 * 60)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("channel", choices=["tg", "wa", "x"])
    args = p.parse_args()
    if args.channel == "tg":
        from tg_client import listen
        listen()
    elif args.channel == "wa":
        listen_wa()
    elif args.channel == "x":
        listen_x()


if __name__ == "__main__":
    main()
