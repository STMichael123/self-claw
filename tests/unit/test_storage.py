"""数据库与会话管理测试。"""

from __future__ import annotations

import pytest

from src.storage.database import get_connection
from src.sessions.manager import SessionManager


@pytest.fixture
def db():
    conn = get_connection(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def session_mgr(db) -> SessionManager:
    return SessionManager(db, timeout_min=1)


class TestDatabase:
    def test_connection_and_tables(self, db) -> None:
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in tables}
        assert "tasks" in names
        assert "sessions" in names
        assert "skills" not in names
        assert "skill_drafts" not in names
        assert "skill_revisions" not in names
        assert "skill_catalog_entries" in names
        assert "memory_documents" in names
        assert "tool_approvals" in names
        assert "file_locks" in names
        assert "file_operations" in names


class TestSessionManager:
    def test_create_session(self, session_mgr: SessionManager) -> None:
        sid = session_mgr.create_session("user1")
        assert sid
        session = session_mgr.get_session(sid)
        assert session is not None
        assert session["user_id"] == "user1"
        assert session["status"] == "active"
        assert session["title"] == "Agent 1"

    def test_get_or_create_returns_same(self, session_mgr: SessionManager) -> None:
        sid1 = session_mgr.get_or_create_session("user2")
        sid2 = session_mgr.get_or_create_session("user2")
        assert sid1 == sid2

    def test_close_session(self, session_mgr: SessionManager) -> None:
        sid = session_mgr.create_session("user3")
        session_mgr.close_session(sid, summary="test summary")
        session = session_mgr.get_session(sid)
        assert session["status"] == "archived"
        assert session["summary"] == "test summary"

    def test_list_sessions(self, session_mgr: SessionManager) -> None:
        session_mgr.create_session("u4")
        session_mgr.create_session("u5")
        result = session_mgr.list_sessions()
        assert len(result) >= 2

    def test_create_session_limits_active_sessions_to_five(self, session_mgr: SessionManager) -> None:
        for _ in range(5):
            session_mgr.create_session("cap-user")

        with pytest.raises(ValueError, match="最多只能保留 5 个顶层会话"):
            session_mgr.create_session("cap-user")

    def test_create_session_reuses_first_available_agent_slot(self, session_mgr: SessionManager) -> None:
        sid1 = session_mgr.create_session("slot-user")
        sid2 = session_mgr.create_session("slot-user")
        sid3 = session_mgr.create_session("slot-user")

        session_mgr.close_session(sid2, summary="cleanup")

        sid4 = session_mgr.create_session("slot-user")
        reused = session_mgr.get_session(sid4)

        assert session_mgr.get_session(sid1)["title"] == "Agent 1"
        assert session_mgr.get_session(sid3)["title"] == "Agent 3"
        assert reused is not None
        assert reused["title"] == "Agent 2"

    def test_add_message_updates_message_count(self, session_mgr: SessionManager) -> None:
        sid = session_mgr.create_session("u6", title="线程 A")
        session_mgr.add_message(sid, "user", "hello")
        session_mgr.add_message(sid, "assistant", "hi")
        session = session_mgr.get_session(sid)
        assert session is not None
        assert session["message_count"] == 2
