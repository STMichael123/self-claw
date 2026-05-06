"""Agent 记忆注入与隔离测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.services.agent_service import AgentService
from src.services.memory import MemoryService
from src.storage.database import get_connection


@pytest.fixture
def db():
    conn = get_connection(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def memory_service(tmp_path, db) -> MemoryService:
    return MemoryService(
        data_dir=str(tmp_path / "memory"),
        principle_file=str(tmp_path / "principle.md"),
        long_term_dir=str(tmp_path / "long-term"),
        db=db,
    )


@pytest.fixture
def agent_service(db, memory_service: MemoryService) -> AgentService:
    return AgentService(db, memory_service=memory_service)


class TestAgentServiceMemory:
    @pytest.mark.asyncio
    async def test_memory_context_injects_long_term_index_and_session_bound_short_term(
        self,
        agent_service: AgentService,
        memory_service: MemoryService,
    ) -> None:
        memory_service.save_principle("始终遵守当前会话隔离原则", operator="test")
        memory_service.save_long_term("risk-policy", "# 风控策略\n\n交易限额为 100 万", title="风控策略")
        memory_service.save_short_term("session-a", "alpha 只属于 session-a")
        memory_service.save_short_term("session-b", "alpha 只属于 session-b")

        context = await agent_service._build_memory_context(
            session_id="session-a",
            user_message="alpha",
            base_snapshot="摘要 A",
        )

        assert "风控策略" in context["long_term_context"]
        assert "摘要 A" in context["short_term_context"]
        assert "alpha 只属于 session-a" in context["short_term_context"]
        assert "alpha 只属于 session-b" not in context["short_term_context"]
        assert "alpha 只属于 session-a" not in context["long_term_context"]

    def test_principle_loaded_as_full_text(self, agent_service: AgentService, memory_service: MemoryService) -> None:
        memory_service.save_principle("我是全局约束文档", operator="test")
        text = memory_service.load_principle()
        assert "我是全局约束文档" in text

    @pytest.mark.asyncio
    async def test_memory_search_can_limit_short_term_and_vectors_by_session(
        self,
        agent_service: AgentService,
        memory_service: MemoryService,
    ) -> None:
        memory_service.save_short_term("session-a", "keyword-one from a")
        memory_service.save_short_term("session-b", "keyword-one from b")
        await memory_service.save_vector("keyword-one vector a", source_type="session_message", source_id="session-a")
        await memory_service.save_vector("keyword-one vector b", source_type="session_message", source_id="session-b")

        payload = await agent_service.memory_search("keyword-one", session_id="session-a")

        assert any(item.get("session_id") == "session-a" for item in payload["files"] if item.get("scope") == "short_term")
        assert not any(item.get("session_id") == "session-b" for item in payload["files"] if item.get("scope") == "short_term")
        assert all(item["metadata"].get("source_id") == "session-a" for item in payload["vectors"])

    def test_status_entry_auto_archives_stale_sessions_and_persists_summary(
        self,
        agent_service: AgentService,
        memory_service: MemoryService,
        db,
    ) -> None:
        created = agent_service.create_session(user_id="archive-user", title="旧会话")
        session_id = created["session_id"]
        agent_service.sessions.add_message(session_id, "user", "这是需要归档的历史内容")

        stale_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        db.execute(
            "UPDATE sessions SET last_active_at = ?, status = ? WHERE id = ?",
            (stale_at, "active", session_id),
        )
        db.commit()

        entry = agent_service.status_entry(user_id="archive-user")

        assert all(item["session_id"] != session_id for item in entry)
        session = agent_service.sessions.get_session(session_id)
        assert session is not None
        assert session["status"] == "archived"
        assert "这是需要归档的历史内容" in memory_service.load_long_term(f"session-{session_id}")