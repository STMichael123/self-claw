"""SQLite 数据库初始化与表定义 — 对应 SPEC 8。"""

from __future__ import annotations

import sqlite3
from pathlib import Path


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    skill_id      TEXT,
    schedule_type TEXT NOT NULL,
    schedule_expr TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    next_run_at   TEXT,
    last_run_at   TEXT,
    last_result   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_logs (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    ended_at       TEXT,
    status         TEXT NOT NULL,
    error_category TEXT,
    error_detail   TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS usage_logs (
    id              TEXT PRIMARY KEY,
    task_id         TEXT,
    session_id      TEXT,
    agent_run_id    TEXT,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    estimated_cost  REAL NOT NULL DEFAULT 0.0,
    model_name      TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    channel_type      TEXT NOT NULL DEFAULT 'web',
    status            TEXT NOT NULL DEFAULT 'active',
    context_snapshot  TEXT,
    summary           TEXT,
    created_at        TEXT NOT NULL,
    last_active_at    TEXT NOT NULL,
    expired_at        TEXT
);

CREATE TABLE IF NOT EXISTS memory_index (
    id           TEXT PRIMARY KEY,
    scope        TEXT NOT NULL,
    session_id   TEXT,
    ref_path     TEXT,
    summary      TEXT,
    embedding_id TEXT,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skills (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE,
    version        TEXT NOT NULL DEFAULT 'v1',
    status         TEXT NOT NULL DEFAULT 'enabled',
    sop_source     TEXT,
    skill_prompt   TEXT,
    allowed_tools  TEXT,
    input_schema   TEXT,
    output_schema  TEXT,
    examples       TEXT,
    max_steps      INTEGER,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_versions (
    id               TEXT PRIMARY KEY,
    skill_id         TEXT NOT NULL,
    version          TEXT NOT NULL,
    change_note      TEXT,
    content_snapshot TEXT,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (skill_id) REFERENCES skills(id)
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id             TEXT PRIMARY KEY,
    parent_run_id  TEXT,
    agent_role     TEXT NOT NULL,
    skill_id       TEXT,
    session_id     TEXT,
    task_ref       TEXT,
    context_ref    TEXT,
    result_ref     TEXT,
    started_at     TEXT NOT NULL,
    ended_at       TEXT,
    status         TEXT NOT NULL DEFAULT 'queued',
    steps_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id            TEXT PRIMARY KEY,
    agent_run_id  TEXT NOT NULL,
    tool_name     TEXT NOT NULL,
    parameters    TEXT,
    result        TEXT,
    duration_ms   INTEGER,
    status        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (agent_run_id) REFERENCES agent_runs(id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id           TEXT PRIMARY KEY,
    operator     TEXT NOT NULL,
    action       TEXT NOT NULL,
    entity_type  TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    version      TEXT,
    diff_summary TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_configs (
    id              TEXT PRIMARY KEY,
    channel_type    TEXT NOT NULL UNIQUE,
    display_name    TEXT,
    adapter_class   TEXT NOT NULL,
    credentials_ref TEXT,
    rate_limit      INTEGER,
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """获取 SQLite 连接，自动建表。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn
