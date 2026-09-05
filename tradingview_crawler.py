#!/usr/bin/env python3
"""TradingView 线索爬虫 v3(走 Clash 代理)。

两种模式:
  --mode ideas    抓热门观点页作者(泛,通用)
  --mode symbols  按期货品种页抓作者(精准,每个品种页 = 该品种活跃交易者池)
  --mode relink   补抓已有 CSV 作者的外链(不重复抓作者,只刷新 social 列)

数据来源:
  - https://www.tradingview.com/ideas/             热门观点
  - https://www.tradingview.com/symbols/{SYM}/     品种页(如 ES1! 标普期货, GC1! 黄金)
  - https://www.tradingview.com/u/{name}/          作者主页

产出: data/tradingview_leads.csv
字段: author, context, followers, ideas, scripts, joined,
      social_x/youtube/telegram/facebook/instagram/discord/website, profile_url

注意: 页面结构可能变;限速 1.2s/请求;必须走代理(直连被墙)。
      Also on 区块的文案可能被 React 注释符(<!-- -->)拆开,按 aria-label 定位链接。
"""
import argparse
import csv
import re
import time
from collections import Counter, OrderedDict
from pathlib import Path

import requests

BASE = Path("/opt/leads")
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}

IDEAS_URL = "https://www.tradingview.com/ideas/"
SYMBOL_URL = "https://www.tradingview.com/symbols/{}/"
USER_URL = "https://www.tradingview.com/u/{}/"

# 常用期货品种(TradingView 连续合约代码)
DEFAULT_SYMBOLS = ["ES1!", "NQ1!", "GC1!", "CL1!", "6E1!", "ZN1!"]

# aria-label -> 字段名
LABEL_MAP = {
    "X": "x",
    "Twitter": "x",
    "Youtube": "youtube",
    "YouTube": "youtube",
    "Telegram": "telegram",
    "Facebook": "facebook",
    "Instagram": "instagram",
    "Discord": "discord",
}
SOCIAL_FIELDS = ["x", "youtube", "telegram", "facebook", "instagram", "discord", "website"]


def fetch(url, timeout=25):
    r = requests.get(url, headers=HEADERS, proxies=PROXY, timeout=timeout)
    r.raise_for_status()
    return r.text


def parse_ideas_authors(html):
    authors = re.findall(r'/u/([A-Za-z0-9_\-]+)/', html)
    seen, out = set(), []
    for a in authors:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def parse_symbol_ideas(html):
    """从品种页提取 (author, idea_title) 配对。返回 OrderedDict author->首个观点标题。"""
    authors = re.findall(r'data-qa-id="ui-lib-card-link-author"><a href="/u/([A-Za-z0-9_\-]+)/"', html)
    charts = re.findall(
        r'href="(https://www\.tradingview\.com/chart/[^"]*/)"[^>]*data-qa-id="ui-lib-card-link-image"',
        html,
    )
    titles = []
    for c in charts:
        slug = c.rstrip("/").rsplit("/", 1)[-1]
        parts = slug.split("-", 1)
        titles.append(parts[1].replace("-", " ") if len(parts) > 1 else slug)
    out = OrderedDict()
    for a, t in zip(authors, titles):
        if a not in out:
            out[a] = t
    return out


def parse_user(html):
    def grab(pattern, flags=0):
        m = re.search(pattern, html, flags)
        return m.group(1).strip() if m else ""

    data = {}
    data["followers"] = grab(r'title-[^"]*">Followers</div><span class="value-[^"]*"[^>]*>([^<]+)</span>')
    data["following"] = grab(r'title-[^"]*">Following</div><span class="value-[^"]*"[^>]*>([^<]+)</span>')
    data["ideas"] = grab(r'title-[^"]*">Ideas</div><span class="value-[^"]*"[^>]*>([^<]+)</span>')
    data["scripts"] = grab(r'title-[^"]*">Scripts</div><span class="value-[^"]*"[^>]*>([^<]+)</span>')
    data["joined"] = grab(r'Joined ([A-Z][a-z]{2} \d{1,2}, \d{4})')
    socials = parse_socials(html)
    return data, socials


def parse_socials(html):
    """按 title="xxx on Platform" 或 aria-label 抓作者社交外链(Also on 区块)。

    页面结构两种形态(React 渲染顺序不固定):
      <a title="NAME on X" href="https://x.com/..." ... aria-label="X" ...>
      <a aria-label="X" ... href="https://x.com/..." ...>
    只取明确是社交平台且 href 指向对应域名的链接, 排除 TradingView 官方账号。
    """
    socials = {}

    def domain_ok(field, href):
        d = {
            "x": lambda h: "x.com" in h or "twitter.com" in h,
            "youtube": lambda h: "youtube.com" in h,
            "telegram": lambda h: "t.me" in h or "telegram.me" in h,
            "facebook": lambda h: "facebook.com" in h,
            "instagram": lambda h: "instagram.com" in h,
            "discord": lambda h: "discord.gg" in h,
        }[field](href)
        return d

    # 1) title="xxx on Platform" href=...  (href 紧跟 title)
    for m in re.finditer(
        r'title="[^"]* on (X|Twitter|Youtube|YouTube|Telegram|Facebook|Instagram|Discord)"[^>]*href="(https?://[^"]*)"',
        html,
    ):
        label, href = m.group(1), m.group(2)
        field = LABEL_MAP.get(label)
        if not field or field in socials:
            continue
        if not domain_ok(field, href):
            continue
        if re.search(r'(tradingview|/tradingview/)', href, re.I):
            continue
        socials.setdefault(field, href)

    # 2) aria-label 在 href 前: aria-label="X" ... href=...
    for m in re.finditer(
        r'aria-label="([^"]*)"[^>]*href="(https?://[^"]*)"', html
    ):
        label, href = m.group(1), m.group(2)
        field = LABEL_MAP.get(label)
        if not field or field in socials:
            continue
        if not domain_ok(field, href):
            continue
        if re.search(r'(tradingview|/tradingview/)', href, re.I):
            continue
        socials.setdefault(field, href)

    # 3) 兜底: title="xxx on Platform" 附近 href 但顺序异常
    for field, domain in [("x", r"x\.com|twitter\.com"), ("youtube", r"youtube\.com"),
                          ("telegram", r"t\.me"), ("facebook", r"facebook\.com"),
                          ("instagram", r"instagram\.com"), ("discord", r"discord\.gg")]:
        if field in socials:
            continue
        m = re.search(
            r'href="(https?://[^"]*' + domain + r'/[^"]*)"[^>]*title="[^"]* on ',
            html,
        )
        if not m:
            m = re.search(
                r'href="(https?://' + domain + r'/[^"]*)"[^>]*aria-label="',
                html,
            )
        if m:
            href = m.group(1)
            if not re.search(r'tradingview', href, re.I):
                socials[field] = href
    # 4) website: Also on 区块内的第一个站外链接
    also = html.find("Also on")
    seg = html[also:also + 5000] if also >= 0 else ""
    social_hosts = (
        r"tradingview\.com|x\.com|twitter\.com|youtube\.com|facebook\.com|"
        r"instagram\.com|t\.me|telegram\.me|discord\.gg|static\.tradingview\.com|s3\.tradingview\.com"
    )
    web = re.findall(
        r'href="(https?://(?!' + social_hosts + r')[^"]*)"',
        seg,
    )
    if web:
        # 排除已经被识别为社交平台的那个链接(兜底分支可能混入)
        for w in web:
            if not any(h in w for h in ["x.com", "twitter", "youtube", "facebook", "instagram", "t.me", "discord"]):
                socials["website"] = w
                break
    return socials


def row_for(author, context, data, socials):
    return {
        "author": author,
        "context": context,
        "followers": data["followers"],
        "ideas": data["ideas"],
        "scripts": data["scripts"],
        "joined": data["joined"],
        "social_x": socials.get("x", ""),
        "social_youtube": socials.get("youtube", ""),
        "social_telegram": socials.get("telegram", ""),
        "social_facebook": socials.get("facebook", ""),
        "social_instagram": socials.get("instagram", ""),
        "social_discord": socials.get("discord", ""),
        "social_website": socials.get("website", ""),
        "profile_url": USER_URL.format(author),
    }


def crawl_authors(names, contexts, out):
    rows = []
    for i, a in enumerate(names):
        try:
            ph = fetch(USER_URL.format(a))
        except Exception as e:
            print(f"  [{i+1}/{len(names)}] {a} 失败: {e}", flush=True)
            time.sleep(1.2)
            continue
        data, socials = parse_user(ph)
        rows.append(row_for(a, contexts.get(a, ""), data, socials))
        print(f"  [{i+1}/{len(names)}] {a}: {data['followers'] or '?'}粉 {data['ideas'] or '?'}ideas "
              f"外链={'+'.join(k for k in socials) or '无'} | {contexts.get(a,'')[:40]}", flush=True)
        time.sleep(1.2)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["author"] + SOCIAL_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"saved: {out} ({len(rows)} leads)")
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", default="symbols", choices=["ideas", "symbols", "relink"])
    p.add_argument("--symbols", default="", help="期货品种,逗号分隔,默认 ES1!,NQ1!,GC1!,CL1!,6E1!,ZN1!")
    p.add_argument("--max-authors", type=int, default=30, help="最多抓多少作者主页")
    p.add_argument("--out", default=str(BASE / "data" / "tradingview_leads.csv"))
    args = p.parse_args()

    out = Path(args.out)

    if args.mode == "relink":
        # 补抓模式: 读已有 CSV, 只刷新每个作者的外链, 保留原 context/粉丝等
        if not out.exists():
            print(f"relink 模式需要已有 {out}")
            return
        old = list(csv.DictReader(out.open(encoding="utf-8-sig")))
        names = [r["author"] for r in old if r.get("author")]
        contexts = {r["author"]: r.get("context", "") for r in old}
        print(f"=== relink: 补抓 {len(names)} 个作者外链 ===", flush=True)
        rows = crawl_authors(names, contexts, out)
        # 把新外链写回(合并)
        with out.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["author"] + SOCIAL_FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"relink 完成: {len(rows)} 作者外链已刷新")
        return

    candidates = OrderedDict()  # author -> context
    if args.mode == "ideas":
        print("=== 热门观点页 ===", flush=True)
        html = fetch(IDEAS_URL)
        for a in parse_ideas_authors(html):
            candidates[a] = ""
        print(f"候选作者: {len(candidates)}", flush=True)
    else:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()] or DEFAULT_SYMBOLS
        print(f"=== 品种页: {syms} ===", flush=True)
        for sym in syms:
            try:
                html = fetch(SYMBOL_URL.format(sym))
                found = parse_symbol_ideas(html)
                for a, t in found.items():
                    if a not in candidates:
                        candidates[a] = f"{sym} | {t[:80]}"
                print(f"  {sym}: +{len(found)} 作者 (累计 {len(candidates)})", flush=True)
            except Exception as e:
                print(f"  {sym} 失败: {e}", flush=True)
            time.sleep(1.2)

    names = list(candidates.keys())[:args.max_authors]
    print(f"=== 抓作者主页 ({len(names)} 个) ===", flush=True)
    crawl_authors(names, candidates, out)


if __name__ == "__main__":
    main()
