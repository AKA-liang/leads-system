#!/usr/bin/env python3
"""Leads Ops Web 控制台 — 数据查看 + 审核操作

页面:
  /            仪表盘(统计)
  /persons     人员库(搜索/过滤/排序)
  /person/<id> 个人详情(账号/事件/消息)
  /messages    审核队列(approve/reject)
  /ops         操作日志
  /fusion      融合报告
认证: HTTP Basic Auth(账号密码在 /opt/leads/.env: WEB_USER / WEB_PASS)
运行: waitress-serve(生产) 或 flask run(开发)
"""
import base64
import csv
import io
import os
import subprocess
import sys
from urllib.parse import quote
from pathlib import Path

sys.path.insert(0, '/opt/leads')
from dotenv import load_dotenv, dotenv_values
from flask import Flask, request, redirect, Response

from db import get_conn, log_op

BASE = Path("/opt/leads")
ENV_FILE = BASE / ".env"
load_dotenv(ENV_FILE)

app = Flask(__name__)


def read_env(key, default=""):
    """每次直接读 .env 文件(不依赖进程启动时加载的 env, 改配置即时生效)。"""
    try:
        return str(dotenv_values(ENV_FILE).get(key) or default)
    except Exception:
        return default


def env_bool(key, default=False):
    return read_env(key, "true" if default else "false").strip().lower() in ("1", "true", "yes", "on")
AUTH_USER = os.getenv("WEB_USER", "boss")
AUTH_PASS = os.getenv("WEB_PASS", "")


def check_auth(header):
    if not header or not header.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(header.split(" ", 1)[1]).decode()
        u, _, p = raw.partition(":")
        return u == AUTH_USER and p == AUTH_PASS
    except Exception:
        return False


@app.before_request
def auth():
    if not check_auth(request.headers.get("Authorization")):
        return Response401()


def Response401():
    r = redirect("/") if False else None
    return ("", 401, {"WWW-Authenticate": 'Basic realm="LeadsOps"'})


def html(title, body, active=""):
    nav = f"""
    <div style="background:#1a1a2e;padding:12px 20px;color:#fff;display:flex;gap:24px;align-items:center;flex-wrap:wrap">
      <b style="font-size:17px">🦞 阿仓数据台</b>
      <a href="/" style="color:{'#ffd166' if active=='dash' else '#ccc'};text-decoration:none">仪表盘</a>
      <a href="/persons" style="color:{'#ffd166' if active=='persons' else '#ccc'};text-decoration:none">人员库</a>
      <a href="/messages" style="color:{'#ffd166' if active=='messages' else '#ccc'};text-decoration:none">审核队列</a>
      <a href="/replies" style="color:{'#ffd166' if active=='replies' else '#ccc'};text-decoration:none">回复中心</a>
      <a href="/wa-qr" style="color:{'#ffd166' if active=='wa' else '#ccc'};text-decoration:none">WA扫码</a>
      <a href="/fusion" style="color:{'#ffd166' if active=='fusion' else '#ccc'};text-decoration:none">融合</a>
      <a href="/ops" style="color:{'#ffd166' if active=='ops' else '#ccc'};text-decoration:none">操作日志</a>
      <a href="https://claw.example.com" target="_blank" style="background:#28a745;color:#fff;padding:5px 14px;border-radius:6px;text-decoration:none;font-size:13.5px">🤖 控制面板 →</a>
      <span style="margin-left:auto;font-size:13px;color:#888">数据资产库 v1</span>
    </div>"""
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
    <title>{title}</title>
    <style>
      body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f4f6f9;color:#222}}
      .wrap{{max-width:1200px;margin:20px auto;padding:0 16px}}
      table{{border-collapse:collapse;width:100%;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
      th,td{{padding:8px 10px;border-bottom:1px solid #eee;text-align:left;font-size:13.5px}}
      th{{background:#2d3561;color:#fff;font-weight:600}}
      tr:hover{{background:#f0f4ff}}
      .card{{background:#fff;border-radius:8px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
      .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:18px}}
      .stat{{background:#fff;border-radius:8px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.08);border-top:3px solid #2d3561}}
      .stat b{{font-size:26px;display:block}}
      .stat span{{color:#777;font-size:13px}}
      .btn{{display:inline-block;padding:4px 12px;border-radius:6px;border:none;cursor:pointer;font-size:13px;text-decoration:none}}
      .ok{{background:#28a745;color:#fff}}.no{{background:#dc3545;color:#fff}}.edit{{background:#ffc107;color:#222}}
      input,select{{padding:6px 10px;border:1px solid #ccc;border-radius:6px;font-size:13.5px}}
      .mono{{font-family:Consolas,monospace;font-size:12.5px;color:#555}}
      .msg{{background:#f8f9fa;border-left:4px solid #2d3561;padding:10px 14px;margin:8px 0;border-radius:0 6px 6px 0;font-size:13.5px}}
      a{{color:#2d3561}}
    </style></head><body>{nav}<div class="wrap">{body}</div></body></html>"""


def esc(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------- 仪表盘 ----------
@app.route("/")
def dash():
    c = get_conn()
    stats = {}
    for t in ["persons", "accounts", "events", "messages", "graph_edges"]:
        stats[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    m_status = dict(c.execute("SELECT status, COUNT(*) FROM messages GROUP BY status").fetchall())
    plats = c.execute("SELECT platform, COUNT(*) FROM accounts GROUP BY platform ORDER BY 2 DESC").fetchall()
    scored = c.execute("SELECT COUNT(*) FROM persons WHERE score>0").fetchone()[0]
    high = c.execute("SELECT COUNT(*) FROM persons WHERE score>=80").fetchone()[0]
    replied = int(m_status.get('replied', 0))
    cards = f"""
    <div class="grid">
      <div class="stat"><b>{stats['persons']}</b><span>总人员</span></div>
      <div class="stat"><b>{scored}</b><span>已打分</span></div>
      <div class="stat"><b style="color:#28a745">{high}</b><span>≥80分高质量</span></div>
      <div class="stat"><b>{stats['events']}</b><span>行为事件</span></div>
      <div class="stat"><b>{stats['graph_edges']}</b><span>关系边</span></div>
      <div class="stat"><b style="color:#ff9800">{m_status.get('pending',0)}</b><span>待审消息</span></div>
      <div class="stat"><b style="color:{'#e74c3c' if replied else '#888'}">🔔 {replied}</b><span>未读回复</span></div>
    </div>
    <div class="card"><h3>平台账号分布</h3><table><tr><th>平台</th><th>账号数</th></tr>
    {''.join(f"<tr><td>{esc(p)}</td><td>{n}</td></tr>" for p, n in plats)}</table></div>
    <div class="card" style="margin-top:14px"><h3>高分人员 Top 10</h3><table><tr><th>姓名</th><th>分数</th><th>交易者</th><th>品种</th></tr>
    {''.join(f"<tr><td><a href='/person/{r['id']}'>{esc(r['canonical_name'])}</a></td><td>{r['score']}</td><td>{esc(r['is_trader'])}</td><td>{esc(r['asset_class'])}</td></tr>"
      for r in c.execute("SELECT id,canonical_name,score,is_trader,asset_class FROM persons WHERE score>0 ORDER BY score DESC LIMIT 10"))}
    </table></div>"""
    c.close()
    return html("仪表盘", cards, "dash")


# ---------- 人员库 ----------
@app.route("/persons")
def persons():
    c = get_conn()
    q = request.args.get("q", "").strip()
    score_min = request.args.get("score_min", "").strip()
    trader = request.args.get("trader", "").strip()
    platform = request.args.get("platform", "").strip()
    sort = request.args.get("sort", "score")
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    per_page = 50

    conds, params = [], []
    if q:
        conds.append("p.canonical_name LIKE ?")
        params.append(f"%{q}%")
    if score_min.isdigit():
        conds.append("p.score >= ?")
        params.append(int(score_min))
    if trader in ("true", "false", "unknown"):
        conds.append("p.is_trader = ?")
        params.append(trader)
    if platform:
        conds.append("p.id IN (SELECT person_id FROM accounts WHERE platform=?)")
        params.append(platform)
    where = " AND ".join(conds) if conds else "1=1"

    total = c.execute(f"SELECT COUNT(*) FROM persons p WHERE {where}", params).fetchone()[0]
    order_map = {
        "score": "p.score DESC, n_ev DESC, p.id",
        "events": "n_ev DESC, p.score DESC, p.id",
        "name": "p.canonical_name ASC",
        "id": "p.id DESC",
    }
    order = order_map.get(sort, order_map["score"])
    rows = c.execute(
        f"SELECT p.id, p.canonical_name, p.score, p.is_trader, p.asset_class, p.stage, "
        f"(SELECT COUNT(*) FROM accounts a WHERE a.person_id=p.id) n_acc, "
        f"(SELECT COUNT(*) FROM events e WHERE e.person_id=p.id) n_ev "
        f"FROM persons p WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [per_page, (page - 1) * per_page],
    ).fetchall()

    total_pages = max(1, (total + per_page - 1) // per_page)
    plats = [r["platform"] for r in c.execute("SELECT DISTINCT platform FROM accounts ORDER BY platform")]
    keep = {"q": q, "score_min": score_min, "trader": trader, "platform": platform, "sort": sort}
    def qs(**over):
        p = dict(keep)
        p.update(over)
        return "&".join(f"{k}={v}" for k, v in p.items() if v not in ("", None))
    sort_opts = {"score": "分数↓", "events": "事件数↓", "name": "名称↑", "id": "ID↓"}
    page_nav = f"""
    <div style="margin:12px 0;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
      <span>共 <b>{total}</b> 条 · 第 <b>{page}/{total_pages}</b> 页</span>
      {'<a class="btn edit" href="/persons?' + qs(page=page-1) + '">← 上一页</a>' if page > 1 else ''}
      {'<a class="btn edit" href="/persons?' + qs(page=page+1) + '">下一页 →</a>' if page < total_pages else ''}
      <select onchange="location='/persons?' + this.value">
        <option value="" disabled>跳页</option>
        {''.join(f"<option value='{qs(page=i)}'>第 {i} 页</option>" for i in range(1, min(total_pages, 200) + 1))}
      </select>
    </div>"""
    flt = f"""
    <form method="get" style="margin-bottom:10px" class="card">
      <input name="q" placeholder="搜索用户名" value="{esc(q)}">
      <input name="score_min" placeholder="最低分" value="{esc(score_min)}" style="width:70px">
      <select name="trader"><option value="">交易者(全部)</option>
        <option {'selected' if trader=='true' else ''} value="true">是</option>
        <option {'selected' if trader=='false' else ''} value="false">否</option>
        <option {'selected' if trader=='unknown' else ''} value="unknown">未知</option></select>
      <select name="platform"><option value="">平台(全部)</option>
        {''.join(f"<option {'selected' if platform==p else ''} value='{p}'>{p}</option>" for p in plats)}</select>
      <select name="sort">{''.join(f"<option {'selected' if sort==k else ''} value='{k}'>{v}</option>" for k, v in sort_opts.items())}</select>
      <button class="btn edit" type="submit">筛选</button>
    </form>"""
    tb = f"""<table><tr><th>ID</th><th>用户名</th><th>分数</th><th>交易者</th><th>品种</th><th>账号数</th><th>事件数</th><th>阶段</th></tr>
    {''.join(f"<tr><td>{r['id']}</td><td><a href='/person/{r['id']}'>{esc(r['canonical_name'])}</a></td>"
             f"<td>{r['score']}</td><td>{esc(r['is_trader'])}</td><td>{esc(r['asset_class'])}</td>"
             f"<td>{r['n_acc']}</td><td>{r['n_ev']}</td><td>{esc(r['stage'])}</td></tr>" for r in rows)}
    </table>"""
    export_btn = f"""<a class="btn edit" href="/export/persons?{qs()}">📥 导出当前筛选 CSV</a>"""
    c.close()
    return html("人员库", flt + page_nav + export_btn + tb + page_nav, "persons")


# ---------- 导出 CSV ----------
@app.route("/export/persons")
def export_persons():
    c = get_conn()
    q = request.args.get("q", "").strip()
    score_min = request.args.get("score_min", "").strip()
    trader = request.args.get("trader", "").strip()
    platform = request.args.get("platform", "").strip()
    conds, params = [], []
    if q:
        conds.append("p.canonical_name LIKE ?")
        params.append(f"%{q}%")
    if score_min.isdigit():
        conds.append("p.score >= ?")
        params.append(int(score_min))
    if trader in ("true", "false", "unknown"):
        conds.append("p.is_trader = ?")
        params.append(trader)
    if platform:
        conds.append("p.id IN (SELECT person_id FROM accounts WHERE platform=?)")
        params.append(platform)
    where = " AND ".join(conds) if conds else "1=1"
    rows = c.execute(
        f"SELECT p.id, p.canonical_name, p.score, p.is_trader, p.asset_class, p.stage, "
        f"(SELECT COUNT(*) FROM accounts a WHERE a.person_id=p.id) n_acc, "
        f"(SELECT COUNT(*) FROM events e WHERE e.person_id=p.id) n_ev, "
        f"COALESCE(p.notes,'') notes "
        f"FROM persons p WHERE {where} ORDER BY p.score DESC, n_ev DESC",
        params,
    ).fetchall()
    c.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ID", "用户名", "分数", "交易者", "品种", "阶段", "账号数", "事件数", "备注"])
    for r in rows:
        w.writerow([r["id"], r["canonical_name"], r["score"], r["is_trader"],
                    r["asset_class"], r["stage"], r["n_acc"], r["n_ev"], r["notes"]])
    data = "\ufeff" + buf.getvalue()  # BOM,Excel 中文不乱码
    return Response(data, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=persons.csv"})


@app.route("/export/messages")
def export_messages():
    c = get_conn()
    status = request.args.get("status", "pending")
    rows = c.execute(
        "SELECT m.id, p.canonical_name, p.score, p.asset_class, m.channel, m.content, m.rationale, m.status "
        "FROM messages m JOIN persons p ON p.id=m.person_id WHERE m.status=? ORDER BY p.score DESC",
        (status,),
    ).fetchall()
    c.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["消息ID", "人员", "分数", "品种", "渠道", "消息内容", "起草理由", "状态"])
    for r in rows:
        w.writerow([r["id"], r["canonical_name"], r["score"], r["asset_class"],
                    r["channel"], r["content"], r["rationale"], r["status"]])
    data = "\ufeff" + buf.getvalue()
    return Response(data, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=messages_{status}.csv"})


# ---------- 个人详情 ----------
@app.route("/person/<int:pid>")
def person(pid):
    c = get_conn()
    p = c.execute("SELECT * FROM persons WHERE id=?", (pid,)).fetchone()
    if not p:
        return html("未找到", "<p>人员不存在</p>")
    accs = c.execute("SELECT platform, username, followers, matched_by, raw FROM accounts WHERE person_id=?", (pid,)).fetchall()
    evs = c.execute("SELECT platform, type, content, url, like_score, published_at FROM events WHERE person_id=? ORDER BY id DESC LIMIT 30", (pid,)).fetchall()
    msgs = c.execute("SELECT id, channel, content, rationale, status, reply_content FROM messages WHERE person_id=?", (pid,)).fetchall()
    body = f"""
    <div class="card"><h2>{esc(p['canonical_name'])} <span style="color:#888;font-size:14px">id={pid}</span></h2>
    <p>分数 <b>{p['score']}</b> · 交易者 {esc(p['is_trader'])} · 品种 {esc(p['asset_class'])} · 阶段 {esc(p['stage'])}</p>
    <p class="mono">{esc(p['notes'])[:500]}</p></div>
    <div class="card" style="margin-top:14px"><h3>平台账号 ({len(accs)})</h3><table><tr><th>平台</th><th>用户名</th><th>粉丝</th><th>匹配来源</th></tr>
    {''.join(f"<tr><td>{esc(a['platform'])}</td><td>{esc(a['username'])}</td><td>{esc(a['followers'])}</td><td>{esc(a['matched_by'])}</td></tr>" for a in accs)}</table></div>
    <div class="card" style="margin-top:14px"><h3>行为事件 ({len(evs)})</h3>
    {''.join(f"<div class='msg'><b>{esc(e['platform'])}/{esc(e['type'])}</b> <span class='mono'>{esc(e['published_at'])}</span> 👍{e['like_score']}<br>{esc(e['content'])[:300]}</div>" for e in evs[:15])}</div>
    <div class="card" style="margin-top:14px"><h3>建联消息 ({len(msgs)})</h3>
    {''.join(f"<div class='msg'><b>[{m['id']}] {esc(m['status'])} / {esc(m['channel'])}</b><br>{esc(m['content'])[:400]}"
             + (f"<br><span style='color:#28a745'>↩ 回复: {esc(m['reply_content'])[:300]}</span>" if m['reply_content'] else '')
             + "</div>" for m in msgs)}</div>"""
    c.close()
    return html(p["canonical_name"], body)


# ---------- 审核队列 ----------
@app.route("/messages")
def messages():
    c = get_conn()
    status = request.args.get("status", "pending")
    rows = c.execute(
        "SELECT m.id, p.canonical_name, p.score, p.asset_class, m.channel, m.content, m.rationale, m.status, m.reply_content "
        "FROM messages m JOIN persons p ON p.id=m.person_id WHERE m.status=? ORDER BY p.score DESC",
        (status,),
    ).fetchall()
    counts = {s: c.execute("SELECT COUNT(*) FROM messages WHERE status=?", (s,)).fetchone()[0]
              for s in ["pending", "approved", "rejected", "sent", "replied", "read"]}
    nav_links = []
    for s in ["pending", "approved", "rejected", "sent", "replied", "read"]:
        cls = "edit" if status == s else "ok"
        nav_links.append(f"<a class='btn {cls}' href='/messages?status={s}' style='margin-right:8px'>{s}({counts[s]})</a>")
    nav = f"""<div class="card" style="margin-bottom:14px">{''.join(nav_links)}</div>"""
    auto_enabled = env_bool("AUTO_SEND_ENABLED")
    try:
        win_hours = int(read_env("AUTO_SEND_WINDOW_HOURS", "5"))
        win_max = int(read_env("AUTO_SEND_WINDOW_MAX", "0"))
    except ValueError:
        win_hours, win_max = 5, 0
    win_sent = c.execute(
        "SELECT COUNT(*) FROM messages WHERE status='sent' AND channel != 'mock' "
        "AND datetime(sent_at) >= datetime('now', ?)",
        (f"-{win_hours} hours",),
    ).fetchone()[0]
    quota = f"滑动{win_hours}小时窗口限 {win_max} 条" if win_max > 0 else "不限量"
    toggle_btn = ("<a class='btn no' href='/act/auto_toggle'>关闭自动发送</a>"
                  if auto_enabled else
                  "<a class='btn ok' href='/act/auto_toggle'>开启自动发送</a>")
    auto_card = f"""<div class="card" style="margin-bottom:14px">
      <b>{'🟢 自动发送已开启' if auto_enabled else '🔴 自动发送已关闭'}</b>
      <span style="color:#777;font-size:13px;margin-left:10px">每10分钟轮询 approved 队列 · {quota} · 近{win_hours}小时已发 <b>{win_sent}</b> · 连发无间隔</span>
      {toggle_btn}
    </div>"""
    banner = ""
    if request.args.get("auto") == "on":
        banner = (f"<div class='card' style='border-left:4px solid #28a745;margin-bottom:12px'>"
                  f"✅ 自动发送已开启(每10分钟轮询 approved 队列, 不限量连发)</div>")
    elif request.args.get("auto") == "off":
        banner = (f"<div class='card' style='border-left:4px solid #dc3545;margin-bottom:12px'>"
                  f"🔴 自动发送已关闭(手动通道不受影响)</div>")
    elif request.args.get("auto") == "err":
        banner = (f"<div class='card' style='border-left:4px solid #e67e22;margin-bottom:12px'>"
                  f"⚠️ 切换失败: <b>{esc(request.args.get('err',''))}</b></div>")
    if request.args.get("result") == "ok":
        banner = (f"<div class='card' style='border-left:4px solid #28a745;margin-bottom:12px'>"
                  f"✅ 消息 #{esc(request.args.get('mid',''))} 已通过并发送成功</div>")
    elif request.args.get("result") == "fail":
        banner = (f"<div class='card' style='border-left:4px solid #dc3545;margin-bottom:12px'>"
                  f"❌ 消息 #{esc(request.args.get('mid',''))} 已通过审核, 但发送失败: <b>{esc(request.args.get('err',''))}</b>"
                  f"<br><span style='color:#777;font-size:12.5px'>消息保持 approved, 自动通道会重试</span></div>")
    trs = []
    for r in rows:
        if r["status"] == "pending":
            btns = (f"<a class='btn ok' href='/act/approve/{r['id']}'>通过</a> "
                    f"<a class='btn edit' href='/act/approve_send/{r['id']}'>通过并发送</a> "
                    f"<a class='btn no' href='/act/reject/{r['id']}'>拒绝</a>")
        elif r["status"] == "replied":
            btns = (f"<a class='btn edit' href='/act/read/{r['id']}'>标为已读</a>")
        else:
            btns = esc(r["status"])
        reply_html = ""
        if r["reply_content"]:
            reply_html = (f"<div style='margin-top:6px;background:#eef9f0;padding:6px 8px;border-radius:6px'>"
                          f"<b style='color:#28a745;font-size:12px'>↩ 对方回复:</b><br>{esc(r['reply_content'])[:220]}</div>")
        trs.append(
            f"<tr><td>{r['id']}</td><td>{esc(r['canonical_name'])}</td>"
            f"<td>{r['score']}</td><td>{esc(r['asset_class'])}</td><td>{esc(r['channel'])}</td>"
            f"<td style='max-width:520px'><div class='mono' style='white-space:pre-wrap'>{esc(r['content'])[:300]}</div>"
            f"<div style='color:#888;font-size:12px'>📌 {esc(r['rationale'])[:120]}</div>{reply_html}</td><td>{btns}</td></tr>"
        )
    tb = nav + "<table><tr><th>ID</th><th>人员</th><th>分</th><th>品种</th><th>渠道</th><th>消息/理由/回复</th><th>操作</th></tr>" + "".join(trs) + "</table>"
    export_btn = f"""<a class="btn edit" href="/export/messages?status={status}" style="margin-bottom:10px;display:inline-block">📥 导出{status}队列 CSV</a>"""
    c.close()
    return html("审核队列", auto_card + banner + export_btn + tb, "messages")


@app.route("/act/approve_send/<int:mid>")
def act_approve_send(mid):
    """通过并发送: 先置 approved(过审核门禁), 再调 sender.py 立即发送。"""
    c = get_conn()
    m = c.execute("SELECT * FROM messages WHERE id=? AND status='pending'", (mid,)).fetchone()
    if not m:
        c.close()
        return redirect("/messages?status=pending&result=fail&mid=" + str(mid)
                        + "&err=" + quote("消息不存在或已被处理"))
    c.execute("UPDATE messages SET status='approved' WHERE id=?", (mid,))
    c.commit()
    log_op("web.approve_send", f"message_id={mid}", "approved")
    ch = m["channel"] or "mock"
    try:
        r = subprocess.run(
            ["/opt/leads/venv/bin/python", "/opt/leads/sender.py", "send", str(mid), "--channel", ch],
            capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        c.close()
        return redirect("/messages?status=pending&result=fail&mid=" + str(mid) + "&err=" + quote("发送超时"))
    c.close()
    if r.returncode == 0:
        return redirect("/messages?status=pending&result=ok&mid=" + str(mid))
    detail = (r.stdout or r.stderr or "").strip()
    msg = detail.replace("\n", " | ")[-300:] if detail else f"exit={r.returncode}"
    return redirect("/messages?status=pending&result=fail&mid=" + str(mid) + "&err=" + quote(msg))


@app.route("/act/auto_toggle")
def act_auto_toggle():
    """切换自动发送开关(写 .env 的 AUTO_SEND_ENABLED), 即时生效无需重启。"""
    try:
        cur = env_bool("AUTO_SEND_ENABLED")
        new = "false" if cur else "true"
        lines = []
        with ENV_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("AUTO_SEND_ENABLED="):
                    lines.append(f"AUTO_SEND_ENABLED={new}\n")
                else:
                    lines.append(line)
        with ENV_FILE.open("w", encoding="utf-8") as f:
            f.writelines(lines)
        log_op("web.auto_toggle", f"AUTO_SEND_ENABLED={new}", "ok")
        return redirect(f"/messages?status=pending&auto={'on' if new == 'true' else 'off'}")
    except Exception as e:
        return redirect(f"/messages?status=pending&auto=err&err={quote(str(e))}")


@app.route("/act/<action>/<int:mid>")
def act(action, mid):
    c = get_conn()
    if action == "approve":
        c.execute("UPDATE messages SET status='approved' WHERE id=?", (mid,))
    elif action == "reject":
        c.execute("UPDATE messages SET status='rejected' WHERE id=?", (mid,))
    elif action == "read":
        c.execute("UPDATE messages SET status='read' WHERE id=? AND status='replied'", (mid,))
    c.commit()
    log_op(f"web.{action}", f"message_id={mid}", "ok")
    c.close()
    if action == "read":
        return redirect("/replies")
    return redirect("/messages?status=pending")


# ---------- 回复中心(未读) ----------
@app.route("/replies")
def replies():
    c = get_conn()
    rows = c.execute(
        "SELECT m.id, p.id person_id, p.canonical_name, p.score, p.asset_class, m.channel, "
        "m.content, m.reply_content, m.sent_at, m.status, m.created_at "
        "FROM messages m JOIN persons p ON p.id=m.person_id "
        "WHERE m.reply_content IS NOT NULL AND m.reply_content != '' "
        "ORDER BY (m.status='replied') DESC, m.id DESC LIMIT 200"
    ).fetchall()
    replied_n = c.execute("SELECT COUNT(*) FROM messages WHERE status='replied'").fetchone()[0]
    card = f"""<div class="card" style="margin-bottom:14px">
      🔔 <b>{replied_n}</b> 条未读回复 ·
      <a class="btn ok" href="/replies">全部</a> ·
      <a class="btn edit" href="/export/messages?status=replied">导出 CSV</a>
      <span style="color:#888;font-size:12.5px;margin-left:10px">点"标为已读"后从未读列表消失, 但保留在下方记录中</span>
    </div>"""
    trs = []
    for r in rows:
        unread = 'style="background:#fff5f5"' if r["status"] == "replied" else ""
        badge = '<span class="btn no" style="font-size:11.5px;padding:2px 8px">未读</span>' if r["status"] == "replied" else '<span class="btn ok" style="font-size:11.5px;padding:2px 8px">已读</span>'
        trs.append(
            f"<tr {unread}><td>{r['id']}</td><td><a href='/person/{r['person_id']}'>{esc(r['canonical_name'])}</a></td>"
            f"<td>{r['score']}</td><td>{esc(r['asset_class'])}</td><td>{esc(r['channel'])}</td>"
            f"<td style='max-width:420px'><div class='mono' style='white-space:pre-wrap;color:#2d3561'>{esc(r['content'])[:220]}</div>"
            f"<div style='margin-top:6px;color:#888;font-size:12px'>🕐 发送于 {esc(r['sent_at'])}</div></td>"
            f"<td style='max-width:420px;background:#eef9f0'><div style='white-space:pre-wrap'>{esc(r['reply_content'])[:400]}</div></td>"
            f"<td>{badge}"
            + (f" <a class='btn edit' href='/act/read/{r['id']}'>标为已读</a>" if r["status"] == "replied" else "")
            + "</td></tr>"
        )
    tb = "<table><tr><th>ID</th><th>人员</th><th>分</th><th>品种</th><th>渠道</th><th>我方消息</th><th>对方回复</th><th>状态/操作</th></tr>" + "".join(trs) + "</table>"
    if not rows:
        tb = "<div class='card'><p>暂无回复记录。消息发出去后, 对方回复会出现在这里。</p></div>"
    c.close()
    return html("回复中心", card + tb, "replies")


# ---------- WhatsApp 扫码/配对 ----------
@app.route("/wa-qr")
def wa_qr():
    import requests as _req
    try:
        st = _req.get("http://127.0.0.1:18791/status", timeout=5).json()
        connected = st.get("connected", False)
        phone = st.get("phone")
        pairing = st.get("pairing", False)
        code = st.get("pairingCode")
    except Exception:
        connected, phone, pairing, code = False, None, False, None
    if connected:
        body = f"""
        <div class="card"><h2>✅ WhatsApp 已连接</h2>
        <p>手机号: <b>{esc(phone)}</b></p>
        <p>此窗口可以关闭了。断线后重新打开本页会再出二维码/配对码。</p></div>"""
        return html("WhatsApp 已连接", body, "wa")
    if code:
        body = f"""
        <div class="card"><h2>📱 WhatsApp 手机号配对</h2>
        <p style="color:#c0392b"><b>配对码 {esc(code)} 有效</b>，本页会自动刷新新码，请尽快在手机上输入：</p>
        <ol>
          <li>手机打开 <b>WhatsApp</b>（需保持网络可访问 WhatsApp）</li>
          <li>菜单 → <b>已关联设备</b> → <b>关联设备</b></li>
          <li>选择 <b>使用手机号关联</b>（Link with phone number instead）</li>
          <li>输入手机号 <b>{esc(phone or '')}</b> → 输入下方配对码</li>
        </ol>
        <div style="text-align:center;margin:24px 0">
          <div style="font-size:44px;font-weight:800;letter-spacing:10px;font-family:Consolas,monospace;
                      background:#1a1a2e;color:#ffd166;display:inline-block;padding:22px 34px;border-radius:14px">
            {esc(code)}
          </div>
          <p style="margin-top:12px;color:#e67e22;font-size:14px">⏱ 码剩余有效期：<b id="countdown">10:00</b>（过期后本页自动刷新新码）</p>
        </div>
        <p style="color:#888;font-size:13px">输入后等几秒，本页会自动刷新为"已连接"状态。</p></div>
        <script>
          var seconds = 10 * 60;
          setInterval(function(){{
            seconds -= 1;
            if (seconds <= 0) {{ location.reload(); }}
            var m = Math.floor(seconds / 60), s = seconds % 60;
            document.getElementById('countdown').textContent = m + ':' + (s < 10 ? '0' : '') + s;
          }}, 1000);
          setInterval(function(){{
            fetch('/wa-qr.json').then(r => r.json()).then(j => {{
              if (j.connected) location.reload();
              if (j.code && j.code !== '{esc(code)}') location.reload();
            }});
          }}, 5000);
        </script>"""
        return html("WhatsApp 配对", body, "wa")
    # 无配对码: 显示输入手机号表单
    form = """
    <div class="card"><h2>📱 WhatsApp 手机号配对</h2>
    <p>扫码不便可用手机号配对。请在下方输入要关联的 <b>WhatsApp 手机号</b>（含国家码）：</p>
    <form method="post" action="/wa-pair" style="margin-top:12px">
      <input name="phone" placeholder="如 8613800000000" style="width:280px;font-size:16px">
      <button class="btn edit" type="submit" style="font-size:15px;padding:8px 20px">生成配对码</button>
    </form>
    <p style="color:#888;font-size:13px;margin-top:14px">
      也可用扫码方式：<a href="/wa-qr.png" target="_blank">打开二维码图片</a>
      （手机 WhatsApp → 已关联设备 → 关联设备 → 扫码）</p></div>"""
    return html("WhatsApp 配对", form, "wa")


@app.route("/wa-pair", methods=["POST"])
def wa_pair():
    import requests as _req
    phone = (request.form.get("phone") or "").strip()
    if not phone:
        return redirect("/wa-qr")
    try:
        r = _req.post("http://127.0.0.1:18791/pair", json={"phone": phone}, timeout=30)
        j = r.json()
        if j.get("ok"):
            return redirect("/wa-qr")
        return html("WhatsApp 配对失败", f"<div class='card'><p style='color:#c0392b'>{esc(j.get('error',''))}</p><p><a href='/wa-qr'>返回重试</a></p></div>", "wa")
    except Exception as e:
        return html("WhatsApp 配对失败", f"<div class='card'><p style='color:#c0392b'>{esc(str(e))}</p><p><a href='/wa-qr'>返回重试</a></p></div>", "wa")


@app.route("/wa-qr.png")
def wa_qr_png():
    import requests as _req
    r = _req.get("http://127.0.0.1:18791/qr.png", timeout=10)
    if r.status_code != 200:
        return ("QR 尚未生成, 稍后刷新重试", 404)
    return Response(r.content, mimetype="image/png")


@app.route("/wa-qr.json")
def wa_qr_json():
    import requests as _req
    try:
        st = _req.get("http://127.0.0.1:18791/status", timeout=5).json()
        return Response(str({"connected": st.get("connected", False),
                             "phone": st.get("phone"),
                             "code": st.get("pairingCode")}).replace("'", '"'),
                        mimetype="application/json")
    except Exception:
        return Response('{"connected": false, "phone": null}', mimetype="application/json")


# ---------- 融合 ----------
@app.route("/fusion")
def fusion():
    c = get_conn()
    rows = c.execute(
        "SELECT p.id, p.canonical_name, COUNT(a.id) n FROM persons p "
        "JOIN accounts a ON a.person_id=p.id GROUP BY p.id HAVING n>=2 ORDER BY n DESC"
    ).fetchall()
    tb = "<table><tr><th>ID</th><th>姓名</th><th>账号数</th><th>构成</th><th>分数</th></tr>"
    for r in rows:
        accs = c.execute("SELECT platform, username FROM accounts WHERE person_id=?", (r["id"],)).fetchall()
        score = c.execute("SELECT score FROM persons WHERE id=?", (r["id"],)).fetchone()[0]
        acc_str = " · ".join(a["platform"] + ":" + esc(a["username"]) for a in accs)
        tb += (f"<tr><td>{r['id']}</td><td><a href='/person/{r['id']}'>{esc(r['canonical_name'])}</a></td>"
               f"<td>{r['n']}</td><td>{acc_str}</td>"
               f"<td>{score}</td></tr>")
    tb += "</table>"
    pend = c.execute("SELECT id, note FROM pending_fusions WHERE status='pending'").fetchall()
    extra = ""
    if pend:
        pend_html = "".join("<p>[" + str(p["id"]) + "] " + esc(p["note"]) + "</p>" for p in pend)
        extra = ("<div class='card' style='margin-top:14px'><h3>待确认合并 (" + str(len(pend)) + ")</h3>"
                 + pend_html + "</div>")
    c.close()
    return html("融合报告", tb + extra, "fusion")


# ---------- 操作日志 ----------
@app.route("/ops")
def ops():
    c = get_conn()
    rows = c.execute("SELECT id, ts, operator, action, params, result FROM ops_log ORDER BY id DESC LIMIT 100").fetchall()
    tb = f"""<table><tr><th>ID</th><th>时间</th><th>操作者</th><th>动作</th><th>参数</th><th>结果</th></tr>
    {''.join(f"<tr><td>{r['id']}</td><td class='mono'>{esc(r['ts'])}</td><td>{esc(r['operator'])}</td>"
             f"<td>{esc(r['action'])}</td><td class='mono'>{esc(r['params'])[:80]}</td><td>{esc(r['result'])[:60]}</td></tr>" for r in rows)}
    </table>"""
    c.close()
    return html("操作日志", tb, "ops")


if __name__ == "__main__":
    port = int(os.getenv("WEB_PORT", "8080"))
    host = os.getenv("WEB_HOST", "127.0.0.1")
    app.run(host=host, port=port)
