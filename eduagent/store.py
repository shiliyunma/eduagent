# -*- coding: utf-8 -*-
"""持久化层：会话 / 消息 / 学情画像 / 全文检索。

为什么用 SQLite 而不是 JSONL（论文"技术选型"一节可以直接用这段）：
1. 会话要跨进程重启存活；
2. 要支持按关键词回查历史（FTS5）；
3. 论文的评测要能按 session 复现，还要能统计工具调用次数、步数、延迟 —— 结构化查询比翻日志快。
WAL 模式让"一边写轨迹一边读结果"不打架。
"""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    source      TEXT DEFAULT 'cli',
    created_at  REAL,
    updated_at  REAL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    role        TEXT NOT NULL,          -- user | assistant | tool | system
    content     TEXT,
    tool_calls  TEXT,                   -- JSON
    tool_call_id TEXT,
    name        TEXT,                   -- tool 名
    created_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);

CREATE TABLE IF NOT EXISTS weakness (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    topic       TEXT NOT NULL,
    evidence    TEXT,
    created_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_weakness_topic ON weakness(topic);

-- 轨迹表：纸面实验数据的唯一来源（每次模型调用 / 每次工具调用都落一行）
CREATE TABLE IF NOT EXISTS trace (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    turn        INTEGER,
    step        INTEGER,
    kind        TEXT,       -- llm | tool
    name        TEXT,
    latency_ms  REAL,
    payload     TEXT,       -- JSON
    created_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_trace_session ON trace(session_id, step);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
USING fts5(content, session_id UNINDEXED, content='messages', content_rowid='id');
"""


@contextmanager
def connect():
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def new_session(title: str = "", source: str = "cli") -> str:
    sid = "s_" + uuid.uuid4().hex[:12]
    now = time.time()
    with connect() as conn:
        conn.execute("INSERT INTO sessions(id,title,source,created_at,updated_at) VALUES(?,?,?,?,?)",
                     (sid, title[:60], source, now, now))
    return sid


def touch_session(session_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), session_id))


def next_seq(session_id: str) -> int:
    with connect() as conn:
        row = conn.execute("SELECT COALESCE(MAX(seq),0)+1 AS n FROM messages WHERE session_id=?",
                           (session_id,)).fetchone()
    return int(row["n"])


def append_message(session_id: str, msg: Dict[str, Any], seq: Optional[int] = None) -> int:
    """把一条内部消息落库。tool_calls 以 JSON 保存，回放时能重建。"""
    seq = seq if seq is not None else next_seq(session_id)
    tcs = msg.get("tool_calls")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO messages(session_id,seq,role,content,tool_calls,tool_call_id,name,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (session_id, seq, msg.get("role", ""), msg.get("content") or "",
             json.dumps(tcs, ensure_ascii=False) if tcs else None,
             msg.get("tool_call_id"), msg.get("name"), time.time()))
        rowid = cur.lastrowid
        if msg.get("content"):
            conn.execute("INSERT INTO messages_fts(rowid,content,session_id) VALUES(?,?,?)",
                         (rowid, msg["content"], session_id))
    return seq


def load_messages(session_id: str, limit: int = 500) -> List[Dict[str, Any]]:
    """按顺序还原成内部消息格式（含 tool_calls）。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY seq ASC LIMIT ?",
            (session_id, limit)).fetchall()
    out = []
    for r in rows:
        m: Dict[str, Any] = {"role": r["role"], "content": r["content"] or ""}
        if r["tool_calls"]:
            try:
                m["tool_calls"] = json.loads(r["tool_calls"])
            except json.JSONDecodeError:
                pass
        if r["tool_call_id"]:
            m["tool_call_id"] = r["tool_call_id"]
        if r["name"]:
            m["name"] = r["name"]
        out.append(m)
    return out


def search_messages(keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
    """FTS5 全文检索：学生问过的问题可以按关键词回查。"""
    with connect() as conn:
        try:
            rows = conn.execute(
                "SELECT session_id, content, created_at FROM messages_fts"
                " WHERE messages_fts MATCH ? LIMIT ?", (keyword, limit)).fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(
                "SELECT session_id, content, created_at FROM messages"
                " WHERE content LIKE ? LIMIT ?", ("%" + keyword + "%", limit)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 学情画像
def save_weakness(session_id: str, topic: str, evidence: str = "") -> None:
    with connect() as conn:
        conn.execute("INSERT INTO weakness(session_id,topic,evidence,created_at) VALUES(?,?,?,?)",
                     (session_id, topic, evidence, time.time()))


def list_weakness(session_id: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    q = "SELECT topic, evidence, created_at, COUNT(*) AS n FROM weakness"
    args: List[Any] = []
    if session_id:
        q += " WHERE session_id=?"
        args.append(session_id)
    q += " GROUP BY topic ORDER BY n DESC, created_at DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        rows = conn.execute(q, args).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 轨迹
def trace(session_id: str, turn: int, step: int, kind: str, name: str,
          latency_ms: float, payload: Any) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO trace(session_id,turn,step,kind,name,latency_ms,payload,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (session_id, turn, step, kind, name, latency_ms,
             json.dumps(payload, ensure_ascii=False)[:4000], time.time()))


def trace_stats(session_id: str) -> Dict[str, Any]:
    """论文实验要用的一行统计：步数、工具调用次数、模型调用次数、总耗时。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT kind, COUNT(*) n, SUM(latency_ms) ms FROM trace WHERE session_id=? GROUP BY kind",
            (session_id,)).fetchall()
    out = {"llm_calls": 0, "tool_calls": 0, "llm_ms": 0.0, "tool_ms": 0.0}
    for r in rows:
        if r["kind"] == "llm":
            out["llm_calls"] = r["n"]
            out["llm_ms"] = round(r["ms"] or 0.0, 1)
        else:
            out["tool_calls"] = r["n"]
            out["tool_ms"] = round(r["ms"] or 0.0, 1)
    out["total_ms"] = round(out["llm_ms"] + out["tool_ms"], 1)
    return out
