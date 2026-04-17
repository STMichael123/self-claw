"""Agent 记忆注入与隔离测试。"""

from __future__ import annotations

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
    return MemoryService(data_dir=str(tmp_path / "memory"), db=db)


@pytest.fixture
def agent_service(db, memory_service: MemoryService) -> AgentService:
    return AgentService(db, memory_service=memory_service)


class TestAgentServiceMemory:
    @pytest.mark.asyncio
    async def test_memory_context_uses_principle_and_session_bound_short_term(
        self,
        agent_service: AgentService,
        memory_service: MemoryService,
    ) -> None:
        memory_service.save_principle("identity", "始终遵守当前会话隔离原则")
        memory_service.save_short_term("session-a", "alpha 只属于 session-a")
        memory_service.save_short_term("session-b", "alpha 只属于 session-b")

        context = await agent_service._build_memory_context(
            session_id="session-a",
            user_message="alpha",
            base_snapshot="摘要 A",
        )

        assert "当前会话隔离原则" in context
        assert "alpha 只属于 session-a" in context
        assert "alpha 只属于 session-b" not in context

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