#!/bin/bash
# ============================================================
# 阿仓操作面板(白名单) — 所有获客操作的唯一入口
# 安全规则(代码层强制):
#   - 花钱操作(fetch-reddit/xleads/xsend)需要 --yes,脚本内部校验
#   - 零成本操作(fetch-tv/fetch-yt/status/fusion 查询)可自主执行
#   - 发送类(send)必须消息已 approved 且逐条确认,代码层强制
#   - 所有操作写 ops_log,可回查
# 用法: ops.sh {子命令} [参数]
# ============================================================
set -e
cd /opt/leads
PY=/opt/leads/venv/bin/python

case "$1" in
  status)      # 体检 + 融合报告 + 队列统计 + 未读回复 + 回复率 + 抓取历史
    $PY platform_status.py
    echo ""
    $PY fusion.py report
    echo ""
    $PY -c "
import sys; sys.path.insert(0,'/opt/leads')
from db import get_conn
c=get_conn()
for t in ['persons','accounts','events','messages','seeds','graph_edges']:
    print(f'  {t:<10}', c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0])
print('  待审消息:', c.execute(\"SELECT COUNT(*) FROM messages WHERE status='pending'\").fetchone()[0])
print('  已批准待发:', c.execute(\"SELECT COUNT(*) FROM messages WHERE status='approved'\").fetchone()[0])
print('  未读回复:', c.execute(\"SELECT COUNT(*) FROM messages WHERE status='replied'\").fetchone()[0])
sent = c.execute(\"SELECT COUNT(*) FROM messages WHERE status IN ('sent','replied','read')\").fetchone()[0]
replied = c.execute(\"SELECT COUNT(*) FROM messages WHERE status IN ('replied','read') AND reply_content != ''\").fetchone()[0]
if sent:
    print(f'  回复率: {replied}/{sent} ({100*replied//sent}%)')
else:
    print('  回复率: 0/0')
print('  最近抓取:')
for r in c.execute('SELECT platform, query, fetched_at, hits, new_count FROM fetch_history ORDER BY id DESC LIMIT 5'):
    print(f'    {r[\"platform\"]:<10} {r[\"fetched_at\"]} 命中{r[\"hits\"]} 新增{r[\"new_count\"]}')
c.close()"
    ;;

  fetch-tv)    # 零成本,可自主;成功后自动入库
    shift
    $PY tradingview_crawler.py "$@" && $PY ingest.py && echo "[ops] fetch-tv 完成并已入库"
    ;;

  fetch-yt)    # 免费配额,需报参数确认;成功后自动入库
    shift
    $PY youtube_crawler.py "$@" && $PY ingest.py && echo "[ops] fetch-yt 完成并已入库"
    ;;

  fetch-reddit) # 花钱(Apify),脚本内部强制 --yes + 预算确认;成功后自动入库
    shift
    if echo "$*" | grep -q -- "--dry-run"; then
      $PY fetch_reddit.py "$@"
    else
      $PY fetch_reddit.py "$@" && $PY ingest.py && echo "[ops] fetch-reddit 完成并已入库"
    fi
    ;;

  fetch-ig)    # Instagram(花钱,Apify actor): 关键词搜交易者账号/标签
    shift
    $PY fetch_instagram.py "$@" && $PY ingest.py && echo "[ops] fetch-ig 完成并已入库"
    ;;

  fetch-fb)    # Facebook 公开群组(花钱,Apify actor): 帖子作者+评论者
    shift
    $PY fetch_facebook.py "$@" && $PY ingest.py && echo "[ops] fetch-fb 完成并已入库"
    ;;

  fetch-threads) # Threads(花钱,Apify actor): 关键词搜帖子/找账号
    shift
    $PY fetch_threads.py "$@" && $PY ingest.py && echo "[ops] fetch-threads 完成并已入库"
    ;;

  fusion)      # scan/report/merge/merge-all/cleanup
    shift
    $PY fusion.py "$@"
    ;;

  score)       # person 级打分(DeepSeek,便宜,报预算)
    shift
    $PY score_persons.py "$@"
    ;;

  draft)       # 起草(DeepSeek,报预算)
    shift
    $PY draft_v2.py draft "$@"
    ;;

  queue)       # 查看待审消息
    $PY draft_v2.py queue
    ;;

  approve)     # 审核通过 message_id
    $PY draft_v2.py approve "$2"
    ;;

  reject)      # 拒绝 message_id
    $PY draft_v2.py reject "$2"
    ;;

  expand-tv)   # 图谱扩散(零成本): 品种观点 → 评论者
    shift
    $PY tv_expand.py "$@"
    ;;

  ingest)      # 重新入库
    $PY ingest.py
    ;;

  send)        # 统一发送网关: ops.sh send <id> --channel mock|tg|wa|x
               # 代码层强制: 消息必须 approved, 发送后 status=sent
    shift
    $PY sender.py send "$@"
    ;;

  mock-reply)  # 模拟对方回复(仅 mock 通道演练用)
    $PY mock_client.py reply "$2" "$3"
    ;;

  listen)      # 启动回复监听: ops.sh listen tg | wa | x (后台运行)
    case "$2" in
      tg) nohup $PY reply_listener.py tg >> data/listener_tg.log 2>&1 &
          echo "[ops] tg 监听已启动 (log: data/listener_tg.log)"
          ;;
      wa) nohup $PY reply_listener.py wa >> data/listener_wa.log 2>&1 &
          echo "[ops] wa 监听已启动 (log: data/listener_wa.log)"
          ;;
      x) nohup $PY reply_listener.py x >> data/listener_x.log 2>&1 &
          echo "[ops] x 监听已启动 (log: data/listener_x.log)"
          ;;
      *) echo "用法: ops.sh listen tg|wa|x" ;;
    esac
    ;;

  reddit-send) # Reddit 私信发送(经 sender.py, 消息须 approved)
    shift
    $PY sender.py send "$@" --channel reddit
    ;;

  reddit-inbox) # 手动拉一次 Reddit 私信回复
    $PY reddit_client.py inbox
    ;;

  wa-start)    # 启动 WhatsApp worker(扫码登录,需手机)
    if [ ! -d venv/lib/node_modules ]; then
      cd /opt/leads
      [ -f package.json ] || echo '{"name":"wa-worker","type":"module"}' > package.json
      npm install @whiskeysockets/baileys qrcode --no-audit --no-fund 2>&1 | tail -3
    fi
    nohup node wa_worker.mjs >> data/wa_worker.log 2>&1 &
    echo "[ops] wa worker 已启动 (log: data/wa_worker.log)"
    sleep 2
    $PY wa_client.py status
    ;;

  wa-status)   # WhatsApp 连接状态
    $PY wa_client.py status
    ;;

  wa-qr)       # 查看/保存扫码二维码
    $PY wa_client.py qr
    ;;

  xleads)      # 生成 X 匹配表(花 credits,需 --yes)
    shift
    $PY x_client.py leads "$@"
    ;;

  xsend)       # X 发送(花 credits,消息须 approved,逐条确认)
    shift
    $PY sender.py send "$@" --channel x
    ;;

  reddit-resume) # 补救: 拉取 Apify 已完成 run 数据并入库(不重复花钱)
    shift
    $PY resume_reddit.py "$@" && $PY ingest.py && echo "[ops] reddit-resume 完成并已入库"
    ;;

  ig-status)    # IG cookie ?????(???)
    $PY ig_client.py status
    ;;

  ig-dm)        # IG ??: ops.sh ig-dm <???> <??>
    shift
    $PY ig_client.py dm "$@"
    ;;

  ig-comment)   # IG ??: ops.sh ig-comment <??URL> <??>
    shift
    $PY ig_client.py comment "$@"
    ;;

  contacts)    # 只读: 联系方式清单(平台分布 + 带 Telegram 的线索)
    shift
    $PY contacts.py "$@"
    ;;

  *)
    echo "用法: ops.sh {status|fetch-tv|fetch-yt|fetch-reddit|fetch-ig|fetch-fb|fetch-threads|fusion|score|draft|queue|approve|reject|expand-tv|ingest|send|mock-reply|listen|reddit-send|reddit-inbox|reddit-resume|wa-start|wa-status|wa-qr|xleads|xsend|ig-status|ig-dm|ig-comment|contacts} [参数]"
    ;;
esac
