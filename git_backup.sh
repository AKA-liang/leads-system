#!/bin/bash
# git_backup.sh — 每天自动备份代码到 GitHub
cd /opt/leads

# 1. 更新 openclaw 引导文件(保持仓库最新)
for f in SOUL.md TOOLS.md AGENTS.md USER.md HEARTBEAT.md IDENTITY.md; do
  if [ -f "/root/.openclaw/workspace/$f" ]; then
    cp "/root/.openclaw/workspace/$f" /opt/leads/openclaw_workspace/ 2>/dev/null
  fi
done

# 2. 更新 openclaw.json 模板(脱敏)
python3 << 'EOF' 2>/dev/null
import json
d = json.load(open('/root/.openclaw/openclaw.json'))
if 'gateway' in d and 'auth' in d.get('gateway', {}) and 'token' in d['gateway'].get('auth', {}):
    d['gateway']['auth']['token'] = '${OPENCLAW_GATEWAY_TOKEN}'
json.dump(d, open('/opt/leads/openclaw_templates/openclaw.json.example', 'w'), indent=2, ensure_ascii=False)
EOF

# 3. git add + commit + push
git add -A
if git diff --cached --quiet; then
  echo "[backup] 无变化, 跳过"
else
  git commit -m "auto-backup $(date '+%Y-%m-%d %H:%M')"
  git push origin main 2>&1 | tail -1
  echo "[backup] 已备份 $(date '+%Y-%m-%d %H:%M')"
fi
