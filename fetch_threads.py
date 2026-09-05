#!/usr/bin/env python3
"""Fetch Threads leads via Apify actor (futurizerush/meta-threads-scraper).

获取对象:
  - search 模式: 按关键词搜公开帖子 -> 帖子作者(username/user_id) = 潜在客户
  - profiles 模式: 按关键词找账号(含粉丝数/bio/链接)

强制流程(防止浪费钱):
  1. 必须提供 --terms(英文关键词)或 --intent(中文意图,自动润色)
  2. 执行前打印预计成本,必须 --yes 确认(除非 --dry-run)

用法:
  python fetch_threads.py --terms "gold futures, day trading" --yes
  python fetch_threads.py --intent "做黄金期货的美欧散户" --mode profiles --yes
  python fetch_threads.py --dry-run --terms "ES futures"
"""
import argparse
import json
import sys
from pathlib import Path

from apify_client import ApifyClient
from dotenv import load_dotenv

import config

BASE = Path("/opt/leads")
load_dotenv(BASE / ".env")

ACTOR = "futurizerush/meta-threads-scraper"
PRICE_PER_1K = 2.5  # $2.5/1000 results


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--terms", default="", help="英文关键词(逗号分隔)")
    p.add_argument("--intent", default="", help="中文意图(自动润色成英文关键词)")
    p.add_argument("--mode", default="search", choices=["search", "profiles"],
                   help="search=搜帖子(作者=线索) profiles=搜账号(推荐,直接得账号)")
    p.add_argument("--recent", action="store_true", help="只看最近帖子(7天内, 适合监控)")
    p.add_argument("--max-posts", type=int, default=30, help="每个关键词最多取多少条")
    p.add_argument("--out", default=str(BASE / "data" / "threads_raw.jsonl"))
    p.add_argument("--yes", action="store_true", help="确认执行")
    p.add_argument("--dry-run", action="store_true", help="只打印预算,不执行")
    args = p.parse_args()

    terms = [t.strip() for t in args.terms.split(",") if t.strip()]
    if args.intent and not terms:
        try:
            from keyword_refine import refine
            r = refine(args.intent)
            terms = r.get("terms", [])[:5]
            print(f"[润色] {args.intent} -> {terms}", flush=True)
        except Exception as e:
            print(f"[润色失败] {e}, 请直接用 --terms 提供英文关键词")
            sys.exit(2)
    if not terms:
        print("必须提供 --terms(英文关键词)或 --intent(中文意图),禁止泛化抓取。")
        sys.exit(2)

    # profiles 模式每个关键词最多 10 个账号; actor 要求 max_posts >= 10
    cap = max(10, min(args.max_posts, 10)) if args.mode == "profiles" else max(10, args.max_posts)
    est = len(terms) * cap * PRICE_PER_1K / 1000.0
    print("=== Threads 抓取计划 ===", flush=True)
    print(f"  关键词: {terms}", flush=True)
    print(f"  模式: {args.mode} ({'搜帖子-作者即线索' if args.mode == 'search' else '搜账号-直接得账号+粉丝数'})", flush=True)
    if args.recent:
        print("  时间: 最近 7 天", flush=True)
    print(f"  每词上限: {cap} 条", flush=True)
    print(f"  预计花费: ${est:.2f} (${PRICE_PER_1K}/1k results)", flush=True)
    if args.dry_run:
        print("[dry-run] 未执行。")
        sys.exit(0)
    if not args.yes:
        print("[确认] 加 --yes 确认执行(例: --yes)。")
        sys.exit(2)

    import os
    token = os.getenv("APIFY_TOKEN")
    if not token:
        print("NO_APIFY_TOKEN")
        sys.exit(1)
    client = ApifyClient(token)

    run_input = {
        "mode": args.mode,
        "keywords": terms,
        "max_posts": cap,
    }
    if args.mode == "search" and args.recent:
        run_input["search_filter"] = "recent"
        run_input["start_date"] = "7 days"
    print("=== starting actor run ===", flush=True)
    run = client.actor(ACTOR).call(run_input=run_input)
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

    posts = [i for i in items if i.get("record_type") == "post"]
    profiles = [i for i in items if i.get("record_type") == "profile"]
    authors = {i.get("username") for i in items if i.get("username")}
    print(f"saved: {out}", flush=True)
    print(f"posts={len(posts)} profiles={len(profiles)} unique_authors={len(authors)} 花费=${spent:.2f}", flush=True)


if __name__ == "__main__":
    main()
