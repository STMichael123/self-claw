"""SQLite 数据库初始化与表定义 — 对应 SPEC 8。"""

from __future__ import annotations

import sqlite3
from pathlib import Path


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    prompt        TEXT,
    skill_id      TEXT,
    requested_skill_name TEXT,
    session_id    TEXT,
    schedule_type TEXT NOT NULL,
    schedule_expr TEXT NOT NULL,
    schedule_text TEXT,
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
    title             TEXT,
    user_id           TEXT NOT NULL,
    channel_type      TEXT NOT NULL DEFAULT 'web',
    status            TEXT NOT NULL DEFAULT 'active',
    current_run_id    TEXT,
    context_snapshot  TEXT,
    summary           TEXT,
    created_at        TEXT NOT NULL,
    last_active_at    TEXT NOT NULL,
    expired_at        TEXT
);

CREATE TABLE IF NOT EXISTS memory_documents (
    id           TEXT PRIMARY KEY,
    tier         TEXT NOT NULL,
    key          TEXT NOT NULL,
    title        TEXT,
    content      TEXT NOT NULL,
    format       TEXT NOT NULL DEFAULT 'markdown',
    version      TEXT NOT NULL DEFAULT 'v1',
    source_type  TEXT NOT NULL DEFAULT 'manual',
    source_ref   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS vector_records (
    id           TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    text_chunk   TEXT NOT NULL,
    embedding    TEXT,
    source_type  TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_catalog_entries (
    skill_name             TEXT PRIMARY KEY,
    description            TEXT NOT NULL,
    location               TEXT NOT NULL,
    compatibility          TEXT,
    status                 TEXT NOT NULL DEFAULT 'enabled',
    source                 TEXT NOT NULL DEFAULT 'project',
    content_hash           TEXT,
    discovered_at          TEXT NOT NULL,
    indexed_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id             TEXT PRIMARY KEY,
    parent_run_id  TEXT,
    agent_role     TEXT NOT NULL,
    skill_id       TEXT,
    activated_skills TEXT,
    session_id     TEXT,
    task_ref       TEXT,
    context_ref    TEXT,
    result_ref     TEXT,
    started_at     TEXT NOT NULL,
    ended_at       TEXT,
    status         TEXT NOT NULL DEFAULT 'queued',
    steps_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    role          TEXT NOT NULL,
    content       TEXT NOT NULL,
    run_id        TEXT,
    metadata      TEXT,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (run_id) REFERENCES agent_runs(id)
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

CREATE TABLE IF NOT EXISTS tool_approvals (
    id            TEXT PRIMARY KEY,
    agent_run_id  TEXT NOT NULL,
    session_id    TEXT,
    tool_name     TEXT NOT NULL,
    arguments     TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    operator      TEXT,
    resume_state  TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (agent_run_id) REFERENCES agent_runs(id),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS file_locks (
    id           TEXT PRIMARY KEY,
    sandbox_path TEXT NOT NULL,
    lock_type    TEXT NOT NULL,
    owner_run_id TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    FOREIGN KEY (owner_run_id) REFERENCES agent_runs(id)
);

CREATE TABLE IF NOT EXISTS file_operations (
    id              TEXT PRIMARY KEY,
    agent_run_id    TEXT NOT NULL,
    session_id      TEXT,
    operation_type  TEXT NOT NULL,
    sandbox_path    TEXT NOT NULL,
    status          TEXT NOT NULL,
    content_preview TEXT,
    checksum_before TEXT,
    checksum_after  TEXT,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    FOREIGN KEY (agent_run_id) REFERENCES agent_runs(id),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
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

CREATE INDEX IF NOT EXISTS idx_sessions_user_status ON sessions(user_id, status, last_active_at);
CREATE INDEX IF NOT EXISTS idx_agent_runs_session_role ON agent_runs(session_id, agent_role, started_at);
CREATE INDEX IF NOT EXISTS idx_messages_session_created ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_logs_session_created ON usage_logs(session_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_memory_documents_tier_key ON memory_documents(tier, key);
CREATE INDEX IF NOT EXISTS idx_memory_index_scope_session ON memory_index(scope, session_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_vector_records_source ON vector_records(source_type, source_id, created_at);
CREATE INDEX IF NOT EXISTS idx_skill_catalog_status_source ON skill_catalog_entries(status, source, indexed_at);
CREATE INDEX IF NOT EXISTS idx_tool_approvals_status_created ON tool_approvals(status, created_at);
CREATE INDEX IF NOT EXISTS idx_file_locks_path_expires ON file_locks(sandbox_path, expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_write_lock_path ON file_locks(sandbox_path)
WHERE lock_type = 'write';
CREATE INDEX IF NOT EXISTS idx_file_operations_run_started ON file_operations(agent_run_id, started_at);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_running_main_run_per_session
ON agent_runs(session_id)
WHERE agent_role = 'main' AND status = 'running';
"""


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """获取 SQLite 连接，自动建表。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _apply_migrations(conn)
    return conn


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """为旧库补齐后续版本新增字段。"""
    _ensure_column(conn, "tasks", "prompt", "prompt TEXT")
    _ensure_column(conn, "tasks", "session_id", "session_id TEXT")
    _ensure_column(conn, "tasks", "schedule_text", "schedule_text TEXT")
    _ensure_column(conn, "tasks", "requested_skill_name", "requested_skill_name TEXT")
    _ensure_column(conn, "sessions", "title", "title TEXT")
    _ensure_column(conn, "sessions", "current_run_id", "current_run_id TEXT")
    _ensure_column(conn, "agent_runs", "activated_skills", "activated_skills TEXT")
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
