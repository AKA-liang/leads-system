#!/usr/bin/env python3
"""Aggregate Reddit authors and score lead quality via DeepSeek.

参数:
  --top N          打分多少个作者(按活跃度排序取前 N)。0 = 全部(便宜,DeepSeek 每千 token 几厘钱)
  --min-activity N 作者至少出现 N 条才参与打分(过滤纯路人),默认 1
  --out FILE       输出文件,默认 data/leads_scored.csv
"""
import argparse
import csv
import json
import os
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

BASE = Path("/opt/leads")
load_dotenv(BASE / ".env")

DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
BATCH_SIZE = 10


def load_items(path):
    items = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def aggregate(items):
    by = defaultdict(lambda: {"posts": 0, "comments": 0, "score": 0, "subs": set(), "texts": []})
    for i in items:
        a = i.get("authorName")
        if not a or a == "[deleted]":
            continue
        rec = by[a]
        if i.get("dataType") == "post":
            rec["posts"] += 1
        else:
            rec["comments"] += 1
        rec["score"] += i.get("score") or 0
        subs = i.get("subredditName") or i.get("parsedCommunityName") or ""
        rec["subs"].add(subs.replace("r/", ""))
        body = (i.get("body") or i.get("title") or "").strip()
        if body and len(rec["texts"]) < 3:
            rec["texts"].append(body[:400])
    return by


def call_deepseek(batch):
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是获客线索分析师。给定一批 Reddit 交易版块的活跃作者档案，逐条评估。\n"
                    "对每个作者输出 JSON 对象，字段："
                    "author(用户名), is_trader(true/false/unknown 是否真实交易者), "
                    "asset_class(期货/期权/股票/外汇/加密/其他/未知), "
                    "score(0-100 建联价值分：真实交易者+高活跃+有资金讨论=高分), "
                    "reason(一句话理由,中文), "
                    "hook(建议的建联切入点,引用他的一条帖子或评论内容,中文)。\n"
                    "只输出 JSON 数组，不要多余文字。"
                ),
            },
            {"role": "user", "content": json.dumps(batch, ensure_ascii=False)},
        ],
        "temperature": 0.3,
        "max_tokens": 6000,
    }
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {DEEPSEEK_KEY}"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"]


def parse_json(content):
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("["), content.rfind("]")
        return json.loads(content[start:end + 1])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=60, help="打分作者数,0=全部")
    p.add_argument("--min-activity", type=int, default=1, help="作者最少出现条数")
    p.add_argument("--input", default=str(BASE / "data" / "reddit_raw.jsonl"))
    p.add_argument("--out", default=str(BASE / "data" / "leads_scored.csv"))
    args = p.parse_args()

    if not DEEPSEEK_KEY:
        print("NO_DEEPSEEK_KEY")
        sys.exit(1)

    items = load_items(args.input)
    agg = aggregate(items)
    ranked = sorted(
        agg.items(),
        key=lambda kv: (-kv[1]["posts"] * 3 - kv[1]["comments"], -kv[1]["score"]),
    )
    # 过滤低活跃作者(用户可配)
    if args.min_activity > 1:
        ranked = [kv for kv in ranked if kv[1]["posts"] + kv[1]["comments"] >= args.min_activity]

    if args.top and args.top > 0:
        ranked = ranked[:args.top]
        print(f"aggregated {len(agg)} authors, scoring top {len(ranked)} (--top {args.top})", flush=True)
    else:
        print(f"aggregated {len(agg)} authors, scoring ALL (--top 0, 可调 DeepSeek 花费)", flush=True)

    profiles = [
        {
            "author": a,
            "posts": r["posts"],
            "comments": r["comments"],
            "karma_sum": r["score"],
            "subreddits": sorted(r["subs"]),
            "samples": r["texts"],
        }
        for a, r in ranked
    ]

    results = []
    total_batches = (len(profiles) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(profiles), BATCH_SIZE):
        batch = profiles[i:i + BATCH_SIZE]
        print(f"scoring batch {i // BATCH_SIZE + 1}/{total_batches}...", flush=True)
        try:
            parsed = parse_json(call_deepseek(batch))
        except Exception as e:
            print(f"batch failed: {e}", flush=True)
            continue
        for p in parsed:
            if isinstance(p, dict) and "author" in p:
                results.append(p)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["author", "is_trader", "asset_class", "score", "reason", "hook"])
        w.writeheader()
        for r in sorted(results, key=lambda x: -(x.get("score") or 0)):
            w.writerow(r)
    print(f"saved: {out} ({len(results)} scored)")


if __name__ == "__main__":
    main()
