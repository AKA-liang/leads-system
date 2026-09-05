#!/usr/bin/env python3
"""Fetch Facebook group leads via Apify actor (facebook-groups-scraper).

获取对象: 公开交易群组里的帖子作者 + 评论者(topComments) = 潜在客户

强制流程(防止浪费钱):
  1. 必须提供 --groups(一个或多个公开群组 URL)
  2. 执行前打印预计成本,必须 --yes 确认(除非 --dry-run)

用法:
  python fetch_facebook.py --groups "https://www.facebook.com/groups/futures" --yes
  python fetch_facebook.py --groups "g1,g2" --results-limit 30 --dry-run
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

ACTOR = "apify/facebook-groups-scraper"
PRICE_PER_1K = 2.6  # 约 $2.6/1000 posts (Free 计划约 $5/1k,按 2.6 保守估)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--groups", default="", help="公开群组 URL(逗号分隔)")
    p.add_argument("--results-limit", type=int, default=30, help="每组最多抓多少帖子")
    p.add_argument("--view", default="CHRONOLOGICAL", choices=["CHRONOLOGICAL", "NEW_ACTIVITY"],
                   help="CHRONOLOGICAL=最新发帖, NEW_ACTIVITY=最新活跃")
    p.add_argument("--out", default=str(BASE / "data" / "facebook_raw.jsonl"))
    p.add_argument("--yes", action="store_true", help="确认执行")
    p.add_argument("--dry-run", action="store_true", help="只打印预算,不执行")
    args = p.parse_args()

    groups = [g.strip() for g in args.groups.split(",") if g.strip()]
    if not groups:
        print("必须提供 --groups(至少一个公开群组 URL),例如:")
        print("  --groups \"https://www.facebook.com/groups/futures\"")
        sys.exit(2)
    # 规范化 URL
    norm = []
    for g in groups:
        g = g.replace("facebook.com", "www.facebook.com")
        if not g.startswith("http"):
            g = "https://" + g
        if "/groups/" not in g:
            print(f"[警告] {g} 看起来不是群组 URL(应含 /groups/),跳过")
            continue
        norm.append(g)
    if not norm:
        sys.exit(2)

    est = len(norm) * args.results_limit * PRICE_PER_1K / 1000.0
    print("=== Facebook 群组抓取计划 ===", flush=True)
    print(f"  群组: {norm}", flush=True)
    print(f"  每组上限: {args.results_limit} 帖 | 排序: {args.view}", flush=True)
    print(f"  预计花费: ${est:.2f}", flush=True)
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
        "startUrls": [{"url": g} for g in norm],
        "resultsLimit": args.results_limit,
        "viewOption": args.view,
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

    authors = {it.get("user", {}).get("name") for it in items if it.get("user", {}).get("name")}
    commenters = set()
    for it in items:
        for c in (it.get("topComments") or []):
            n = c.get("profileName")
            if n:
                commenters.add(n)
    print(f"saved: {out}", flush=True)
    print(f"posts={len(items)} authors={len(authors)} commenters={len(commenters)} 花费=${spent:.2f}", flush=True)


if __name__ == "__main__":
    main()
