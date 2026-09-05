#!/usr/bin/env python3
"""统一线索库 db.py — SQLite schema + 连接(版本化)。

分层: raw(归档文件) → normalized(本库) → derived(打分/关系,后续阶段)
"""
import sqlite3
from pathlib import Path

BASE = Path("/opt/leads")
DB_PATH = BASE / "data" / "leads.db"

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS persons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_name TEXT NOT NULL,
  score INTEGER DEFAULT 0,
  is_trader TEXT DEFAULT '',
  asset_class TEXT DEFAULT '',
  stage TEXT DEFAULT 'discovered',
  notes TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id INTEGER NOT NULL REFERENCES persons(id),
  platform TEXT NOT NULL,
  username TEXT NOT NULL,
  profile_url TEXT DEFAULT '',
  followers TEXT DEFAULT '',
  context TEXT DEFAULT '',
  matched_by TEXT DEFAULT 'direct',
  raw TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now')),
  UNIQUE(platform, username)
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id INTEGER NOT NULL REFERENCES persons(id),
  account_id INTEGER REFERENCES accounts(id),
  platform TEXT NOT NULL,
  type TEXT DEFAULT 'post',
  content TEXT DEFAULT '',
  url TEXT DEFAULT '',
  published_at TEXT DEFAULT '',
  like_score INTEGER DEFAULT 0,
  raw TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id INTEGER NOT NULL REFERENCES persons(id),
  channel TEXT DEFAULT 'x',
  content TEXT DEFAULT '',
  rationale TEXT DEFAULT '',
  status TEXT DEFAULT 'pending',
  sent_at TEXT,
  reply_content TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS seeds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  username TEXT NOT NULL,
  reason TEXT DEFAULT '',
  expanded INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS graph_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_person_id INTEGER REFERENCES persons(id),
  to_person_id INTEGER REFERENCES persons(id),
  relation TEXT NOT NULL,
  platform TEXT NOT NULL,
  raw TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ops_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT DEFAULT (datetime('now')),
  operator TEXT DEFAULT 'agent',
  action TEXT NOT NULL,
  params TEXT DEFAULT '',
  result TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_accounts_person ON accounts(person_id);
CREATE INDEX IF NOT EXISTS idx_accounts_platform ON accounts(platform, username);
CREATE INDEX IF NOT EXISTS idx_events_person ON events(person_id);
CREATE INDEX IF NOT EXISTS idx_messages_person ON messages(person_id);
"""


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA_V1)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', '1')")
    conn.commit()
    conn.close()


def log_op(action, params="", result="", operator="agent"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO ops_log(operator, action, params, result) VALUES(?,?,?,?)",
        (operator, action, params, result),
    )
    conn.commit()
    conn.close()


def get_or_create_person(conn, canonical_name):
    row = conn.execute(
        "SELECT id FROM persons WHERE canonical_name = ?", (canonical_name,)
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO persons(canonical_name) VALUES(?)", (canonical_name,)
    )
    return cur.lastrowid


def upsert_account(conn, person_id, platform, username, profile_url="", followers="",
                   context="", matched_by="direct", raw=""):
    row = conn.execute(
        "SELECT id FROM accounts WHERE platform = ? AND username = ?",
        (platform, username),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE accounts SET person_id=?, profile_url=?, followers=?, "
            "context=COALESCE(NULLIF(?, ''), context), matched_by=?, raw=COALESCE(NULLIF(?, ''), raw) "
            "WHERE id=?",
            (person_id, profile_url, followers, context, matched_by, raw, row["id"]),
        )
        return row["id"]
    cur = conn.execute(
        "INSERT INTO accounts(person_id, platform, username, profile_url, followers, context, matched_by, raw) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (person_id, platform, username, profile_url, followers, context, matched_by, raw),
    )
    return cur.lastrowid


def add_event(conn, person_id, account_id, platform, etype, content, url="",
              published_at="", like_score=0, raw="", dedup_key=""):
    """插入事件; dedup_key 非空时按 (platform, dedup_key) 去重, 重复则跳过并返回 False。"""
    if dedup_key:
        dup = conn.execute(
            "SELECT id FROM events WHERE platform=? AND dedup_key=? LIMIT 1",
            (platform, dedup_key),
        ).fetchone()
        if dup:
            return False
    conn.execute(
        "INSERT INTO events(person_id, account_id, platform, type, content, url, published_at, like_score, raw, dedup_key) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (person_id, account_id, platform, etype, content, url, published_at, like_score, raw, dedup_key),
    )
    return True


def record_reply(conn, mid):
    """回复落库统一入口: 更新 person 的回复统计。"""
    m = conn.execute(
        "SELECT person_id FROM messages WHERE id=?", (mid,)
    ).fetchone()
    if not m:
        return
    conn.execute(
        "UPDATE persons SET n_replies = COALESCE(n_replies, 0) + 1, "
        "last_reply_at = datetime('now') WHERE id=?",
        (m["person_id"],),
    )
