"""沙箱文件工作区测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.contracts.models import RunStatus
from src.services.file_workspace import FileWorkspaceError, FileWorkspaceService
from src.storage.database import get_connection


@pytest.fixture
def db():
    conn = get_connection(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def workspace_service(tmp_path, db) -> FileWorkspaceService:
    session_id = "session-a"
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO sessions (id, title, user_id, channel_type, status, created_at, last_active_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (session_id, "Session A", "user-a", "web", "active", now, now),
    )
    db.execute(
        """
        INSERT INTO agent_runs (id, parent_run_id, agent_role, skill_id, session_id, task_ref, context_ref, started_at, status, steps_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("run-a", None, "main", None, session_id, "test", "{}", now, RunStatus.RUNNING.value, 0),
    )
    db.execute(
        """
        INSERT INTO agent_runs (id, parent_run_id, agent_role, skill_id, session_id, task_ref, context_ref, started_at, status, steps_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("run-b", "run-a", "sub", None, session_id, "test", "{}", now, RunStatus.RUNNING.value, 0),
    )
    db.commit()
    return FileWorkspaceService(db, sandbox_root=str(tmp_path / "workspace"), lock_timeout_sec=30)


class TestFileWorkspaceService:
    def test_write_read_and_patch_file_records_audit(self, workspace_service: FileWorkspaceService, db) -> None:
        written = workspace_service.write_file("notes/todo.txt", "alpha beta", runtime_context={"run_id": "run-a"})
        read_back = workspace_service.read_file("notes/todo.txt", runtime_context={"run_id": "run-a"})
        patched = workspace_service.patch_file(
            "notes/todo.txt",
            "beta",
            "gamma",
            expected_checksum=written["checksum_after"],
            runtime_context={"run_id": "run-a"},
        )

        assert written["path"] == "notes/todo.txt"
        assert read_back["content"] == "alpha beta"
        assert patched["checksum_before"] == written["checksum_after"]

        operations = db.execute("SELECT operation_type, status FROM file_operations ORDER BY started_at ASC").fetchall()
        assert [(row["operation_type"], row["status"]) for row in operations] == [
            ("write", "success"),
            ("read", "success"),
            ("patch", "success"),
        ]

        audits = db.execute("SELECT action, entity_id FROM audit_logs ORDER BY created_at ASC").fetchall()
        assert [(row["action"], row["entity_id"]) for row in audits] == [
            ("write_file", "notes/todo.txt"),
            ("patch_file", "notes/todo.txt"),
        ]

    def test_write_rejects_path_outside_sandbox(self, workspace_service: FileWorkspaceService, db, tmp_path) -> None:
        outside = str(tmp_path / "outside.txt")

        with pytest.raises(FileWorkspaceError) as exc_info:
            workspace_service.write_file(outside, "boom", runtime_context={"run_id": "run-a"})

        assert exc_info.value.code == "FILE_SANDBOX_VIOLATION"
        row = db.execute("SELECT status FROM file_operations ORDER BY started_at DESC LIMIT 1").fetchone()
        assert row["status"] == "failed"

    def test_write_conflict_detected_from_existing_lock(self, workspace_service: FileWorkspaceService, db) -> None:
        now = datetime.now(timezone.utc)
        db.execute(
            "INSERT INTO file_locks (id, sandbox_path, lock_type, owner_run_id, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("lock-1", "conflict.txt", "write", "run-a", now.isoformat(), (now + timedelta(seconds=30)).isoformat()),
        )
        db.commit()

        with pytest.raises(FileWorkspaceError) as exc_info:
            workspace_service.write_file("conflict.txt", "new content", runtime_context={"run_id": "run-b"})

        assert exc_info.value.code == "FILE_WRITE_CONFLICT"
        row = db.execute("SELECT status FROM file_operations ORDER BY started_at DESC LIMIT 1").fetchone()
        assert row["status"] == "failed"