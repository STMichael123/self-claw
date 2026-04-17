"""Skill catalog、draft 审核与发布服务。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import uuid
from typing import Any

import structlog

from src.contracts.errors import ErrorCode
from src.skills.registry import SkillRegistry, SkillRegistryError

logger = structlog.get_logger()

VALID_DRAFT_ACTIONS = {"create", "update"}
VALID_SKILL_ACTIONS = {"enable", "disable", "rollback"}


class SkillService:
    """基于文件系统和 SQLite 的 Skill 生命周期服务。"""

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
                "SELECT status, source, last_approved_revision FROM skill_catalog_entries WHERE skill_name = ?",
                (entry.skill_name,),
            ).fetchone()
            status = existing["status"] if existing is not None else entry.status
            source = existing["source"] if existing is not None else entry.source
            last_revision = (
                existing["last_approved_revision"] if existing is not None else self._latest_revision(entry.skill_name)
            )
            self._db.execute(
                """
                INSERT INTO skill_catalog_entries (
                    skill_name, description, location, compatibility, status, source, content_hash,
                    discovered_at, indexed_at, last_approved_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(skill_name) DO UPDATE SET
                    description = excluded.description,
                    location = excluded.location,
                    compatibility = excluded.compatibility,
                    status = excluded.status,
                    source = excluded.source,
                    content_hash = excluded.content_hash,
                    discovered_at = excluded.discovered_at,
                    indexed_at = excluded.indexed_at,
                    last_approved_revision = excluded.last_approved_revision
                """,
                (
                    entry.skill_name,
                    entry.description,
                    entry.location,
                    entry.compatibility,
                    status,
                    source,
                    entry.content_hash,
                    entry.discovered_at,
                    entry.indexed_at,
                    last_revision,
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
            last_approved_revision=row["last_approved_revision"],
            latest_revision=self._latest_revision(skill_name),
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
            if self._legacy_skill_exists(skill_name):
                raise SkillRegistryError(
                    ErrorCode.LEGACY_SKILL_MIGRATION_REQUIRED,
                    f"legacy skill requires migration before activation: {skill_name}",
                )
            raise SkillRegistryError(ErrorCode.SKILL_NOT_FOUND, f"skill not found: {skill_name}")
        if row["status"] != "enabled":
            raise SkillRegistryError(ErrorCode.SKILL_DISABLED, f"skill disabled: {skill_name}")
        return self._registry.activate(skill_name, resource_paths=resource_paths).to_dict()

    def create_draft(
        self,
        *,
        requested_action: str,
        target_skill_name: str | None = None,
        proposed_name: str | None = None,
        draft_skill_md: str | None = None,
        draft_resources_manifest: list[dict[str, Any]] | None = None,
        source_run_id: str | None = None,
        source_session_id: str | None = None,
        operator: str = "system",
        user_intent_summary: str = "",
    ) -> dict[str, Any]:
        if requested_action not in VALID_DRAFT_ACTIONS:
            raise ValueError("invalid draft action")

        skill_name = (proposed_name or target_skill_name or "").strip()
        if not skill_name:
            raise ValueError("skill name is required")

        initial_md = draft_skill_md or self._default_draft_markdown(
            skill_name=skill_name,
            requested_action=requested_action,
            target_skill_name=target_skill_name,
            user_intent_summary=user_intent_summary,
        )
        initial_resources = draft_resources_manifest
        if initial_resources is None:
            initial_resources = self._snapshot_resources(target_skill_name or skill_name)

        draft_id = str(uuid.uuid4())
        now = _utcnow()
        self._db.execute(
            """
            INSERT INTO skill_drafts (
                id, source_run_id, source_session_id, requested_action, target_skill_name, proposed_name,
                draft_skill_md, draft_resources_manifest, review_status, reviewer, review_note,
                user_intent_summary, suggested_name, skill_prompt, allowed_tools, input_schema,
                output_schema, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_id,
                source_run_id or "",
                source_session_id,
                requested_action,
                target_skill_name,
                proposed_name or skill_name,
                initial_md,
                json.dumps(initial_resources, ensure_ascii=False),
                "draft",
                None,
                None,
                user_intent_summary,
                proposed_name or skill_name,
                initial_md,
                json.dumps([], ensure_ascii=False),
                json.dumps({}, ensure_ascii=False),
                json.dumps({}, ensure_ascii=False),
                now,
                now,
            ),
        )
        self._audit(
            operator=operator,
            action="skill_draft_create",
            entity_type="skill_draft",
            entity_id=draft_id,
            version=None,
            diff_summary=f"{requested_action}:{skill_name}",
        )
        self._db.commit()
        return self.get_draft(draft_id) or {"draft_id": draft_id}

    def list_drafts(
        self,
        *,
        review_status: str | None = None,
        skill_name: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM skill_drafts WHERE 1 = 1"
        params: list[Any] = []
        if review_status:
            query += " AND review_status = ?"
            params.append(review_status)
        if skill_name:
            query += " AND (target_skill_name = ? OR proposed_name = ?)"
            params.extend([skill_name, skill_name])
        query += " ORDER BY updated_at DESC"
        rows = self._db.execute(query, params).fetchall()
        return [self._row_to_draft(row) for row in rows]

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM skill_drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_draft(row)

    def update_draft(
        self,
        draft_id: str,
        *,
        proposed_name: str | None = None,
        draft_skill_md: str | None = None,
        draft_resources_manifest: list[dict[str, Any]] | None = None,
        user_intent_summary: str | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_draft(draft_id)
        if current is None:
            return None
        if current["review_status"] != "draft":
            raise SkillRegistryError(
                ErrorCode.SKILL_DRAFT_REVIEW_REQUIRED,
                "only draft status can be updated",
            )

        next_name = proposed_name if proposed_name is not None else current.get("proposed_name")
        next_md = draft_skill_md if draft_skill_md is not None else current.get("draft_skill_md")
        next_resources = (
            draft_resources_manifest if draft_resources_manifest is not None else current.get("draft_resources_manifest", [])
        )
        next_summary = user_intent_summary if user_intent_summary is not None else current.get("user_intent_summary", "")

        self._db.execute(
            """
            UPDATE skill_drafts
            SET proposed_name = ?, draft_skill_md = ?, draft_resources_manifest = ?, user_intent_summary = ?,
                suggested_name = ?, skill_prompt = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_name,
                next_md,
                json.dumps(next_resources, ensure_ascii=False),
                next_summary,
                next_name,
                next_md,
                _utcnow(),
                draft_id,
            ),
        )
        self._db.commit()
        return self.get_draft(draft_id)

    def approve_draft(
        self,
        draft_id: str,
        *,
        reviewer: str,
        change_note: str = "",
    ) -> dict[str, Any] | None:
        draft = self.get_draft(draft_id)
        if draft is None:
            return None
        if draft["review_status"] != "draft":
            raise SkillRegistryError(
                ErrorCode.SKILL_DRAFT_REVIEW_REQUIRED,
                "draft already reviewed",
            )

        skill_name = (draft.get("proposed_name") or draft.get("target_skill_name") or "").strip()
        if not skill_name:
            raise SkillRegistryError(ErrorCode.SKILL_VALIDATION_FAILED, "draft missing skill name")

        skill_md = str(draft.get("draft_skill_md") or "")
        self._registry.validate_skill_text(skill_name, skill_md)
        resources = self._normalize_resources_manifest(draft.get("draft_resources_manifest") or [])
        publish_result = self._publish_skill(skill_name, skill_md, resources)
        revision = self._next_revision(skill_name)
        now = _utcnow()

        self._db.execute(
            """
            INSERT INTO skill_revisions (
                id, skill_name, revision, source_draft_id, skill_md_snapshot,
                resources_manifest_snapshot, content_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                skill_name,
                revision,
                draft_id,
                skill_md,
                json.dumps(resources, ensure_ascii=False),
                publish_result["content_hash"],
                now,
            ),
        )
        self._db.execute(
            """
            INSERT INTO skill_catalog_entries (
                skill_name, description, location, compatibility, status, source, content_hash,
                discovered_at, indexed_at, last_approved_revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(skill_name) DO UPDATE SET
                description = excluded.description,
                location = excluded.location,
                compatibility = excluded.compatibility,
                status = excluded.status,
                source = excluded.source,
                content_hash = excluded.content_hash,
                discovered_at = excluded.discovered_at,
                indexed_at = excluded.indexed_at,
                last_approved_revision = excluded.last_approved_revision
            """,
            (
                skill_name,
                publish_result["description"],
                publish_result["location"],
                publish_result["compatibility"],
                "enabled",
                "project",
                publish_result["content_hash"],
                now,
                now,
                revision,
            ),
        )
        self._db.execute(
            """
            UPDATE skill_drafts
            SET review_status = 'approved', reviewer = ?, review_note = ?, updated_at = ?
            WHERE id = ?
            """,
            (reviewer, change_note or "approved", now, draft_id),
        )
        self._audit(
            operator=reviewer,
            action="skill_publish",
            entity_type="skill",
            entity_id=skill_name,
            version=revision,
            diff_summary=change_note or f"publish {skill_name}",
        )
        self._audit(
            operator=reviewer,
            action="skill_draft_approve",
            entity_type="skill_draft",
            entity_id=draft_id,
            version=revision,
            diff_summary=change_note or f"approve {skill_name}",
        )
        self._db.commit()
        self.reload_catalog()
        return self.get_catalog_entry(skill_name)

    def reject_draft(self, draft_id: str, *, reviewer: str, review_note: str) -> dict[str, Any] | None:
        draft = self.get_draft(draft_id)
        if draft is None:
            return None
        self._db.execute(
            """
            UPDATE skill_drafts
            SET review_status = 'rejected', reviewer = ?, review_note = ?, updated_at = ?
            WHERE id = ?
            """,
            (reviewer, review_note, _utcnow(), draft_id),
        )
        self._audit(
            operator=reviewer,
            action="skill_draft_reject",
            entity_type="skill_draft",
            entity_id=draft_id,
            version=None,
            diff_summary=review_note,
        )
        self._db.commit()
        return self.get_draft(draft_id)

    def perform_action(
        self,
        skill_name: str,
        *,
        action: str,
        operator: str,
        reason: str = "",
        target_revision: str | None = None,
    ) -> dict[str, Any] | None:
        if action not in VALID_SKILL_ACTIONS:
            raise ValueError("invalid skill action")
        self._ensure_catalog_loaded()

        if action == "rollback":
            if not target_revision:
                raise ValueError("target_revision is required for rollback")
            revision = self._db.execute(
                "SELECT * FROM skill_revisions WHERE skill_name = ? AND revision = ?",
                (skill_name, target_revision),
            ).fetchone()
            if revision is None:
                return None
            skill_md = revision["skill_md_snapshot"] or ""
            resources = _from_json(revision["resources_manifest_snapshot"], default=[])
            publish_result = self._publish_skill(skill_name, skill_md, self._normalize_resources_manifest(resources))
            now = _utcnow()
            self._db.execute(
                """
                INSERT INTO skill_catalog_entries (
                    skill_name, description, location, compatibility, status, source, content_hash,
                    discovered_at, indexed_at, last_approved_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(skill_name) DO UPDATE SET
                    description = excluded.description,
                    location = excluded.location,
                    compatibility = excluded.compatibility,
                    status = excluded.status,
                    source = excluded.source,
                    content_hash = excluded.content_hash,
                    discovered_at = excluded.discovered_at,
                    indexed_at = excluded.indexed_at,
                    last_approved_revision = excluded.last_approved_revision
                """,
                (
                    skill_name,
                    publish_result["description"],
                    publish_result["location"],
                    publish_result["compatibility"],
                    "enabled",
                    "project",
                    publish_result["content_hash"],
                    now,
                    now,
                    target_revision,
                ),
            )
            self._audit(
                operator=operator,
                action="skill_rollback",
                entity_type="skill",
                entity_id=skill_name,
                version=target_revision,
                diff_summary=reason or f"rollback to {target_revision}",
            )
            self._db.commit()
            self.reload_catalog()
            return self.get_catalog_entry(skill_name)

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

    def list_revisions(self, skill_name: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT revision, source_draft_id, content_hash, created_at FROM skill_revisions WHERE skill_name = ? ORDER BY created_at DESC",
            (skill_name,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_audit(
        self,
        *,
        skill_name: str | None = None,
        draft_id: str | None = None,
        action: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit_logs WHERE 1 = 1"
        params: list[Any] = []
        if skill_name:
            query += " AND (entity_id = ? OR diff_summary LIKE ?)"
            params.extend([skill_name, f"%{skill_name}%"])
        if draft_id:
            query += " AND entity_id = ?"
            params.append(draft_id)
        if action:
            query += " AND action = ?"
            params.append(action)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._db.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_migration_candidates(self) -> list[dict[str, Any]]:
        self._ensure_catalog_loaded()
        catalog_names = {
            row["skill_name"]
            for row in self._db.execute("SELECT skill_name FROM skill_catalog_entries").fetchall()
        }
        legacy_rows = self._db.execute(
            "SELECT id, name, version, status, updated_at FROM skills ORDER BY updated_at DESC"
        ).fetchall()
        return [
            {
                "legacy_skill_id": row["id"],
                "skill_name": row["name"],
                "version": row["version"],
                "status": row["status"],
                "updated_at": row["updated_at"],
                "migration_status": "already-migrated" if row["name"] in catalog_names else "pending",
            }
            for row in legacy_rows
        ]

    def _ensure_catalog_loaded(self) -> None:
        row = self._db.execute("SELECT COUNT(*) AS count FROM skill_catalog_entries").fetchone()
        if row is None or int(row["count"] or 0) == 0:
            self.reload_catalog()

    def _default_draft_markdown(
        self,
        *,
        skill_name: str,
        requested_action: str,
        target_skill_name: str | None,
        user_intent_summary: str,
    ) -> str:
        current_skill = target_skill_name or skill_name
        current_path = self._registry.root / current_skill / "SKILL.md"
        if requested_action == "update" and current_path.exists():
            return current_path.read_text(encoding="utf-8", errors="replace")
        summary_line = user_intent_summary.strip() or "TODO: summarize the intended workflow and acceptance boundary."
        return (
            "---\n"
            f"name: {skill_name}\n"
            f"description: {summary_line[:120]}\n"
            "compatibility: self-claw@1.0\n"
            "allowed-tools: []\n"
            "metadata:\n"
            f"  requested-action: {requested_action}\n"
            "---\n\n"
            f"# {skill_name}\n\n"
            f"{summary_line}\n"
        )

    def _snapshot_resources(self, skill_name: str) -> list[dict[str, Any]]:
        skill_dir = self._registry.root / skill_name
        if not skill_dir.exists():
            return []
        snapshot: list[dict[str, Any]] = []
        for manifest_item in self._registry.build_resource_manifest(skill_name):
            resource_path = skill_dir / manifest_item["path"]
            snapshot.append(
                {
                    "path": manifest_item["path"],
                    "content": resource_path.read_text(encoding="utf-8", errors="replace"),
                }
            )
        return snapshot

    def _publish_skill(
        self,
        skill_name: str,
        skill_md: str,
        resources: list[dict[str, Any]],
    ) -> dict[str, Any]:
        document = self._registry.validate_skill_text(skill_name, skill_md)
        skill_dir = self._registry.root / skill_name
        temp_dir = self._registry.root / f".{skill_name}.tmp-{uuid.uuid4().hex}"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        (temp_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

        for resource in resources:
            relative_path = self._normalize_publish_resource_path(str(resource.get("path") or ""))
            target = temp_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(resource.get("content") or ""), encoding="utf-8")

        if skill_dir.exists():
            shutil.rmtree(skill_dir)
        temp_dir.replace(skill_dir)

        content_hash = hashlib.sha256(
            (skill_md + json.dumps(resources, ensure_ascii=False, sort_keys=True)).encode("utf-8")
        ).hexdigest()
        return {
            "description": document.description,
            "compatibility": document.compatibility,
            "location": str(skill_dir),
            "content_hash": content_hash,
        }

    def _latest_revision(self, skill_name: str) -> str | None:
        row = self._db.execute(
            "SELECT revision FROM skill_revisions WHERE skill_name = ? ORDER BY created_at DESC LIMIT 1",
            (skill_name,),
        ).fetchone()
        if row is None:
            return None
        return row["revision"]

    def _next_revision(self, skill_name: str) -> str:
        current = self._latest_revision(skill_name)
        if not current or not current.startswith("r"):
            return "r1"
        try:
            value = int(current[1:])
        except ValueError:
            return "r1"
        return f"r{value + 1}"

    def _legacy_skill_exists(self, skill_name: str) -> bool:
        row = self._db.execute("SELECT 1 FROM skills WHERE name = ? LIMIT 1", (skill_name,)).fetchone()
        return row is not None

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

    def _row_to_draft(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["draft_id"] = item["id"]
        item["draft_skill_md"] = item.get("draft_skill_md") or item.get("skill_prompt") or ""
        item["draft_resources_manifest"] = _from_json(item.get("draft_resources_manifest"), default=[])
        item["source_run_id"] = item.get("source_run_id") or None
        return item

    def _normalize_resources_manifest(self, resources: Any) -> list[dict[str, Any]]:
        if isinstance(resources, str):
            resources = _from_json(resources, default=[])
        if not isinstance(resources, list):
            raise SkillRegistryError(ErrorCode.SKILL_VALIDATION_FAILED, "resources manifest must be a list")
        normalized: list[dict[str, Any]] = []
        for item in resources:
            if not isinstance(item, dict):
                raise SkillRegistryError(ErrorCode.SKILL_VALIDATION_FAILED, "resource manifest item must be a mapping")
            normalized.append(
                {
                    "path": self._normalize_publish_resource_path(str(item.get("path") or "")),
                    "content": str(item.get("content") or ""),
                }
            )
        return normalized

    @staticmethod
    def _normalize_publish_resource_path(raw_path: str) -> str:
        normalized = raw_path.strip().replace("\\", "/")
        if not normalized:
            raise SkillRegistryError(ErrorCode.SKILL_VALIDATION_FAILED, "resource path is required")
        path = Path(normalized)
        if path.is_absolute():
            raise SkillRegistryError(ErrorCode.SKILL_VALIDATION_FAILED, "resource path must be relative")
        if not path.parts or path.parts[0] not in {"scripts", "references", "assets"}:
            raise SkillRegistryError(
                ErrorCode.SKILL_VALIDATION_FAILED,
                "resource path must stay inside scripts/references/assets",
            )
        if any(part == ".." for part in path.parts):
            raise SkillRegistryError(ErrorCode.SKILL_VALIDATION_FAILED, "resource path cannot escape skill root")
        return path.as_posix()


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _from_json(raw: str | None, *, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default
