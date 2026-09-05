#!/usr/bin/env python3
"""三源数据入库: reddit / youtube / tradingview → leads.db

归一化规则:
  - 每个作者/评论者 = person(canonical_name=首个出现平台名)
  - 每平台账号 = account(reddit/tv/x/youtube)
  - 每条发言 = event
  - TradingView 外链 → 直接为该 person 创建对应平台 account(强匹配,matched_by=外链)
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, '/opt/leads')
from db import get_conn, init_db, get_or_create_person, upsert_account, log_op
from db import add_event as _add_event_raw

# ---- 去重统计(模块级): add_event 自动计数, 零侵入各函数 ----
_INGEST_STATS = {"new": 0, "dup": 0}


def add_event(conn, *args, **kwargs):
    ok = _add_event_raw(conn, *args, **kwargs)
    if ok:
        _INGEST_STATS["new"] += 1
    else:
        _INGEST_STATS["dup"] += 1
    return ok

BASE = Path("/opt/leads")
DATA = BASE / "data"


def dedup_key_for(platform, it):
    """按平台从原始数据提取事件唯一 ID, 用于去重。
    返回 None 表示无法提取(不参与去重)。
    """
    if platform == "reddit":
        return str(it.get("id") or "") or None
    if platform == "youtube":
        vid = it.get("video_id", "")
        return f"{vid}:{it.get('author', '')}" if vid else None
    if platform == "tv":
        url = it.get("profile_url") or ""
        return url or None
    if platform == "instagram":
        # post 用 id/url; 评论用 comment id
        iid = it.get("id") or it.get("url") or ""
        return str(iid) or None
    if platform == "facebook":
        return str(it.get("id") or it.get("url") or it.get("commentUrl") or "") or None
    if platform == "threads":
        return str(it.get("post_code") or it.get("post_url") or it.get("profile_url") or "") or None
    return None


def ingest_reddit(conn):
    p = DATA / "reddit_raw.jsonl"
    if not p.exists():
        print("reddit_raw.jsonl 不存在,跳过")
        return 0
    n = 0
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                it = json.loads(line)
            except json.JSONDecodeError:
                continue
            author = it.get("authorName")
            if not author or author == "[deleted]":
                continue
            pid = get_or_create_person(conn, author)
            sub = (it.get("parsedCommunityName") or it.get("subredditName") or "").replace("r/", "")
            url = it.get("url") or it.get("postUrl") or ""
            is_post = it.get("dataType") == "post"
            aid = upsert_account(
                conn, pid, "reddit", author,
                profile_url=f"https://www.reddit.com/user/{author}/",
                context=sub, raw=json.dumps(it, ensure_ascii=False)[:2000],
            )
            add_event(
                conn, pid, aid, "reddit",
                "post" if is_post else "comment",
                (it.get("body") or it.get("title") or "")[:1000],
                url=url,
                published_at=it.get("createdAt") or it.get("commentCreatedAt") or "",
                like_score=it.get("score") or 0,
                raw=json.dumps(it, ensure_ascii=False)[:2000],
                dedup_key=dedup_key_for("reddit", it),
            )
            n += 1
    print(f"reddit: {n} 条事件入库")
    return n


def ingest_instagram(conn):
    p = DATA / "instagram_raw.jsonl"
    if not p.exists():
        print("instagram_raw.jsonl 不存在,跳过")
        return 0
    n = 0
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                it = json.loads(line)
            except json.JSONDecodeError:
                continue
            owner = it.get("ownerUsername")
            if owner:
                pid = get_or_create_person(conn, owner)
                aid = upsert_account(
                    conn, pid, "instagram", owner,
                    profile_url=f"https://www.instagram.com/{owner}/",
                    context=it.get("caption", "")[:200],
                    raw=json.dumps(it, ensure_ascii=False)[:2000],
                )
                add_event(
                    conn, pid, aid, "instagram", "post",
                    it.get("caption", "")[:1000],
                    url=it.get("url", ""),
                    published_at=it.get("timestamp", ""),
                    like_score=it.get("likesCount") or 0,
                    raw=json.dumps(it, ensure_ascii=False)[:2000],
                    dedup_key=dedup_key_for("instagram", it),
                )
                n += 1
            # 评论者
            for c in (it.get("latestComments") or []):
                cu = c.get("ownerUsername")
                if not cu:
                    continue
                cpid = get_or_create_person(conn, cu)
                caid = upsert_account(
                    conn, cpid, "instagram", cu,
                    profile_url=f"https://www.instagram.com/{cu}/",
                    context=it.get("ownerUsername", ""),
                    raw=json.dumps(c, ensure_ascii=False)[:2000],
                )
                add_event(
                    conn, cpid, caid, "instagram", "comment",
                    c.get("text", "")[:1000],
                    url=it.get("url", ""),
                    published_at=c.get("timestamp", ""),
                    like_score=c.get("likesCount") or 0,
                    raw=json.dumps(c, ensure_ascii=False)[:2000],
                    dedup_key=dedup_key_for("instagram", c) or dedup_key_for("instagram", it),
                )
                n += 1
    print(f"instagram: {n} 条事件入库")
    return n


def ingest_facebook(conn):
    p = DATA / "facebook_raw.jsonl"
    if not p.exists():
        print("facebook_raw.jsonl 不存在,跳过")
        return 0
    n = 0
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                it = json.loads(line)
            except json.JSONDecodeError:
                continue
            user = it.get("user", {}) or {}
            author = user.get("name")
            if author:
                pid = get_or_create_person(conn, author)
                aid = upsert_account(
                    conn, pid, "facebook", author,
                    profile_url=f"https://www.facebook.com/groups/",
                    context=it.get("groupTitle", ""),
                    raw=json.dumps(it, ensure_ascii=False)[:2000],
                )
                add_event(
                    conn, pid, aid, "facebook", "post",
                    it.get("text", "")[:1000],
                    url=it.get("url", ""),
                    published_at=it.get("time", ""),
                    like_score=it.get("likesCount") or 0,
                    raw=json.dumps(it, ensure_ascii=False)[:2000],
                    dedup_key=dedup_key_for("facebook", it),
                )
                n += 1
            # 评论者
            for c in (it.get("topComments") or []):
                cn = c.get("profileName")
                if not cn:
                    continue
                cpid = get_or_create_person(conn, cn)
                caid = upsert_account(
                    conn, cpid, "facebook", cn,
                    profile_url=c.get("profileUrl", ""),
                    context=it.get("groupTitle", ""),
                    raw=json.dumps(c, ensure_ascii=False)[:2000],
                )
                add_event(
                    conn, cpid, caid, "facebook", "comment",
                    c.get("text", "")[:1000],
                    url=c.get("commentUrl", ""),
                    published_at=c.get("date", ""),
                    like_score=int(c.get("likesCount") or 0),
                    raw=json.dumps(c, ensure_ascii=False)[:2000],
                    dedup_key=dedup_key_for("facebook", c) or dedup_key_for("facebook", it),
                )
                n += 1
    print(f"facebook: {n} 条事件入库")
    return n


def ingest_threads(conn):
    p = DATA / "threads_raw.jsonl"
    if not p.exists():
        print("threads_raw.jsonl 不存在,跳过")
        return 0
    n = 0
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                it = json.loads(line)
            except json.JSONDecodeError:
                continue
            username = it.get("username")
            if not username:
                continue
            pid = get_or_create_person(conn, username)
            # 账号(含资料)
            followers = it.get("followers_count") or ""
            bio = (it.get("bio") or "")[:300]
            upsert_account(
                conn, pid, "threads", username,
                profile_url=it.get("profile_url", "") or f"https://www.threads.net/@{username}",
                followers=str(followers),
                context=bio,
                raw=json.dumps(it, ensure_ascii=False)[:2000],
            )
            if it.get("record_type") == "post" and it.get("text_content"):
                add_event(
                    conn, pid, None, "threads", "post",
                    it.get("text_content", "")[:1000],
                    url=it.get("post_url", ""),
                    published_at=it.get("created_at", ""),
                    like_score=it.get("like_count") or 0,
                    raw=json.dumps(it, ensure_ascii=False)[:2000],
                    dedup_key=dedup_key_for("threads", it),
                )
                n += 1
            elif it.get("record_type") == "profile":
                add_event(
                    conn, pid, None, "threads", "profile",
                    (it.get("bio") or "")[:1000],
                    url=it.get("profile_url", ""),
                    like_score=0,
                    raw=json.dumps(it, ensure_ascii=False)[:2000],
                    dedup_key=dedup_key_for("threads", it),
                )
                n += 1
            else:
                n += 1
    print(f"threads: {n} 条事件入库")
    return n


def ingest_youtube(conn):
    p = DATA / "youtube_raw.jsonl"
    if not p.exists():
        print("youtube_raw.jsonl 不存在,跳过")
        return 0
    n = 0
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                it = json.loads(line)
            except json.JSONDecodeError:
                continue
            author = it.get("author")
            if not author:
                continue
            pid = get_or_create_person(conn, author)
            aid = upsert_account(
                conn, pid, "youtube", author,
                profile_url=it.get("author_url", ""),
                context=it.get("video_id", ""),
                raw=json.dumps(it, ensure_ascii=False)[:2000],
            )
            add_event(
                conn, pid, aid, "youtube", "comment",
                it.get("text", "")[:1000],
                url=f"https://www.youtube.com/watch?v={it.get('video_id', '')}",
                published_at=it.get("published", ""),
                like_score=it.get("like_count") or 0,
                raw=json.dumps(it, ensure_ascii=False)[:2000],
                dedup_key=dedup_key_for("youtube", it),
            )
            n += 1
    print(f"youtube: {n} 条事件入库")
    return n


def ingest_tradingview(conn):
    p = DATA / "tradingview_leads.csv"
    if not p.exists():
        print("tradingview_leads.csv 不存在,跳过")
        return 0
    n = 0
    with p.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            author = r.get("author", "")
            if not author:
                continue
            pid = get_or_create_person(conn, author)
            # TV 账号
            upsert_account(
                conn, pid, "tv", author,
                profile_url=r.get("profile_url", ""),
                followers=r.get("followers", ""),
                context=r.get("context", ""),
                raw=json.dumps(r, ensure_ascii=False)[:2000],
            )
            if r.get("context"):
                add_event(
                    conn, pid, None, "tv", "idea",
                    r["context"][:1000],
                    url=r.get("profile_url", ""),
                    like_score=0,
                    raw=json.dumps(r, ensure_ascii=False)[:2000],
                    dedup_key=dedup_key_for("tv", r),
                )
            # 外链 → 强匹配,直接挂同一 person
            for plat, key in [("x", "social_x"), ("youtube", "social_youtube"),
                              ("telegram", "social_telegram"), ("facebook", "social_facebook"),
                              ("instagram", "social_instagram"), ("discord", "social_discord"),
                              ("website", "social_website")]:
                link = (r.get(key) or "").strip()
                if not link:
                    continue
                uname = link.rstrip("/").rsplit("/", 1)[-1]
                if plat == "x":
                    upsert_account(conn, pid, "x", uname, profile_url=link,
                                   matched_by="外链", raw=json.dumps(r, ensure_ascii=False)[:2000])
                elif plat == "telegram":
                    upsert_account(conn, pid, "telegram", uname, profile_url=link,
                                   matched_by="外链", raw=json.dumps(r, ensure_ascii=False)[:2000])
                elif plat == "youtube":
                    upsert_account(conn, pid, "youtube", uname, profile_url=link,
                                   matched_by="外链", raw=json.dumps(r, ensure_ascii=False)[:2000])
                elif plat == "discord":
                    upsert_account(conn, pid, "discord", uname, profile_url=link,
                                   matched_by="外链", raw=json.dumps(r, ensure_ascii=False)[:2000])
                elif plat in ("facebook", "instagram"):
                    upsert_account(conn, pid, plat, uname, profile_url=link,
                                   matched_by="外链", raw=json.dumps(r, ensure_ascii=False)[:2000])
            n += 1
    print(f"tradingview: {n} 条入库(含外链账号)")
    return n


def summary(conn):
    print("\n=== 线索库统计 ===")
    for t in ["persons", "accounts", "events", "seeds", "graph_edges", "messages"]:
        c = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<12} {c}")
    print("\n=== 按平台账号数 ===")
    for r in conn.execute(
        "SELECT platform, COUNT(*) c FROM accounts GROUP BY platform ORDER BY c DESC"
    ):
        print(f"  {r['platform']:<10} {r['c']}")
    print("\n=== 拥有多平台账号的人(融合潜力) ===")
    for r in conn.execute(
        "SELECT p.canonical_name, COUNT(a.id) n FROM persons p "
        "JOIN accounts a ON a.person_id = p.id GROUP BY p.id HAVING n >= 2 ORDER BY n DESC LIMIT 10"
    ):
        plats = [x["platform"] for x in conn.execute(
            "SELECT platform FROM accounts WHERE person_id = (SELECT id FROM persons WHERE canonical_name=?)",
            (r["canonical_name"],))]
        print(f"  {r['canonical_name']:<20} {r['n']} 账号: {','.join(plats)}")
    print("\n=== 有 X 账号的 person(建联主通道) ===")
    for r in conn.execute(
        "SELECT p.canonical_name, a.username FROM persons p "
        "JOIN accounts a ON a.person_id = p.id WHERE a.platform='x' LIMIT 10"
    ):
        print(f"  {r['canonical_name']:<20} → @{r['username']}")


def main():
    init_db()
    conn = get_conn()
    _INGEST_STATS["new"] = 0
    _INGEST_STATS["dup"] = 0
    n1 = ingest_reddit(conn)
    n2 = ingest_youtube(conn)
    n3 = ingest_tradingview(conn)
    n4 = ingest_instagram(conn)
    n5 = ingest_facebook(conn)
    n6 = ingest_threads(conn)
    conn.commit()
    summary(conn)
    log_op("ingest", f"reddit={n1} youtube={n2} tv={n3} ig={n4} fb={n5} threads={n6}", "ok")
    print(f"\n=== 去重统计: 新增 {_INGEST_STATS['new']} 条 / 重复跳过 {_INGEST_STATS['dup']} 条 ===")
    # 写抓取历史
    try:
        for plat, cnt in [("reddit", n1), ("youtube", n2), ("tv", n3),
                          ("instagram", n4), ("facebook", n5), ("threads", n6)]:
            if cnt:
                conn.execute(
                    "INSERT INTO fetch_history(platform, query, hits, new_count) VALUES(?,?,?,?)",
                    (plat, "ingest", cnt, _INGEST_STATS["new"] if plat == "reddit" else cnt),
                )
        conn.commit()
    except Exception as e:
        print(f"[fetch_history] 写入失败(不影响主流程): {e}")
    conn.close()
    print("\n=== 入库完成 ===")


if __name__ == "__main__":
    main()
