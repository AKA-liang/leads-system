#!/usr/bin/env python3
"""Telegram 官方 Bot API 客户端。

用法:
  python tg_client.py dm @username "text"   # 发私聊(免费, 无模板限制)
  python tg_client.py listen                # 长轮询收回复, 写回 leads.db

依赖 .env: TG_BOT_TOKEN(从 @BotFather 获取)
"""
import argparse
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, "/opt/leads")
from db import get_conn, log_op

BASE = Path("/opt/leads")
load_dotenv(BASE / ".env")

TOKEN = os.getenv("TG_BOT_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}"
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
LAST_OFFSET_FILE = BASE / "data" / "tg_offset.txt"

_last_error = ""


def api(method, payload=None, retries=3):
    global _last_error
    if not TOKEN:
        _last_error = "NO_TG_BOT_TOKEN (请先在 .env 填 TG_BOT_TOKEN)"
        print(_last_error)
        return None
    last = None
    for i in range(retries):
        try:
            r = requests.post(
                f"{API}/{method}", json=payload or {}, proxies=PROXY, timeout=45
            )
            if r.status_code == 409:
                _last_error = f"TG {method} HTTP 409 (其他实例占用)"
                print(f"{_last_error}, 重试 {i + 1}/{retries}")
                time.sleep(2)
                continue
            if r.status_code != 200:
                _last_error = f"TG {method} HTTP {r.status_code}: {r.text[:200]}"
                print(_last_error)
                time.sleep(2)
                continue
            j = r.json()
            if not j.get("ok"):
                _last_error = f"TG {method} error: {j.get('description')}"
                print(_last_error)
                time.sleep(2)
                continue
            return j["result"]
        except Exception as e:
            last = e
            _last_error = f"TG {method} 异常({type(e).__name__}): {e}"
            print(f"TG {method} 异常(重试 {i + 1}/{retries}): {type(e).__name__}")
            time.sleep(3)
    if last:
        _last_error = f"{_last_error} | 最终: {last}"
        print(f"TG {method} 最终失败: {last}")
    return None


def send_dm(username, text):
    """username: @xxx 或纯用户名; 返回 (ok, 错误详情)"""
    name = username.lstrip("@")
    res = api("sendMessage", {"chat_id": name, "text": text})
    if not res:
        return False, (_last_error or "TG 发送失败")
    print(f"  [tg] 已发送给 @{name}, chat_id={res.get('chat', {}).get('id')}")
    return True, ""


def handle_new_contact(conn, chat_id, username, first_name, text):
    """新客户主动点开 bot(私域入口): 建档 + 自动回复欢迎语。"""
    from db import get_or_create_person, upsert_account
    name = username or f"tg_{chat_id}"
    pid = get_or_create_person(conn, name)
    upsert_account(
        conn, pid, "telegram", name,
        profile_url=f"https://t.me/{name}" if username else "",
        context=f"chat_id={chat_id}",
        matched_by="tg_self_start",
        raw=f'{{"first_name": "{first_name}", "first_msg": "{text[:200]}"}}',
    )
    conn.commit()
    log_op("tg.new_contact", f"person={name} chat_id={chat_id}", "welcome_sent")
    print(f"  [tg] 新客户建档: {name} (chat_id={chat_id}), 发送欢迎语")
    api("sendMessage", {"chat_id": chat_id, "text": WELCOME_TEMPLATE.format(name=first_name)})


def _find_target_message(conn, chat_id, username):
    """按 chat_id 优先、username 兜底, 找最近的已发送消息。"""
    rows = conn.execute(
        "SELECT id FROM messages WHERE channel='tg' AND status='sent' "
        "AND recipient=? ORDER BY id DESC LIMIT 1",
        (str(chat_id),),
    ).fetchall()
    if rows:
        return rows[0]["id"]
    if username:
        rows = conn.execute(
            "SELECT id FROM messages WHERE channel='tg' AND status='sent' "
            "AND recipient=? ORDER BY id DESC LIMIT 1",
            ("@" + username,),
        ).fetchall()
        if rows:
            return rows[0]["id"]
    return None


WELCOME_TEMPLATE = (
    "Hi {name}! 👋 I'm a trader's corner bot — I share ideas on futures, gold, ES/NQ "
    "and general market structure.\n\n"
    "Quick questions so I can point you to the right stuff:\n"
    "1️⃣ What markets do you trade? (GC / ES / NQ / CL / options...)\n"
    "2️⃣ Day trading or swing?\n"
    "3️⃣ Which platform do you hang out on most — TradingView, X, Reddit?\n\n"
    "Just reply here whenever. No spam, I promise. 🙌"
)


def handle_message(msg):
    if not msg or "text" not in msg:
        return
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    username = chat.get("username") or msg.get("from", {}).get("username", "")
    first_name = msg.get("from", {}).get("first_name") or chat.get("first_name") or username or "there"
    text = msg.get("text", "")[:2000]
    conn = get_conn()
    mid = _find_target_message(conn, chat_id, username)
    if not mid:
        # 新客户主动找 bot: 建档 + 回复欢迎语(私域入口)
        handle_new_contact(conn, chat_id, username, first_name, text)
        conn.close()
        return
    conn.execute(
        "UPDATE messages SET reply_content=?, status='replied' WHERE id=?",
        (text, mid),
    )
    from db import record_reply
    record_reply(conn, mid)
    conn.commit()
    conn.close()
    log_op("reply.received", f"message_id={mid} channel=tg", "ok")
    print(f"  [tg] [{mid}] 收到回复 from={username}: {text[:60]}")


def listen_once():
    offset = 0
    if LAST_OFFSET_FILE.exists():
        offset = int(LAST_OFFSET_FILE.read_text().strip() or "0")
    # 短轮询(timeout=2s): 代理对长连接不稳定, 每次都用新连接更可靠
    res = api("getUpdates", {"offset": offset, "timeout": 2}, retries=2)
    if not res:
        return
    for u in res:
        upd_id = u["update_id"]
        if "message" in u:
            handle_message(u["message"])
        elif "edited_message" in u:
            handle_message(u["edited_message"])
        offset = upd_id + 1
    LAST_OFFSET_FILE.write_text(str(offset))


def listen():
    print("  [tg] 开始长轮询收回复 (Ctrl+C 退出)")
    while True:
        try:
            listen_once()
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"  [tg] 轮询异常: {e}")
            time.sleep(5)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    d = sub.add_parser("dm")
    d.add_argument("username")
    d.add_argument("text")
    sub.add_parser("listen")
    args = p.parse_args()
    if args.cmd == "dm":
        ok, err = send_dm(args.username, args.text)
        sys.exit(0 if ok else 1)
    elif args.cmd == "listen":
        listen()
    else:
        p.print_help()


if __name__ == "__main__":
    main()
