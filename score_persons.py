#!/usr/bin/env python3
"""person 级跨平台打分(阶段3)

从统一线索库聚合每个人的多平台档案(账号+各平台发言样本),
DeepSeek 综合判断: 是否真实交易者 / 品种 / 建联价值分 / 理由。

用法:
  python score_persons.py --top 50        # 打分活跃度前 50 人(默认)
  python score_persons.py --top 0         # 全部(DeepSeek 便宜,几千人也几块钱)
  python score_persons.py --person 1410   # 只打某一个
结果写回 persons 表,并导出 data/persons_scored.csv
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, '/opt/leads')
from dotenv import load_dotenv
from db import get_conn, log_op

BASE = Path("/opt/leads")
load_dotenv(BASE / ".env")

DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
BATCH_SIZE = 10


def load_person_profiles(conn, top, only_id=0, unscored_only=False):
    """聚合 person 档案: 账号 + 各平台样本发言。"""
    sql = ("SELECT p.id, p.canonical_name, p.score, p.is_trader, "
           "(SELECT COUNT(*) FROM events e WHERE e.person_id=p.id) n_events "
           "FROM persons p")
    conds = []
    if unscored_only:
        conds.append("p.score = 0")
    if only_id:
        conds.append("p.id = ?")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY n_events DESC"
    params = (only_id,) if only_id else ()
    rows = conn.execute(sql, params).fetchall()
    if only_id:
        rows = [r for r in rows if r["id"] == only_id]
    elif top and top > 0:
        rows = rows[:top]

    profiles = []
    for r in rows:
        accs = conn.execute(
            "SELECT platform, username, followers FROM accounts WHERE person_id=?", (r["id"],)
        ).fetchall()
        evs = conn.execute(
            "SELECT platform, type, content FROM events WHERE person_id=? "
            "ORDER BY like_score DESC LIMIT 6", (r["id"],)
        ).fetchall()
        samples = []
        for e in evs:
            if e["content"] and len(e["content"]) > 5:
                samples.append(f"[{e['platform']}/{e['type']}] {e['content'][:300]}")
        profiles.append({
            "person_id": r["id"],
            "name": r["canonical_name"],
            "platforms": [f"{a['platform']}({a['username']})" for a in accs],
            "followers": {a["platform"]: a["followers"] for a in accs if a["followers"]},
            "samples": samples[:4],
        })
    return profiles


def call_deepseek(batch):
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是获客线索分析师。给定一批交易者档案(含跨平台身份和发言样本),逐条评估。\n"
                    "对每个 person_id 输出 JSON 对象: "
                    "person_id(数字), is_trader(true/false/unknown), "
                    "asset_class(期货/期权/股票/外汇/加密/其他/未知), "
                    "score(0-100), reason(中文一句话理由,注明依据了哪些平台的信息)。\n"
                    "只输出 JSON 数组。"
                ),
            },
            {"role": "user", "content": json.dumps(batch, ensure_ascii=False)},
        ],
        "temperature": 0.3,
        "max_tokens": 5000,
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
        s, e = content.find("["), content.rfind("]")
        return json.loads(content[s:e + 1])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=50, help="打分数(0=全部)")
    p.add_argument("--person", type=int, default=0, help="只打某 person_id")
    p.add_argument("--unscored-only", action="store_true", help="只打未打分的(score=0)")
    p.add_argument("--rescore", action="store_true", help="重新打分(含已打分的,默认只打未打分防浪费)")
    p.add_argument("--sleep", type=float, default=1.0, help="每批间隔秒数(默认1,防内存过冲)")
    args = p.parse_args()

    if not DEEPSEEK_KEY:
        print("NO_DEEPSEEK_KEY")
        sys.exit(1)

    conn = get_conn()
    # 默认只打未打分的,防重复浪费;--rescore 或 --person 指定时全打
    unscored_only = args.unscored_only or (not args.rescore and not args.person)
    profiles = load_person_profiles(conn, args.top, args.person, unscored_only)
    if unscored_only and not args.person:
        print("[防重复] 仅打分未打分的(score=0);如需重打全部加 --rescore", flush=True)
    print(f"待打分: {len(profiles)} 人", flush=True)

    results = []
    for i in range(0, len(profiles), BATCH_SIZE):
        batch = profiles[i:i + BATCH_SIZE]
        print(f"打分批次 {i // BATCH_SIZE + 1}/{(len(profiles) + BATCH_SIZE - 1) // BATCH_SIZE}...", flush=True)
        try:
            parsed = parse_json(call_deepseek(batch))
        except Exception as e:
            print(f"  批次失败: {e}", flush=True)
            continue
        # 每批即时落库(可断点续跑)
        for x in parsed:
            if not isinstance(x, dict) or "person_id" not in x:
                continue
            trader = str(x.get("is_trader", "")).strip().lower()
            trader = "true" if trader in ("true", "1") else ("false" if trader in ("false", "0") else "unknown")
            conn.execute(
                "UPDATE persons SET score=?, is_trader=?, asset_class=?, "
                "notes=COALESCE(notes, '') || ' | ' || ?, updated_at=datetime('now') WHERE id=?",
                (x.get("score") or 0, trader, x.get("asset_class", ""),
                 x.get("reason", ""), x["person_id"]),
            )
            results.append(x)
        conn.commit()
        time.sleep(args.sleep)

    # 导出 CSV
    out = BASE / "data" / "persons_scored.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["person_id", "name", "score", "is_trader", "asset_class", "reason", "platforms"])
        prof_map = {pr["person_id"]: pr for pr in profiles}
        for x in sorted(results, key=lambda v: -(v.get("score") or 0)):
            pr = prof_map.get(x["person_id"], {})
            w.writerow([x["person_id"], pr.get("name", ""), x.get("score"),
                        x.get("is_trader"), x.get("asset_class"), x.get("reason", ""),
                        ";".join(pr.get("platforms", []))])
    print(f"saved: {out}")
    log_op("score.persons", f"top={args.top} person={args.person}", f"scored={len(results)}")
    conn.close()


if __name__ == "__main__":
    main()
