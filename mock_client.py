#!/usr/bin/env python3
"""模拟通道 mock_client.py — 不联网, 用来演练/验收全链路。

用法:
  python mock_client.py send <message_id>    # 标记为已发送(仅 approved 可发)
  python mock_client.py reply <message_id> "text"   # 模拟对方回复
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, "/opt/leads")
from db import get_conn, log_op

BASE = Path("/opt/leads")


def do_send_mock(mid):
    conn = get_conn()
    m = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    if not m:
        print(f"  [mock] [{mid}] 消息不存在")
        conn.close()
        return False
    if m["status"] != "approved":
        print(f"  [mock] [{mid}] 状态={m['status']!r}, 必须 approved 才能发送(已拦截)")
        conn.close()
        return False
    conn.execute(
        "UPDATE messages SET status='sent', sent_at=datetime('now') WHERE id=?",
        (mid,),
    )
    conn.commit()
    conn.close()
    log_op("mock.send", f"message_id={mid}", "ok")
    print(f"  [mock] [{mid}] 已模拟发送")
    return True


def do_reply_mock(mid, text):
    conn = get_conn()
    m = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    if not m:
        print(f"  [mock] [{mid}] 消息不存在")
        conn.close()
        sys.exit(1)
    if m["status"] not in ("sent", "replied"):
        print(f"  [mock] [{mid}] 状态={m['status']!r}, 仅已发送的消息可模拟回复")
        conn.close()
        sys.exit(1)
    conn.execute(
        "UPDATE messages SET reply_content=?, status='replied' WHERE id=?",
        (text, mid),
    )
    from db import record_reply
    record_reply(conn, mid)
    conn.commit()
    conn.close()
    log_op("mock.reply", f"message_id={mid}", "ok")
    print(f"  [mock] [{mid}] 已模拟客户回复: {text[:60]}")
    return True


def main():
    p = argparse.ArgumentParser(description="模拟通道")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("send", help="模拟发送")
    s.add_argument("mid", type=int)
    r = sub.add_parser("reply", help="模拟对方回复")
    r.add_argument("mid", type=int)
    r.add_argument("text")
    args = p.parse_args()
    if args.cmd == "send":
        sys.exit(0 if do_send_mock(args.mid) else 1)
    elif args.cmd == "reply":
        sys.exit(0 if do_reply_mock(args.mid, args.text) else 1)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
