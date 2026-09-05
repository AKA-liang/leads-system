#!/usr/bin/env python3
"""x_reply.py — X 帖子互动(引导关注 → 解锁 DM)。

流程:
  1. 输入目标 X 用户名/ID
  2. 拉取他最近帖子
  3. 选 1 条有内容的帖子, DeepSeek 起草个性化回复(引用原话, 提问式, 像真人)
  4. 展示给你审核 → approve 后回复 → 标记 accounts.raw replied=1

用法:
  python x_reply.py <username_or_id>            # 完整流程(起草后等你审核)
  python x_reply.py --dry <username_or_id>      # 只拉帖+起草, 不发送
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, "/opt/leads")
from dotenv import load_dotenv
from db import get_conn, log_op
from x_client import lookup_user, user_tweets, reply_tweet

BASE = Path("/opt/leads")
load_dotenv(BASE / ".env")

DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"


def call_deepseek(text):
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an English-speaking retail futures trader. Your goal: reply to another trader's post on X in a natural, human way so they notice you. Hard rules:\n"
                    "- Reference their specific content\n"
                    "- Casual conversational tone (no AI-sounding phrasing)\n"
                    "- End with ONE specific question\n"
                    "- No selling, no self-promotion, no lead-gen language\n"
                    "- 2-3 sentences, 60-120 English words. Reply in English only."
                ),
            },
            {"role": "user", "content": text},
        ],
        "temperature": 0.7,
        "max_tokens": 8000,
    }
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {DEEPSEEK_KEY}"},
    )
    for _try in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
            _c = data["choices"][0]["message"]["content"].strip()
            if _c:
                return _c
            print("[retry] 空响应, 重试", flush=True)
        except Exception as _e:
            print(f"[retry] {_e}", flush=True)
        import time as _t
        _t.sleep(2)
    return ""


def resolve_user_id(target):
    if target.isdigit():
        return target, target
    r = lookup_user(target.lstrip("@"))
    if r.status_code == 200:
        d = r.json()["data"]
        return d["id"], d.get("username", target)
    print(f"lookup 失败: {r.status_code} {r.text[:120]}")
    sys.exit(1)


def pick_tweet(tweets):
    """选 1 条适合回复的帖子: 排除 RT/转推/纯链接/非英文, 优先有内容有互动的。"""
    best = None
    for t in tweets:
        text = t.get("text", "")
        if not text or text.startswith("RT @") or text.strip().startswith("@"):
            continue
        # 简单的英文检测
        if not re.search(r"[A-Za-z]{4,}", text):
            continue
        if len(text) < 30:
            continue
        metrics = t.get("public_metrics") or {}
        score = metrics.get("like_count", 0) + metrics.get("reply_count", 0)
        if best is None or score > best[0]:
            best = (score, t)
    if not best:
        return None
    return best[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("target", help="X 用户名或用户ID")
    p.add_argument("--dry", action="store_true", help="只起草不发送")
    p.add_argument("--tweet-id", default="", help="指定回复某条帖子ID(跳过自动选帖)")
    args = p.parse_args()

    uid, uname = resolve_user_id(args.target)
    print(f"目标: @{uname} (id={uid})", flush=True)

    tweets = user_tweets(uid, "5")
    if not tweets:
        print("无可用帖子")
        sys.exit(1)

    t = None
    if args.tweet_id:
        for x in tweets:
            if x["id"] == args.tweet_id:
                t = x
                break
        if not t:
            print(f"帖子 {args.tweet_id} 不在最近5条里")
            sys.exit(1)
    else:
        t = pick_tweet(tweets)
        if not t:
            print("没有适合回复的帖子(全转推/非英文/太短)")
            sys.exit(1)

    print(f"\n=== 选中帖子 ===\n[{t['id']}] {t.get('text','')[:200]}\n")

    draft = call_deepseek(
        f"对方最近的一条交易相关帖子:\n{t.get('text','')[:500]}\n\n"
        f"请起草一条回复(英文, 2-3句, 引用帖中内容, 结尾提问):"
    )
    print(f"=== 起草的回复 ===\n{draft}\n")

    if args.dry:
        print("[dry] 未发送。")
        sys.exit(0)

    print("审核: [a]pprove 发送 / [r]eject 拒绝 / 输入修改意见:")
    choice = input("> ").strip().lower()
    if choice.startswith("a"):
        ok = reply_tweet(t["id"], draft)
        if ok:
            # 标记已互动
            conn = get_conn()
            conn.execute(
                "UPDATE accounts SET raw=json_set(COALESCE(raw,'{}'),'$.replied',1,'$.replied_at',date('now')) "
                "WHERE platform='x' AND username=?",
                ("@" + uname if not uname.startswith("@") else uname,),
            )
            conn.commit()
            conn.close()
            log_op("x.reply", f"user={uname} tweet={t['id']}", "ok")
            print("✅ 已回复并标记 replied=1")
    else:
        print("已取消, 未发送")


if __name__ == "__main__":
    main()
