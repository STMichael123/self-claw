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
    return MemoryService(
        data_dir=str(tmp_path / "memory"),
        principle_file=str(tmp_path / "principle.md"),
        long_term_dir=str(tmp_path / "long-term"),
        db=db,
    )


class TestMemoryService:
    def test_save_principle_creates_file_and_index(self, memory_service: MemoryService, db) -> None:
        principle_path = memory_service.save_principle("系统原则：先审批后写入", operator="test")

        assert principle_path.exists()
        assert memory_service.load_principle() == "系统原则：先审批后写入"

        rows = db.execute("SELECT scope FROM memory_index WHERE scope = 'principle'").fetchall()
        assert len(rows) == 1

    def test_save_long_term_creates_file_and_index(self, memory_service: MemoryService, db) -> None:
        long_term_path = memory_service.save_long_term("sales-playbook", "标准销售话术 alpha-beta")

        assert long_term_path.exists()
        assert memory_service.load_long_term("sales-playbook") == "标准销售话术 alpha-beta"

        entries = memory_service.list_long_term()
        assert any(e["key"] == "sales-playbook" for e in entries)

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

    def test_sync_principle_index_detects_file_changes(self, memory_service: MemoryService, tmp_path) -> None:
        principle_file = tmp_path / "principle.md"
        principle_file.write_text("初始原则", encoding="utf-8")
        synced = memory_service.sync_principle_index()
        assert synced is True

        synced_again = memory_service.sync_principle_index()
        assert synced_again is False

    def test_sync_long_term_index(self, memory_service: MemoryService, tmp_path) -> None:
        lt_dir = tmp_path / "long-term"
        lt_dir.mkdir(parents=True, exist_ok=True)
        (lt_dir / "test-entry.md").write_text("测试条目内容", encoding="utf-8")
        synced = memory_service.sync_long_term_index()
        assert synced == 1
