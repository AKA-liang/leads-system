#!/usr/bin/env python3
"""X API 客户端封装(读取用 Bearer,发 DM 用 OAuth 1.0a)。

用法:
  python x_client.py lookup EXCAVO            # 按用户名查用户
  python x_client.py dm <user_id> "hi"        # 发私信(需 credits)
  python x_client.py leads                     # 从 TradingView 线索生成 X 线索表

成本参考(官方按量付费):
  读用户 $0.01/个 · 读帖 $0.005/条 · 发 DM $0.015/次
"""
import argparse
import csv
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests_oauthlib import OAuth1

BASE = Path("/opt/leads")
load_dotenv(BASE / ".env")

API = "https://api.x.com/2"
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}


def bearer_auth():
    token = os.getenv("X_BEARER_TOKEN")
    if not token:
        print("NO_X_BEARER_TOKEN")
        sys.exit(1)
    return token


def oauth1_auth():
    return OAuth1(
        os.getenv("X_API_KEY", ""),
        os.getenv("X_API_KEY_SECRET", ""),
        os.getenv("X_ACCESS_TOKEN", ""),
        os.getenv("X_ACCESS_TOKEN_SECRET", ""),
    )


def lookup_user(username):
    r = requests.get(
        f"{API}/users/by/username/{username}",
        params={"user.fields": "id,name,username,description,location,created_at,public_metrics,receives_your_dm,verified"},
        headers={"Authorization": f"Bearer {bearer_auth()}"},
        proxies=PROXY,
        timeout=25,
    )
    return r


def send_dm(user_id, text):
    r = requests.post(
        f"{API}/dm_conversations/with/{user_id}/messages",
        json={"text": text},
        auth=oauth1_auth(),
        proxies=PROXY,
        timeout=25,
    )
    return r


def build_leads(csv_path=BASE / "data" / "tradingview_leads.csv", out=BASE / "data" / "x_leads.csv"):
    """TradingView 线索 → X 用户表: 用 social_x 里的用户名(或作者名)查 X。"""
    rows = []
    with csv_path.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)

    results = []
    for i, r in enumerate(rows):
        name = r["author"]
        # 优先用 social_x 里的用户名;否则用作者名(可能不一致,标注)
        x_username = ""
        sx = (r.get("social_x") or "").strip()
        if sx:
            x_username = sx.rstrip("/").rsplit("/", 1)[-1]
        use = x_username or name
        src = "social_x" if x_username else "guess"
        try:
            resp = lookup_user(use)
            if resp.status_code == 200:
                d = resp.json()["data"]
                results.append({
                    "tv_author": name,
                    "x_username": d.get("username", ""),
                    "x_user_id": d.get("id", ""),
                    "followers": (d.get("public_metrics") or {}).get("followers_count", ""),
                    "description": (d.get("description") or "")[:120],
                    "location": d.get("location", ""),
                    "created_at": d.get("created_at", ""),
                    "receives_your_dm": d.get("receives_your_dm", ""),
                    "matched_by": src,
                    "context": r.get("context", ""),
                })
                print(f"  [{i+1}/{len(rows)}] {name} → @{d.get('username')} id={d.get('id')} "
                      f"dm={d.get('receives_your_dm')} ({src})", flush=True)
            elif resp.status_code == 402:
                print(f"  [{i+1}/{len(rows)}] {name} → @{use}: CREDITS_DEPLETED,充值后重跑", flush=True)
                return results
            else:
                print(f"  [{i+1}/{len(rows)}] {name} → @{use}: {resp.status_code} {resp.text[:120]}", flush=True)
        except Exception as e:
            print(f"  [{i+1}/{len(rows)}] {name} 异常: {e}", flush=True)

    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "tv_author", "x_username", "x_user_id", "followers", "description",
            "location", "created_at", "receives_your_dm", "matched_by", "context",
        ])
        w.writeheader()
        for r in results:
            w.writerow(r)
    print(f"saved: {out} ({len(results)} matched)")
    return results


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["lookup", "dm", "leads", "dm-inbox", "follow", "reply", "user-tweets"])
    p.add_argument("arg1", nargs="?", default="")
    p.add_argument("arg2", nargs="?", default="")
    args = p.parse_args()

    if args.cmd == "lookup":
        resp = lookup_user(args.arg1)
        print(f"status={resp.status_code}")
        print(resp.text[:800])
    elif args.cmd == "dm":
        # 修复: X API 需要数字 user_id; 传入用户名时先 lookup 解析
        user_id = args.arg1
        if not user_id.isdigit():
            lu = lookup_user(user_id.lstrip("@"))
            if lu.status_code != 200:
                print(f"lookup {args.arg1} status={lu.status_code} {lu.text[:300]}")
                sys.exit(1)
            user_id = lu.json()["data"]["id"]
            print(f"lookup {args.arg1} -> id={user_id}")
        resp = send_dm(user_id, args.arg2)
        print(f"status={resp.status_code}")
        print(resp.text[:800])
        if resp.status_code not in (200, 201):
            # 修复: 失败必须非零退出码, 避免 sender.py 误标 sent
            sys.exit(1)
    elif args.cmd == "leads":
        build_leads()
    elif args.cmd == "dm-inbox":
        dm_inbox()
    elif args.cmd == "follow":
        follow_user(args.arg1)
    elif args.cmd == "reply":
        reply_tweet(args.arg1, args.arg2)
    elif args.cmd == "user-tweets":
        user_tweets(args.arg1, args.arg2)




def dm_inbox():
    """拉取 DM 事件, 匹配已发送的 x 消息, 写回复到库。

    X API v2: GET /2/dm_events (OAuth 1.0a, 读取按量计费)
    """
    from db import get_conn, log_op

    r = requests.get(
        f"{API}/dm_events",
        params={"dm_event.fields": "id,text,created_at,sender_id,participant_ids",
                "event_types": "MessageCreate",
                "max_results": 100},
        auth=oauth1_auth(),
        proxies=PROXY,
        timeout=30,
    )
    if r.status_code != 200:
        print(f"status={r.status_code} {r.text[:300]}")
        return
    data = r.json().get("data", [])
    print(f"DM 事件数: {len(data)}")

    conn = get_conn()
    hits = 0
    for ev in data:
        sender_id = ev.get("sender_id", "")
        text = (ev.get("text") or "").strip()
        if not text or not sender_id:
            continue
        # 找该 sender 在 accounts 表中的 x 账号
        acc = conn.execute(
            "SELECT a.person_id, a.username FROM accounts a "
            "WHERE a.platform='x' AND (a.username=? OR a.username=?) LIMIT 1",
            (sender_id, "@" + sender_id),
        ).fetchone()
        if not acc:
            continue
        # 找该 person 最近的已发送 x 消息
        m = conn.execute(
            "SELECT id FROM messages WHERE person_id=? AND channel='x' AND status='sent' "
            "ORDER BY id DESC LIMIT 1",
            (acc["person_id"],),
        ).fetchone()
        if not m:
            continue
        existing = conn.execute(
            "SELECT reply_content FROM messages WHERE id=?", (m["id"],)
        ).fetchone()
        if existing and existing["reply_content"]:
            continue
        conn.execute(
            "UPDATE messages SET reply_content=?, status='replied' WHERE id=?",
            (text, m["id"]),
        )
        from db import record_reply
        record_reply(conn, m["id"])
        conn.commit()
        log_op("x.dm_reply", f"message_id={m['id']} from={sender_id}", "ok")
        print(f"  [{m['id']}] 收到回复 from={sender_id}: {text[:60]}")
        hits += 1
    conn.close()
    print(f"匹配回复: {hits} 条")

def follow_user(username_or_id):
    """关注一个用户(POST /2/users/{id}/following, OAuth1)。

    用途: 对方设置"仅关注者可DM"时, 关注后可解锁私信。
    成本: 约 $0.05/次
    """
    target_id = username_or_id.strip()
    # 如果是用户名, 先查 id
    if not target_id.isdigit():
        r = lookup_user(target_id.lstrip("@"))
        if r.status_code == 200:
            target_id = r.json()["data"]["id"]
        elif r.status_code == 402:
            print("CREDITS_DEPLETED")
            return False
        else:
            print(f"lookup 失败: {r.status_code} {r.text[:120]}")
            return False
    # 自己的 id
    me = requests.get(
        f"{API}/users/me", params={"user.fields": "id"},
        auth=oauth1_auth(), proxies=PROXY, timeout=25,
    )
    if me.status_code != 200:
        print(f"users/me 失败: {me.status_code} {me.text[:120]}")
        return False
    my_id = me.json()["data"]["id"]
    r = requests.post(
        f"{API}/users/{my_id}/following",
        json={"target_user_id": target_id},
        auth=oauth1_auth(),
        proxies=PROXY, timeout=25,
    )
    if r.status_code == 200:
        d = r.json().get("data", {})
        print(f"已关注 {target_id}: following={d.get('following')} pending={d.get('pending_follow')}")
        return True
    print(f"关注失败: {r.status_code} {r.text[:200]}")
    return False




def reply_tweet(tweet_id, text):
    """回复一条帖子(POST /2/tweets with reply.in_reply_to_tweet_id, OAuth1)。

    成本: 约 $0.05/条
    """
    r = requests.post(
        f"{API}/tweets",
        json={"text": text, "reply": {"in_reply_to_tweet_id": tweet_id}},
        auth=oauth1_auth(),
        proxies=PROXY, timeout=25,
    )
    if r.status_code == 201:
        d = r.json().get("data", {})
        print(f"已回复 tweet={tweet_id} -> 新tweet_id={d.get('id')}")
        return True
    print(f"回复失败: {r.status_code} {r.text[:250]}")
    return False


def user_tweets(user_id, max_results="5"):
    """拉取某用户最近帖子(GET /2/users/{id}/tweets, Bearer)。

    成本: 约 $0.005/条
    """
    r = requests.get(
        f"{API}/users/{user_id}/tweets",
        params={"max_results": max_results,
                "tweet.fields": "id,text,created_at,public_metrics"},
        headers={"Authorization": f"Bearer {bearer_auth()}"},
        proxies=PROXY, timeout=25,
    )
    if r.status_code == 200:
        tweets = r.json().get("data", [])
        print(f"拉取 {len(tweets)} 条帖子:")
        for t in tweets:
            likes = (t.get("public_metrics") or {}).get("like_count", 0)
            print(f"  [{t['id']}] (👍{likes}) {t.get('text','')[:120]}")
        return tweets
    print(f"拉取失败: {r.status_code} {r.text[:150]}")
    return []


if __name__ == "__main__":
    main()





