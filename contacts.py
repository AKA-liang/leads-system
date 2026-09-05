#!/usr/bin/env python3
"""只读: 联系方式清单 — 平台分布统计 + 列出带 Telegram 账号的线索。
用法: ops.sh contacts [--limit N]
"""
import sys
sys.path.insert(0, '/opt/leads')
from db import get_conn


def main():
    c = get_conn()
    limit = None
    args = sys.argv[1:]
    if '--limit' in args:
        i = args.index('--limit')
        try:
            limit = int(args[i + 1])
        except (IndexError, ValueError):
            pass

    print('=== 账号平台分布(全库) ===')
    for r in c.execute("SELECT platform, COUNT(*) n FROM accounts GROUP BY platform ORDER BY n DESC"):
        print(f'  {r["platform"]:<12} {r["n"]}')

    print()
    print('=== 带 Telegram 账号的线索 ===')
    sql = """
        SELECT p.id, p.canonical_name, p.score, p.is_trader, p.asset_class, p.stage,
               a.username AS tg, a.profile_url AS tg_url,
               (SELECT GROUP_CONCAT(a2.platform || ':' || a2.username, ' | ')
                  FROM accounts a2 WHERE a2.person_id = p.id
                   AND a2.platform NOT IN ('telegram','tg')) AS others,
               (SELECT COUNT(*) FROM messages m WHERE m.person_id = p.id
                 AND m.status IN ('sent','replied','read')) AS n_sent,
               (SELECT COUNT(*) FROM messages m WHERE m.person_id = p.id
                 AND m.reply_content != '') AS n_replied
        FROM persons p
        JOIN accounts a ON a.person_id = p.id AND a.platform IN ('telegram','tg')
        ORDER BY p.score DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = c.execute(sql).fetchall()
    if not rows:
        print('  (无)')
    for r in rows:
        tg = r['tg']
        kind = 'username' if not str(tg).startswith('+') else 'invite-hash'
        print(f"  [{r['id']}] {r['canonical_name']}  score={r['score']}  stage={r['stage']}"
              f"  trader={r['is_trader'] or '-'} {r['asset_class'] or ''}")
        print(f"      TG({kind}): {tg}  url={r['tg_url'] or '-'}")
        if r['others']:
            print(f"      其他: {r['others']}")
        print(f"      消息: 已发{r['n_sent']} 已回{r['n_replied']}")
    c.close()


if __name__ == '__main__':
    main()
