#!/usr/bin/env python3
"""Fetch Instagram leads via Apify actor (instagram-scraper).

获取对象:
  - 帖子作者(ownerUsername) + 评论者(latestComments) = 潜在客户
  - 支持关键词搜用户(searchType=user)或标签(searchType=hashtag)

强制流程(防止浪费钱):
  1. 必须提供 --terms(关键词) 或 --intent(中文意图,自动润色)
  2. 执行前打印预计成本,必须 --yes 确认(除非 --dry-run)
  3. 默认小量测试;禁泛化

用法:
  python fetch_instagram.py --terms "gold futures" --yes
  python fetch_instagram.py --intent "做黄金期货的美欧散户" --search-type user --yes
  python fetch_instagram.py --dry-run --terms "day trading"
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

ACTOR = "apify/instagram-scraper"
PRICE_PER_1K = 2.7  # Free 计划 $2.7/1000 results


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--terms", default="", help="英文关键词(逗号分隔)")
    p.add_argument("--intent", default="", help="中文意图(自动润色成英文关键词)")
    p.add_argument("--search-type", default="user", choices=["user", "hashtag"],
                   help="user=搜交易者账号(推荐) hashtag=搜标签帖子")
    p.add_argument("--results-limit", type=int, default=50, help="最多取多少条结果")
    p.add_argument("--out", default=str(BASE / "data" / "instagram_raw.jsonl"))
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

    est = args.results_limit * PRICE_PER_1K / 1000.0
    print(f"=== Instagram 抓取计划 ===", flush=True)
    print(f"  关键词: {terms}", flush=True)
    print(f"  搜索类型: {args.search_type} (user=交易者账号, hashtag=标签帖子)", flush=True)
    print(f"  结果上限: {args.results_limit} 条", flush=True)
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
        "search": ", ".join(terms),
        "searchType": args.search_type,
        "resultsLimit": args.results_limit,
        "proxy": {"useApifyProxy": True},
    }
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

    authors = {it.get("ownerUsername") for it in items if it.get("ownerUsername")}
    commenters = set()
    for it in items:
        for c in (it.get("latestComments") or []):
            u = c.get("ownerUsername")
            if u:
                commenters.add(u)
    print(f"saved: {out}", flush=True)
    print(f"posts={len(items)} authors={len(authors)} commenters={len(commenters)} 花费=${spent:.2f}", flush=True)


if __name__ == "__main__":
    main()
