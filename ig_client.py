#!/usr/bin/env python3
"""ig_client.py — Instagram 全触达(基于 Apify actor, 无需 Meta API)。

能力:
  status   验证 cookie 登录态(零成本, 直接调 IG 接口)
  dm       发私信(api402/ig-bulk-dm, 消息随机化+智能延迟+住宅代理)
  comment  评论帖子(mikolabs/ig-post-reel-comment-bot, 真人打字延迟)

凭据: .env IG_COOKIES(浏览器导出的 IG cookies JSON)
护栏:
  - 每日 DM 上限 40 条 / 评论上限 20 条(actor 官方安全建议)
  - 消息间隔 90-180 秒(随机)
  - 内容必须经审核(由调用方/阿仓执行 approve 流)
成本: DM $0.012/条 · 评论 $0.005/条
"""
import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import requests
from apify_client import ApifyClient
from dotenv import load_dotenv

sys.path.insert(0, "/opt/leads")
from db import get_conn, log_op

BASE = Path("/opt/leads")
load_dotenv(BASE / ".env")

IG_API = "https://i.instagram.com/api/v1"
UA = "Instagram 275.0.0.27.98 Android (33/13; 420dpi; 1080x2340; samsung; SM-S911B; s911b; qcom; en_US; 422)"
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
DAILY_LIMIT_FILE = BASE / "data" / "ig_sent_today.json"

DM_ACTOR = "api402/ig-bulk-dm"
COMMENT_ACTOR = "mikolabs/ig-post-reel-comment-bot"
DM_DAILY_MAX = int(os.getenv("IG_DM_DAILY_MAX", "40"))
COMMENT_DAILY_MAX = int(os.getenv("IG_COMMENT_DAILY_MAX", "20"))


def get_cookies():
    raw = os.getenv("IG_COOKIES", "")
    if not raw:
        print("NO_IG_COOKIES, 先在 .env 配置 IG_COOKIES")
        sys.exit(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print("IG_COOKIES 格式错误(应为 JSON 数组)")
        sys.exit(1)


def ig_headers():
    cookies = get_cookies()
    sess = {c["name"]: c["value"] for c in cookies if c["name"] in ("sessionid", "csrftoken", "ds_user_id", "mid", "ig_did")}
    return {"User-Agent": UA, "X-IG-App-ID": "936619743392459", "Cookie": "; ".join(f"{k}={v}" for k, v in sess.items())}


def status():
    """零成本验证 cookie 登录态。"""
    r = requests.get(f"{IG_API}/accounts/current_user/", headers=ig_headers(), proxies=PROXY, timeout=25)
    if r.status_code == 200:
        d = r.json().get("user", {})
        print(f"✅ IG 登录有效: @{d.get('username')} (id={d.get('pk')})")
        return True
    print(f"❌ IG 登录无效: {r.status_code} {r.text[:150]}")
    return False


def _check_daily_limit(kind):
    """每日限额检查(kind: dm/comment)。"""
    today = time.strftime("%Y-%m-%d")
    counts = {}
    if DAILY_LIMIT_FILE.exists():
        counts = json.loads(DAILY_LIMIT_FILE.read_text())
    day = counts.get(today, {"dm": 0, "comment": 0})
    limit = DM_DAILY_MAX if kind == "dm" else COMMENT_DAILY_MAX
    if day[kind] >= limit:
        print(f"[ig] 今日 {kind} 已达上限 {limit}, 拒绝执行")
        return False
    return True


def _bump(kind, n=1):
    today = time.strftime("%Y-%m-%d")
    counts = {}
    if DAILY_LIMIT_FILE.exists():
        counts = json.loads(DAILY_LIMIT_FILE.read_text())
    day = counts.setdefault(today, {"dm": 0, "comment": 0})
    day[kind] += n
    counts[today] = day
    DAILY_LIMIT_FILE.write_text(json.dumps(counts))


def run_apify_actor(actor_id, run_input, label):
    token = os.getenv("APIFY_TOKEN", "")
    if not token:
        print("NO_APIFY_TOKEN")
        return False
    client = ApifyClient(token)
    print(f"=== 启动 {label} (actor: {actor_id}) ===", flush=True)
    run = client.actor(actor_id).call(run_input=run_input)
    spent = run.get("usageTotalUsd", 0)
    print(f"status={run.get('status')} 花费=${spent:.3f}", flush=True)
    if run.get("status") != "SUCCEEDED":
        print("RUN_FAILED")
        return False
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    for it in items[:10]:
        print("  ", json.dumps(it, ensure_ascii=False)[:200])
    return True


def spintax(text, n=1):
    """生成 n 个随机变体(支持 {a|b|c} 语法)。"""
    variants = []
    for _ in range(n):
        out = text
        while "{" in out:
            s = out.find("{")
            e = out.find("}", s)
            if e == -1:
                break
            opts = out[s + 1:e].split("|")
            out = out[:s] + random.choice(opts) + out[e + 1:]
        variants.append(out)
    return variants


def dm(usernames, message, min_delay=90, max_delay=180):
    """发 IG 私信。usernames: 逗号分隔。message: 支持 {a|b} 随机变体。"""
    if not _check_daily_limit("dm"):
        return False
    users = [u.strip() for u in usernames.split(",") if u.strip()]
    if not users:
        print("需要至少一个用户名")
        return False
    print(f"计划发送 {len(users)} 人, 间隔 {min_delay}-{max_delay}s")
    cookies = get_cookies()
    run_input = {
        "usernames": users,
        "message": message,
        "min_delay": min_delay,
        "max_delay": max_delay,
        "cookies": json.dumps(cookies),
    }
    ok = run_apify_actor(DM_ACTOR, run_input, f"IG DM {len(users)}人")
    if ok:
        _bump("dm", len(users))
        log_op("ig.dm", f"to={len(users)} users", "ok")
    return ok


def comment(post_urls, message):
    """评论 IG 帖子/Reels。post_urls: 逗号分隔的 URL。message: 支持 {a|b}。"""
    if not _check_daily_limit("comment"):
        return False
    urls = [u.strip() for u in post_urls.split(",") if u.strip()]
    if not urls:
        print("需要至少一个帖子URL")
        return False
    cookies = get_cookies()
    run_input = {
        "postUrls": [{"url": u} for u in urls],
        "commentMessage": message,
        "cookies": cookies,
        "slowdownMaxMs": 1800,
        "typingDelayMaxMs": 140,
        "maxRetries": 2,
    }
    ok = run_apify_actor(COMMENT_ACTOR, run_input, f"IG 评论 {len(urls)}帖")
    if ok:
        _bump("comment", len(urls))
        log_op("ig.comment", f"posts={len(urls)}", "ok")
    return ok


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("status")
    d = sub.add_parser("dm")
    d.add_argument("usernames")
    d.add_argument("message")
    d.add_argument("--min-delay", type=int, default=90)
    d.add_argument("--max-delay", type=int, default=180)
    c = sub.add_parser("comment")
    c.add_argument("post_urls")
    c.add_argument("message")
    args = p.parse_args()

    if args.cmd == "status":
        status()
    elif args.cmd == "dm":
        sys.exit(0 if dm(args.usernames, args.message, args.min_delay, args.max_delay) else 1)
    elif args.cmd == "comment":
        sys.exit(0 if comment(args.post_urls, args.message) else 1)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
