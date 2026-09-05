#!/usr/bin/env python3
import sys
sys.path.insert(0, '/opt/leads')
from db import get_conn

c = get_conn()
print("=== 打分完成度 ===")
total = c.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
scored = c.execute("SELECT COUNT(*) FROM persons WHERE score > 0").fetchone()[0]
print(f"  已打分 {scored}/{total}")
print("分数分布:")
for r in c.execute("SELECT CASE WHEN score>=80 THEN '>=80' WHEN score>=60 THEN '60-79' WHEN score>0 THEN '1-59' ELSE '0' END band, COUNT(*) n FROM persons GROUP BY band ORDER BY band DESC"):
    print(f"  {r['band']:<6} {r['n']}")
print("\nis_trader 分布:")
for r in c.execute("SELECT is_trader, COUNT(*) n FROM persons WHERE score>0 GROUP BY is_trader"):
    print(f"  {r['is_trader']:<10} {r['n']}")
print("\n高分 top10:")
for r in c.execute("SELECT canonical_name, score, is_trader, asset_class FROM persons WHERE score>0 ORDER BY score DESC LIMIT 10"):
    print(f"  {r['canonical_name']:<24} {r['score']} {r['is_trader']:<7} {r['asset_class']}")
print("\n扩散线索打分情况(Premium):")
for r in c.execute(
    "SELECT p.canonical_name, p.score, p.is_trader FROM persons p "
    "JOIN accounts a ON a.person_id=p.id WHERE a.platform='tv' AND a.raw LIKE '%is_pro%true%' "
    "ORDER BY p.score DESC LIMIT 8"
):
    print(f"  {r['canonical_name']:<24} {r['score']} {r['is_trader']}")
c.close()
