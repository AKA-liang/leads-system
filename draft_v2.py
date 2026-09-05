#!/usr/bin/env python3
"""person 级起草(阶段3): 跨平台档案 → 英文建联消息 → messages 表

用法:
  python draft_v2.py --top 10                 # 为分数最高的 10 个 true 交易者起草
  python draft_v2.py --min-score 85
  python draft_v2.py approve <message_id>     # 审核通过
  python draft_v2.py reject <message_id>      # 拒绝
  python draft_v2.py queue                    # 查看待审队列
"""
import argparse
import csv
import json
import os
import sys
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
BATCH_SIZE = 8  # 每批人数,过大 DeepSeek 可能返回空内容


SYSTEM_PROMPTS = {
    "en": (
        "你是专业外联文案。为每位交易者起草一条英文建联私信。\n"
        "要求: 1)必须引用对方在【任意平台】的具体发言(注明平台),体现你认真研究过他;"
        " 2)像真人聊天,口语化,无推销感,60-120词;"
        " 3)合规:不承诺收益/不劝诱开户/不附链接/不以交易信号诱饵;"
        " 4)结尾一个具体问题。\n"
        "输出 JSON 数组: {\"person_id\":数字, \"message\":\"英文\", "
        "\"rationale\":\"中文:为什么这么写,引用了哪个平台哪句话\"}。只输出 JSON。"
    ),
    "zh-hant": (
        "你是專業外聯文案。為每位交易者起草一條繁體中文建聯私訊。\n"
        "要求: 1)必須引用對方在【任意平台】的具體發言(註明平台),體現你認真研究過他;"
        " 2)像真人聊天,口語化,無推銷感,60-120字;"
        " 3)合規:不承諾收益/不勸誘開戶/不附連結/不以交易信號誘餌;"
        " 4)結尾一個具體問題。\n"
        "輸出 JSON 陣列: {\"person_id\":數字, \"message\":\"繁體中文\", "
        "\"rationale\":\"中文:為什麼這樣寫,引用了哪個平台哪句話\"}。只輸出 JSON。"
    ),
}


def call_deepseek(batch, lang="en"):
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPTS.get(lang, SYSTEM_PROMPTS["en"]),
            },
            {"role": "user", "content": json.dumps(batch, ensure_ascii=False)},
        ],
        "temperature": 0.7,
        "max_tokens": 8000,
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
        if s == -1 or e == -1:
            raise ValueError(f"DeepSeek 返回内容不含 JSON 数组: {content[:200]!r}")
        return json.loads(content[s:e + 1])


def load_candidates(conn, top, min_score, person_ids=None):
    if person_ids:
        ph = ",".join(["?"] * len(person_ids))
        rows = conn.execute(
            "SELECT id, canonical_name, score, is_trader, asset_class FROM persons "
            f"WHERE id IN ({ph}) ORDER BY score DESC",
            person_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, canonical_name, score, is_trader, asset_class FROM persons "
            "WHERE LOWER(is_trader)='true' AND score >= ? ORDER BY score DESC LIMIT ?",
            (min_score, top),
        ).fetchall()
    out = []
    for r in rows:
        evs = conn.execute(
            "SELECT platform, type, content FROM events WHERE person_id=? "
            "ORDER BY like_score DESC LIMIT 8", (r["id"],)
        ).fetchall()
        samples = [f"[{e['platform']}/{e['type']}] {e['content'][:280]}" for e in evs if e["content"]]
        accs = conn.execute(
            "SELECT platform, username FROM accounts WHERE person_id=?", (r["id"],)
        ).fetchall()
        out.append({
            "person_id": r["id"],
            "name": r["canonical_name"],
            "score": r["score"],
            "asset_class": r["asset_class"],
            "platforms": [f"{a['platform']}:{a['username']}" for a in accs],
            "samples": samples[:5],
        })
    return out


def draft(conn, top, min_score, person_ids=None, lang="en"):
    cands = load_candidates(conn, top, min_score, person_ids)
    print(f"起草 {len(cands)} 条 (语言: {lang})", flush=True)
    if not cands:
        return
    parsed_all = []
    total_batches = (len(cands) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(cands), BATCH_SIZE):
        batch = cands[i:i + BATCH_SIZE]
        print(f"起草批次 {i // BATCH_SIZE + 1}/{total_batches}...", flush=True)
        try:
            parsed = parse_json(call_deepseek(batch, lang))
        except Exception as e:
            print(f"批次失败: {e}", flush=True)
            continue
        parsed_all.extend(parsed)
    parsed = parsed_all
    for x in parsed:
        if not isinstance(x, dict) or "person_id" not in x:
            continue
        # 已有 pending 消息则不重复
        exists = conn.execute(
            "SELECT id FROM messages WHERE person_id=? AND status='pending'",
            (x["person_id"],),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            "INSERT INTO messages(person_id, channel, content, rationale, status) VALUES(?,?,?,?,?)",
            (x["person_id"], "x", x.get("message", ""), x.get("rationale", ""), "pending"),
        )
    conn.commit()
    export_queue(conn)
    log_op("draft.v2", f"top={top} min={min_score}", f"drafted={len(parsed)}")
    print("完成,已导出审核表")


def export_queue(conn):
    out = BASE / "data" / "drafts_v2.csv"
    rows = conn.execute(
        "SELECT m.id, p.canonical_name, p.score, p.asset_class, p.is_trader, "
        "m.content, m.rationale, m.status "
        "FROM messages m JOIN persons p ON p.id=m.person_id "
        "WHERE m.status='pending' ORDER BY p.score DESC"
    ).fetchall()
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["message_id", "person", "score", "asset_class", "is_trader", "message", "rationale", "status"])
        for r in rows:
            w.writerow(list(r))
    print(f"审核表: {out} ({len(rows)} 条待审)")


def show_queue(conn):
    rows = conn.execute(
        "SELECT m.id, p.canonical_name, p.score, m.channel, m.content, m.rationale "
        "FROM messages m JOIN persons p ON p.id=m.person_id WHERE m.status='pending' "
        "ORDER BY p.score DESC"
    ).fetchall()
    for r in rows:
        print(f"\n--- [{r['id']}] {r['canonical_name']} ({r['score']}分)[{r['channel']}] ---")
        print(f"理由: {r['rationale']}")
        print(f"消息: {r['content']}")
    if not rows:
        print("(无待审消息)")


def approve(conn, mid):
    cur = conn.execute(
        "UPDATE messages SET status='approved' WHERE id=? AND status='pending'", (mid,))
    conn.commit()
    log_op("draft.approve", f"message_id={mid}", "ok")
    print(f"  [{mid}] 已通过" if cur.rowcount else f"  [{mid}] 不存在或非待审")


def reject(conn, mid):
    cur = conn.execute(
        "UPDATE messages SET status='rejected' WHERE id=? AND status='pending'", (mid,))
    conn.commit()
    log_op("draft.reject", f"message_id={mid}", "ok")
    print(f"  [{mid}] 已拒绝" if cur.rowcount else f"  [{mid}] 不存在或非待审")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    d = sub.add_parser("draft")
    d.add_argument("--top", type=int, default=10)
    d.add_argument("--min-score", type=int, default=0)
    d.add_argument("--person", type=str, default="", help="指定 person_id 起草,逗号分隔(如 1750,1760,1766);指定后忽略 top/min-score")
    d.add_argument("--lang", type=str, default="en", choices=["en", "zh-hant"], help="起草语言: en=英文 zh-hant=繁体中文")
    q = sub.add_parser("queue")
    a = sub.add_parser("approve")
    a.add_argument("id", type=int)
    r = sub.add_parser("reject")
    r.add_argument("id", type=int)
    sub.add_parser("export")
    args = p.parse_args()

    conn = get_conn()
    if args.cmd == "draft":
        if not DEEPSEEK_KEY:
            print("NO_DEEPSEEK_KEY"); sys.exit(1)
        person_ids = [int(x) for x in args.person.split(",") if x.strip()] if args.person else None
        draft(conn, args.top, args.min_score, person_ids, lang=args.lang)
    elif args.cmd == "queue":
        show_queue(conn)
    elif args.cmd == "approve":
        approve(conn, args.id)
    elif args.cmd == "reject":
        reject(conn, args.id)
    elif args.cmd == "export":
        export_queue(conn)
    else:
        p.print_help()
    conn.close()


if __name__ == "__main__":
    main()
