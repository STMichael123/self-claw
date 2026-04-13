"""混合记忆服务（文件 + 向量） — 对应 SPEC FR-005。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


class MemoryService:
    """文件型 + 向量型混合记忆。"""

    def __init__(
        self,
        *,
        data_dir: str = "data/memory",
        vector_store: Any | None = None,  # ChromaDB collection（可选）
    ) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._vector_store = vector_store

    # ── 文件型记忆 ──────────────────────────────────────

    def save_short_term(self, session_id: str, content: str) -> Path:
        """保存短期会话日志（按日归档的 Markdown 文件）。"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dir_path = self._data_dir / "short_term" / today
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path = dir_path / f"{session_id}.md"
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(f"\n---\n{datetime.now(timezone.utc).isoformat()}\n{content}\n")
        return file_path

    def save_long_term(self, key: str, content: str) -> Path:
        """保存长期记忆（可编辑的 Markdown 文件）。"""
        dir_path = self._data_dir / "long_term"
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path = dir_path / f"{key}.md"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return file_path

    def search_files(self, query: str, *, scope: str = "long_term") -> list[dict[str, Any]]:
        """基础全文检索。"""
        dir_path = self._data_dir / scope
        if not dir_path.exists():
            return []
        results = []
        query_lower = query.lower()
        for fp in dir_path.rglob("*.md"):
            text = fp.read_text(encoding="utf-8")
            if query_lower in text.lower():
                results.append({"path": str(fp), "snippet": text[:500]})
        return results

    # ── 向量记忆 ────────────────────────────────────────

    async def save_vector(self, text: str, *, source_type: str, source_id: str, metadata: dict[str, Any] | None = None) -> bool:
        """将文本写入向量数据库。失败时降级（不阻断主流程）。"""
        if self._vector_store is None:
            logger.debug("vector_store_not_configured")
            return False
        try:
            import hashlib

            doc_id = hashlib.sha256(text.encode()).hexdigest()[:16]
            self._vector_store.add(
                documents=[text],
                ids=[doc_id],
                metadatas=[{"source_type": source_type, "source_id": source_id, **(metadata or {})}],
            )
            return True
        except Exception as exc:
            logger.error("vector_write_failed", error=str(exc))
            return False

    async def search_vector(self, query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
        """语义相似度检索。"""
        if self._vector_store is None:
            return []
        try:
            results = self._vector_store.query(query_texts=[query], n_results=top_k)
            items = []
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            dists = results.get("distances", [[]])[0]
            for doc, meta, dist in zip(docs, metas, dists):
                items.append({"text": doc, "metadata": meta, "distance": dist})
            return items
        except Exception as exc:
            logger.error("vector_search_failed", error=str(exc))
            return []
