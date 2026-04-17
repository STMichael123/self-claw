"""分层记忆服务测试。"""

from __future__ import annotations

import pytest

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


class TestMemoryService:
    def test_save_principle_and_long_term_create_files_and_documents(self, memory_service: MemoryService, db) -> None:
        principle_path = memory_service.save_principle("identity", "系统原则：先审批后写入")
        long_term_path = memory_service.save_long_term("sales-playbook", "标准销售话术 alpha-beta")

        assert principle_path.exists()
        assert long_term_path.exists()

        rows = db.execute("SELECT tier, key FROM memory_documents ORDER BY tier, key").fetchall()
        assert [(row["tier"], row["key"]) for row in rows] == [
            ("long_term", "sales-playbook"),
            ("principle", "identity"),
        ]

    def test_search_short_term_can_be_limited_by_session(self, memory_service: MemoryService) -> None:
        memory_service.save_short_term("session-a", "alpha only for a")
        memory_service.save_short_term("session-b", "alpha only for b")

        session_a_hits = memory_service.search_files("alpha", scope="short_term", session_id="session-a")
        all_hits = memory_service.search_files("alpha", scope="short_term")

        assert len(session_a_hits) == 1
        assert session_a_hits[0]["session_id"] == "session-a"
        assert len(all_hits) >= 2

    @pytest.mark.asyncio
    async def test_vector_search_falls_back_to_database_records(self, memory_service: MemoryService) -> None:
        saved = await memory_service.save_vector(
            "vector memory keyword alpha-beta-gamma",
            source_type="session_summary",
            source_id="session-a",
        )

        results = await memory_service.search_vector("alpha-beta-gamma")

        assert saved is True
        assert results
        assert results[0]["metadata"]["source_id"] == "session-a"