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
        assert "skills" in names
        assert "tool_calls" in names


class TestSessionManager:
    def test_create_session(self, session_mgr: SessionManager) -> None:
        sid = session_mgr.create_session("user1")
        assert sid
        session = session_mgr.get_session(sid)
        assert session is not None
        assert session["user_id"] == "user1"
        assert session["status"] == "active"

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
