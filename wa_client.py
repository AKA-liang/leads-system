#!/usr/bin/env python3
"""WhatsApp 通道封装 wa_client.py — 通过 wa_worker.mjs 的本地 HTTP 接口操作。

用法:
  python wa_client.py status                  # 连接状态
  python wa_client.py qr                      # 生成/展示扫码 PNG (data/wa_qr.png)
  python wa_client.py send <jid> "text"       # 发送消息

前置:
  - node wa_worker.mjs 已启动 (ops.sh wa-start)
  - 手机已扫码登录 (qr 页面给用户扫)
"""
import argparse
import os
import sys
from pathlib import Path

import requests

BASE = Path("/opt/leads")
WORKER = os.getenv("WA_WORKER_URL", "http://127.0.0.1:18791")


def status():
    try:
        j = requests.get(f"{WORKER}/status", timeout=5).json()
    except Exception as e:
        print(f"  [wa] worker 不可达: {e} (先 ops.sh wa-start)")
        sys.exit(1)
    if j.get("connected"):
        print(f"  [wa] 已连接: {j['phone']}")
    else:
        print(f"  [wa] 未连接, QR {'就绪' if j.get('qrReady') else '未生成'} (ops.sh wa-qr 查看)")
    return j


def qr():
    r = requests.get(f"{WORKER}/qr.png", timeout=10)
    if r.status_code != 200:
        print("  [wa] 暂无 QR(worker 未启动或未生成), 先启动 wa-start")
        sys.exit(1)
    out = BASE / "data" / "wa_qr.png"
    out.write_bytes(r.content)
    print(f"  [wa] QR 已保存: {out}")
    print(f"  [wa] 用手机 WhatsApp: 设置 -> 已关联设备 -> 扫码登录")


def send_wa(jid, text):
    """jid: 手机号或 8613800000000@s.whatsapp.net; 返回 (ok, 错误详情)"""
    if "@" not in jid:
        jid = f"{jid}@s.whatsapp.net"
    try:
        r = requests.post(f"{WORKER}/send", json={"jid": jid, "text": text}, timeout=30)
        j = r.json()
    except Exception as e:
        print(f"  [wa] worker 不可达: {e} (先 ops.sh wa-start)")
        return False, f"WA worker 不可达: {e}"
    if j.get("ok"):
        print(f"  [wa] 已发送给 {jid}")
        return True, ""
    err = str(j.get("error") or "未知错误")
    print(f"  [wa] 发送失败: {err}")
    return False, f"WA {err}"


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("status")
    sub.add_parser("qr")
    s = sub.add_parser("send")
    s.add_argument("jid")
    s.add_argument("text")
    args = p.parse_args()
    if args.cmd == "status":
        status()
    elif args.cmd == "qr":
        qr()
    elif args.cmd == "send":
        ok, err = send_wa(args.jid, args.text)
        sys.exit(0 if ok else 1)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
