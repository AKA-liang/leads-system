import sys

# 用法: 传入 client_id client_secret reddit_username reddit_password
if len(sys.argv) != 5:
    print("用法: setup_reddit.py <client_id> <client_secret> <username> <password>")
    sys.exit(1)

cid, csec, user, pwd = sys.argv[1:5]

import shutil, os
env_path = '/opt/leads/.env'
bak = env_path + '.bak.' + os.popen("date +%s").read().strip()
shutil.copy(env_path, bak)
print('备份:', bak)

vals = {
    'REDDIT_CLIENT_ID': cid,
    'REDDIT_CLIENT_SECRET': csec,
    'REDDIT_USER': user,
    'REDDIT_PASS': pwd,
}

lines = open(env_path, encoding='utf-8').read().splitlines()
existing = {l.split('=')[0] for l in lines if '=' in l}
changed = []
for k, v in vals.items():
    found = False
    for i, l in enumerate(lines):
        if l.startswith(k + '='):
            lines[i] = f'{k}={v}'
            found = True
            changed.append(k)
            break
    if not found:
        lines.append(f'{k}={v}')
        changed.append(k + '(新增)')

open(env_path, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
print('已写入:', changed)
