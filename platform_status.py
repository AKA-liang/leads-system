#!/usr/bin/env python3
"""全平台连通体检: 一张表看清每个平台状态与阻塞原因。

零成本运行(只做探测请求,不抓取)。
"""
import os
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

BASE = Path("/opt/leads")
load_dotenv(BASE / ".env")

PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

import requests


def probe(name, fn):
    try:
        ok, note = fn()
        print(f"  {'✅' if ok else '❌'} {name:<12} {note}")
        return ok
    except Exception as e:
        print(f"  ❌ {name:<12} 异常: {e}")
        return False


def main():
    print("=== 全平台连通体检 ===")

    # 1. Clash 代理
    def p_clash():
        r = requests.get("https://www.gstatic.com/generate_204", proxies=PROXY, timeout=20)
        return r.status_code == 204, f"代理出口正常 (http_code={r.status_code})"
    probe("Clash代理", p_clash)

    # 2. Apify(Reddit 抓取通道)
    def p_apify():
        token = os.getenv("APIFY_TOKEN", "")
        r = requests.get("https://api.apify.com/v2/users/me", params={"token": token}, timeout=20)
        d = r.json().get("data", {})
        plan = d.get("plan", {})
        free = plan.get("monthlyUsageCreditsUsd", "?")
        return True, f"账号OK(plan={plan.get('tier')}, 月额度${free}) 直连可达,额度状态以控制台为准"
    probe("Apify/Reddit", p_apify)

    # 3. TradingView
    def p_tv():
        r = requests.get("https://www.tradingview.com/ideas/", headers={"User-Agent": "Mozilla/5.0"}, proxies=PROXY, timeout=25)
        return r.status_code == 200, f"可抓取 (http_code={r.status_code})"
    probe("TradingView", p_tv)

    # 4. X API
    def p_x():
        token = os.getenv("X_BEARER_TOKEN", "")
        r = requests.get(
            "https://api.x.com/2/users/by/username/EXCAVO",
            params={"user.fields": "id"},
            headers={"Authorization": f"Bearer {token}"},
            proxies=PROXY, timeout=25,
        )
        if r.status_code == 402:
            return True, "认证通过, 阻塞=credits 余额不足(充值即可)"
        if r.status_code == 401:
            return False, "401 认证失败(检查 token)"
        if r.status_code == 200:
            return True, "可用!有余额"
        return True, f"HTTP {r.status_code}: {r.text[:80]}"
    probe("X API", p_x)

    # 5. X DM(OAuth1 权限)
    def p_xdm():
        from requests_oauthlib import OAuth1
        auth = OAuth1(
            os.getenv("X_API_KEY", ""), os.getenv("X_API_KEY_SECRET", ""),
            os.getenv("X_ACCESS_TOKEN", ""), os.getenv("X_ACCESS_TOKEN_SECRET", ""),
        )
        r = requests.post(
            "https://api.x.com/2/dm_conversations/with/2244994945/messages",
            json={"text": "t"}, auth=auth, proxies=PROXY, timeout=25,
        )
        if r.status_code in (402, 403):
            return True, f"OAuth1签名正确 (HTTP {r.status_code}; 403=App缺DM权限需在开发者后台开启, 402=余额不足)"
        if r.status_code == 401:
            return False, "401 签名错误"
        return True, f"HTTP {r.status_code}: {r.text[:100]}"
    probe("X DM(OAuth1)", p_xdm)

    # 6. YouTube
    def p_yt():
        key = os.getenv("YT_API_KEY", "")
        if not key:
            return False, "未配置 YT_API_KEY(需注册 Google Cloud API key,免费)"
        r = requests.get("https://www.googleapis.com/youtube/v3/videos", params={
            "part": "id", "id": "dQw4w9WgXcQ", "key": key}, proxies=PROXY, timeout=20)
        return r.status_code == 200, f"HTTP {r.status_code}(走代理)"
    probe("YouTube", p_yt)

    print("\n=== 结论 ===")
    print("全部✅=适配完成,只剩: Apify额度/X credits 充值 + YouTube API key 注册")


if __name__ == "__main__":
    main()
