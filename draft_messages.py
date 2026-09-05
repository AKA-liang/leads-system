#!/usr/bin/env python3
"""Draft personalized first-contact messages for top leads via DeepSeek.

每条消息都带 rationale(为什么这么写:引用了对方的哪句原话、切入逻辑),
方便用户逐条审核修改。审核表 drafts.csv 的 approved 列留空=待审,
可填 approved / edit / reject。

参数:
  --top N          起草前 N 条(默认 15,按分数降序)
  --all-score-from 0-100 分数下限(只起草分数 >= 该值的线索)
  --input FILE     打分结果,默认 data/leads_scored.csv
  --out FILE       审核表,默认 data/drafts.csv
"""
import argparse
import csv
import json
import os
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

BASE = Path("/opt/leads")
load_dotenv(BASE / ".env")

DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


def load_scored(path):
    rows = []
    with Path(path).open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def call_deepseek(batch):
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是专业的外联文案。为每位 Reddit 交易者起草一条建联消息。\n"
                    "硬性要求：\n"
                    "1. 必须引用对方的具体帖子或评论内容(钩子)，体现你认真读过对方的东西\n"
                    "2. 像真人聊天，口语化，绝不群发感；不要一上来推销任何东西\n"
                    "3. 英文撰写(对象是欧美交易者)，自然、简短(60-120词)\n"
                    "4. 合规红线：不承诺收益、不劝诱开户、不贬低其他平台、不要求私聊交易\n"
                    "5. 结尾自然收束(问一个具体问题或邀请对方分享看法)，不附链接\n"
                    "输出 JSON 数组，每项：{\"author\":用户名, \"message\":\"英文消息\", "
                    "\"rationale\":\"为什么这么写(中文,说明引用了对方哪句原话、切入逻辑,60字内)\"}。"
                    "只输出 JSON。"
                ),
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
        start, end = content.find("["), content.rfind("]")
        return json.loads(content[start:end + 1])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--min-score", type=int, default=0)
    p.add_argument("--input", default=str(BASE / "data" / "leads_scored.csv"))
    p.add_argument("--out", default=str(BASE / "data" / "drafts.csv"))
    args = p.parse_args()

    if not DEEPSEEK_KEY:
        print("NO_DEEPSEEK_KEY")
        sys.exit(1)

    rows = [
        r for r in load_scored(args.input)
        if r.get("is_trader", "").lower() == "true" and int(r.get("score") or 0) >= args.min_score
    ]
    rows.sort(key=lambda r: -(int(r.get("score") or 0)))
    rows = rows[:args.top]
    print(f"drafting {len(rows)} messages (is_trader=true, score>={args.min_score}, top {args.top})", flush=True)

    batch = [
        {"author": r["author"], "score": r["score"], "reason": r["reason"], "hook": r["hook"]}
        for r in rows
    ]
    try:
        parsed = parse_json(call_deepseek(batch))
    except Exception as e:
        print(f"draft failed: {e}")
        sys.exit(1)

    by_author = {p["author"]: p for p in parsed if isinstance(p, dict)}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "author", "score", "asset_class", "reason", "hook", "rationale", "message", "approved",
        ])
        w.writeheader()
        for r in rows:
            d = by_author.get(r["author"], {})
            w.writerow({
                "author": r["author"],
                "score": r["score"],
                "asset_class": r.get("asset_class", ""),
                "reason": r["reason"],
                "hook": r["hook"],
                "rationale": d.get("rationale", ""),
                "message": d.get("message", ""),
                "approved": "",  # 待审: approved / edit / reject
            })
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
