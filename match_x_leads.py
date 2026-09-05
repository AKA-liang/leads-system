#!/usr/bin/env python3
"""X 匹配工具(新增): 从线索库读 approved 且无 X 账号的 person,
用其 Reddit/YouTube 用户名到 X lookup, 结果写 data/x_match_leads.csv。
只读库 + 只写 CSV, 不改现有代码/不写库。按量花 X credits(约 $0.05/58次)。"""
import sys
import csv
import time
import sqlite3
from pathlib import Path

BASE = Path("/opt/leads")
sys.path.insert(0, str(BASE))
import x_client  # noqa: E402

OUT = BASE / "data" / "x_match_leads.csv"

conn = sqlite3.connect(BASE / "data" / "leads.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    """
    SELECT DISTINCT p.id, p.canonical_name, p.score,
      (SELECT username FROM accounts a WHERE a.person_id=p.id AND a.platform='reddit' ORDER BY a.id LIMIT 1) AS r_user,
      (SELECT username FROM accounts a WHERE a.person_id=p.id AND a.platform='youtube' ORDER BY a.id LIMIT 1) AS y_user
    FROM messages m JOIN persons p ON p.id=m.person_id
    WHERE m.status='approved' AND m.channel='x'
      AND NOT EXISTS(SELECT 1 FROM accounts a WHERE a.person_id=p.id AND a.platform='x')
    ORDER BY p.score DESC, p.id
    """
).fetchall()
conn.close()

print(f"待匹配 {len(rows)} 人", flush=True)
results = []
for i, r in enumerate(rows, 1):
    name = (r["canonical_name"] or "").lstrip("@")
    src = "reddit" if r["r_user"] else "youtube"
    try:
        resp = x_client.lookup_user(name)
        if resp.status_code == 200:
            d = resp.json()["data"]
            results.append({
                "person_id": r["id"],
                "source_name": name,
                "source_platform": src,
                "score": r["score"],
                "x_username": d.get("username", ""),
                "x_user_id": d.get("id", ""),
                "followers": (d.get("public_metrics") or {}).get("followers_count", ""),
                "description": (d.get("description") or "")[:120],
                "location": d.get("location", ""),
                "created_at": d.get("created_at", ""),
                "receives_your_dm": d.get("receives_your_dm", ""),
            })
            print(f"  [{i}/{len(rows)}] ✅ {name} → @{d.get('username')} "
                  f"dm={d.get('receives_your_dm')} (来自{src})", flush=True)
        elif resp.status_code == 402:
            print(f"  [{i}/{len(rows)}] ❌ {name}: CREDITS_DEPLETED 充值后重跑", flush=True)
            break
        else:
            print(f"  [{i}/{len(rows)}] ❌ {name}: {resp.status_code}", flush=True)
    except Exception as e:
        print(f"  [{i}/{len(rows)}] ⚠️ {name}: {e}", flush=True)
    time.sleep(1.2)

with OUT.open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=[
        "person_id", "source_name", "source_platform", "score", "x_username",
        "x_user_id", "followers", "description", "location", "created_at",
        "receives_your_dm",
    ])
    w.writeheader()
    for r in results:
        w.writerow(r)
print(f"done: {OUT} (命中 {len(results)}/{len(rows)})")
