#!/usr/bin/env python3
"""x_dm_filter.py v2 — 筛选"可发 DM"名单。

X 规则: receives_your_dm 为 true 或 null(未公开) 时可发; false 明确拒绝。
v1 的 bug: 把 null 误判为不可发。v2 修正: true/null 均可发, false 不可发。
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/opt/leads")
from dotenv import load_dotenv
import requests

from db import get_conn, log_op

BASE = Path("/opt/leads")
load_dotenv(BASE / ".env")

API = "https://api.x.com/2"
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}


def lookup_user(username):
    token = os.getenv("X_BEARER_TOKEN", "")
    r = requests.get(
        f"{API}/users/by/username/{username}",
        params={"user.fields": "id,name,username,receives_your_dm,verified"},
        headers={"Authorization": f"Bearer {token}"},
        proxies=PROXY, timeout=25,
    )
    return r


def main():
    conn = get_conn()
    rows = conn.execute(
        "SELECT m.id mid, p.id pid, p.canonical_name, a.id aid, a.username, a.raw "
        "FROM messages m JOIN persons p ON p.id=m.person_id "
        "JOIN accounts a ON a.person_id=p.id AND a.platform='x' "
        "WHERE m.channel='x' AND m.status='approved' ORDER BY m.id"
    ).fetchall()
    print(f"待筛选: {len(rows)} 个账号", flush=True)

    can, cannot, errs = [], [], []
    for r in rows:
        username = r["username"].lstrip("@")
        try:
            resp = lookup_user(username)
            if resp.status_code == 200:
                d = resp.json()["data"]
                receives = d.get("receives_your_dm")
                # true/null -> 可发; false -> 不可发
                sendable = receives is not False
                raw = {}
                if r["raw"]:
                    try:
                        raw = json.loads(r["raw"])
                    except Exception:
                        raw = {"source": "unknown"}
                raw["receives_your_dm"] = receives
                raw["dm_checked_at"] = "2026-08-16"
                conn.execute(
                    "UPDATE accounts SET raw=? WHERE id=?",
                    (json.dumps(raw, ensure_ascii=False), r["aid"]),
                )
                conn.commit()
                mark = "✅ 可发" if sendable else "❌ 拒收"
                print(f"  {mark} [{r['mid']}] {r['canonical_name'][:20]:<22} @{username:<20} receives={receives}", flush=True)
                if sendable:
                    can.append((r["mid"], r["canonical_name"], username))
                else:
                    cannot.append((r["mid"], r["canonical_name"], username))
            elif resp.status_code == 402:
                print(f"  💰 credits 耗尽, 停止", flush=True)
                errs.append((r["mid"], r["canonical_name"], "credits"))
                break
            else:
                print(f"  ⚠️ [{r['mid']}] @{username}: {resp.status_code} {resp.text[:80]}", flush=True)
                errs.append((r["mid"], r["canonical_name"], str(resp.status_code)))
        except Exception as e:
            print(f"  ⚠️ [{r['mid']}] @{username}: {str(e)[:50]}", flush=True)
            errs.append((r["mid"], r["canonical_name"], "exception"))

    conn.close()
    print()
    print(f"=== 结果: 可发 {len(can)} / 拒收 {len(cannot)} / 异常 {len(errs)} ===")
    print("\n✅ 可发名单:")
    for m in can:
        print(f"  ops.sh send {m[0]} --channel x   # {m[1]} @{m[2]}")
    if cannot:
        print("\n❌ 拒收(排除):")
        for m in cannot:
            print(f"  [{m[0]}] {m[1]} @{m[2]}")
    log_op("x.dm_filter", f"can={len(can)} cannot={len(cannot)} errs={len(errs)}", "ok")


if __name__ == "__main__":
    main()
