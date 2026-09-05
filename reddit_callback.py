#!/usr/bin/env python3
"""Reddit OAuth 回调服务 — 监听 /callback 自动捕获 code 换 token(零延迟)。"""
import base64
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, '/opt/leads')
from dotenv import load_dotenv
import requests

BASE = '/opt/leads'
load_dotenv(BASE + '/.env')
DONE = False


def exchange(code):
    global DONE
    cid = os.getenv('REDDIT_CLIENT_ID', '')
    csec = os.getenv('REDDIT_CLIENT_SECRET', '')
    user = os.getenv('REDDIT_USER', '')
    UA = f"linux:leads-bot:v1.0 (by /u/{user})"
    PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
    basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    for ru in ["https://example.com/callback", "https://example.com/callback/"]:
        r = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": ru},
            headers={"Authorization": f"Basic {basic}", "User-Agent": UA},
            proxies=PROXY, timeout=25,
        )
        print(f"  redirect={ru} -> {r.status_code} {r.text[:120]}", flush=True)
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
            print(f"✅ TOKEN 已写入! refresh={'有' if refresh else '无'}", flush=True)
            DONE = True
            return True
    return False


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global DONE
        q = parse_qs(urlparse(self.path).query)
        code = (q.get('code') or [''])[0]
        if code:
            print(f"🎯 捕获 code: {code}", flush=True)
            ok = exchange(code)
            body = ("<h2 style='font-family:sans-serif'>✅ Reddit 授权成功! token 已保存, 可以关闭此页。</h2>"
                    if ok else
                    "<h2 style='font-family:sans-serif'>❌ 换 token 失败, 看服务器日志。</h2>")
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body.encode())
            if ok:
                threading.Timer(2.0, self.server.shutdown).start()
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"no code param")

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    port = int(os.getenv('REDDIT_CB_PORT', '8081'))
    print(f"回调服务监听 0.0.0.0:{port} ...", flush=True)
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()
