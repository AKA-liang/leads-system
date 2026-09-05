# Leads System — 海外交易者获客系统(阿仓)

面向期货/股票从业者的**海外散户交易者获客系统**。系统在海外社交平台发现、筛选、建联美欧散户交易者,引导至私域(TG/WA)转化。

> ⚠️ 开源仅供学习参考。实际使用请遵守各平台 ToS 与所在国家/地区法律法规(见「合规声明」)。

## 功能总览

| 模块 | 能力 | 状态 |
|---|---|---|
| **发现** | Reddit/YouTube/TradingView/IG/Threads/FB 抓取 + 图谱扩散(品种→观点→评论者) | 可用 |
| **融合** | 跨平台身份匹配(外链强匹配/同名待确认/官方账号清理) | 可用 |
| **画像** | DeepSeek 按「person 全平台档案」打分(是否真交易者/品种/建联价值) | 可用 |
| **起草** | 引用对方具体发言的英文建联文案 + 起草理由,人工逐条审核 | 可用 |
| **触达** | X DM / IG 私信 / 评论 / 关注;TG/WA 私域承接 | X 需 credits |
| **数据台** | 轻量 Web 面板(人员库/审核队列/融合/操作日志,导出 CSV) | 可用 |

## 目录结构

```
.
├── ops.sh             # 操作面板(白名单,agent 唯一入口)
├── sender.py          # 统一发送网关(mock/tg/wa/x/reddit/ig)
├── ig_client.py       # IG 私信/评论(Apify actor)
├── x_client.py        # X lookup/dm/dm-inbox/follow/reply
├── fetch_*.py         # 各平台抓取与图谱扩散
├── score_persons.py   # person 级打分(DeepSeek)
├── draft_v2.py        # 建联文案起草 + 审核队列
├── leads_web.py       # 数据台网页(Flask)
├── db.py              # SQLite 统一线索库(schema 版本化)
├── fusion.py          # 身份融合器
├── auto_send.py       # 自动发送调度(限速/熔断/防骚扰,默认关闭)
└── openclaw_templates/  # OpenClaw agent 配置示例(环境变量占位)
```

## 快速开始(本地/自部署)

```bash
# 1. 依赖(Python >= 3.8)
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt  # 或手动装: requests python-dotenv apify-client flask waitress pyyaml

# 2. 密钥(.env,参照以下最小集;不提交仓库)
cat > .env <<'ENV'
APIFY_TOKEN=          # Apify 平台
DEEPSEEK_API_KEY=     # DeepSeek 模型
DEEPSEEK_MODEL=deepseek-v4-flash
ENV

# 3. 数据台
waitress-serve --host=127.0.0.1 --port=8080 leads_web:app

# 4. 抓取-打分-起草一步到位
./run_all.sh --intent "做NQ/ES期货的美国散户" --yes   # 会先打印预算再执行
```

## 密钥与数据安全(内置防护)

- `.env`、`data/`(SQLite+抓取数据)、会话凭据:`.gitignore` 已全部排除,**提交即漏,默认拦截**
- 抓取前强制「预算报告 + 确认」,默认只打未打分(不重复烧钱)
- 发送链路:逐条人工审核 + 限速(随机 2-10 分钟)+ 连续失败熔断 + 7 天防骚扰
- `data/` 保存的是**公开可见的社交数据**,用于建联分析,不包含隐私字段

## 合规声明

1. **平台条款**:各平台(Reddit/X/IG 等)禁止或限制自动化与批量私信,使用前请阅读并遵守 ToS;账号风险自负。
2. **数据合规**:抓取内容为公开数据;涉及欧盟用户时遵守 GDPR——只采集公开信息、最小化留存、仅用于自己服务的建联分析,不转售。
3. **金融合规**:不得用于承诺收益/劝诱开户/证券期货招揽等违规场景;触达话术内置「无收益承诺、不附链接」红线。
4. **第三方 API**:Apify/DeepSeek 按各自许可与计费规则使用;商业使用请遵循其协议。

## License

[![MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Released under MIT License. Copyright (c) 2026 AKA-liang.

---

> 本项目为个人获客自动化实验沉淀。代码可自由使用修改,但请你自己评估并承担使用后果。
