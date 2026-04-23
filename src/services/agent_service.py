"""Agent、会话与状态管理服务。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from jsonschema import ValidationError as JSONSchemaValidationError, validate as jsonschema_validate
import structlog

from src.agents.main import MainAgent
from src.agents.sub import SubAgentExecutor
from src.contracts.errors import ErrorCode
from src.contracts.models import RunStatus, SessionStatus, SubAgentRequest, TaskStatus
from src.models.llm import ChatMessage
from src.models.router import ModelRouter
from src.services.cost import CostService
from src.skills.registry import SkillRegistryError
from src.sessions.manager import SessionManager
from src.tools.builtins import build_builtin_tools
from src.tools.registry import ToolExecutor, ToolRegistry

logger = structlog.get_logger()

DEFAULT_WEB_USER_ID = "web-user"


class AgentService:
    """封装会话、运行记录与 Agent 调用。"""

    def __init__(
        self,
        db: sqlite3.Connection,
        *,
        skill_service: Any | None = None,
        memory_service: Any | None = None,
        file_workspace_service: Any | None = None,
        notification_service: Any | None = None,
        hook_registry: Any | None = None,
        max_parallel_sub_agents: int = 5,
        max_parallel_main_runs: int = 3,
        model_name: str = "gpt-4o",
    ) -> None:
        self._db = db
        self.sessions = SessionManager(db, model_name=model_name)
        self.cost = CostService(db)
        self.skill_service = skill_service
        self.memory_service = memory_service
        self.file_workspace_service = file_workspace_service
        self.notification_service = notification_service
        self._hook_registry = hook_registry
        self.model_router = ModelRouter()
        self._model_name = model_name
        self.sessions.set_llm(self.model_router.get_primary())
        self.tool_registry = ToolRegistry()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._sub_agent_semaphore = asyncio.Semaphore(max_parallel_sub_agents)
        self._max_parallel_main_runs = max_parallel_main_runs
        for descriptor in build_builtin_tools(file_workspace_service, skill_service):
            self.tool_registry.register(descriptor)
        self.tool_executor = ToolExecutor(self.tool_registry)

    def create_session(
        self,
        *,
        user_id: str = DEFAULT_WEB_USER_ID,
        title: str | None = None,
        channel_type: str = "web",
    ) -> dict[str, Any]:
        session_id = self.sessions.create_session(user_id, title=title, channel_type=channel_type)
        session = self.sessions.get_session(session_id)
        return {
            "session_id": session_id,
            "status": "active",
            "created_at": session["created_at"] if session else _utcnow(),
            "title": session.get("title") if session else title,
        }

    def list_sessions(self, *, status: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
        sessions = self.sessions.list_sessions(status=status, user_id=user_id)
        return [self._serialize_session_summary(item) for item in sessions]

    def get_session_detail(self, session_id: str) -> dict[str, Any] | None:
        session = self.sessions.get_session(session_id)
        if session is None:
            return None

        main_runs = self._list_runs(session_id=session_id, agent_role="main")
        current_run = self._get_current_main_run(session)
        return {
            **session,
            "messages": [self._serialize_message(message) for message in self.sessions.list_messages(session_id)],
            "main_runs": [self._serialize_run_summary(item) for item in main_runs],
            "current_main_run": self._serialize_run_summary(current_run) if current_run else None,
        }

    def close_session(self, session_id: str) -> dict[str, Any] | None:
        session = self.sessions.get_session(session_id)
        if session is None:
            return None
        self._cancel_running_main_runs(session_id)
        summary = self.sessions.generate_summary(session_id)
        message_count = session.get("message_count") or 0
        self.sessions.close_session(session_id, summary=summary)

        # on_session_archive hook (FR-016)
        if self._hook_registry is not None:
            import asyncio as _asyncio
            try:
                _asyncio.create_task(
                    self._hook_registry.run_hooks("on_session_archive", {
                        "session_id": session_id,
                        "summary": summary,
                        "message_count": message_count,
                    })
                )
            except Exception as exc:
                logger.warning("on_session_archive_hook_failed", session_id=session_id, error=str(exc))

        if self.memory_service is not None and summary:
            try:
                self.memory_service.save_long_term(
                    f"session-{session_id}",
                    summary,
                    title=f"session-{session_id}",
                    source_type="session_archive",
                    source_ref=session_id,
                )
                background = asyncio.create_task(
                    self.memory_service.save_vector(
                        summary,
                        source_type="session_summary",
                        source_id=session_id,
                        metadata={"session_id": session_id},
                    )
                )
                self._background_tasks.add(background)
                background.add_done_callback(self._background_tasks.discard)
            except Exception as exc:
                logger.warning("archive_session_memory_failed", session_id=session_id, error=str(exc))
        return {
            "session_id": session_id,
            "status": "archived",
            "summary": summary,
        }

    async def chat(
        self,
        *,
        message: str,
        task_mode: str = "auto",
        session_id: str | None = None,
        session_title: str | None = None,
        stream: bool = False,
        user_id: str = DEFAULT_WEB_USER_ID,
        requested_skill_name: str | None = None,
        task_id: str | None = None,
        task_run_log_id: str | None = None,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        parse_slash: bool = True,
    ) -> dict[str, Any]:
        # 解析斜杠命令（FR-015）
        if parse_slash and message.startswith("/"):
            slash_skill, slash_args = self._parse_slash_command(message)
            if slash_skill:
                resolved = requested_skill_name or slash_skill
                message = slash_args
                requested_skill_name = resolved
            else:
                return {
                    "reply": slash_args,
                    "session_id": session_id,
                    "error": True,
                    "error_code": "SKILL_NOT_FOUND",
                }

        target_session_id, session_action = self._resolve_session(
            task_mode=task_mode,
            session_id=session_id,
            session_title=session_title,
            user_id=user_id,
        )
        session = self.sessions.get_session(target_session_id)
        if session is None:
            raise ValueError("session not found")

        history = self._build_history(target_session_id)
        resolved_skill_name = requested_skill_name
        skill_context = self._resolve_requested_skill_context(resolved_skill_name)
        available_skills_catalog = self._list_available_skills_catalog()
        self._validate_skill_input(skill_context, message)
        self._ensure_main_run_capacity()

        try:
            run_id = self._create_run(
                session_id=target_session_id,
                agent_role="main",
                status=RunStatus.RUNNING.value,
                task_ref=message[:120],
                context_ref={
                    "task_mode": task_mode,
                    "session_action": session_action,
                    "stream": stream,
                    "requested_skill_name": resolved_skill_name,
                    "task_id": task_id,
                    "task_run_log_id": task_run_log_id,
                },
                skill_id=resolved_skill_name,
                activated_skills=[item.get("skill_name") for item in skill_context.get("activated_skills", [])],
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("当前线程已有运行中的主 Agent，请等待完成或使用 cancel_and_rerun") from exc

        self.sessions.add_message(
            target_session_id,
            "user",
            message,
            metadata={
                "task_mode": task_mode,
                "session_action": session_action,
                "requested_skill_name": resolved_skill_name,
                "task_id": task_id,
            },
        )
        self._remember_session_message(target_session_id, "user", message)
        self.sessions.set_current_run(target_session_id, run_id)

        memory_context = await self._build_memory_context(
            session_id=target_session_id,
            user_message=message,
            base_snapshot=session.get("context_snapshot") or "",
        )
        principle_text = self.memory_service.load_principle() if self.memory_service else ""
        child_runs: list[dict[str, Any]] = []
        if self._should_spawn_sub_agent(message):
            child_run = await self._run_sub_agent(
                parent_run_id=run_id,
                session_id=target_session_id,
                goal=message,
                session_title=session.get("title") or "",
            )
            child_runs.append(child_run)
            reply_hint = _dig(child_run, "result_ref", "reply") or _dig(child_run, "result_ref", "output", "reply") or ""
            if reply_hint:
                memory_context = f"{memory_context}\n子 Agent 结果:\n{reply_hint}".strip()

        llm = self.model_router.get_primary()
        tool_defs, tool_executor = self._build_tool_runtime(None)
        main_agent = MainAgent(
            llm,
            tools=tool_defs,
            tool_executor=tool_executor,
            max_steps=int(skill_context.get("max_steps") or 10),
            hook_registry=self._hook_registry,
        )
        result = await main_agent.chat(
            message,
            history=history,
            available_skills_catalog=available_skills_catalog,
            activated_skills=skill_context.get("activated_skills") or None,
            principle=principle_text,
            long_term_context=memory_context,
            short_term_context="",
            run_id=run_id,
            cancellation_checker=lambda: self._is_run_cancelled(run_id),
            cancellation_waiter=lambda: self._wait_for_run_cancelled(run_id),
            event_callback=event_callback,
            runtime_context=skill_context.get("runtime_context") or None,
        )

        result = self._apply_skill_output_validation(result=result, skill_context=skill_context)
        activated_skills = self._extract_activated_skills(
            result.steps,
            initial=skill_context.get("activated_skills") or [],
        )
        activated_skill_names = [item.get("skill_name") for item in activated_skills if item.get("skill_name")]

        if result.pending_approval and result.resume_state:
            approval = self._create_tool_approval(
                session_id=target_session_id,
                run_id=run_id,
                tool_name=str(result.pending_approval.get("tool_name", "")),
                arguments=result.pending_approval.get("arguments", {}),
                resume_state=result.resume_state,
            )
            pending_result = {
                "reply": f"工具 {approval['tool_name']} 正在等待审批。",
                "latest_error": ErrorCode.TOOL_APPROVAL_PENDING,
                "pending_approval": approval,
            }
            self._update_run_progress(
                run_id,
                steps_count=len(result.steps),
                result_ref=pending_result,
                activated_skills=activated_skill_names,
            )
            self.sessions.add_message(
                target_session_id,
                "assistant",
                pending_result["reply"],
                run_id=run_id,
                metadata={
                    "run_status": RunStatus.RUNNING.value,
                    "pending_approval": approval,
                    "requested_skill_name": resolved_skill_name,
                    "activated_skills": activated_skill_names,
                },
            )
            self._remember_session_message(target_session_id, "assistant", pending_result["reply"])
            await self._emit_event(
                event_callback,
                {
                    "event": "approval_pending",
                    **approval,
                },
            )
            return {
                "session_id": target_session_id,
                "run_id": run_id,
                "session_action": session_action,
                "status": "waiting_approval",
                "reply": pending_result["reply"],
                "steps": [step.model_dump() for step in result.steps],
                "usage": {
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "estimated_cost": 0.0,
                    "model": getattr(llm, "model", ""),
                },
                "pending_approval": approval,
            }

        if result.status == RunStatus.CANCELLED or self._is_run_cancelled(run_id):
            logger.info("run_cancelled_before_persist", run_id=run_id, session_id=target_session_id)
            return self._build_cancelled_payload(
                session_id=target_session_id,
                run_id=run_id,
                session_action=session_action,
                steps=[step.model_dump() for step in result.steps],
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                model_name=getattr(llm, "model", ""),
            )

        estimated_cost = self._estimate_cost(
            model_name=getattr(llm, "model", ""),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        persisted = self._persist_run_runtime_artifacts(
            session_id=target_session_id,
            run_id=run_id,
            steps=result.steps,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost=estimated_cost,
            model_name=getattr(llm, "model", ""),
            task_id=task_id,
        )
        if not persisted:
            logger.info("run_cancelled_before_persist", run_id=run_id, session_id=target_session_id)
            return self._build_cancelled_payload(
                session_id=target_session_id,
                run_id=run_id,
                session_action=session_action,
                steps=[step.model_dump() for step in result.steps],
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                model_name=getattr(llm, "model", ""),
            )

        if self._is_run_cancelled(run_id):
            logger.info("run_cancelled_before_finalize", run_id=run_id, session_id=target_session_id)
            return self._build_cancelled_payload(
                session_id=target_session_id,
                run_id=run_id,
                session_action=session_action,
                steps=[step.model_dump() for step in result.steps],
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                model_name=getattr(llm, "model", ""),
            )

        result_ref = {
            "reply": result.reply,
            "steps": [step.model_dump() for step in result.steps],
            "child_runs": [self._serialize_run_summary(item) for item in child_runs],
            "latest_error": result.error_code,
            "requested_skill_name": resolved_skill_name,
            "task_id": task_id,
            "activated_skills": activated_skill_names,
        }
        self._update_run(
            run_id,
            status=result.status.value,
            steps_count=len(result.steps),
            result_ref=result_ref,
            activated_skills=activated_skill_names,
        )
        message_id = self.sessions.add_message(
            target_session_id,
            "assistant",
            result.reply,
            run_id=run_id,
            metadata={
                "session_action": session_action,
                "run_status": result.status.value,
                "steps": [step.model_dump() for step in result.steps],
                "requested_skill_name": resolved_skill_name,
                "task_id": task_id,
                "activated_skills": activated_skill_names,
            },
            guard_run_not_cancelled=True,
        )
        if not message_id:
            logger.info("run_cancelled_during_finalize", run_id=run_id, session_id=target_session_id)
            return self._build_cancelled_payload(
                session_id=target_session_id,
                run_id=run_id,
                session_action=session_action,
                steps=[step.model_dump() for step in result.steps],
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                model_name=getattr(llm, "model", ""),
            )
        self._remember_session_message(target_session_id, "assistant", result.reply)
        self.sessions.set_current_run(target_session_id, run_id)

        if self._is_run_cancelled(run_id):
            logger.info("run_cancelled_after_finalize", run_id=run_id, session_id=target_session_id)
            return self._build_cancelled_payload(
                session_id=target_session_id,
                run_id=run_id,
                session_action=session_action,
                steps=[step.model_dump() for step in result.steps],
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                model_name=getattr(llm, "model", ""),
            )

        return {
            "session_id": target_session_id,
            "run_id": run_id,
            "session_action": session_action,
            "status": result.status.value,
            "reply": result.reply,
            "steps": [step.model_dump() for step in result.steps],
            "usage": {
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "estimated_cost": estimated_cost,
                "model": getattr(llm, "model", ""),
            },
            "activated_skills": activated_skill_names,
        }

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None

        run = self._row_to_run(row)
        tool_calls = self._db.execute(
            "SELECT * FROM tool_calls WHERE agent_run_id = ? ORDER BY created_at ASC",
            (run_id,),
        ).fetchall()
        usage_rows = self._db.execute(
            "SELECT * FROM usage_logs WHERE agent_run_id = ? ORDER BY created_at DESC",
            (run_id,),
        ).fetchall()
        payload = {
            **run,
            "tool_calls": [dict(item) for item in tool_calls],
            "usage": [dict(item) for item in usage_rows],
        }
        if run["agent_role"] == "main":
            child_runs = self._list_runs(parent_run_id=run_id)
            payload["child_runs"] = [self._serialize_run_summary(item) for item in child_runs]
            payload["child_run_summary"] = self._build_child_run_summary(child_runs)
        return payload

    def list_tools(self, *, category: str | None = None) -> list[dict[str, Any]]:
        return [descriptor.to_dict() for descriptor in self.tool_registry.list_tools(category=category)]

    def list_tool_approvals(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM tool_approvals WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._db.execute(query, params).fetchall()
        return [self._serialize_tool_approval(dict(row)) for row in rows]

    async def decide_tool_approval(
        self,
        approval_id: str,
        *,
        decision: str,
        operator: str = DEFAULT_WEB_USER_ID,
    ) -> dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise ValueError("invalid approval decision")

        row = self._db.execute("SELECT * FROM tool_approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise ValueError("approval not found")

        current = dict(row)
        if current["status"] != "pending":
            return {
                **self._serialize_tool_approval(current),
                "resumed_run_id": current["agent_run_id"],
            }

        now = _utcnow()
        self._db.execute(
            "UPDATE tool_approvals SET status = ?, operator = ?, updated_at = ? WHERE id = ?",
            (decision, operator, now, approval_id),
        )
        self._db.execute(
            """
            INSERT INTO audit_logs (id, operator, action, entity_type, entity_id, version, diff_summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                operator,
                f"tool_approval_{decision}",
                "tool_approval",
                approval_id,
                None,
                f"{current['tool_name']} -> {decision}",
                now,
            ),
        )
        self._db.commit()

        background = asyncio.create_task(self._resume_tool_approval(approval_id))
        self._background_tasks.add(background)
        background.add_done_callback(self._background_tasks.discard)
        return {
            **self._serialize_tool_approval({**current, "status": decision, "operator": operator, "updated_at": now}),
            "resumed_run_id": current["agent_run_id"],
        }

    def list_file_operations(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if self.file_workspace_service is None:
            return []
        return self.file_workspace_service.list_operations(
            session_id=session_id, run_id=run_id, status=status, limit=limit,
        )

    def list_file_locks(self, *, sandbox_path: str | None = None) -> list[dict[str, Any]]:
        if self.file_workspace_service is None:
            return []
        return self.file_workspace_service.list_locks(sandbox_path=sandbox_path)

    def cost_summary(self, *, task_id: str | None = None, days: int = 7) -> dict[str, Any]:
        if task_id:
            return self.cost.get_task_summary(task_id)
        return self.cost.get_daily_summary()

    async def memory_search(
        self,
        query: str,
        session_id: str | None = None,
        tiers: list[str] | None = None,
    ) -> dict[str, Any]:
        if self.memory_service is None:
            return {"files": [], "vectors": []}
        selected = set(tiers) if tiers else {"principle", "long_term", "short_term"}
        files = []
        if "principle" in selected:
            files += self.memory_service.search_files(query, scope="principle")
        if "long_term" in selected:
            files += self.memory_service.search_files(query, scope="long_term")
        if "short_term" in selected:
            files += self.memory_service.search_files(query, scope="short_term", session_id=session_id)
        vectors = await self.memory_service.search_vector(
            query,
            source_id=session_id,
            source_types=["session_message", "session_summary"] if session_id else None,
        )
        return {"files": files, "vectors": vectors}

    def status_entry(self, *, user_id: str = DEFAULT_WEB_USER_ID, status: str | None = None) -> list[dict[str, Any]]:
        sessions = self.sessions.list_sessions(status=status or SessionStatus.ACTIVE, user_id=user_id)
        return [
            {
                "session_id": item["id"],
                "title": item.get("title") or "",
                "display_label": item.get("title") or "",
                "status": item["status"],
                "current_run_status": item.get("current_run_status") or "idle",
                "unread_events": 0,
                "last_active_at": item["last_active_at"],
            }
            for item in sessions
        ]

    def status_overview(
        self,
        *,
        user_id: str = DEFAULT_WEB_USER_ID,
        session_status: str | None = None,
        run_status: str | None = None,
    ) -> dict[str, Any]:
        sessions = self.sessions.list_sessions(status=session_status or SessionStatus.ACTIVE, user_id=user_id)
        result = []
        for session in sessions:
            current_main_run = self._get_current_main_run(session)
            if run_status and current_main_run is None:
                continue
            if run_status and current_main_run and current_main_run["status"] != run_status:
                continue
            child_runs = self._list_runs(parent_run_id=current_main_run["id"]) if current_main_run else []
            result.append(
                {
                    "session_id": session["id"],
                    "title": session.get("title") or "",
                    "status": session["status"],
                    "current_main_run": self._serialize_run_summary(current_main_run) if current_main_run else None,
                    "child_run_summary": self._build_child_run_summary(child_runs),
                    "last_event_at": (current_main_run or {}).get("ended_at") or (current_main_run or {}).get("started_at") or session["last_active_at"],
                }
            )
        return {"sessions": result}

    def status_session(self, session_id: str) -> dict[str, Any] | None:
        session = self.sessions.get_session(session_id)
        if session is None:
            return None
        current_main_run = self._get_current_main_run(session)
        runs = self._list_runs(session_id=session_id)
        return {
            "session_id": session_id,
            "title": session.get("title") or "",
            "current_main_run": self._serialize_run_summary(current_main_run) if current_main_run else None,
            "runs": [self._serialize_status_run(item) for item in runs],
        }

    def _resolve_session(
        self,
        *,
        task_mode: str,
        session_id: str | None,
        session_title: str | None,
        user_id: str,
    ) -> tuple[str, str]:
        normalized = task_mode or "auto"
        if normalized not in {"auto", "continue", "new_task", "cancel_and_rerun"}:
            raise ValueError("unsupported task_mode")

        if normalized == "continue":
            if not session_id:
                raise ValueError("session_id is required when task_mode=continue")
            self._require_active_session(session_id)
            self._require_no_running_main_run(session_id)
            return session_id, "continued"

        if normalized == "new_task":
            created = self.sessions.create_session(user_id, title=session_title)
            return created, "created_new"

        if normalized == "cancel_and_rerun":
            if not session_id:
                raise ValueError("session_id is required when task_mode=cancel_and_rerun")
            self._require_active_session(session_id)
            self._cancel_running_main_runs(session_id)
            return session_id, "cancelled_and_reran"

        if session_id:
            self._require_active_session(session_id)
            self._require_no_running_main_run(session_id)
            return session_id, "continued"

        active_sessions = self.sessions.list_sessions(status="active", user_id=user_id)
        if not active_sessions:
            created = self.sessions.create_session(user_id, title=session_title)
            return created, "created_new"
        if len(active_sessions) == 1:
            self._require_no_running_main_run(active_sessions[0]["id"])
            return active_sessions[0]["id"], "continued"
        raise ValueError("auto 模式存在多个活跃线程，请显式指定 session_id 或 task_mode")

    def _cancel_running_main_runs(self, session_id: str) -> None:
        rows = self._db.execute(
            "SELECT id FROM agent_runs WHERE session_id = ? AND agent_role = 'main' AND status = ?",
            (session_id, RunStatus.RUNNING.value),
        ).fetchall()
        run_ids = [row["id"] for row in rows]
        now = _utcnow()
        self._db.execute(
            """
            UPDATE agent_runs
            SET status = ?, ended_at = ?
            WHERE session_id = ? AND agent_role = 'main' AND status = ?
            """,
            (RunStatus.CANCELLED.value, now, session_id, RunStatus.RUNNING.value),
        )
        if run_ids:
            placeholders = ", ".join("?" for _ in run_ids)
            self._db.execute(
                f"UPDATE agent_runs SET status = ?, ended_at = ? WHERE parent_run_id IN ({placeholders}) AND status = ?",
                (RunStatus.CANCELLED.value, now, *run_ids, RunStatus.RUNNING.value),
            )
        self._db.commit()

    def _require_active_session(self, session_id: str) -> None:
        session = self.sessions.get_session(session_id)
        if session is None:
            raise ValueError("session not found")
        if session.get("status") != "active":
            raise ValueError("session is not active")

    def _has_running_main_run(self, session_id: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM agent_runs WHERE session_id = ? AND agent_role = 'main' AND status = ? LIMIT 1",
            (session_id, RunStatus.RUNNING.value),
        ).fetchone()
        return row is not None

    def _is_run_cancelled(self, run_id: str) -> bool:
        row = self._db.execute("SELECT status FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        return bool(row and row["status"] == RunStatus.CANCELLED.value)

    async def _wait_for_run_cancelled(self, run_id: str) -> None:
        while not self._is_run_cancelled(run_id):
            await asyncio.sleep(0.05)

    async def _emit_event(
        self,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
        payload: dict[str, Any],
    ) -> None:
        if event_callback is None:
            return
        await event_callback(payload)

    async def _build_memory_context(
        self,
        *,
        session_id: str,
        user_message: str,
        base_snapshot: str,
    ) -> str:
        parts: list[str] = []
        if self.memory_service is not None:
            long_term_entries = self.memory_service.list_long_term()
            if long_term_entries:
                lines = []
                for entry in long_term_entries:
                    line = f"- {entry.get('title') or entry['key']}: {entry.get('snippet', '')[:120].strip()}"
                    line += _staleness_tag(entry.get("mtime"))
                    lines.append(line)
                parts.append("长期记忆（索引摘要）:\n" + "\n".join(lines))

        if base_snapshot.strip():
            parts.append(f"会话摘要:\n{base_snapshot.strip()}")

        if self.memory_service is None:
            return "\n\n".join(parts)

        file_hits = self.memory_service.search_files(user_message, scope="short_term", session_id=session_id, limit=3)
        vector_hits = await self.memory_service.search_vector(
            user_message,
            top_k=3,
            source_types=["session_message", "session_summary"],
            source_id=session_id,
        )
        shared_vector_hits = await self.memory_service.search_vector(
            user_message,
            top_k=2,
            source_types=["principle_memory", "long_term_memory"],
        )

        if file_hits:
            parts.append(
                "近期记忆:\n" + "\n".join(
                    f"- {item['snippet'][:180].strip()}" for item in file_hits if item.get("snippet")
                )
            )
        if vector_hits:
            parts.append(
                "会话语义记忆:\n" + "\n".join(
                    f"- {item['text'][:180].strip()}" for item in vector_hits if item.get("text")
                )
            )
        if shared_vector_hits:
            parts.append(
                "共享语义记忆:\n" + "\n".join(
                    f"- {item['text'][:180].strip()}" for item in shared_vector_hits if item.get("text")
                )
            )
        return "\n\n".join(part for part in parts if part.strip())

    def _remember_session_message(self, session_id: str, role: str, content: str) -> None:
        if self.memory_service is None:
            return
        try:
            payload = f"{role}: {content}"
            self.memory_service.save_short_term(session_id, payload)
            background = asyncio.create_task(
                self.memory_service.save_vector(
                    payload,
                    source_type="session_message",
                    source_id=session_id,
                    metadata={"role": role, "session_id": session_id},
                )
            )
            self._background_tasks.add(background)
            background.add_done_callback(self._background_tasks.discard)
        except Exception as exc:
            logger.warning("remember_session_message_failed", session_id=session_id, error=str(exc))

    def _resolve_requested_skill_context(self, skill_name: str | None) -> dict[str, Any]:
        if not skill_name:
            return {"activated_skills": [], "runtime_context": {}, "max_steps": 10}
        if self.skill_service is None:
            raise ValueError("skill service is not configured")
        try:
            activated = self.skill_service.activate_skill(skill_name)
        except SkillRegistryError as exc:
            raise ValueError(exc.message) from exc
        return {
            "requested_skill_name": skill_name,
            "activated_skills": [activated],
            "runtime_context": {
                "activated_skills": [skill_name],
                "skill_tool_allowlist_active": True,
                "skill_tool_allowlist": sorted(set(activated.get("allowed_tools") or [])),
            },
            "input_schema": {},
            "output_schema": {},
            "max_steps": 10,
        }

    def _list_available_skills_catalog(self) -> list[dict[str, Any]]:
        if self.skill_service is None:
            return []
        return self.skill_service.list_catalog(status="enabled")

    def _parse_slash_command(self, message: str) -> tuple[str | None, str]:
        """解析斜杠命令（FR-015）。返回 (skill_name, 剩余消息)。

        如果 skill 不存在或已禁用，返回 (None, 错误提示)。
        """
        text = message[1:].strip()
        if not text:
            return None, "斜杠命令格式：/skill-name 或 /skill-name 参数文本"

        parts = text.split(None, 1)
        token = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        # /skill 或 /skill create → create-skill 元技能
        if token == "skill" or token == "create":
            return "create-skill", args or "请引导我创建一个新的 Skill。"

        # 查找已启用的 Skill
        if self.skill_service is None:
            return None, f"Skill 服务未配置，无法执行 /{token}"

        catalog = self.skill_service.list_catalog(status="enabled")
        matched = [s for s in catalog if (s.get("skill_name") or s.get("name") or "").lower() == token]
        if not matched:
            available = ", ".join(s.get("skill_name") or s.get("name") or "" for s in catalog)
            hint = f"（可用 Skill：{available}）" if available else "（当前无可用 Skill）"
            return None, f"Skill `{token}` 不存在或未启用。{hint}"

        return matched[0].get("skill_name") or token, args

    def _extract_activated_skills(
        self,
        steps: list[Any],
        *,
        initial: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ordered: dict[str, dict[str, Any]] = {}
        for item in initial:
            name = item.get("skill_name")
            if name:
                ordered[name] = item
        for step in steps:
            if getattr(step, "action", "") != "activate_skill":
                continue
            observation = getattr(step, "observation", "")
            payload = _from_json(observation, default={})
            if not isinstance(payload, dict):
                continue
            name = payload.get("skill_name")
            if name:
                ordered[str(name)] = payload
        return list(ordered.values())

    def _build_tool_runtime(self, allowed_tools: list[str] | None) -> tuple[dict[str, dict[str, Any]], ToolExecutor]:
        if allowed_tools is None:
            return self.tool_registry.get_tool_defs(), self.tool_executor

        filtered_registry = self.tool_registry.clone_filtered(allowed_tools)
        return filtered_registry.get_tool_defs(), ToolExecutor(filtered_registry)

    def _validate_skill_input(self, skill_context: dict[str, Any], message: str) -> None:
        schema = skill_context.get("input_schema") or {}
        if not schema:
            return
        try:
            jsonschema_validate(instance={"message": message}, schema=schema)
        except JSONSchemaValidationError as exc:
            raise ValueError(f"skill 输入不符合 schema: {exc.message}") from exc

    def _apply_skill_output_validation(self, *, result: Any, skill_context: dict[str, Any]) -> Any:
        schema = skill_context.get("output_schema") or {}
        if not schema or result.status != RunStatus.SUCCESS:
            return result
        try:
            payload = json.loads(result.reply)
            jsonschema_validate(instance=payload, schema=schema)
            return result
        except (json.JSONDecodeError, JSONSchemaValidationError) as exc:
            result.status = RunStatus.FAILED
            result.error_code = ErrorCode.SCHEMA_VALIDATION_FAILED
            result.reply = f"Skill 输出不符合 schema: {str(exc)}"
            return result

    def _estimate_cost(self, *, model_name: str, input_tokens: int, output_tokens: int) -> float:
        pricing = {
            "gpt-4.1": (2.0, 8.0),
            "gpt-4o": (5.0, 15.0),
            "gpt-4o-mini": (0.15, 0.6),
            "claude-3-5-sonnet": (3.0, 15.0),
            "claude-3-7-sonnet": (3.0, 15.0),
        }
        input_rate = 0.0
        output_rate = 0.0
        lowered = (model_name or "").lower()
        for prefix, rates in pricing.items():
            if lowered.startswith(prefix):
                input_rate, output_rate = rates
                break
        return round((input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate, 6)

    def _create_tool_approval(
        self,
        *,
        session_id: str,
        run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        resume_state: dict[str, Any],
    ) -> dict[str, Any]:
        approval_id = str(uuid.uuid4())
        now = _utcnow()
        row = {
            "id": approval_id,
            "agent_run_id": run_id,
            "session_id": session_id,
            "tool_name": tool_name,
            "arguments": _to_json(arguments),
            "status": "pending",
            "operator": None,
            "resume_state": _to_json(resume_state),
            "created_at": now,
            "updated_at": now,
        }
        self._db.execute(
            """
            INSERT INTO tool_approvals (
                id, agent_run_id, session_id, tool_name, arguments, status, operator, resume_state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["agent_run_id"],
                row["session_id"],
                row["tool_name"],
                row["arguments"],
                row["status"],
                row["operator"],
                row["resume_state"],
                row["created_at"],
                row["updated_at"],
            ),
        )
        self._db.commit()
        return self._serialize_tool_approval(row)

    async def _resume_tool_approval(self, approval_id: str) -> None:
        approval_row = self._db.execute("SELECT * FROM tool_approvals WHERE id = ?", (approval_id,)).fetchone()
        if approval_row is None:
            return
        approval = dict(approval_row)
        run_row = self._db.execute("SELECT * FROM agent_runs WHERE id = ?", (approval["agent_run_id"],)).fetchone()
        if run_row is None:
            return
        run = self._row_to_run(run_row)
        session_id = approval.get("session_id") or run.get("session_id")
        if not session_id:
            return

        resume_state = _from_json(approval.get("resume_state"), default={})
        context_ref = run.get("context_ref") or {}
        requested_skill_name = (
            run.get("skill_id")
            or context_ref.get("requested_skill_name")
        )
        task_id = context_ref.get("task_id")
        task_run_log_id = context_ref.get("task_run_log_id")
        skill_context = self._resolve_requested_skill_context(requested_skill_name)
        available_skills_catalog = self._list_available_skills_catalog()
        llm = self.model_router.get_primary()
        tool_defs, tool_executor = self._build_tool_runtime(None)
        main_agent = MainAgent(
            llm,
            tools=tool_defs,
            tool_executor=tool_executor,
            max_steps=int(skill_context.get("max_steps") or 10),
            hook_registry=self._hook_registry,
        )
        result = await main_agent.chat(
            "",
            history=[],
            available_skills_catalog=available_skills_catalog,
            activated_skills=skill_context.get("activated_skills") or None,
            principle="",
            long_term_context="",
            short_term_context="",
            run_id=run["id"],
            cancellation_checker=lambda: self._is_run_cancelled(run["id"]),
            cancellation_waiter=lambda: self._wait_for_run_cancelled(run["id"]),
            resume_state=resume_state,
            approved_approval={
                "status": approval["status"],
                "approval_id": approval_id,
                "tool_name": approval["tool_name"],
                "arguments": _from_json(approval.get("arguments"), default={}),
            },
            runtime_context=skill_context.get("runtime_context") or None,
        )
        result = self._apply_skill_output_validation(result=result, skill_context=skill_context)
        activated_skills = self._extract_activated_skills(
            result.steps,
            initial=skill_context.get("activated_skills") or [],
        )
        activated_skill_names = [item.get("skill_name") for item in activated_skills if item.get("skill_name")]

        if result.pending_approval and result.resume_state:
            next_approval = self._create_tool_approval(
                session_id=session_id,
                run_id=run["id"],
                tool_name=str(result.pending_approval.get("tool_name", "")),
                arguments=result.pending_approval.get("arguments", {}),
                resume_state=result.resume_state,
            )
            self._update_run_progress(
                run["id"],
                steps_count=len(result.steps),
                result_ref={
                    "reply": f"工具 {next_approval['tool_name']} 正在等待审批。",
                    "latest_error": ErrorCode.TOOL_APPROVAL_PENDING,
                    "pending_approval": next_approval,
                },
                activated_skills=activated_skill_names,
            )
            self.sessions.add_message(
                session_id,
                "assistant",
                f"工具 {next_approval['tool_name']} 正在等待审批。",
                run_id=run["id"],
                metadata={
                    "run_status": RunStatus.RUNNING.value,
                    "pending_approval": next_approval,
                    "requested_skill_name": requested_skill_name,
                    "activated_skills": activated_skill_names,
                },
            )
            self._remember_session_message(session_id, "assistant", f"工具 {next_approval['tool_name']} 正在等待审批。")
            if task_id:
                self._update_task_runtime_state(
                    task_id=task_id,
                    session_id=session_id,
                    run_id=run["id"],
                    run_log_id=str(task_run_log_id) if task_run_log_id else None,
                    result_status="waiting_approval",
                    reply=f"工具 {next_approval['tool_name']} 正在等待审批。",
                    pending_approval=next_approval,
                )
            return

        if result.status == RunStatus.CANCELLED or self._is_run_cancelled(run["id"]):
            if task_id:
                self._update_task_runtime_state(
                    task_id=task_id,
                    session_id=session_id,
                    run_id=run["id"],
                    run_log_id=str(task_run_log_id) if task_run_log_id else None,
                    result_status=RunStatus.CANCELLED.value,
                    reply="该运行已被取消，线程已切换到新的主运行。",
                )
            return

        estimated_cost = self._estimate_cost(
            model_name=getattr(llm, "model", ""),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        persisted = self._persist_run_runtime_artifacts(
            session_id=session_id,
            run_id=run["id"],
            steps=result.steps,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost=estimated_cost,
            model_name=getattr(llm, "model", ""),
            task_id=task_id,
        )
        if not persisted:
            return

        self._update_run(
            run["id"],
            status=result.status.value,
            steps_count=len(result.steps),
            result_ref={
                "reply": result.reply,
                "steps": [step.model_dump() for step in result.steps],
                "latest_error": result.error_code,
                "requested_skill_name": requested_skill_name,
                "task_id": task_id,
                "activated_skills": activated_skill_names,
            },
            activated_skills=activated_skill_names,
        )
        self.sessions.add_message(
            session_id,
            "assistant",
            result.reply,
            run_id=run["id"],
            metadata={
                "session_action": "approval_resumed",
                "run_status": result.status.value,
                "steps": [step.model_dump() for step in result.steps],
                "requested_skill_name": requested_skill_name,
                "task_id": task_id,
                "activated_skills": activated_skill_names,
            },
            guard_run_not_cancelled=True,
        )
        self._remember_session_message(session_id, "assistant", result.reply)

        if task_id:
            self._update_task_runtime_state(
                task_id=task_id,
                session_id=session_id,
                run_id=run["id"],
                run_log_id=str(task_run_log_id) if task_run_log_id else None,
                result_status=result.status.value,
                reply=result.reply,
            )

        if task_id and result.status in {RunStatus.FAILED, RunStatus.TIMEOUT}:
            await self._notify_task_failure(task_id=task_id, session_id=session_id, reply=result.reply)

    async def _notify_task_failure(self, *, task_id: str, session_id: str, reply: str) -> None:
        if self.notification_service is None:
            return
        session = self.sessions.get_session(session_id) if session_id and session_id != "-" else None
        channel_type = (session or {}).get("channel_type") or "test"
        target_uid = (session or {}).get("user_id") or DEFAULT_WEB_USER_ID
        try:
            await self.notification_service.notify(
                channel_type=channel_type,
                target_uid=target_uid,
                content=f"任务 {task_id} 执行失败，线程 {session_id}：{reply}",
            )
        except Exception as exc:
            logger.warning("task_failure_notification_failed", task_id=task_id, error=str(exc))

    def _update_task_runtime_state(
        self,
        *,
        task_id: str,
        session_id: str,
        run_id: str,
        run_log_id: str | None,
        result_status: str,
        reply: str,
        pending_approval: dict[str, Any] | None = None,
    ) -> None:
        task_row = self._db.execute(
            "SELECT schedule_type FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if task_row is None:
            return

        now = _utcnow()
        last_result: dict[str, Any] = {
            "reply": reply,
            "run_id": run_id,
            "status": result_status,
        }
        if pending_approval:
            last_result["pending_approval"] = pending_approval

        updates = [
            "session_id = ?",
            "last_run_at = ?",
            "last_result = ?",
            "updated_at = ?",
        ]
        values: list[Any] = [session_id, now, _to_json(last_result), now]
        if pending_approval is None and task_row["schedule_type"] == "once":
            updates.extend(["status = ?", "next_run_at = ?"])
            values.extend([TaskStatus.COMPLETED.value, None])
        values.append(task_id)
        self._db.execute(
            f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
            values,
        )

        if run_log_id:
            ended_at = None if pending_approval else now
            error_category = None
            error_detail = None
            if pending_approval is None and result_status in {RunStatus.FAILED.value, RunStatus.TIMEOUT.value}:
                error_category = "task_execution"
                error_detail = reply
            elif pending_approval is None and result_status == RunStatus.CANCELLED.value:
                error_category = "task_cancelled"
                error_detail = reply
            self._db.execute(
                "UPDATE run_logs SET ended_at = ?, status = ?, error_category = ?, error_detail = ? WHERE id = ?",
                (ended_at, result_status, error_category, error_detail, run_log_id),
            )
        self._db.commit()

    def _require_no_running_main_run(self, session_id: str) -> None:
        if self._has_running_main_run(session_id):
            raise ValueError("当前线程已有运行中的主 Agent，请等待完成或使用 cancel_and_rerun")

    def _create_run(
        self,
        *,
        session_id: str,
        agent_role: str,
        status: str,
        task_ref: str = "",
        parent_run_id: str | None = None,
        context_ref: dict[str, Any] | None = None,
        skill_id: str | None = None,
        activated_skills: list[str] | None = None,
    ) -> str:
        run_id = str(uuid.uuid4())
        self._db.execute(
            """
            INSERT INTO agent_runs (
                id, parent_run_id, agent_role, skill_id, activated_skills, session_id, task_ref,
                context_ref, started_at, status, steps_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                parent_run_id,
                agent_role,
                skill_id,
                _to_json(activated_skills),
                session_id,
                task_ref,
                _to_json(context_ref),
                _utcnow(),
                status,
                0,
            ),
        )
        self._db.commit()
        return run_id

    def _update_run_progress(
        self,
        run_id: str,
        *,
        steps_count: int,
        result_ref: dict[str, Any] | None = None,
        activated_skills: list[str] | None = None,
    ) -> None:
        current = self._db.execute("SELECT status FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if current and current["status"] == RunStatus.CANCELLED.value:
            return
        self._db.execute(
            "UPDATE agent_runs SET steps_count = ?, result_ref = ?, activated_skills = ? WHERE id = ?",
            (steps_count, _to_json(result_ref), _to_json(activated_skills), run_id),
        )
        self._db.commit()

    def _update_run(
        self,
        run_id: str,
        *,
        status: str,
        steps_count: int,
        result_ref: dict[str, Any] | None = None,
        activated_skills: list[str] | None = None,
    ) -> None:
        current = self._db.execute("SELECT status FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if current and current["status"] == RunStatus.CANCELLED.value and status != RunStatus.CANCELLED.value:
            logger.info("skip_update_cancelled_run", run_id=run_id, attempted_status=status)
            return
        self._db.execute(
            """
            UPDATE agent_runs
            SET status = ?, ended_at = ?, steps_count = ?, result_ref = ?, activated_skills = ?
            WHERE id = ?
            """,
            (status, _utcnow(), steps_count, _to_json(result_ref), _to_json(activated_skills), run_id),
        )
        self._db.commit()

    def _list_runs(
        self,
        *,
        session_id: str | None = None,
        agent_role: str | None = None,
        parent_run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM agent_runs WHERE 1=1"
        params: list[Any] = []
        if session_id is not None:
            query += " AND session_id = ?"
            params.append(session_id)
        if agent_role is not None:
            query += " AND agent_role = ?"
            params.append(agent_role)
        if parent_run_id is not None:
            query += " AND parent_run_id = ?"
            params.append(parent_run_id)
        query += " ORDER BY started_at DESC"
        rows = self._db.execute(query, params).fetchall()
        return [self._row_to_run(row) for row in rows]

    def _get_current_main_run(self, session: dict[str, Any]) -> dict[str, Any] | None:
        current_run_id = session.get("current_run_id")
        if current_run_id:
            row = self._db.execute("SELECT * FROM agent_runs WHERE id = ?", (current_run_id,)).fetchone()
            if row:
                return self._row_to_run(row)
        rows = self._list_runs(session_id=session["id"], agent_role="main")
        return rows[0] if rows else None

    def _build_history(self, session_id: str) -> list[ChatMessage]:
        """构建消息历史，使用滑动窗口 + 前文摘要（Spec FR-013）。"""
        recent_messages, older_summary = self.sessions.get_context_for_llm(session_id, recent_n=10)
        history: list[ChatMessage] = []
        if older_summary:
            history.append(ChatMessage(role="system", content=f"[历史对话摘要]\n{older_summary}"))
        for item in recent_messages:
            if item["role"] not in {"user", "assistant"}:
                continue
            history.append(ChatMessage(role=item["role"], content=item["content"]))
        return history

    def _ensure_main_run_capacity(self) -> None:
        """检查当前用户活跃的顶层主 Agent 运行数量是否已达上限。"""
        running_count = self._db.execute(
            "SELECT COUNT(*) AS total FROM agent_runs WHERE agent_role = 'main' AND parent_run_id IS NULL AND status = ?",
            (RunStatus.RUNNING.value,),
        ).fetchone()
        if running_count and int(running_count["total"]) >= self._max_parallel_main_runs:
            raise ValueError(f"当前已有 {self._max_parallel_main_runs} 个主 Agent 并行运行中，请等待完成或取消后再试")

    def _should_spawn_sub_agent(self, message: str) -> bool:
        return any(keyword in message for keyword in ["分析", "调研", "比较", "拆解", "汇总"])

    async def _run_sub_agent(
        self,
        *,
        parent_run_id: str,
        session_id: str,
        goal: str,
        session_title: str,
    ) -> dict[str, Any]:
        async with self._sub_agent_semaphore:
            return await self._run_sub_agent_inner(
                parent_run_id=parent_run_id,
                session_id=session_id,
                goal=goal,
                session_title=session_title,
            )

    async def _run_sub_agent_inner(
        self,
        *,
        parent_run_id: str,
        session_id: str,
        goal: str,
        session_title: str,
    ) -> dict[str, Any]:
        child_run_id = self._create_run(
            session_id=session_id,
            agent_role="sub",
            status=RunStatus.RUNNING.value,
            parent_run_id=parent_run_id,
            task_ref=goal[:120],
            context_ref={"display_name": "子 Agent · 分析", "goal": goal},
        )
        llm = self.model_router.get_primary()
        executor = SubAgentExecutor(llm, tool_executor=self.tool_executor)
        response = await executor.run(
            SubAgentRequest(
                run_id=child_run_id,
                parent_run_id=parent_run_id,
                sub_agent_role="analyst",
                goal=goal,
                context_pack={"session_title": session_title or "未命名会话"},
            ),
            cancellation_checker=lambda: self._is_run_cancelled(child_run_id),
            cancellation_waiter=lambda: self._wait_for_run_cancelled(child_run_id),
        )
        if response.status == RunStatus.CANCELLED or self._is_run_cancelled(child_run_id):
            row = self._db.execute("SELECT * FROM agent_runs WHERE id = ?", (child_run_id,)).fetchone()
            return self._row_to_run(row) if row else {"id": child_run_id, "status": RunStatus.CANCELLED.value}
        if response.usage:
            persisted = self._record_usage_for_active_run(
                session_id=session_id,
                agent_run_id=child_run_id,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                estimated_cost=response.usage.estimated_cost,
                model_name=getattr(llm, "model", ""),
            )
            if not persisted:
                row = self._db.execute("SELECT * FROM agent_runs WHERE id = ?", (child_run_id,)).fetchone()
                return self._row_to_run(row) if row else {"id": child_run_id, "status": RunStatus.CANCELLED.value}
        self._update_run(
            child_run_id,
            status=response.status.value,
            steps_count=int(response.output.get("steps_count", 0)),
            result_ref={
                "output": response.output,
                "reply": response.output.get("reply", ""),
                "latest_error": response.error.message if response.error else None,
            },
        )
        row = self._db.execute("SELECT * FROM agent_runs WHERE id = ?", (child_run_id,)).fetchone()
        return self._row_to_run(row) if row else {"id": child_run_id, "status": response.status.value}

    def _persist_run_runtime_artifacts(
        self,
        *,
        session_id: str,
        run_id: str,
        steps: list[Any],
        input_tokens: int,
        output_tokens: int,
        estimated_cost: float,
        model_name: str,
        task_id: str | None = None,
    ) -> bool:
        try:
            self._db.execute("BEGIN IMMEDIATE")
            if not self._run_is_active_locked(run_id):
                self._db.rollback()
                return False

            for step in steps:
                if not getattr(step, "action", ""):
                    continue
                observation = getattr(step, "observation", "")
                self._db.execute(
                    """
                    INSERT INTO tool_calls (id, agent_run_id, tool_name, parameters, result, duration_ms, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        run_id,
                        step.action,
                        _to_json(getattr(step, "action_input", {})),
                        observation,
                        None,
                        "failed" if str(observation).startswith("Error:") else "success",
                        _utcnow(),
                    ),
                )

            self.cost.record_usage(
                task_id=task_id,
                session_id=session_id,
                agent_run_id=run_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=estimated_cost,
                model_name=model_name,
                commit=False,
            )
            self._db.commit()
            return True
        except Exception:
            self._db.rollback()
            raise

    def _record_usage_for_active_run(
        self,
        *,
        session_id: str,
        agent_run_id: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost: float,
        model_name: str,
        task_id: str | None = None,
    ) -> bool:
        try:
            self._db.execute("BEGIN IMMEDIATE")
            if not self._run_is_active_locked(agent_run_id):
                self._db.rollback()
                return False

            self.cost.record_usage(
                task_id=task_id,
                session_id=session_id,
                agent_run_id=agent_run_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=estimated_cost,
                model_name=model_name,
                commit=False,
            )
            self._db.commit()
            return True
        except Exception:
            self._db.rollback()
            raise

    def _run_is_active_locked(self, run_id: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM agent_runs WHERE id = ? AND status != ?",
            (run_id, RunStatus.CANCELLED.value),
        ).fetchone()
        return row is not None

    def _build_cancelled_payload(
        self,
        *,
        session_id: str,
        run_id: str,
        session_action: str,
        steps: list[dict[str, Any]],
        input_tokens: int,
        output_tokens: int,
        model_name: str,
    ) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "run_id": run_id,
            "session_action": session_action,
            "status": RunStatus.CANCELLED.value,
            "reply": "该运行已被取消，线程已切换到新的主运行。",
            "steps": steps,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_cost": 0.0,
                "model": model_name,
            },
        }

    def _build_child_run_summary(self, child_runs: list[dict[str, Any]]) -> dict[str, Any]:
        summary = {
            "total": len(child_runs),
            "queued": 0,
            "running": 0,
            "success": 0,
            "failed": 0,
            "timeout": 0,
            "cancelled": 0,
            "latest_error": None,
        }
        for item in child_runs:
            status = item.get("status") or ""
            if status in summary:
                summary[status] += 1
            latest_error = _dig(item, "result_ref", "latest_error")
            if latest_error:
                summary["latest_error"] = latest_error
        return summary

    def _serialize_session_summary(self, session: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": session["id"],
            "title": session.get("title") or "",
            "user_id": session["user_id"],
            "channel": session["channel_type"],
            "status": session["status"],
            "current_run_status": session.get("current_run_status") or "idle",
            "active_child_runs": session.get("active_child_runs") or 0,
            "created_at": session["created_at"],
            "last_active_at": session["last_active_at"],
            "message_count": session.get("message_count") or 0,
        }

    def _serialize_message(self, message: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": message["role"],
            "content": message["content"],
            "timestamp": message["created_at"],
            "run_id": message.get("run_id"),
            "metadata": message.get("metadata") or {},
        }

    def _serialize_run_summary(self, run: dict[str, Any] | None) -> dict[str, Any] | None:
        if run is None:
            return None
        return {
            "run_id": run["id"],
            "parent_run_id": run.get("parent_run_id"),
            "agent_role": run["agent_role"],
            "status": run["status"],
            "started_at": run["started_at"],
            "ended_at": run.get("ended_at"),
            "steps_count": run.get("steps_count") or 0,
            "task_ref": run.get("task_ref") or "",
            "reply_preview": (_dig(run, "result_ref", "reply") or "")[:120],
            "activated_skills": run.get("activated_skills") or [],
        }

    def _serialize_status_run(self, run: dict[str, Any]) -> dict[str, Any]:
        context_ref = run.get("context_ref") or {}
        result_ref = run.get("result_ref") or {}
        display_name = context_ref.get("display_name")
        if not display_name:
            display_name = "Main Agent" if run["agent_role"] == "main" else "Sub Agent"
        return {
            "run_id": run["id"],
            "parent_run_id": run.get("parent_run_id"),
            "agent_role": run["agent_role"],
            "display_name": display_name,
            "status": run["status"],
            "progress_text": _progress_text(run["status"]),
            "started_at": run["started_at"],
            "ended_at": run.get("ended_at"),
            "latest_event": (result_ref.get("reply") or run.get("task_ref") or "")[:120],
            "latest_error": result_ref.get("latest_error"),
        }

    def _serialize_tool_approval(self, approval: dict[str, Any]) -> dict[str, Any]:
        return {
            "approval_id": approval["id"],
            "run_id": approval["agent_run_id"],
            "session_id": approval.get("session_id"),
            "tool_name": approval["tool_name"],
            "arguments": _from_json(approval.get("arguments"), default={}),
            "status": approval["status"],
            "operator": approval.get("operator"),
            "created_at": approval["created_at"],
            "updated_at": approval["updated_at"],
        }

    def _row_to_run(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["context_ref"] = _from_json(item.get("context_ref"), default={})
        item["result_ref"] = _from_json(item.get("result_ref"), default={})
        item["activated_skills"] = _from_json(item.get("activated_skills"), default=[])
        return item


def _progress_text(status: str) -> str:
    mapping = {
        "queued": "等待执行",
        "running": "执行中",
        "success": "已完成",
        "failed": "执行失败",
        "timeout": "执行超时",
        "cancelled": "已取消",
    }
    return mapping.get(status, status or "未知状态")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_json(value: dict[str, Any] | list[Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _from_json(raw: str | None, *, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _dig(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _staleness_tag(mtime: float | None) -> str:
    """根据文件修改时间生成时效性标注。"""
    if mtime is None:
        return ""
    import time
    days = int((time.time() - mtime) / 86400)
    if days == 0:
        return " 最后更新: 今天"
    if days < 30:
        return f" 最后更新: {days}天前"
    return f" 最后更新: {days}天前 [可能过时]"
