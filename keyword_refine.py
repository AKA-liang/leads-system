#!/usr/bin/env python3
"""中文意图 → 英文搜索词润色(DeepSeek)。

强制约束: 版块只能从 config.SUBREDDIT_CATALOG 中选择,不允许推荐目录外的版块。

用法:
  python keyword_refine.py "我想找做美国期货的散户,主要做NQ和ES"
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

import config

BASE = Path("/opt/leads")
load_dotenv(BASE / ".env")

DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


def refine(intent_zh, extra_context=""):
    catalog_lines = "\n".join(
        f"- r/{s} ({v['label']},业务线:{v['vertical']})" for s, v in config.SUBREDDIT_CATALOG.items()
    )
    system = (
        "你是跨境获客搜索词优化师。用户用中文描述想找的目标人群,你需要:\n"
        "1. 把它转成 3-6 个精准的英文搜索关键词(适合 Reddit 搜索,组合词+同义词)\n"
        f"2. 从以下版块目录中选择 2-5 个最合适的版块,【严禁推荐目录外的版块】:\n{catalog_lines}\n"
        "3. 推荐排序(sort: top/hot/new)和时间窗(time: day/week/month/year/all)\n"
        "4. 对每个关键词用中文说明它瞄准什么人群\n"
        "输出 JSON,字段: terms(字符串数组,如[\"NQ futures scalping\"]), "
        "term_purposes(对象,key=关键词,value=中文人群说明), "
        "subreddits(数组,只能来自目录), sort, time, rationale(中文,150字内)。"
        "只输出 JSON。"
    )
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"目标描述:{intent_zh}\n额外背景:{extra_context or '无'}"},
        ],
        "temperature": 0.3,
        "max_tokens": 8000,
    }
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {DEEPSEEK_KEY}"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    content = data["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
    r = json.loads(content)
    # 兜底:过滤掉目录外的版块,防止模型不听话
    valid = set(config.SUBREDDIT_CATALOG.keys())
    r["subreddits"] = [s for s in r.get("subreddits", []) if s in valid]
    if not r["subreddits"]:
        # 模型推荐的全被目录过滤掉时,回落垂直默认版块,不返回空
        r["subreddits"] = list(config.DEFAULT_SUBS)
        r["rationale"] = (r.get("rationale", "") + " 版块已回落至垂直相关默认(futures/Daytrading/algotrading/options/thetagang),可在执行前调整。")
    return r


def format_report(r):
    """润色结果 → 中文可读报告(给用户看)。"""
    lines = ["【润色结果】"]
    for t in r.get("terms", []):
        purpose = r.get("term_purposes", {}).get(t, "")
        lines.append(f"- {t}  ← {purpose}")
    subs = ", ".join(f"r/{s}({config.SUBREDDIT_CATALOG.get(s, {}).get('label', s)})" for s in r.get("subreddits", []))
    lines.append(f"- 版块: {subs}")
    lines.append(f"- 排序/时间: sort={r.get('sort', 'top')} / time={r.get('time', 'month')}(筛选高质量帖)")
    if r.get("rationale"):
        lines.append(f"- 理由: {r['rationale']}")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("intent", help="中文意图描述")
    p.add_argument("--context", default="", help="额外背景(可选)")
    args = p.parse_args()
    if not DEEPSEEK_KEY:
        print("NO_DEEPSEEK_KEY")
        sys.exit(1)
    r = refine(args.intent, args.context)
    print(format_report(r))
    terms = ",".join(r.get("terms", []))
    subs = ",".join(r.get("subreddits", []))
    print("\n[可直接执行的抓取命令]")
    print(f"./run_all.sh --search-terms \"{terms}\" --subreddits \"{subs}\" --sort {r.get('sort','top')} --time {r.get('time','month')}")


if __name__ == "__main__":
    main()
