"""HTTP API 路由 — 对应 SPEC §9。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.services.agent_service import DEFAULT_WEB_USER_ID, AgentService
from src.services.skill_service import SkillService

router = APIRouter(prefix="/api/v1")


# ── 请求/响应模型 ───────────────────────────────────────

class CreateSkillDraftRequest(BaseModel):
    requested_action: Literal["create", "update"]
    target_skill_name: str | None = None
    proposed_name: str | None = None
    draft_skill_md: str | None = None
    draft_resources_manifest: list[dict[str, Any]] = Field(default_factory=list)
    source_run_id: str | None = None
    source_session_id: str | None = None
    operator: str = DEFAULT_WEB_USER_ID
    user_intent_summary: str = ""


class UpdateSkillDraftRequest(BaseModel):
    proposed_name: str | None = None
    draft_skill_md: str | None = None
    draft_resources_manifest: list[dict[str, Any]] | None = None
    user_intent_summary: str | None = None


class ReviewSkillDraftRequest(BaseModel):
    reviewer: str = DEFAULT_WEB_USER_ID
    note: str = ""


class SkillActionRequest(BaseModel):
    action: Literal["enable", "disable", "rollback"]
    operator: str = DEFAULT_WEB_USER_ID
    reason: str = ""
    target_revision: str | None = None


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    task_mode: Literal["auto", "continue", "new_task", "cancel_and_rerun"] = "auto"
    session_title: str | None = None
    stream: bool = False
    user_id: str = DEFAULT_WEB_USER_ID
    requested_skill_name: str | None = None
    task_id: str | None = None


class ToolApprovalRequest(BaseModel):
    decision: str  # approved | rejected
    operator: str = DEFAULT_WEB_USER_ID


class CreateSessionRequest(BaseModel):
    user_id: str = DEFAULT_WEB_USER_ID
    title: str | None = None
    channel_type: str = "web"


class CreateTaskRequest(BaseModel):
    title: str
    prompt: str
    schedule_text: str
    requested_skill_name: str | None = None


class CloseSessionRequest(BaseModel):
    summary: str = ""


# ── 健康检查 ────────────────────────────────────────────

@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── Skill Catalog / Review API — SPEC 9.1 ─────────────────

@router.post("/skills/reload")
async def reload_skill_catalog(request: Request) -> dict[str, Any]:
    return _skill_service(request).reload_catalog()


@router.get("/skills")
async def list_skills(
    request: Request,
    status: str | None = None,
    source: str | None = None,
    keyword: str | None = None,
) -> list[dict[str, Any]]:
    return _skill_service(request).list_catalog(status=status, source=source, keyword=keyword)


@router.get("/skills/audit")
async def list_skill_audit(
    request: Request,
    skill_name: str | None = None,
    draft_id: str | None = None,
    action: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return _skill_service(request).list_audit(skill_name=skill_name, draft_id=draft_id, action=action, limit=limit)


@router.get("/skills/migrations")
async def list_skill_migrations(request: Request) -> list[dict[str, Any]]:
    return _skill_service(request).list_migration_candidates()


@router.post("/skills/drafts")
async def create_skill_draft(req: CreateSkillDraftRequest, request: Request) -> dict[str, Any]:
    try:
        return _skill_service(request).create_draft(
            requested_action=req.requested_action,
            target_skill_name=req.target_skill_name,
            proposed_name=req.proposed_name,
            draft_skill_md=req.draft_skill_md,
            draft_resources_manifest=req.draft_resources_manifest,
            source_run_id=req.source_run_id,
            source_session_id=req.source_session_id,
            operator=req.operator,
            user_intent_summary=req.user_intent_summary,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/skills/drafts")
async def list_skill_drafts(
    request: Request,
    review_status: str | None = None,
    skill_name: str | None = None,
) -> list[dict[str, Any]]:
    return _skill_service(request).list_drafts(review_status=review_status, skill_name=skill_name)


@router.get("/skills/drafts/{draft_id}")
async def get_skill_draft(draft_id: str, request: Request) -> dict[str, Any]:
    draft = _skill_service(request).get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Skill draft not found")
    return draft


@router.patch("/skills/drafts/{draft_id}")
async def update_skill_draft(draft_id: str, req: UpdateSkillDraftRequest, request: Request) -> dict[str, Any]:
    try:
        draft = _skill_service(request).update_draft(
            draft_id,
            proposed_name=req.proposed_name,
            draft_skill_md=req.draft_skill_md,
            draft_resources_manifest=req.draft_resources_manifest,
            user_intent_summary=req.user_intent_summary,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if draft is None:
        raise HTTPException(status_code=404, detail="Skill draft not found")
    return draft


@router.post("/skills/drafts/{draft_id}/approve")
async def approve_skill_draft(draft_id: str, req: ReviewSkillDraftRequest, request: Request) -> dict[str, Any]:
    try:
        approved = _skill_service(request).approve_draft(draft_id, reviewer=req.reviewer, change_note=req.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if approved is None:
        raise HTTPException(status_code=404, detail="Skill draft not found")
    return approved


@router.post("/skills/drafts/{draft_id}/reject")
async def reject_skill_draft(draft_id: str, req: ReviewSkillDraftRequest, request: Request) -> dict[str, Any]:
    rejected = _skill_service(request).reject_draft(draft_id, reviewer=req.reviewer, review_note=req.note)
    if rejected is None:
        raise HTTPException(status_code=404, detail="Skill draft not found")
    return rejected


@router.post("/skills/{skill_name}/actions")
async def skill_action(skill_name: str, req: SkillActionRequest, request: Request) -> dict[str, Any]:
    try:
        result = _skill_service(request).perform_action(
            skill_name,
            action=req.action,
            operator=req.operator,
            reason=req.reason,
            target_revision=req.target_revision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Skill or revision not found")
    return result


@router.get("/skills/{skill_name}/revisions")
async def list_skill_revisions(skill_name: str, request: Request) -> list[dict[str, Any]]:
    return _skill_service(request).list_revisions(skill_name)


@router.get("/skills/{skill_name}")
async def get_skill(skill_name: str, request: Request) -> dict[str, Any]:
    skill = _skill_service(request).get_catalog_entry(skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


# ── Agent 执行 API — SPEC 9.5 ──────────────────────────

@router.post("/agent/chat")
async def agent_chat(req: ChatRequest, request: Request):
    if req.stream:
        async def event_stream():
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            finished = asyncio.Event()

            async def push_event(payload: dict[str, Any]) -> None:
                event_name = str(payload.get("event") or "message")
                await queue.put(
                    {
                        "event": event_name,
                        "payload": {key: value for key, value in payload.items() if key != "event"},
                    }
                )

            async def run_chat() -> None:
                await queue.put({"event": "thinking", "payload": {"content": "任务已接收，正在启动 Agent。"}})
                try:
                    result = await _agent_service(request).chat(
                        message=req.message,
                        session_id=req.session_id,
                        task_mode=req.task_mode,
                        session_title=req.session_title,
                        stream=req.stream,
                        user_id=req.user_id,
                        requested_skill_name=req.requested_skill_name,
                        task_id=req.task_id,
                        event_callback=push_event,
                    )
                except ValueError as exc:
                    await queue.put({"event": "reply", "payload": {"content": f"请求失败：{str(exc)}"}})
                    await queue.put({"event": "done", "payload": {"error": True}})
                    return
                except Exception as exc:
                    await queue.put({"event": "reply", "payload": {"content": f"服务器错误：{str(exc)}"}})
                    await queue.put({"event": "done", "payload": {"error": True}})
                    return

                if result.get("pending_approval"):
                    await queue.put({"event": "reply", "payload": {"content": result.get("reply", "")}})
                await queue.put({"event": "usage", "payload": result.get("usage", {})})
                await queue.put(
                    {
                        "event": "done",
                        "payload": {
                            "run_id": result.get("run_id"),
                            "session_id": result.get("session_id"),
                            "pending_approval": result.get("pending_approval"),
                        },
                    }
                )
                
                
            
            async def run_chat_with_finish() -> None:
                try:
                    await run_chat()
                finally:
                    finished.set()

            worker = asyncio.create_task(run_chat_with_finish())
            try:
                while not (finished.is_set() and queue.empty()):
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=10)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield _format_sse(item["event"], item["payload"])
            finally:
                await worker

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    try:
        result = await _agent_service(request).chat(
            message=req.message,
            session_id=req.session_id,
            task_mode=req.task_mode,
            session_title=req.session_title,
            stream=req.stream,
            user_id=req.user_id,
            requested_skill_name=req.requested_skill_name,
            task_id=req.task_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.get("/agent/runs/{run_id}")
async def get_agent_run(run_id: str, request: Request) -> dict[str, Any]:
    run = _agent_service(request).get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


# ── 工具管理 API — SPEC 9.6 ────────────────────────────

@router.get("/tools")
async def list_tools(request: Request, category: str | None = None) -> list[dict[str, Any]]:
    return _agent_service(request).list_tools(category=category)


@router.get("/tools/approvals")
async def list_tool_approvals(request: Request, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    return _agent_service(request).list_tool_approvals(status=status, limit=limit)


@router.post("/tools/approvals/{approval_id}")
async def tool_approval(approval_id: str, req: ToolApprovalRequest, request: Request) -> dict[str, Any]:
    try:
        return await _agent_service(request).decide_tool_approval(
            approval_id,
            decision=req.decision,
            operator=req.operator,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── 会话管理 API — SPEC 9.7 ────────────────────────────

@router.post("/sessions")
async def create_session(req: CreateSessionRequest, request: Request) -> dict[str, Any]:
    try:
        return _agent_service(request).create_session(
            user_id=req.user_id,
            title=req.title,
            channel_type=req.channel_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sessions")
async def list_sessions(request: Request, status: str | None = None, user_id: str = DEFAULT_WEB_USER_ID) -> list[dict[str, Any]]:
    return _agent_service(request).list_sessions(status=status, user_id=user_id)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> dict[str, Any]:
    session = _agent_service(request).get_session_detail(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/sessions/{session_id}/close")
async def close_session(session_id: str, request: Request, req: CloseSessionRequest | None = None) -> dict[str, Any]:
    result = _agent_service(request).close_session(session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if req and req.summary:
        result["summary"] = req.summary
    return result


@router.get("/status/entry")
async def status_entry(request: Request, status: str | None = None, user_id: str = DEFAULT_WEB_USER_ID) -> list[dict[str, Any]]:
    return _agent_service(request).status_entry(user_id=user_id, status=status)


@router.get("/status/overview")
async def status_overview(
    request: Request,
    session_status: str | None = None,
    run_status: str | None = None,
    user_id: str = DEFAULT_WEB_USER_ID,
) -> dict[str, Any]:
    return _agent_service(request).status_overview(
        user_id=user_id,
        session_status=session_status,
        run_status=run_status,
    )


@router.get("/status/sessions/{session_id}")
async def status_session(session_id: str, request: Request) -> dict[str, Any]:
    payload = _agent_service(request).status_session(session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return payload


@router.get("/tasks")
async def list_tasks(request: Request, status: str | None = None) -> list[dict[str, Any]]:
    return _task_service(request).list_tasks(status=status)


@router.post("/tasks")
async def create_task(req: CreateTaskRequest, request: Request) -> dict[str, Any]:
    try:
        return _task_service(request).create_task(
            title=req.title,
            prompt=req.prompt,
            schedule_text=req.schedule_text,
            requested_skill_name=req.requested_skill_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request) -> dict[str, Any]:
    task = _task_service(request).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/tasks/{task_id}/pause")
async def pause_task(task_id: str, request: Request) -> dict[str, Any]:
    task = _task_service(request).pause_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/tasks/{task_id}/resume")
async def resume_task(task_id: str, request: Request) -> dict[str, Any]:
    task = _task_service(request).resume_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/tasks/{task_id}/run-now")
async def run_task_now(task_id: str, request: Request) -> dict[str, Any]:
    task = await _task_service(request).run_task_now(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request) -> dict[str, Any]:
    task = _task_service(request).cancel_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/cost/summary")
async def cost_summary(request: Request, task_id: str | None = None) -> dict[str, Any]:
    return _agent_service(request).cost_summary(task_id=task_id)


@router.get("/memory/search")
async def memory_search(request: Request, query: str, session_id: str | None = None) -> dict[str, Any]:
    return await _agent_service(request).memory_search(query, session_id=session_id)


def _agent_service(request: Request) -> AgentService:
    return request.app.state.agent_service


def _skill_service(request: Request) -> SkillService:
    return request.app.state.skill_service


def _task_service(request: Request):
    return request.app.state.task_service


def _format_sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
