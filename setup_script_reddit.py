import shutil, os, sys, subprocess

# 用法: setup_script_reddit.py <client_id> <client_secret>
cid, csec = sys.argv[1], sys.argv[2]

env_path = '/opt/leads/.env'
bak = env_path + '.bak.' + os.popen("date +%s").read().strip()
shutil.copy(env_path, bak)
print('备份:', bak)

# 读取现有 REDDIT_USER/PASS
vals = {}
for line in open(env_path, encoding='utf-8'):
    line = line.strip()
    if line.startswith('REDDIT_USER='):
        vals['user'] = line.split('=', 1)[1].strip("'").strip('"')
    elif line.startswith('REDDIT_PASS='):
        vals['pass'] = line.split('=', 1)[1].strip("'").strip('"')

lines = open(env_path, encoding='utf-8').read().splitlines()
updates = {
    'REDDIT_CLIENT_ID': cid,
    'REDDIT_CLIENT_SECRET': csec,
}
for k, v in updates.items():
    found = False
    for i, l in enumerate(lines):
        if l.startswith(k + '='):
            lines[i] = f'{k}={v}'
            found = True
            break
    if not found:
        lines.append(f'{k}={v}')
open(env_path, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
print('script 凭据已写入:', list(updates.keys()))

# 密码登录换 token
r = subprocess.run(
    [sys.executable, '/opt/leads/reddit_client.py', 'login'],
    capture_output=True, text=True, timeout=60,
)
print(r.stdout)
if r.returncode != 0:
    print('ERR:', r.stderr[:300])
