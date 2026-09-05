#!/bin/bash
# setup_git_repo.sh — 搭建 GitHub 私密仓库版本控制
set -e
cd /opt/leads

GITHUB_TOKEN="${GITHUB_TOKEN:-}"  # 从环境变量读取,勿硬编码
REPO_NAME="leads-system"
USER="AKA-liang"
PROXY="http://127.0.0.1:7890"

echo "=== 1. 创建 GitHub 私有仓库 ==="
curl -s -x "$PROXY" -X POST "https://api.github.com/user/repos" \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  -d "{\"name\": \"$REPO_NAME\", \"private\": true, \"description\": \"Leads acquisition system - 获客系统(阿仓)\"}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('created:', d.get('full_name') or d.get('message'))"

echo "=== 2. 准备 openclaw 配置模板 ==="
mkdir -p /opt/leads/openclaw_templates
python3 << 'EOF'
import json
d = json.load(open('/root/.openclaw/openclaw.json'))
if 'gateway' in d and 'auth' in d['gateway'] and 'token' in d['gateway'].get('auth', {}):
    d['gateway']['auth']['token'] = '${OPENCLAW_GATEWAY_TOKEN}'
json.dump(d, open('/opt/leads/openclaw_templates/openclaw.json.example', 'w'), indent=2, ensure_ascii=False)
print('openclaw.json.example 已生成(密钥已脱敏)')
EOF

echo "=== 3. 复制 workspace 引导文件 ==="
mkdir -p /opt/leads/openclaw_workspace
for f in SOUL.md TOOLS.md AGENTS.md USER.md HEARTBEAT.md IDENTITY.md; do
  if [ -f "/root/.openclaw/workspace/$f" ]; then
    cp "/root/.openclaw/workspace/$f" /opt/leads/openclaw_workspace/
  fi
done
echo "workspace 引导文件已复制"

echo "=== 4. git init ==="
if [ ! -d .git ]; then
  git init -b main
else
  echo "git 已初始化"
fi

echo "=== 5. git config ==="
git config user.email "AKA-liang@users.noreply.github.com"
git config user.name "AKA-liang"

echo "=== 6. 代理配置(git 走代理) ==="
git config http.proxy "$PROXY"
git config https.proxy "$PROXY"

echo "=== 7. 添加远程 ==="
git remote remove origin 2>/dev/null || true
git remote add origin "https://$USER:$GITHUB_TOKEN@github.com/$USER/$REPO_NAME.git"

echo "=== 8. 添加文件 ==="
git add -A
echo "=== 待提交文件 ==="
git status --short | head -40
echo "..."
git status --short | wc -l
