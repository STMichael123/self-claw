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

class SkillActionRequest(BaseModel):
    action: Literal["enable", "disable"]
    operator: str = DEFAULT_WEB_USER_ID
    reason: str = ""


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    task_mode: Literal["auto", "continue", "new_task", "cancel_and_rerun"] = "auto"
    session_title: str | None = None
    stream: bool = False
    user_id: str = DEFAULT_WEB_USER_ID
    requested_skill_name: str | None = None
    task_id: str | None = None
    parse_slash: bool = True


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


class ParseScheduleRequest(BaseModel):
    schedule_text: str


class CloseSessionRequest(BaseModel):
    summary: str = ""


class UpdatePrincipleRequest(BaseModel):
    content: str
    change_note: str = ""
    operator: str = DEFAULT_WEB_USER_ID


class CreateLongTermRequest(BaseModel):
    key: str
    content: str
    title: str = ""
    operator: str = DEFAULT_WEB_USER_ID


# ── 健康检查 ────────────────────────────────────────────

@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── Skill Catalog / Management API — SPEC 9.1 ─────────────────

@router.post("/skills/reload")
async def reload_skill_catalog(request: Request) -> dict[str, Any]:
    return _skill_service(request).reload_catalog()


@router.get("/skills")
async def list_skills(
    request: Request,
    status: str | None = None,
    keyword: str | None = None,
) -> list[dict[str, Any]]:
    return _skill_service(request).list_catalog(status=status, keyword=keyword)


@router.get("/skills/audit")
async def list_skill_audit(
    request: Request,
    skill_name: str | None = None,
    action: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return _skill_service(request).list_audit(skill_name=skill_name, action=action, limit=limit)


@router.post("/skills/{skill_name}/actions")
async def skill_action(skill_name: str, req: SkillActionRequest, request: Request) -> dict[str, Any]:
    try:
        result = _skill_service(request).perform_action(
            skill_name,
            action=req.action,
            operator=req.operator,
            reason=req.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return result


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
                        parse_slash=req.parse_slash,
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
            parse_slash=req.parse_slash,
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


@router.get("/tools/file-operations")
async def list_file_operations(
    request: Request,
    session_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return _agent_service(request).list_file_operations(
        session_id=session_id, run_id=run_id, status=status, limit=limit,
    )


@router.get("/tools/file-locks")
async def list_file_locks(request: Request, sandbox_path: str | None = None) -> list[dict[str, Any]]:
    return _agent_service(request).list_file_locks(sandbox_path=sandbox_path)


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


@router.post("/tasks/parse")
async def parse_schedule(req: ParseScheduleRequest, request: Request) -> dict[str, Any]:
    try:
        return _task_service(request).parse_schedule(req.schedule_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
async def memory_search(
    request: Request,
    query: str,
    session_id: str | None = None,
    tiers: str | None = None,
) -> dict[str, Any]:
    parsed_tiers = [t.strip() for t in tiers.split(",") if t.strip()] if tiers else None
    if parsed_tiers and "short_term" in parsed_tiers and not session_id:
        raise HTTPException(status_code=400, detail="session_id is required when tiers includes short_term")
    return await _agent_service(request).memory_search(query, session_id=session_id, tiers=parsed_tiers)


# ── 记忆管理 API — SPEC 9.9 ────────────────────────────

@router.get("/memory/principle")
async def get_principle(request: Request) -> dict[str, Any]:
    ms = _memory_service(request)
    content = ms.load_principle()
    return {"content": content, "path": str(ms._principle_file)}


@router.put("/memory/principle")
async def update_principle(req: UpdatePrincipleRequest, request: Request) -> dict[str, Any]:
    ms = _memory_service(request)
    path = ms.save_principle(
        req.content,
        operator=req.operator,
        change_note=req.change_note,
    )
    return {"content": req.content, "path": str(path), "status": "updated"}


@router.get("/memory/long-term")
async def list_long_term(request: Request) -> list[dict[str, Any]]:
    return _memory_service(request).list_long_term()


@router.post("/memory/long-term")
async def create_long_term(req: CreateLongTermRequest, request: Request) -> dict[str, Any]:
    try:
        ms = _memory_service(request)
        path = ms.save_long_term(
            req.key,
            req.content,
            title=req.title,
            operator=req.operator,
        )
        return {"key": req.key, "path": str(path), "status": "created"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _agent_service(request: Request) -> AgentService:
    return request.app.state.agent_service


def _skill_service(request: Request) -> SkillService:
    return request.app.state.skill_service


def _task_service(request: Request):
    return request.app.state.task_service


def _memory_service(request: Request):
    return request.app.state.memory_service


def _format_sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
