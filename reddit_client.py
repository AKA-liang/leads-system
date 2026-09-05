#!/usr/bin/env python3
"""Reddit 私信客户端 — 官方 API (script app, 免费)。

用法:
  python reddit_client.py login            # 用 REDDIT_USER/REDDIT_PASS 换 token 存 .env
  python reddit_client.py lookup @用户名    # 查用户是否存在
  python reddit_client.py dm <用户名> "text"  # 发私信
  python reddit_client.py inbox            # 读私信回复(写回 leads.db)

凭据(.env):
  REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET  (reddit.com/prefs/apps 创建 script 应用)
  REDDIT_USER / REDDIT_PASS                (账号密码, 仅用于换 token)
  REDDIT_TOKEN                             (登录后自动写入)

护栏:
  - 发送频率: .env REDDIT_DAILY_LIMIT(默认10条/天), 超限拒绝
  - 发送内容必须经审核队列(走 sender.py --channel reddit)
"""
import argparse
import base64
import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, "/opt/leads")
from db import get_conn, log_op

BASE = Path("/opt/leads")
load_dotenv(BASE / ".env")

AUTH_URL = "https://www.reddit.com/api/v1/access_token"
API_URL = "https://oauth.reddit.com"
UA = f"linux:leads-bot:v1.0 (by /u/{os.getenv('REDDIT_USER', 'leadsbot')})"
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

DAILY_LIMIT_FILE = BASE / "data" / "reddit_sent_today.txt"


def get_token():
    token = os.getenv("REDDIT_TOKEN", "")
    if not token:
        print("NO_REDDIT_TOKEN, 先运行: python reddit_client.py login")
        sys.exit(1)
    return token


def auth_headers():
    return {"Authorization": f"bearer {get_token()}", "User-Agent": UA}


def login():
    cid = os.getenv("REDDIT_CLIENT_ID", "")
    csec = os.getenv("REDDIT_CLIENT_SECRET", "")
    user = os.getenv("REDDIT_USER", "")
    pwd = os.getenv("REDDIT_PASS", "")
    if not (cid and csec and user and pwd):
        print("缺少 .env 凭据: REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET / REDDIT_USER / REDDIT_PASS")
        sys.exit(1)
    basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    r = requests.post(
        AUTH_URL,
        data={"grant_type": "password", "username": user, "password": pwd},
        headers={"Authorization": f"Basic {basic}", "User-Agent": UA},
        proxies=PROXY,
        timeout=25,
    )
    if r.status_code != 200:
        print(f"登录失败: {r.status_code} {r.text[:200]}")
        sys.exit(1)
    token = r.json()["access_token"]
    # 更新 .env
    env_path = BASE / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines()
    found = False
    for i, l in enumerate(lines):
        if l.startswith("REDDIT_TOKEN="):
            lines[i] = f"REDDIT_TOKEN={token}"
            found = True
            break
    if not found:
        lines.append(f"REDDIT_TOKEN={token}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("登录成功, token 已写入 .env")
    # 验证
    me = requests.get(f"{API_URL}/api/v1/me", headers=auth_headers(), proxies=PROXY, timeout=25)
    print(f"身份: {me.json().get('name', '?') if me.status_code == 200 else me.status_code}")


def oauth_auth_url():
    """生成授权 URL, 用户在浏览器打开并同意后拿到 code。"""
    cid = os.getenv("REDDIT_CLIENT_ID", "")
    if not cid:
        print("缺少 REDDIT_CLIENT_ID")
        sys.exit(1)
    url = (
        "https://www.reddit.com/api/v1/authorize"
        f"?client_id={cid}"
        "&response_type=code"
        "&state=leads_bot_state"
        "&redirect_uri=http://localhost:8080"
        "&duration=permanent"
        "&scope=privatemessages,read,identity"
    )
    print("=== 请在浏览器打开以下链接(需已登录 Reddit 账号) ===")
    print(url)
    print()
    print("同意后浏览器会跳转到 http://localhost:8080/?code=XXXX&state=...")
    print("把 URL 里 code= 后面的值发给我(或直接粘贴完整跳转 URL)")


def oauth_exchange_code(code):
    """用授权码换 access_token + refresh_token, 存入 .env。"""
    cid = os.getenv("REDDIT_CLIENT_ID", "")
    csec = os.getenv("REDDIT_CLIENT_SECRET", "")
    if not (cid and csec):
        print("缺少 REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET")
        sys.exit(1)
    basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    r = requests.post(
        AUTH_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://localhost:8080",
        },
        headers={"Authorization": f"Basic {basic}", "User-Agent": UA},
        proxies=PROXY,
        timeout=25,
    )
    if r.status_code != 200:
        print(f"换 token 失败: {r.status_code} {r.text[:300]}")
        sys.exit(1)
    j = r.json()
    token = j.get("access_token", "")
    refresh = j.get("refresh_token", "")
    if not token:
        print(f"响应无 access_token: {j}")
        sys.exit(1)
    env_path = BASE / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines()
    updates = {"REDDIT_TOKEN": token}
    if refresh:
        updates["REDDIT_REFRESH_TOKEN"] = refresh
    for k, v in updates.items():
        found = False
        for i, l in enumerate(lines):
            if l.startswith(k + "="):
                lines[i] = f"{k}={v}"
                found = True
                break
        if not found:
            lines.append(f"{k}={v}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"token 已写入 .env (refresh_token={'有' if refresh else '无'})")
    # 验证
    me = requests.get(f"{API_URL}/api/v1/me", headers=auth_headers(), proxies=PROXY, timeout=25)
    print(f"身份: {me.json().get('name', '?') if me.status_code == 200 else me.status_code}")


def oauth_refresh():
    """用 refresh_token 续期 access_token。"""
    refresh = os.getenv("REDDIT_REFRESH_TOKEN", "")
    cid = os.getenv("REDDIT_CLIENT_ID", "")
    csec = os.getenv("REDDIT_CLIENT_SECRET", "")
    if not (refresh and cid and csec):
        print("缺少 REDDIT_REFRESH_TOKEN 或凭据")
        return False
    basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    r = requests.post(
        AUTH_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh},
        headers={"Authorization": f"Basic {basic}", "User-Agent": UA},
        proxies=PROXY,
        timeout=25,
    )
    if r.status_code != 200:
        print(f"刷新失败: {r.status_code} {r.text[:200]}")
        return False
    token = r.json()["access_token"]
    env_path = BASE / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(lines):
        if l.startswith("REDDIT_TOKEN="):
            lines[i] = f"REDDIT_TOKEN={token}"
            break
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("access_token 已刷新")
    return True


def get_token():
    """带自动续期的 token 获取。"""
    token = os.getenv("REDDIT_TOKEN", "")
    if not token and os.getenv("REDDIT_REFRESH_TOKEN"):
        if oauth_refresh():
            load_dotenv(BASE / ".env", override=True)
            token = os.getenv("REDDIT_TOKEN", "")
    if not token:
        print("NO_REDDIT_TOKEN, 先运行: python reddit_client.py oauth-login")
        sys.exit(1)
    return token


def lookup(username):
    name = username.lstrip("u/").lstrip("/u/").lstrip("@")
    r = requests.get(
        f"{API_URL}/user/{name}/about",
        headers=auth_headers(), proxies=PROXY, timeout=25,
    )
    if r.status_code == 200:
        d = r.json().get("data", {})
        print(f"@{name}: {d.get('comment_karma', 0)} karma, 创建于 {d.get('created_utc', '?')}")
        return True
    print(f"@{name}: {r.status_code} {r.text[:150]}")
    return False


def _check_daily_limit():
    """每日发送上限(默认10), 记录日期, 超限拒绝。"""
    limit = int(os.getenv("REDDIT_DAILY_LIMIT", "10"))
    today = time.strftime("%Y-%m-%d")
    count = 0
    if DAILY_LIMIT_FILE.exists():
        d, n = DAILY_LIMIT_FILE.read_text().strip().split()
        if d == today:
            count = int(n)
    if count >= limit:
        print(f"[reddit] 今日已发 {count} 条, 超过上限 {limit}, 拒绝发送(明日重置)")
        return False
    return True


def _bump_daily_count():
    today = time.strftime("%Y-%m-%d")
    count = 0
    if DAILY_LIMIT_FILE.exists():
        d, n = DAILY_LIMIT_FILE.read_text().strip().split()
        if d == today:
            count = int(n)
    DAILY_LIMIT_FILE.write_text(f"{today} {count + 1}")


def send_dm(username, text):
    """返回 (ok, 错误详情)"""
    name = username.lstrip("u/").lstrip("/u/").lstrip("@")
    if not _check_daily_limit():
        return False, "超过每日发送上限(见 .env REDDIT_DAILY_LIMIT)"
    # 先确认用户存在
    if not lookup(name):
        return False, f"lookup u/{name} 失败(用户不存在)"
    # Reddit 发私信: POST /api/compose (OAuth endpoint)
    r = requests.post(
        f"{API_URL}/api/compose",
        data={"to": name, "subject": "Quick question about your trading", "text": text},
        headers=auth_headers(), proxies=PROXY, timeout=25,
    )
    if r.status_code == 200:
        _bump_daily_count()
        log_op("reddit.dm", f"to={name}", "ok")
        print(f"  [reddit] 已发送给 u/{name}")
        return True, ""
    print(f"  [reddit] 发送失败: {r.status_code} {r.text[:200]}")
    return False, f"Reddit HTTP {r.status_code} {r.text[:150]}"


def inbox():
    """读收件箱, 匹配已发送消息, 写回复到库。"""
    r = requests.get(
        f"{API_URL}/message/inbox",
        headers=auth_headers(), proxies=PROXY, timeout=25,
    )
    if r.status_code != 200:
        print(f"inbox 失败: {r.status_code} {r.text[:200]}")
        return
    msgs = r.json().get("data", {}).get("children", [])
    print(f"收件箱消息: {len(msgs)}")
    conn = get_conn()
    hits = 0
    for item in msgs:
        m = item.get("data", {})
        author = (m.get("author") or "").lstrip("u/")
        body = (m.get("body") or "").strip()
        if not author or not body or author == "[deleted]":
            continue
        if m.get("was_comment"):
            continue  # 只处理私信, 跳过评论回复
        # 找该 author 的 reddit 账号
        acc = conn.execute(
            "SELECT person_id FROM accounts WHERE platform='reddit' AND username=? LIMIT 1",
            (author,),
        ).fetchone()
        if not acc:
            continue
        # 找该 person 最近已发送的 reddit 消息
        msg = conn.execute(
            "SELECT id, reply_content FROM messages WHERE person_id=? AND channel='reddit' "
            "AND status='sent' ORDER BY id DESC LIMIT 1",
            (acc["person_id"],),
        ).fetchone()
        if not msg or msg["reply_content"]:
            continue
        conn.execute(
            "UPDATE messages SET reply_content=?, status='replied' WHERE id=?",
            (body, msg["id"]),
        )
        from db import record_reply
        record_reply(conn, msg["id"])
        conn.commit()
        log_op("reddit.reply", f"message_id={msg['id']} from={author}", "ok")
        print(f"  [{msg['id']}] 收到回复 from=u/{author}: {body[:60]}")
        hits += 1
    conn.close()
    print(f"匹配回复: {hits} 条")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["login", "oauth-login", "oauth-code", "lookup", "dm", "inbox"])
    p.add_argument("arg1", nargs="?", default="")
    p.add_argument("arg2", nargs="?", default="")
    args = p.parse_args()
    if args.cmd == "login":
        login()
    elif args.cmd == "oauth-login":
        oauth_auth_url()
    elif args.cmd == "oauth-code":
        oauth_exchange_code(args.arg1)
    elif args.cmd == "lookup":
        lookup(args.arg1)
    elif args.cmd == "dm":
        ok, err = send_dm(args.arg1, args.arg2)
        if not ok:
            print(f"[reddit] 失败: {err}")
            sys.exit(1)
    elif args.cmd == "inbox":
        inbox()


if __name__ == "__main__":
    main()
