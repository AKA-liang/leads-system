# Leads System — 海外交易者获客系统(阿仓)

面向期货/股票从业者的海外获客系统。AI 助手「阿仓」在海外社交平台发现、筛选、建联美欧散户交易者,引导至私域(TG/WA)转化。

## 主攻平台(2026-08 收缩策略)

| 平台 | 能力 | 状态 |
|---|---|---|
| **X** | DM 发送/回信监听/发帖 | ✅ 已通(陌生人DM受限~18%可发) |
| **Instagram** | 私信/评论/like/follow(Apify actor) | ✅ 全链路已通 |
| **Threads** | 发现;触达走 IG 联动(共享账号体系) | ✅ 发现已通 |
| Telegram | 私域承接 | ✅ 已通 |

**冻结平台**: TradingView / YouTube / Reddit / Facebook(保留历史数据,不主动抓取)

## 目录结构

```
/opt/leads/            # 主代码
├── ops.sh             # 操作面板(白名单,阿仓唯一入口)
├── sender.py          # 统一发送网关(mock/tg/wa/x/reddit)
├── ig_client.py       # IG 私信/评论(Apify actor)
├── x_client.py        # X lookup/dm/dm-inbox/follow/reply
├── fetch_*.py         # 各平台抓取(IG/Threads/Reddit/FB)
├── score_persons.py   # person 级打分(DeepSeek)
├── draft_v2.py        # 建联文案起草+审核队列
├── leads_web.py       # 数据台网页(example.com)
├── data/              # SQLite + 抓取数据(不上传)
└── openclaw_workspace/  # 阿仓引导文件(SOUL/TOOLS 等)
```

## 安全

- `.env`(全部 API 密钥)不上传
- `data/`(客户数据)不上传
- `openclaw.json.example` 为脱敏模板

## 自动备份

每天 23:00 cron 自动 commit+push(`git_backup.sh`)

## 核心链路

```
发现(fetch-ig/threads) → 打分(DeepSeek) → 审核(阿仓/网页)
→ 触达(ig-dm/ig-comment 或 x DM) → 回复监听 → 私域承接(TG/WA)
```
