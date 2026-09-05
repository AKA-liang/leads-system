#!/usr/bin/env python3
"""补救: 从 Apify 已完成 run 拉取 Reddit 数据并写 raw 文件(不重新跑 actor,不重复花钱)。
用法: ops.sh reddit-resume [run_id]   (省略 run_id = 用最近一次 SUCCEEDED run)
"""
import sys
import json
from pathlib import Path
sys.path.insert(0, '/opt/leads')
from apify_client import ApifyClient
from dotenv import load_dotenv, find_dotenv
import os

BASE = Path('/opt/leads')


def main():
    load_dotenv(find_dotenv())
    tok = os.getenv('APIFY_TOKEN', '')
    if not tok:
        print('NO_TOKEN')
        sys.exit(1)
    c = ApifyClient(tok)
    run_id = sys.argv[1] if len(sys.argv) > 1 else ''
    if not run_id:
        runs = c.runs().list(limit=1, desc=True)
        if not runs.items:
            print('NO_RUNS')
            sys.exit(1)
        run_id = runs.items[0]['id']
    run = c.run(run_id).get()
    print(f"run={run_id} status={run.get('status')} 花费=${run.get('usageTotalUsd', 0):.2f}")
    if run.get('status') != 'SUCCEEDED':
        print('RUN_NOT_SUCCEEDED')
        sys.exit(1)
    ds = run.get('defaultDatasetId')
    items = list(c.dataset(ds).iterate_items())
    out = BASE / 'data' / 'reddit_raw.jsonl'
    with out.open('w', encoding='utf-8') as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + '\n')
    posts = [i for i in items if i.get('dataType') == 'post']
    comments = [i for i in items if i.get('dataType') == 'comment']
    authors = {i.get('authorName') for i in items
               if i.get('authorName') and i.get('authorName') != '[deleted]'}
    print(f"saved: {out}")
    print(f"posts={len(posts)} comments={len(comments)} unique_authors={len(authors)}")


if __name__ == '__main__':
    main()
