#!/usr/bin/env python3
import sys
sys.path.insert(0, '/opt/leads')
from db import get_conn

c = get_conn()
print("=== 消息队列 ===")
for r in c.execute("SELECT status, COUNT(*) n FROM messages GROUP BY status ORDER BY n DESC"):
    print(f"  {r['status']:<10} {r['n']}")
print("\n=== 待审队列(新起草 30 条) ===")
rows = c.execute(
    "SELECT m.id, p.canonical_name, p.score, p.asset_class, substr(m.rationale,1,45) r "
    "FROM messages m JOIN persons p ON p.id=m.person_id WHERE m.status='pending' "
    "ORDER BY p.score DESC LIMIT 30"
).fetchall()
for r in rows:
    print(f"  [{r['id']}] {r['canonical_name']:<24} {r['score']} {r['asset_class']:<6} | {r['r']}")
c.close()
