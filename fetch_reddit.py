#!/usr/bin/env python3
"""Fetch Reddit leads via Apify actor (intent-driven).

强制流程(防止浪费钱):
  1. 必须提供 --intent(中文意图)或 --search-terms(英文关键词)或显式 --mode subreddit
  2. 执行前打印量级边界与预计成本,必须 --yes 确认(除非 --dry-run 只看预算)
  3. 默认 search 专向模式;泛化模式(subreddit)需显式指定并确认

用法:
  python fetch_reddit.py --intent "做NQ/ES期货的美国散户" --yes
  python fetch_reddit.py --search-terms "NQ futures,ES micro" --subreddits futures,Daytrading --yes
  python fetch_reddit.py --dry-run --intent "..."   # 只看预算不跑
"""
import argparse
import json
import sys
from pathlib import Path

from apify_client import ApifyClient
from dotenv import load_dotenv

import config
from keyword_refine import refine

BASE = Path("/opt/leads")
load_dotenv(BASE / ".env")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--intent", default="", help="中文意图(自动润色成英文关键词)")
    p.add_argument("--search-terms", default="", help="英文关键词(逗号分隔,跳过润色)")
    p.add_argument("--subreddits", default="", help="版块(逗号分隔,默认垂直相关)")
    p.add_argument("--mode", default="search", choices=["search", "subreddit"], help="subreddit=泛化,默认 search=专向")
    p.add_argument("--sort", default="top", choices=["hot", "top", "new", "comments"])
    p.add_argument("--time", default="month", choices=["hour", "day", "week", "month", "year", "all"])
    p.add_argument("--max-posts", type=int, default=40)
    p.add_argument("--max-comments", type=int, default=20)
    p.add_argument("--out", default=str(config.BASE / "data" / "reddit_raw.jsonl"))
    p.add_argument("--yes", action="store_true", help="确认执行(跳过预算确认)")
    p.add_argument("--dry-run", action="store_true", help="只打印预算,不执行")
    args = p.parse_args()

    # ---- 1. 解析意图 / 关键词 ----
    terms = []
    if args.search_terms:
        terms = [t.strip() for t in args.search_terms.split(",") if t.strip()]
    elif args.intent:
        print(f"[润色] 中文意图: {args.intent}", flush=True)
        r = refine(args.intent)
        terms = r.get("terms", [])
        subs_rec = r.get("subreddits", [])
        if not args.subreddits:
            args.subreddits = ",".join(subs_rec)
        if not args.sort:
            args.sort = r.get("sort", "top")
        if not args.time:
            args.time = r.get("time", "month")
        from keyword_refine import format_report
        print(format_report(r), flush=True)
        print(f"[提示] 每个关键词单独搜索一次,结果合并去重;{len(terms)} 词 × 每词 {args.max_posts} 帖 = 最多 {len(terms) * args.max_posts} 帖", flush=True)
    elif args.mode != "subreddit":
        print("必须提供 --intent(中文意图)或 --search-terms(英文关键词),禁止泛化抓取。")
        sys.exit(2)

    # ---- 2. 版块 ----
    if args.subreddits:
        subs = [s.strip() for s in args.subreddits.split(",") if s.strip()]
    else:
        subs = config.DEFAULT_SUBS
        print(f"[默认版块] {subs}(垂直相关,可用 --subreddits 覆盖,目录见 config.py)", flush=True)

    # ---- 3. 预算边界 ----
    print(config.budget_report(len(subs), args.max_posts, args.max_comments), flush=True)
    if args.dry_run:
        print("[dry-run] 未执行。")
        sys.exit(0)
    if not args.yes:
        print("[确认] 加 --yes 确认执行(例: --yes)。")
        sys.exit(2)

    # ---- 4. 执行 ----
    import os
    token = os.getenv("APIFY_TOKEN")
    if not token:
        print("NO_TOKEN")
        sys.exit(1)
    client = ApifyClient(token)

    if args.mode == "subreddit":
        run_input = {
            "startUrls": [{"url": f"https://www.reddit.com/r/{s}/"} for s in subs],
            "crawlCommentsPerPost": True,
            "maxPostsCount": args.max_posts,
            "maxCommentsPerPost": args.max_comments,
            "maxCommentsCount": 100000,
            "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        }
        print(f"[subreddit 泛化模式] {subs}", flush=True)
    else:
        run_input = {
            "searchTerms": terms,
            "searchPosts": True,
            "searchComments": False,
            "searchSort": args.sort,
            "searchTime": args.time,
            "maxPostsCount": args.max_posts,
            "maxCommentsCount": 100000,
            "crawlCommentsPerPost": True,
            "maxCommentsPerPost": args.max_comments,
            "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        }
        if len(subs) == 1:
            run_input["withinCommunity"] = f"r/{subs[0]}"
        print(f"[search 专向模式] 关键词={terms} 版块={subs} sort={args.sort} time={args.time}", flush=True)

    print("=== starting actor run ===", flush=True)
    run = client.actor("harshmaur/reddit-scraper").call(run_input=run_input)
    spent = run.get("usageTotalUsd", 0)
    print(f"run_id={run.get('id')} status={run.get('status')} 本次花费=${spent:.2f}", flush=True)
    if run.get("status") != "SUCCEEDED":
        print("RUN_FAILED")
        sys.exit(1)

    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    posts = [i for i in items if i.get("dataType") == "post"]
    comments = [i for i in items if i.get("dataType") == "comment"]
    authors = {i.get("authorName") for i in items if i.get("authorName") and i.get("authorName") != "[deleted]"}
    print(f"saved: {out}", flush=True)
    print(f"posts={len(posts)} comments={len(comments)} unique_authors={len(authors)} 花费=${spent:.2f}", flush=True)


if __name__ == "__main__":
    main()
