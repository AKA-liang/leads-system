import os, base64, requests, sys
sys.path.insert(0, '/opt/leads')
from dotenv import load_dotenv
load_dotenv('/opt/leads/.env')

code = sys.argv[1].strip()
cid = os.getenv('REDDIT_CLIENT_ID', '')
csec = os.getenv('REDDIT_CLIENT_SECRET', '')
user = os.getenv('REDDIT_USER', '')
UA = f"linux:leads-bot:v1.0 (by /u/{user})"
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()

# 尝试多种 redirect_uri 变体(Reddit 要求精确匹配, 逐一试)
variants = [
    "http://localhost:8080",
    "http://localhost:8080/",
    "https://localhost:8080",
    "https://localhost:8080/",
]
for ru in variants:
    r = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": ru},
        headers={"Authorization": f"Basic {basic}", "User-Agent": UA},
        proxies=PROXY, timeout=25,
    )
    print(f"redirect={ru:<28} -> {r.status_code} {r.text[:120]}")
    if r.status_code == 200 and "access_token" in r.text:
        print("SUCCESS!")
        break
