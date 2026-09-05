#!/bin/bash
/opt/leads/venv/bin/python /opt/leads/tv_expand.py --symbols "ES1!,NQ1!,GC1!,CL1!,6E1!,ZN1!" --max-ideas 30 --max-comments 30
echo "=== 扩散后库统计 ==="
/opt/leads/venv/bin/python -c "
import sys; sys.path.insert(0,'/opt/leads')
from db import get_conn
c=get_conn()
for t in ['persons','accounts','events','graph_edges']:
    print(f'  {t:<12}', c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0])
print('  Premium 标记人数:', c.execute(\"SELECT COUNT(*) FROM accounts WHERE platform='tv' AND raw LIKE '%is_pro: true%'\").fetchone()[0])
print('  最近 5 条扩散线索:')
for r in c.execute(\"SELECT username FROM accounts WHERE platform='tv' AND matched_by='扩散' ORDER BY id DESC LIMIT 5\"):
    print('   ', r['username'])
c.close()"
