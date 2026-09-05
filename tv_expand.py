#!/usr/bin/env python3
"""TradingView 图谱扩散(零成本,种子机制)

从品种页的观点出发,抓每篇观点下的评论者 → 新线索入库 + 建立 graph_edges。

用法:
  python tv_expand.py --symbols "GC1!,ES1!,NQ1!" --max-ideas 30
  python tv_expand.py --symbols "GC1!" --max-ideas 10
产出:
  - 新评论者 → persons/accounts/events
  - graph_edges: 观点作者 → 评论者 (relation=idea_comment)
  - 评论者 is_pro(Premium 付费)标记在 account.raw
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, '/opt/leads')
from db import get_conn, get_or_create_person, upsert_account, add_event, log_op

BASE = Path("/opt/leads")
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
SYMBOL_URL = "https://www.tradingview.com/symbols/{}/"
COMMENTS_API = "https://www.tradingview.com/api/v1/ideas/{}/comments"


def fetch(url, timeout=25):
    r = requests.get(url, headers=HEADERS, proxies=PROXY, timeout=timeout)
    r.raise_for_status()
    return r.text


def symbol_idea_urls(html):
    """symbol 页 → [(author, idea_id)]"""
    authors = re.findall(r'data-qa-id="ui-lib-card-link-author"><a href="/u/([A-Za-z0-9_\-]+)/"', html)
    charts = re.findall(
        r'href="(https://www\.tradingview\.com/chart/[^"]*/)"[^>]*data-qa-id="ui-lib-card-link-image"',
        html,
    )
    out = []
    for a, c in zip(authors, charts):
        m = re.search(r'/chart/[^/]+/([A-Za-z0-9]+)-', c)
        if m:
            out.append((a, m.group(1)))
    return out


def fetch_comments(idea_id, max_comments=30):
    try:
        r = requests.get(COMMENTS_API.format(idea_id), headers=HEADERS, proxies=PROXY, timeout=20)
        if r.status_code != 200:
            return []
        d = r.json()
        return d.get("results", [])[:max_comments]
    except Exception:
        return []


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="GC1!,ES1!,NQ1!", help="品种,逗号分隔")
    p.add_argument("--max-ideas", type=int, default=30, help="每品种最多观点数")
    p.add_argument("--max-comments", type=int, default=30, help="每观点最多评论数")
    args = p.parse_args()

    conn = get_conn()
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    total_new = 0
    total_edges = 0
    pro_count = 0

    for sym in syms:
        print(f"=== {sym} ===", flush=True)
        try:
            html = fetch(SYMBOL_URL.format(sym))
        except Exception as e:
            print(f"  symbol 页失败: {e}", flush=True)
            continue
        ideas = symbol_idea_urls(html)[:args.max_ideas]
        print(f"  观点 {len(ideas)} 篇", flush=True)
        for i, (author, idea_id) in enumerate(ideas):
            author_pid = get_or_create_person(conn, author)
            comments = fetch_comments(idea_id, args.max_comments)
            new_in_idea = 0
            for c in comments:
                u = c.get("user") or {}
                uname = u.get("username", "")
                if not uname:
                    continue
                is_pro = bool(u.get("is_pro")) or bool(u.get("is_paid_pro"))
                # 评论者入库
                pid = get_or_create_person(conn, uname)
                raw = json.dumps({"is_pro": is_pro, "pro_plan": u.get("pro_plan"),
                                  "likes": c.get("likes_count", 0)}, ensure_ascii=False)
                existing = conn.execute(
                    "SELECT id FROM accounts WHERE platform='tv' AND username=?", (uname,)
                ).fetchone()
                if not existing:
                    total_new += 1
                    new_in_idea += 1
                upsert_account(conn, pid, "tv", uname, context=f"{sym} 观点评论",
                               matched_by="扩散", raw=raw)
                add_event(conn, pid, None, "tv", "comment",
                          (c.get("comment") or "")[:1000],
                          url=f"https://www.tradingview.com/chart/{sym}/{idea_id}-/",
                          published_at=c.get("created_at", ""),
                          like_score=c.get("likes_count") or 0, raw=raw)
                if is_pro:
                    pro_count += 1
                # graph edge: 观点作者 → 评论者
                edge = conn.execute(
                    "SELECT id FROM graph_edges WHERE from_person_id=? AND to_person_id=? AND relation='idea_comment'",
                    (author_pid, pid),
                ).fetchone()
                if not edge:
                    conn.execute(
                        "INSERT INTO graph_edges(from_person_id, to_person_id, relation, platform) VALUES(?,?,?,?)",
                        (author_pid, pid, "idea_comment", "tv"),
                    )
                    total_edges += 1
            if new_in_idea:
                print(f"  [{i+1}/{len(ideas)}] {author} 的观点: +{new_in_idea} 新评论者", flush=True)
            time.sleep(0.8)
        conn.commit()

    conn.commit()
    log_op("expand.tv", f"symbols={args.symbols}", f"new={total_new} edges={total_edges}")
    print(f"\n=== 扩散完成: 新线索 {total_new}, 关系边 {total_edges}, 其中 Premium 用户 {pro_count} ===")
    conn.close()


if __name__ == "__main__":
    main()
