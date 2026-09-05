#!/usr/bin/env python3
"""身份融合器 fusion.py

能力:
  scan      同名/近似名匹配 → 生成待确认合并清单(不自动合并,护栏3)
  merge     --id N 执行合并(用户确认后)
  merge-all 批量合并(用户批量确认后)
  report    融合报告

护栏:
  - 同名匹配默认进 pending_fusions 待确认,不自动合并
  - 疑似官方账号(如 tradingview/ninjatrader)单独标记,不参与合并
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, '/opt/leads')
from db import get_conn, log_op

OFFICIAL = {"tradingview", "ninjatrader", "cme_group", "cmegroup"}


def init_fusion_table(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS pending_fusions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      person_a_id INTEGER NOT NULL,
      person_b_id INTEGER NOT NULL,
      basis TEXT DEFAULT '同名',
      status TEXT DEFAULT 'pending',   -- pending/done/rejected
      note TEXT DEFAULT '',
      created_at TEXT DEFAULT (datetime('now'))
    )
    """)


def scan(conn):
    """同名匹配: 不同平台同用户名 → 候选合并"""
    rows = conn.execute(
        "SELECT LOWER(username) uname, platform, username, person_id FROM accounts"
    ).fetchall()
    groups = {}
    for r in rows:
        groups.setdefault(r["uname"], []).append(r)

    init_fusion_table(conn)
    added = 0
    for uname, items in groups.items():
        if len(items) < 2:
            continue
        # 官方账号跳过
        if uname in OFFICIAL:
            continue
        plats = {i["platform"] for i in items}
        if len(plats) < 2:
            continue
        # 取两个 person 配对(同 person 内多平台不产生建议)
        pids = {}
        for i in items:
            pids.setdefault(i["person_id"], i["platform"])
        if len(pids) < 2:
            continue
        pid_list = list(pids.keys())
        for a in range(len(pid_list)):
            for b in range(a + 1, len(pid_list)):
                exists = conn.execute(
                    "SELECT id FROM pending_fusions WHERE "
                    "((person_a_id=? AND person_b_id=?) OR (person_a_id=? AND person_b_id=?)) AND status='pending'",
                    (pid_list[a], pid_list[b], pid_list[b], pid_list[a]),
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO pending_fusions(person_a_id, person_b_id, basis, note) VALUES(?,?,?,?)",
                        (pid_list[a], pid_list[b], "同名",
                         f"同名 '{uname}' 出现在平台: {','.join(sorted(plats))}"),
                    )
                    added += 1
    conn.commit()
    return added


def report(conn):
    print("=== 融合报告 ===")
    for r in conn.execute(
        "SELECT p.id, p.canonical_name, COUNT(a.id) n FROM persons p "
        "JOIN accounts a ON a.person_id=p.id GROUP BY p.id HAVING n>=2 ORDER BY n DESC"
    ):
        accs = conn.execute(
            "SELECT platform, username FROM accounts WHERE person_id=?", (r["id"],)
        ).fetchall()
        print(f"  [{r['id']}] {r['canonical_name']:<18} {r['n']}账号: "
              + " | ".join(f"{a['platform']}:{a['username']}" for a in accs))

    print("\n=== 待确认合并(同名,需人工确认) ===")
    rows = conn.execute(
        "SELECT f.id, pa.canonical_name a_name, pb.canonical_name b_name, f.note "
        "FROM pending_fusions f JOIN persons pa ON pa.id=f.person_a_id "
        "JOIN persons pb ON pb.id=f.person_b_id WHERE f.status='pending'"
    ).fetchall()
    for r in rows:
        print(f"  [{r['id']}] {r['a_name']} ⟷ {r['b_name']}  ({r['note']})")
    if not rows:
        print("  (无)")
    return rows


def merge(conn, fid):
    row = conn.execute(
        "SELECT * FROM pending_fusions WHERE id=? AND status='pending'", (fid,)
    ).fetchone()
    if not row:
        print(f"  [{fid}] 不存在或已处理")
        return
    keep, drop = row["person_a_id"], row["person_b_id"]
    conn.execute("UPDATE accounts SET person_id=? WHERE person_id=?", (keep, drop))
    conn.execute("UPDATE events SET person_id=? WHERE person_id=?", (keep, drop))
    conn.execute("UPDATE messages SET person_id=? WHERE person_id=?", (keep, drop))
    conn.execute("DELETE FROM persons WHERE id=?", (drop,))
    conn.execute("UPDATE pending_fusions SET status='done' WHERE id=?", (fid,))
    conn.commit()
    log_op("fusion.merge", f"fid={fid}", "merged")
    print(f"  [{fid}] 已合并")


def cleanup(conn):
    """清理误抓的官方账号(x/youtube/telegram 平台上的 TradingView 官号等)。"""
    rows = conn.execute(
        "SELECT id, platform, username FROM accounts "
        "WHERE platform IN ('x','youtube','telegram') AND REPLACE(LOWER(username), '@', '') IN ("
        + ",".join("?" for _ in OFFICIAL) + ")",
        tuple(OFFICIAL),
    ).fetchall()
    for r in rows:
        conn.execute("DELETE FROM accounts WHERE id=?", (r["id"],))
        print(f"  已删除官方账号: {r['platform']}:{r['username']} (id={r['id']})")
    conn.commit()
    log_op("fusion.cleanup", f"removed={len(rows)}", "ok")
    print(f"清理 {len(rows)} 个官方账号")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["scan", "report", "merge", "merge-all", "cleanup"])
    p.add_argument("--id", type=int, default=0)
    args = p.parse_args()

    conn = get_conn()
    init_fusion_table(conn)
    if args.cmd == "scan":
        n = scan(conn)
        print(f"新增 {n} 条待确认合并建议")
        report(conn)
    elif args.cmd == "report":
        report(conn)
    elif args.cmd == "merge":
        merge(conn, args.id)
        report(conn)
    elif args.cmd == "merge-all":
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM pending_fusions WHERE status='pending'")]
        for fid in ids:
            merge(conn, fid)
        print(f"批量合并 {len(ids)} 条")
    elif args.cmd == "cleanup":
        cleanup(conn)
    conn.close()


if __name__ == "__main__":
    main()
