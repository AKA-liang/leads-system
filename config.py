#!/usr/bin/env python3
"""版块目录与量级/成本边界计算。

垂直相关默认版块: futures / Daytrading / algotrading / options / thetagang
其余为可选项,执行前由用户确认是否添加。
"""
from pathlib import Path

BASE = Path("/opt/leads")

# 版块目录: vertical=业务线, default=True 表示垂直相关默认启用
SUBREDDIT_CATALOG = {
    "futures":      {"label": "期货",          "vertical": "期货", "default": True},
    "Daytrading":   {"label": "日内交易",      "vertical": "日内", "default": True},
    "algotrading":  {"label": "量化/自动化",   "vertical": "量化", "default": True},
    "options":      {"label": "期权",          "vertical": "期权", "default": True},
    "thetagang":    {"label": "期权卖方(Theta)","vertical": "期权", "default": True},
    "stocks":       {"label": "股票",          "vertical": "股票", "default": False},
    "investing":    {"label": "投资",          "vertical": "股票", "default": False},
    "wallstreetbets":{"label": "WSB",          "vertical": "股票", "default": False},
    "Forex":        {"label": "外汇",          "vertical": "外汇", "default": False},
    "trading":      {"label": "通用交易",      "vertical": "通用", "default": False},
    "CFD":          {"label": "CFD",           "vertical": "外汇", "default": False},
    "optionswheel": {"label": "期权轮动",      "vertical": "期权", "default": False},
    "TradingViewIdeas": {"label": "(非Reddit,后续接入)", "vertical": "通用", "default": False},
}

DEFAULT_SUBS = [s for s, v in SUBREDDIT_CATALOG.items() if v["default"]]

# 成本边界(单位:美元)
APIFY_ACTOR_START = 0.02          # 每次 actor 启动
APIFY_PER_ITEM = 0.002            # 每条存储结果(帖/评论都算)
APIFY_FREE_MONTHLY = 5.0          # 免费档每月额度
APIFY_ITEM_LIMIT_PER_LISTING = 1000  # Reddit 单 listing/搜索 最多约 1000 条
DEEPSEEK_PER_1K_TOKENS = 0.0009   # 大约值,打分/起草按 token 计

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"


def estimate_cost(n_subs, max_posts, max_comments, items_per_post_est=12):
    """估算一次抓取的 Apify 花费。
    items ≈ 帖数 + 评论数(按平均每帖 items_per_post_est 条评论估算)。
    """
    posts = n_subs * max_posts
    comments = posts * min(max_comments, items_per_post_est)
    items = posts + comments
    cost = APIFY_ACTOR_START + items * APIFY_PER_ITEM
    return items, cost


def budget_report(n_subs, max_posts, max_comments):
    """生成量级边界报告(中文,给用户看)。"""
    items, cost = estimate_cost(n_subs, max_posts, max_comments)
    lines = [
        "【量级边界】",
        f"- 单次抓取上限:每版块最多约 1000 帖(Reddit 单 listing 限制),评论数可调",
        f"- 本次配置: {n_subs} 个版块 × 每版块 {max_posts} 帖 × 每帖最多 {max_comments} 条评论",
        f"- 预计数据量: 约 {items} 条(帖+评论)",
        f"- 预计 Apify 花费: 约 ${cost:.2f}(免费档每月 $5,剩余额度可在 apify.com 查)",
        f"- 打分(DeepSeek): 按 --top N 决定,全量几千人也只要几块钱",
        f"- 时间: fast mode 约 500-1000 帖/分钟",
        "",
        "确认执行请回复: 执行 (或调整 --max-posts/--max-comments/--top 后再跑)",
    ]
    return "\n".join(lines)


def catalog_text():
    lines = ["【版块目录】"]
    for s, v in SUBREDDIT_CATALOG.items():
        mark = "✓默认" if v["default"] else "  可选"
        lines.append(f"- r/{s:<14} {v['label']:<12} {mark}")
    return "\n".join(lines)
