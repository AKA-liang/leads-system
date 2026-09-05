#!/usr/bin/env python3
"""监听 localhost:8080 捕获 Reddit 授权 code 并立即换 token(零延迟)。"""
import base64
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, '/opt/leads')
from dotenv import load_dotenv
import requests

BASE = '/opt/leads'
load_dotenv(BASE + '/.env')


def exchange(code):
    cid = os.getenv('REDDIT_CLIENT_ID', '')
    csec = os.getenv('REDDIT_CLIENT_SECRET', '')
    user = os.getenv('REDDIT_USER', '')
    UA = f"linux:leads-bot:v1.0 (by /u/{user})"
    PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
    basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    for ru in ["http://localhost:8080", "http://localhost:8080/"]:
        r = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": ru},
            headers={"Authorization": f"Basic {basic}", "User-Agent": UA},
            proxies=PROXY, timeout=25,
        )
        if r.status_code == 200 and "access_token" in r.text:
            j = r.json()
            token = j.get("access_token", "")
            refresh = j.get("refresh_token", "")
            env_path = BASE + '/.env'
            lines = open(env_path, encoding='utf-8').read().splitlines()
            updates = {"REDDIT_TOKEN": token}
            if refresh:
                updates["REDDIT_REFRESH_TOKEN"] = refresh
            for k, v in updates.items():
                found = False
                for i, l in enumerate(lines):
                    if l.startswith(k + "="):
                        lines[i] = f"{k}={v}"
                        found = True
                        break
                if not found:
                    lines.append(f"{k}={v}")
            open(env_path, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
            print(f"\n✅ token 已写入! refresh={'有' if refresh else '无'}", flush=True)
            return True
        print(f"  redirect={ru} -> {r.status_code}", flush=True)
    return False


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        code = (q.get('code') or [''])[0]
        if code:
            print(f"🎯 捕获到 code: {code}", flush=True)
            ok = exchange(code)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>Auth captured! You can close this page.</h2>" if ok else b"<h2>Failed, check server log</h2>")
            print("监听完成, 退出", flush=True)
            import threading
            threading.Timer(1.0, self.server.shutdown).start()
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"no code")

    def log_message(self, *a):
        pass


print("监听 http://localhost:8080 ... 现在去浏览器点 Allow 授权!", flush=True)
HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
