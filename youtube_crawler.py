#!/usr/bin/env python3
"""YouTube 线索爬虫(官方 Data API v3,免费)。

流程: 搜索关键词 → 视频列表 → 逐视频抓评论 → 聚合评论者
配额(免费): search 100次/天(每次50条), 评论接口 10000 单位/天(每页100条=1单位)
用法:
  python youtube_crawler.py --search-terms "NQ futures,NQ scalping,ES futures" --max-videos 10 --max-comments-per-video 100
  python youtube_crawler.py --intent "做NQ/ES期货的美国散户"   # 中文意图自动润色
产出: data/youtube_leads.csv + data/youtube_raw.jsonl
"""
import argparse
import csv
import json
import os
import sys
import time
from collections import OrderedDict
from pathlib import Path

import requests
from dotenv import load_dotenv

import config
from keyword_refine import refine

BASE = Path("/opt/leads")
load_dotenv(BASE / ".env")

YT_KEY = os.getenv("YT_API_KEY", "")
YT_API = "https://www.googleapis.com/youtube/v3"
# googleapis.com 在国内直连不通,必须走 Clash 代理
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}


def yt_get(path, params):
    params["key"] = YT_KEY
    r = requests.get(f"{YT_API}/{path}", params=params, proxies=PROXY, timeout=30)
    r.raise_for_status()
    return r.json()


def search_videos(terms, max_videos):
    out = []
    for t in terms:
        try:
            d = yt_get("search", {
                "part": "snippet", "type": "video", "q": t,
                "maxResults": min(max_videos, 50), "order": "relevance",
                "regionCode": "US",
            })
            for it in d.get("items", []):
                vid = it["id"]["videoId"]
                sn = it["snippet"]
                out.append({
                    "video_id": vid, "title": sn["title"][:150],
                    "channel": sn["channelTitle"], "term": t,
                })
            print(f"  [{t}] +{len(d.get('items', []))} 视频", flush=True)
        except Exception as e:
            print(f"  [{t}] 失败: {e}", flush=True)
        time.sleep(0.3)
    return out


def fetch_comments(video_id, max_comments):
    comments = []
    page = None
    while len(comments) < max_comments:
        params = {
            "part": "snippet", "videoId": video_id,
            "maxResults": 100, "textFormat": "plainText",
        }
        if page:
            params["pageToken"] = page
        try:
            d = yt_get("commentThreads", params)
        except requests.HTTPError as e:
            # 403 通常是视频禁评/地区限制,跳过该视频不中断
            print(f"    评论 403({e.response.status_code}): {video_id} 可能禁评,跳过", flush=True)
            return comments
        except Exception as e:
            print(f"    评论失败: {e}", flush=True)
            break
        for it in d.get("items", []):
            sn = it["snippet"]["topLevelComment"]["snippet"]
            comments.append({
                "author": sn["authorDisplayName"],
                "author_url": sn.get("authorChannelUrl", ""),
                "channel_id": (sn.get("authorChannelId") or {}).get("value", ""),
                "text": sn["textDisplay"][:500],
                "like_count": sn.get("likeCount", 0),
                "published": sn.get("publishedAt", ""),
            })
        page = d.get("nextPageToken")
        if not page:
            break
        time.sleep(0.2)
    return comments[:max_comments]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--search-terms", default="", help="英文关键词,逗号分隔")
    p.add_argument("--intent", default="", help="中文意图(自动润色)")
    p.add_argument("--max-videos", type=int, default=10, help="每关键词最多视频数(≤50)")
    p.add_argument("--max-comments-per-video", type=int, default=100, help="每视频评论上限")
    p.add_argument("--out", default=str(BASE / "data" / "youtube_leads.csv"))
    args = p.parse_args()

    if not YT_KEY:
        print("NO_YT_API_KEY: 在 console.cloud.google.com 创建 API key,写入 /opt/leads/.env 的 YT_API_KEY")
        sys.exit(1)

    if args.search_terms:
        terms = [t.strip() for t in args.search_terms.split(",") if t.strip()]
    elif args.intent:
        r = refine(args.intent)
        terms = r.get("terms", [])
        print("[润色]", json.dumps(r.get("term_purposes", {}), ensure_ascii=False, indent=2), flush=True)
    else:
        print("必须提供 --search-terms 或 --intent")
        sys.exit(2)

    print(f"=== 搜索视频(关键词 x{len(terms)}) ===", flush=True)
    videos = search_videos(terms, args.max_videos)
    print(f"共 {len(videos)} 个视频", flush=True)

    print(f"=== 抓评论(每视频最多 {args.max_comments_per_video} 条) ===", flush=True)
    authors = OrderedDict()  # author -> {count, subs, texts}
    raw = []
    for i, v in enumerate(videos):
        print(f"  [{i+1}/{len(videos)}] {v['title'][:60]}", flush=True)
        comments = fetch_comments(v["video_id"], args.max_comments_per_video)
        for c in comments:
            raw.append({**v, **c})
            a = c["author"]
            if a not in authors:
                authors[a] = {"count": 0, "videos": set(), "texts": []}
            authors[a]["count"] += 1
            authors[a]["videos"].add(v["title"][:60])
            if len(authors[a]["texts"]) < 2:
                authors[a]["texts"].append(c["text"][:300])
        time.sleep(0.3)

    (BASE / "data" / "youtube_raw.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in raw), encoding="utf-8")
    print(f"raw saved: {len(raw)} 条评论", flush=True)

    rows = []
    for a, d in sorted(authors.items(), key=lambda kv: -kv[1]["count"]):
        rows.append({
            "author": a,
            "comments": d["count"],
            "videos": " | ".join(list(d["videos"])[:3]),
            "samples": " | ".join(d["texts"]),
        })
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["author", "comments", "videos", "samples"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"saved: {out} ({len(rows)} 个评论者)")


if __name__ == "__main__":
    main()
