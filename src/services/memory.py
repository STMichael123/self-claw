"""混合记忆服务（文件 + 向量） — 对应 SPEC FR-005。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any

import structlog

logger = structlog.get_logger()

DOCUMENT_TIERS = {"principle", "long_term"}
MEMORY_SCOPES = DOCUMENT_TIERS | {"short_term"}
FORMAT_SUFFIX = {"markdown": ".md", "json": ".json"}


class MemoryService:
    """文件型 + 向量型混合记忆。"""

    def __init__(
        self,
        *,
        data_dir: str = "data/memory",
        db: sqlite3.Connection | None = None,
        vector_store: Any | None = None,  # ChromaDB collection（可选）
    ) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db = db
        self._vector_store = vector_store
        for scope in MEMORY_SCOPES:
            (self._data_dir / scope).mkdir(parents=True, exist_ok=True)

    # ── 文件型记忆 ──────────────────────────────────────

    def save_principle(
        self,
        key: str,
        content: str,
        *,
        title: str = "",
        format: str = "markdown",
        source_type: str = "manual",
        source_ref: str | None = None,
    ) -> Path:
        """保存全局 Principle 记忆。"""

        return self._save_document(
            tier="principle",
            key=key,
            title=title,
            content=content,
            format=format,
            source_type=source_type,
            source_ref=source_ref,
        )

    def save_short_term(self, session_id: str, content: str) -> Path:
        """保存短期会话日志（按日归档的 Markdown 文件）。"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dir_path = self._data_dir / "short_term" / today
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path = dir_path / f"{session_id}.md"
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(f"\n---\n{datetime.now(timezone.utc).isoformat()}\n{content}\n")
        self._upsert_memory_index(
            scope="short_term",
            session_id=session_id,
            ref_path=file_path,
            summary=self._build_summary(content),
        )
        return file_path

    def save_long_term(
        self,
        key: str,
        content: str,
        *,
        title: str = "",
        format: str = "markdown",
        source_type: str = "manual",
        source_ref: str | None = None,
    ) -> Path:
        """保存长期记忆（可编辑的 Markdown 文件）。"""
        return self._save_document(
            tier="long_term",
            key=key,
            title=title,
            content=content,
            format=format,
            source_type=source_type,
            source_ref=source_ref,
        )

    def search_files(
        self,
        query: str,
        *,
        scope: str = "long_term",
        session_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """基础全文检索。"""

        query_lower = query.lower()
        results: list[dict[str, Any]] = []
        for target_scope in self._normalize_scopes(scope):
            for fp in self._iter_scope_files(target_scope, session_id=session_id):
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if query_lower not in text.lower():
                    continue
                results.append(
                    {
                        "scope": target_scope,
                        "path": str(fp),
                        "snippet": self._build_snippet(text, query_lower),
                        "session_id": fp.stem if target_scope == "short_term" else None,
                        "updated_at": fp.stat().st_mtime,
                    }
                )
        results.sort(key=lambda item: float(item.get("updated_at") or 0.0), reverse=True)
        trimmed = results[:limit] if limit > 0 else results
        for item in trimmed:
            item.pop("updated_at", None)
        return trimmed

    # ── 向量记忆 ────────────────────────────────────────

    async def save_vector(self, text: str, *, source_type: str, source_id: str, metadata: dict[str, Any] | None = None) -> bool:
        """将文本写入向量数据库。失败时降级（不阻断主流程）。"""
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        saved_to_db = self._persist_vector_record(
            text=text,
            content_hash=content_hash,
            source_type=source_type,
            source_id=source_id,
        )

        if self._vector_store is None:
            logger.debug("vector_store_not_configured")
            return saved_to_db

        try:
            doc_id = self._build_vector_record_id(source_type=source_type, source_id=source_id, content_hash=content_hash)
            payload = {
                "documents": [text],
                "ids": [doc_id],
                "metadatas": [{"source_type": source_type, "source_id": source_id, **(metadata or {})}],
            }
            upsert = getattr(self._vector_store, "upsert", None)
            if callable(upsert):
                upsert(**payload)
            else:
                self._vector_store.add(**payload)
            return True
        except Exception as exc:
            logger.error("vector_write_failed", error=str(exc))
            return saved_to_db

    async def search_vector(
        self,
        query: str,
        *,
        top_k: int = 5,
        source_types: list[str] | None = None,
        source_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """语义相似度检索。"""
        if self._vector_store is None:
            return self._search_vector_fallback(query, top_k=top_k, source_types=source_types, source_id=source_id)
        try:
            results = self._vector_store.query(query_texts=[query], n_results=top_k)
            items = []
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            dists = results.get("distances", [[]])[0]
            for doc, meta, dist in zip(docs, metas, dists):
                items.append({"text": doc, "metadata": meta, "distance": dist})
            filtered = self._filter_vector_hits(items, source_types=source_types, source_id=source_id)
            return filtered[:top_k] or self._search_vector_fallback(query, top_k=top_k, source_types=source_types, source_id=source_id)
        except Exception as exc:
            logger.error("vector_search_failed", error=str(exc))
            return self._search_vector_fallback(query, top_k=top_k, source_types=source_types, source_id=source_id)

    def _save_document(
        self,
        *,
        tier: str,
        key: str,
        title: str,
        content: str,
        format: str,
        source_type: str,
        source_ref: str | None,
    ) -> Path:
        normalized_tier = self._normalize_document_tier(tier)
        suffix = self._normalize_format(format)
        file_path = self._data_dir / normalized_tier / f"{key}{suffix}"
        file_path.write_text(content, encoding="utf-8")
        self._upsert_memory_document(
            tier=normalized_tier,
            key=key,
            title=title,
            content=content,
            format=format,
            source_type=source_type,
            source_ref=source_ref,
        )
        self._upsert_memory_index(
            scope=normalized_tier,
            session_id=None,
            ref_path=file_path,
            summary=self._build_summary(content),
        )
        self._index_document_vector(tier=normalized_tier, key=key, content=content)
        return file_path

    def _normalize_scopes(self, scope: str) -> list[str]:
        normalized = (scope or "long_term").strip().lower()
        if normalized == "all":
            return ["principle", "short_term", "long_term"]
        if normalized not in MEMORY_SCOPES:
            raise ValueError(f"unsupported memory scope: {scope}")
        return [normalized]

    def _normalize_document_tier(self, tier: str) -> str:
        normalized = (tier or "").strip().lower()
        if normalized not in DOCUMENT_TIERS:
            raise ValueError(f"unsupported document tier: {tier}")
        return normalized

    def _normalize_format(self, format: str) -> str:
        normalized = (format or "markdown").strip().lower()
        if normalized not in FORMAT_SUFFIX:
            raise ValueError(f"unsupported memory format: {format}")
        return FORMAT_SUFFIX[normalized]

    def _iter_scope_files(self, scope: str, *, session_id: str | None) -> list[Path]:
        dir_path = self._data_dir / scope
        if not dir_path.exists():
            return []
        files = [path for path in dir_path.rglob("*") if path.is_file()]
        if scope != "short_term" or not session_id:
            return files
        return [path for path in files if path.stem == session_id]

    def _upsert_memory_document(
        self,
        *,
        tier: str,
        key: str,
        title: str,
        content: str,
        format: str,
        source_type: str,
        source_ref: str | None,
    ) -> None:
        if self._db is None:
            return
        now = self._utcnow()
        existing = self._db.execute(
            "SELECT created_at FROM memory_documents WHERE tier = ? AND key = ?",
            (tier, key),
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        self._db.execute(
            """
            INSERT INTO memory_documents (id, tier, key, title, content, format, version, source_type, source_ref, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tier, key) DO UPDATE SET
                title = excluded.title,
                content = excluded.content,
                format = excluded.format,
                source_type = excluded.source_type,
                source_ref = excluded.source_ref,
                updated_at = excluded.updated_at
            """,
            (
                self._build_memory_index_id(scope=tier, session_id=None, ref_path=f"{tier}:{key}"),
                tier,
                key,
                title,
                content,
                format,
                "v1",
                source_type,
                source_ref,
                created_at,
                now,
            ),
        )
        self._db.commit()

    def _upsert_memory_index(self, *, scope: str, session_id: str | None, ref_path: Path, summary: str) -> None:
        if self._db is None:
            return
        self._db.execute(
            """
            INSERT OR REPLACE INTO memory_index (id, scope, session_id, ref_path, summary, embedding_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._build_memory_index_id(scope=scope, session_id=session_id, ref_path=str(ref_path)),
                scope,
                session_id,
                str(ref_path),
                summary,
                None,
                self._utcnow(),
            ),
        )
        self._db.commit()

    def _persist_vector_record(self, *, text: str, content_hash: str, source_type: str, source_id: str) -> bool:
        if self._db is None:
            return False
        record_id = self._build_vector_record_id(source_type=source_type, source_id=source_id, content_hash=content_hash)
        self._db.execute(
            """
            INSERT OR REPLACE INTO vector_records (id, content_hash, text_chunk, embedding, source_type, source_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (record_id, content_hash, text, None, source_type, source_id, self._utcnow()),
        )
        self._db.commit()
        return True

    def _search_vector_fallback(
        self,
        query: str,
        *,
        top_k: int,
        source_types: list[str] | None,
        source_id: str | None,
    ) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        conditions = ["lower(text_chunk) LIKE ?"]
        params: list[Any] = [f"%{query.lower()}%"]
        if source_types:
            placeholders = ", ".join("?" for _ in source_types)
            conditions.append(f"source_type IN ({placeholders})")
            params.extend(source_types)
        if source_id:
            conditions.append("source_id = ?")
            params.append(source_id)
        params.append(top_k)
        rows = self._db.execute(
            """
            SELECT text_chunk, source_type, source_id, created_at
            FROM vector_records
            WHERE """ + " AND ".join(conditions) + """
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [
            {
                "text": row["text_chunk"],
                "metadata": {"source_type": row["source_type"], "source_id": row["source_id"]},
                "distance": None,
            }
            for row in rows
        ]

    def _build_summary(self, text: str, *, limit: int = 200) -> str:
        collapsed = " ".join(text.split())
        return collapsed[:limit]

    def _build_snippet(self, text: str, query_lower: str, *, limit: int = 500) -> str:
        lowered = text.lower()
        index = lowered.find(query_lower)
        if index < 0:
            return text[:limit]
        start = max(index - 80, 0)
        end = min(index + len(query_lower) + 160, len(text))
        return text[start:end][:limit]

    def _build_memory_index_id(self, *, scope: str, session_id: str | None, ref_path: str) -> str:
        payload = f"{scope}:{session_id or '-'}:{ref_path}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _build_vector_record_id(self, *, source_type: str, source_id: str, content_hash: str) -> str:
        payload = f"{source_type}:{source_id}:{content_hash}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _utcnow(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _filter_vector_hits(
        self,
        items: list[dict[str, Any]],
        *,
        source_types: list[str] | None,
        source_id: str | None,
    ) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        allowed_types = set(source_types or [])
        for item in items:
            metadata = item.get("metadata") or {}
            item_source_type = str(metadata.get("source_type") or "")
            item_source_id = str(metadata.get("session_id") or metadata.get("source_id") or "")
            if allowed_types and item_source_type not in allowed_types:
                continue
            if source_id and item_source_id != source_id:
                continue
            filtered.append(item)
        return filtered

    def _index_document_vector(self, *, tier: str, key: str, content: str) -> None:
        source_type = "principle_memory" if tier == "principle" else "long_term_memory"
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self._persist_vector_record(
            text=content,
            content_hash=content_hash,
            source_type=source_type,
            source_id=key,
        )
        if self._vector_store is None:
            return
        try:
            doc_id = self._build_vector_record_id(source_type=source_type, source_id=key, content_hash=content_hash)
            payload = {
                "documents": [content],
                "ids": [doc_id],
                "metadatas": [{"source_type": source_type, "source_id": key, "tier": tier}],
            }
            upsert = getattr(self._vector_store, "upsert", None)
            if callable(upsert):
                upsert(**payload)
            else:
                self._vector_store.add(**payload)
        except Exception as exc:
            logger.error("document_vector_index_failed", error=str(exc), tier=tier, key=key)
