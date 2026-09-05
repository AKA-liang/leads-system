#!/bin/bash
# 获客流水线一键运行(意图驱动版)
#
# 用法:
#   ./run_all.sh --intent "做NQ/ES期货的美国散户" --yes     # 推荐:中文意图,自动润色
#   ./run_all.sh --intent "..." --top 200 --max-comments 30 --yes
#   ./run_all.sh --search-terms "NQ futures,ES micro" --subreddits futures,Daytrading --yes
#   ./run_all.sh --dry-run --intent "..."                   # 只看预算不跑
#
# 流程: 润色关键词 → 预算确认 → 抓取 → 打分 → 起草
# 费用: Apify 按条计费(见脚本输出预算);DeepSeek 打分/起草很便宜
set -e
cd /opt/leads

INTENT=""
SEARCH_TERMS=""
SUBREDDITS=""
TOP=60
MAX_POSTS=40
MAX_COMMENTS=20
YES=""
DRY=""
SORT="top"
TIME="month"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --intent)       INTENT="$2"; shift 2 ;;
    --search-terms) SEARCH_TERMS="$2"; shift 2 ;;
    --subreddits)   SUBREDDITS="--subreddits $2"; shift 2 ;;
    --top)          TOP="$2"; shift 2 ;;
    --max-posts)    MAX_POSTS="$2"; shift 2 ;;
    --max-comments) MAX_COMMENTS="$2"; shift 2 ;;
    --sort)         SORT="$2"; shift 2 ;;
    --time)         TIME="$2"; shift 2 ;;
    --yes)          YES="--yes"; shift ;;
    --dry-run)      DRY="--dry-run"; shift ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done

if [ -z "$INTENT" ] && [ -z "$SEARCH_TERMS" ]; then
  echo "❌ 必须提供 --intent \"中文意图\" 或 --search-terms \"英文关键词\",禁止无目标抓取。"
  echo "示例: ./run_all.sh --intent \"做NQ/ES期货的美国散户\" --yes"
  exit 2
fi

FETCH_ARGS="--max-posts $MAX_POSTS --max-comments $MAX_COMMENTS --sort $SORT --time $TIME $SUBREDDITS $YES $DRY"
[ -n "$INTENT" ] && FETCH_ARGS="--intent \"$INTENT\" $FETCH_ARGS"
[ -n "$SEARCH_TERMS" ] && FETCH_ARGS="--search-terms \"$SEARCH_TERMS\" $FETCH_ARGS"

echo "========== 1/3 抓取 =========="
eval /opt/leads/venv/bin/python fetch_reddit.py $FETCH_ARGS

if [ -n "$DRY" ]; then echo "[dry-run] 流程结束,未花费任何钱。"; exit 0; fi

echo "========== 2/3 打分 =========="
/opt/leads/venv/bin/python score_leads.py --top "$TOP"

echo "========== 3/3 起草消息 =========="
/opt/leads/venv/bin/python draft_messages.py --top 15

echo "========== 完成 =========="
echo "审核表: /opt/leads/data/drafts.csv (approved 列: 空=待审, approved/edit/reject)"
