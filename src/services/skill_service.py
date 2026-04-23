"""Skill catalog、启停管理与激活服务。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import uuid
from typing import Any

import structlog

from src.contracts.errors import ErrorCode
from src.skills.registry import SkillRegistry, SkillRegistryError

logger = structlog.get_logger()

VALID_SKILL_ACTIONS = {"enable", "disable"}


class SkillService:
    """基于文件系统和 SQLite 的 Skill 管理服务。"""

    def __init__(
        self,
        db: sqlite3.Connection,
        *,
        registry: SkillRegistry | None = None,
        skill_root: str | Path | None = None,
    ) -> None:
        self._db = db
        self._registry = registry or SkillRegistry(skill_root or ".agents/skills")

    @property
    def registry(self) -> SkillRegistry:
        return self._registry

    def reload_catalog(self) -> dict[str, Any]:
        disabled_names = {
            row["skill_name"]
            for row in self._db.execute(
                "SELECT skill_name FROM skill_catalog_entries WHERE status = 'disabled'"
            ).fetchall()
        }
        summary = self._registry.reload(disabled_names=disabled_names)
        seen_names: set[str] = set()

        for entry in self._registry.list_catalog():
            existing = self._db.execute(
                "SELECT status FROM skill_catalog_entries WHERE skill_name = ?",
                (entry.skill_name,),
            ).fetchone()
            status = existing["status"] if existing is not None else entry.status
            self._db.execute(
                """
                INSERT INTO skill_catalog_entries (
                    skill_name, description, location, compatibility, status, source, content_hash,
                    discovered_at, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(skill_name) DO UPDATE SET
                    description = excluded.description,
                    location = excluded.location,
                    compatibility = excluded.compatibility,
                    source = excluded.source,
                    content_hash = excluded.content_hash,
                    discovered_at = excluded.discovered_at,
                    indexed_at = excluded.indexed_at
                """,
                (
                    entry.skill_name,
                    entry.description,
                    entry.location,
                    entry.compatibility,
                    status,
                    entry.source,
                    entry.content_hash,
                    entry.discovered_at,
                    entry.indexed_at,
                ),
            )
            seen_names.add(entry.skill_name)

        stale_rows = self._db.execute(
            "SELECT skill_name FROM skill_catalog_entries WHERE source = 'project'"
        ).fetchall()
        for row in stale_rows:
            if row["skill_name"] in seen_names:
                continue
            self._db.execute(
                "DELETE FROM skill_catalog_entries WHERE skill_name = ? AND source = 'project'",
                (row["skill_name"],),
            )

        self._db.commit()
        return {
            **summary,
            "catalog_size": len(seen_names),
        }

    def list_catalog(
        self,
        *,
        status: str | None = None,
        source: str | None = None,
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        self._ensure_catalog_loaded()
        query = "SELECT * FROM skill_catalog_entries WHERE 1 = 1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if source:
            query += " AND source = ?"
            params.append(source)
        if keyword:
            query += " AND (skill_name LIKE ? OR description LIKE ?)"
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        query += " ORDER BY skill_name ASC"
        rows = self._db.execute(query, params).fetchall()
        return [self._row_to_catalog_entry(row) for row in rows]

    def get_catalog_entry(self, skill_name: str) -> dict[str, Any] | None:
        self._ensure_catalog_loaded()
        row = self._db.execute(
            "SELECT * FROM skill_catalog_entries WHERE skill_name = ?",
            (skill_name,),
        ).fetchone()
        if row is None:
            return None
        detail = self._registry.get_skill_detail(skill_name)
        detail.update(
            status=row["status"],
            source=row["source"],
            recent_audit=self.list_audit(skill_name=skill_name, limit=10),
        )
        return detail

    def activate_skill(self, skill_name: str, *, resource_paths: list[str] | None = None) -> dict[str, Any]:
        self._ensure_catalog_loaded()
        row = self._db.execute(
            "SELECT status FROM skill_catalog_entries WHERE skill_name = ?",
            (skill_name,),
        ).fetchone()
        if row is None:
            raise SkillRegistryError(ErrorCode.SKILL_NOT_FOUND, f"skill not found: {skill_name}")
        if row["status"] != "enabled":
            raise SkillRegistryError(ErrorCode.SKILL_DISABLED, f"skill disabled: {skill_name}")
        return self._registry.activate(skill_name, resource_paths=resource_paths).to_dict()

    def perform_action(
        self,
        skill_name: str,
        *,
        action: str,
        operator: str,
        reason: str = "",
    ) -> dict[str, Any] | None:
        if action not in VALID_SKILL_ACTIONS:
            raise ValueError("invalid skill action")
        self._ensure_catalog_loaded()

        status = "enabled" if action == "enable" else "disabled"
        cursor = self._db.execute(
            "UPDATE skill_catalog_entries SET status = ?, indexed_at = ? WHERE skill_name = ?",
            (status, _utcnow(), skill_name),
        )
        if cursor.rowcount == 0:
            return None
        self._audit(
            operator=operator,
            action=f"skill_{action}",
            entity_type="skill",
            entity_id=skill_name,
            version=None,
            diff_summary=reason or action,
        )
        self._db.commit()
        self.reload_catalog()
        return self.get_catalog_entry(skill_name)

    def list_audit(
        self,
        *,
        skill_name: str | None = None,
        action: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit_logs WHERE 1 = 1"
        params: list[Any] = []
        if skill_name:
            query += " AND (entity_id = ? OR diff_summary LIKE ?)"
            params.extend([skill_name, f"%{skill_name}%"])
        if action:
            query += " AND action = ?"
            params.append(action)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._db.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def _ensure_catalog_loaded(self) -> None:
        row = self._db.execute("SELECT COUNT(*) AS count FROM skill_catalog_entries").fetchone()
        if row is None or int(row["count"] or 0) == 0:
            self.reload_catalog()

    def _audit(
        self,
        *,
        operator: str,
        action: str,
        entity_type: str,
        entity_id: str,
        version: str | None,
        diff_summary: str,
    ) -> None:
        self._db.execute(
            """
            INSERT INTO audit_logs (id, operator, action, entity_type, entity_id, version, diff_summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), operator, action, entity_type, entity_id, version, diff_summary, _utcnow()),
        )

    def _row_to_catalog_entry(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["name"] = item["skill_name"]
        item["last_indexed_at"] = item.get("indexed_at")
        return item


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
