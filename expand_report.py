#!/usr/bin/env python3
import sys
sys.path.insert(0, '/opt/leads')
from db import get_conn

c = get_conn()
print("=== 库总量 ===")
for t in ['persons', 'accounts', 'events', 'graph_edges', 'seeds']:
    print(f"  {t:<12} {c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
print("\n=== Premium 用户(PV 付费) ===")
n = c.execute("SELECT COUNT(*) FROM accounts WHERE platform='tv' AND raw LIKE '%is_pro%true%'").fetchone()[0]
print(f"  TV Premium 标记: {n}")
print("\n=== 扩散线索按品种 ===")
for r in c.execute("SELECT context, COUNT(*) n FROM accounts WHERE platform='tv' AND matched_by='扩散' GROUP BY context ORDER BY n DESC LIMIT 8"):
    print(f"  {r['context']:<12} {r['n']}")
print("\n=== 扩散线索样例(带评论文本) ===")
for r in c.execute(
    "SELECT p.canonical_name, e.content FROM events e JOIN persons p ON p.id=e.person_id "
    "WHERE e.platform='tv' AND e.type='comment' AND p.canonical_name IN "
    "(SELECT username FROM accounts WHERE platform='tv' AND matched_by='扩散') "
    "ORDER BY e.id DESC LIMIT 5"
):
    print(f"  {r['canonical_name']}: {r['content'][:90]}")
c.close()
