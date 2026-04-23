"""沙箱文件工作区服务。"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.contracts.errors import ErrorCode


class FileWorkspaceError(Exception):
    """文件工作区错误。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class FileWorkspaceService:
    """负责沙箱内文件的读写、加锁与审计。"""

    def __init__(
        self,
        db: sqlite3.Connection,
        *,
        sandbox_root: str,
        protected_relative_prefixes: tuple[str, ...] = (".agents/skills",),
        protected_roots: list[str | Path] | None = None,
        read_max_bytes: int = 100_000,
        write_max_bytes: int = 100_000,
        lock_timeout_sec: int = 30,
    ) -> None:
        self._db = db
        self._sandbox_root = Path(sandbox_root).expanduser().resolve()
        self._sandbox_root.mkdir(parents=True, exist_ok=True)
        self._protected_relative_prefixes = tuple(
            prefix.strip("/") for prefix in protected_relative_prefixes if prefix.strip("/")
        )
        self._protected_roots = [
            Path(root).expanduser().resolve(strict=False)
            for root in (protected_roots or [])
        ]
        self._read_max_bytes = read_max_bytes
        self._write_max_bytes = write_max_bytes
        self._lock_timeout_sec = lock_timeout_sec

    @property
    def sandbox_root(self) -> Path:
        return self._sandbox_root

    def list_dir(self, path: str = ".", *, runtime_context: dict[str, Any] | None = None) -> dict[str, Any]:
        operation = self._start_operation(operation_type="list", sandbox_path=self._normalize_operation_path(path), runtime_context=runtime_context)
        try:
            target, sandbox_path = self._resolve_path(path)
            if not target.exists():
                raise FileWorkspaceError(ErrorCode.TOOL_EXECUTION_FAILED, f"path not found: {sandbox_path}")
            if not target.is_dir():
                raise FileWorkspaceError(ErrorCode.TOOL_EXECUTION_FAILED, f"path is not a directory: {sandbox_path}")
            entries = self._list_entries(target)
            self._finish_operation(operation["id"], status="success", content_preview="\n".join(entries))
            return {"path": sandbox_path, "entries": entries}
        except FileWorkspaceError as exc:
            self._finish_operation(operation["id"], status="failed", content_preview=exc.message)
            raise

    def read_file(self, path: str, *, runtime_context: dict[str, Any] | None = None) -> dict[str, Any]:
        operation = self._start_operation(operation_type="read", sandbox_path=self._normalize_operation_path(path), runtime_context=runtime_context)
        checksum_before: str | None = None
        try:
            target, sandbox_path = self._resolve_path(path)
            if not target.exists():
                raise FileWorkspaceError(ErrorCode.TOOL_EXECUTION_FAILED, f"path not found: {sandbox_path}")
            if target.is_dir():
                raise FileWorkspaceError(ErrorCode.TOOL_EXECUTION_FAILED, f"path is a directory: {sandbox_path}")
            content = target.read_text(encoding="utf-8", errors="replace")
            self._ensure_text_size(content, limit=self._read_max_bytes, error_code=ErrorCode.TOOL_EXECUTION_FAILED)
            checksum_before = self._checksum(content)
            self._finish_operation(
                operation["id"],
                status="success",
                content_preview=content,
                checksum_before=checksum_before,
            )
            return {"path": sandbox_path, "content": content, "checksum": checksum_before}
        except FileWorkspaceError as exc:
            self._finish_operation(
                operation["id"],
                status="failed",
                content_preview=exc.message,
                checksum_before=checksum_before,
            )
            raise

    def write_file(
        self,
        path: str,
        content: str,
        *,
        expected_checksum: str | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operation = self._start_operation(operation_type="write", sandbox_path=self._normalize_operation_path(path), runtime_context=runtime_context)
        run_id = operation["run_id"]
        checksum_before: str | None = None
        lock_path: str | None = None
        try:
            target, sandbox_path = self._resolve_path(path)
            lock_path = sandbox_path
            self._ensure_text_size(content, limit=self._write_max_bytes, error_code=ErrorCode.TOOL_EXECUTION_FAILED)
            self._acquire_write_lock(sandbox_path=sandbox_path, owner_run_id=run_id)
            checksum_before = self._read_checksum_if_exists(target)
            self._assert_expected_checksum(expected_checksum, actual_checksum=checksum_before, sandbox_path=sandbox_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(target, content)
            checksum_after = self._checksum(content)
            self._finish_operation(
                operation["id"],
                status="success",
                content_preview=content,
                checksum_before=checksum_before,
                checksum_after=checksum_after,
            )
            self._audit_file_change(
                action="write_file",
                sandbox_path=sandbox_path,
                operator=operation["operator"],
                diff_summary=f"write {sandbox_path}",
            )
            return {
                "path": sandbox_path,
                "checksum_before": checksum_before,
                "checksum_after": checksum_after,
                "bytes_written": len(content.encode("utf-8")),
            }
        except FileWorkspaceError as exc:
            self._finish_operation(
                operation["id"],
                status="failed",
                content_preview=exc.message,
                checksum_before=checksum_before,
            )
            raise
        finally:
            if lock_path:
                self._release_write_lock(lock_path, owner_run_id=run_id)

    def patch_file(
        self,
        path: str,
        old_text: str,
        new_text: str,
        *,
        expected_checksum: str | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operation = self._start_operation(operation_type="patch", sandbox_path=self._normalize_operation_path(path), runtime_context=runtime_context)
        run_id = operation["run_id"]
        checksum_before: str | None = None
        lock_path: str | None = None
        try:
            target, sandbox_path = self._resolve_path(path)
            lock_path = sandbox_path
            if not target.exists():
                raise FileWorkspaceError(ErrorCode.TOOL_EXECUTION_FAILED, f"path not found: {sandbox_path}")
            if target.is_dir():
                raise FileWorkspaceError(ErrorCode.TOOL_EXECUTION_FAILED, f"path is a directory: {sandbox_path}")
            self._acquire_write_lock(sandbox_path=sandbox_path, owner_run_id=run_id)
            original = target.read_text(encoding="utf-8", errors="replace")
            checksum_before = self._checksum(original)
            self._assert_expected_checksum(expected_checksum, actual_checksum=checksum_before, sandbox_path=sandbox_path)
            occurrences = original.count(old_text)
            if occurrences != 1:
                raise FileWorkspaceError(
                    ErrorCode.TOOL_EXECUTION_FAILED,
                    f"patch target must match exactly once, found {occurrences}: {sandbox_path}",
                )
            updated = original.replace(old_text, new_text, 1)
            self._ensure_text_size(updated, limit=self._write_max_bytes, error_code=ErrorCode.TOOL_EXECUTION_FAILED)
            self._atomic_write(target, updated)
            checksum_after = self._checksum(updated)
            self._finish_operation(
                operation["id"],
                status="success",
                content_preview=updated,
                checksum_before=checksum_before,
                checksum_after=checksum_after,
            )
            self._audit_file_change(
                action="patch_file",
                sandbox_path=sandbox_path,
                operator=operation["operator"],
                diff_summary=f"patch {sandbox_path}",
            )
            return {
                "path": sandbox_path,
                "checksum_before": checksum_before,
                "checksum_after": checksum_after,
                "replacements": 1,
            }
        except FileWorkspaceError as exc:
            self._finish_operation(
                operation["id"],
                status="failed",
                content_preview=exc.message,
                checksum_before=checksum_before,
            )
            raise
        finally:
            if lock_path:
                self._release_write_lock(lock_path, owner_run_id=run_id)

    def list_operations(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """查询文件操作审计记录。"""
        query = "SELECT * FROM file_operations WHERE 1 = 1"
        params: list[Any] = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if run_id:
            query += " AND agent_run_id = ?"
            params.append(run_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        rows = self._db.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_locks(self, *, sandbox_path: str | None = None) -> list[dict[str, Any]]:
        """查询当前活跃文件锁。"""
        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            "DELETE FROM file_locks WHERE expires_at <= ?",
            (now,),
        )
        self._db.commit()
        query = "SELECT * FROM file_locks WHERE 1 = 1"
        params: list[Any] = []
        if sandbox_path:
            query += " AND sandbox_path = ?"
            params.append(sandbox_path)
        rows = self._db.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def _resolve_path(self, path: str) -> tuple[Path, str]:
        raw = self._normalize_operation_path(path)
        requested = Path(raw)
        if requested.is_absolute():
            candidate = requested.expanduser().resolve(strict=False)
        else:
            candidate = (self._sandbox_root / requested).expanduser().resolve(strict=False)
        try:
            relative = candidate.relative_to(self._sandbox_root)
        except ValueError as exc:
            raise FileWorkspaceError(ErrorCode.FILE_SANDBOX_VIOLATION, f"path escapes sandbox: {raw}") from exc
        self._ensure_no_symlink(candidate)
        sandbox_path = relative.as_posix() if relative.as_posix() else "."
        self._ensure_not_protected(candidate, sandbox_path)
        return candidate, sandbox_path

    def _ensure_not_protected(self, candidate: Path, sandbox_path: str) -> None:
        normalized = sandbox_path.strip("/")
        for prefix in self._protected_relative_prefixes:
            if normalized == prefix or normalized.startswith(f"{prefix}/"):
                raise FileWorkspaceError(
                    ErrorCode.FILE_SANDBOX_VIOLATION,
                    f"path is protected and cannot be accessed by file tools: {sandbox_path}",
                )

        for protected_root in self._protected_roots:
            try:
                candidate.relative_to(protected_root)
            except ValueError:
                continue
            raise FileWorkspaceError(
                ErrorCode.FILE_SANDBOX_VIOLATION,
                f"path is protected and cannot be accessed by file tools: {sandbox_path}",
            )

    def _ensure_no_symlink(self, candidate: Path) -> None:
        current = candidate if candidate.exists() else candidate.parent
        while True:
            if current.exists() and current.is_symlink():
                raise FileWorkspaceError(ErrorCode.FILE_SANDBOX_VIOLATION, f"symlink is not allowed: {current}")
            if current == self._sandbox_root:
                return
            if self._sandbox_root not in current.parents:
                raise FileWorkspaceError(ErrorCode.FILE_SANDBOX_VIOLATION, f"path escapes sandbox: {candidate}")
            current = current.parent

    def _start_operation(self, *, operation_type: str, sandbox_path: str, runtime_context: dict[str, Any] | None) -> dict[str, Any]:
        run_id = str((runtime_context or {}).get("run_id") or "-")
        session_id = self._resolve_session_id(run_id)
        operation_id = str(uuid.uuid4())
        self._db.execute(
            """
            INSERT INTO file_operations (
                id, agent_run_id, session_id, operation_type, sandbox_path, status, content_preview,
                checksum_before, checksum_after, started_at, ended_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                run_id,
                session_id,
                operation_type,
                sandbox_path,
                "running",
                None,
                None,
                None,
                self._utcnow(),
                None,
            ),
        )
        self._db.commit()
        return {"id": operation_id, "run_id": run_id, "session_id": session_id, "operator": f"agent:{run_id}"}

    def _finish_operation(
        self,
        operation_id: str,
        *,
        status: str,
        content_preview: str,
        checksum_before: str | None = None,
        checksum_after: str | None = None,
    ) -> None:
        self._db.execute(
            """
            UPDATE file_operations
            SET status = ?, content_preview = ?, checksum_before = ?, checksum_after = ?, ended_at = ?
            WHERE id = ?
            """,
            (status, self._preview(content_preview), checksum_before, checksum_after, self._utcnow(), operation_id),
        )
        self._db.commit()

    def _acquire_write_lock(self, *, sandbox_path: str, owner_run_id: str) -> None:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self._lock_timeout_sec)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                "DELETE FROM file_locks WHERE sandbox_path = ? AND expires_at <= ?",
                (sandbox_path, now.isoformat()),
            )
            existing = self._db.execute(
                "SELECT owner_run_id FROM file_locks WHERE sandbox_path = ? AND lock_type = 'write'",
                (sandbox_path,),
            ).fetchone()
            if existing and existing["owner_run_id"] != owner_run_id:
                self._db.rollback()
                raise FileWorkspaceError(ErrorCode.FILE_WRITE_CONFLICT, f"write conflict on sandbox path: {sandbox_path}")
            if existing:
                self._db.execute(
                    "UPDATE file_locks SET created_at = ?, expires_at = ? WHERE sandbox_path = ? AND lock_type = 'write'",
                    (now.isoformat(), expires_at.isoformat(), sandbox_path),
                )
            else:
                self._db.execute(
                    """
                    INSERT INTO file_locks (id, sandbox_path, lock_type, owner_run_id, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), sandbox_path, "write", owner_run_id, now.isoformat(), expires_at.isoformat()),
                )
            self._db.commit()
        except FileWorkspaceError:
            raise
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            raise FileWorkspaceError(ErrorCode.FILE_WRITE_CONFLICT, f"write conflict on sandbox path: {sandbox_path}") from exc
        except Exception:
            self._db.rollback()
            raise

    def _release_write_lock(self, sandbox_path: str, *, owner_run_id: str) -> None:
        self._db.execute(
            "DELETE FROM file_locks WHERE sandbox_path = ? AND lock_type = 'write' AND owner_run_id = ?",
            (sandbox_path, owner_run_id),
        )
        self._db.commit()

    def _audit_file_change(self, *, action: str, sandbox_path: str, operator: str, diff_summary: str) -> None:
        self._db.execute(
            """
            INSERT INTO audit_logs (id, operator, action, entity_type, entity_id, version, diff_summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), operator, action, "file", sandbox_path, None, diff_summary, self._utcnow()),
        )
        self._db.commit()

    def _resolve_session_id(self, run_id: str) -> str | None:
        if not run_id or run_id == "-":
            return None
        row = self._db.execute("SELECT session_id FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        return str(row["session_id"]) if row and row["session_id"] else None

    def _read_checksum_if_exists(self, target: Path) -> str | None:
        if not target.exists():
            return None
        if target.is_dir():
            raise FileWorkspaceError(ErrorCode.TOOL_EXECUTION_FAILED, f"path is a directory: {target.name}")
        return self._checksum(target.read_text(encoding="utf-8", errors="replace"))

    def _assert_expected_checksum(self, expected_checksum: str | None, *, actual_checksum: str | None, sandbox_path: str) -> None:
        if expected_checksum and expected_checksum != actual_checksum:
            raise FileWorkspaceError(
                ErrorCode.FILE_WRITE_CONFLICT,
                f"checksum mismatch before write: {sandbox_path}",
            )

    def _atomic_write(self, target: Path, content: str) -> None:
        tmp_path = target.with_name(f".{target.name}.tmp.{uuid.uuid4().hex}")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(target)

    def _ensure_text_size(self, content: str, *, limit: int, error_code: str) -> None:
        size = len(content.encode("utf-8"))
        if size > limit:
            raise FileWorkspaceError(error_code, f"content exceeds size limit: {size} > {limit}")

    def _list_entries(self, target: Path) -> list[str]:
        entries = []
        for entry in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            suffix = "/" if entry.is_dir() else ""
            entries.append(f"{entry.name}{suffix}")
        return entries

    def _normalize_operation_path(self, path: str | None) -> str:
        raw = (path or ".").strip().strip('"').strip("'")
        return raw or "."

    def _preview(self, text: str, *, limit: int = 200) -> str:
        return " ".join(text.split())[:limit]

    def _checksum(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _utcnow(self) -> str:
        return datetime.now(timezone.utc).isoformat()