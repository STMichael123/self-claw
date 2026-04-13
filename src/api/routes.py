"""HTTP API 路由 — 对应 SPEC §9。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1")


# ── 请求/响应模型 ───────────────────────────────────────

class CreateSkillRequest(BaseModel):
    name: str
    display_name: str = ""
    scenario: str = ""
    sop_source: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    content: str = ""


class UpdateSkillRequest(BaseModel):
    change_note: str = ""
    content: str = ""
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


class SkillStatusRequest(BaseModel):
    status: str  # enabled | disabled


class RollbackRequest(BaseModel):
    target_version: str
    reason: str = ""


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    stream: bool = False


class ToolApprovalRequest(BaseModel):
    decision: str  # approved | rejected
    operator: str


class CloseSessionRequest(BaseModel):
    pass


# ── 健康检查 ────────────────────────────────────────────

@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── Skill 管理 API — SPEC 9.1 ──────────────────────────

@router.post("/skills")
async def create_skill(req: CreateSkillRequest) -> dict[str, Any]:
    """新建 Skill — 占位实现，需接入 SkillRegistry + DB。"""
    # TODO: 接入 SkillRegistry 和 database
    return {"skill_id": "placeholder", "version": "v1", "status": "enabled"}


@router.get("/skills")
async def list_skills(status: str | None = None, keyword: str | None = None) -> list[dict[str, Any]]:
    return []


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: str) -> dict[str, Any]:
    raise HTTPException(status_code=404, detail="Skill not found")


@router.post("/skills/{skill_id}/versions")
async def update_skill(skill_id: str, req: UpdateSkillRequest) -> dict[str, Any]:
    return {"skill_id": skill_id, "version": "v2", "previous_version": "v1"}


@router.patch("/skills/{skill_id}/status")
async def patch_skill_status(skill_id: str, req: SkillStatusRequest) -> dict[str, Any]:
    return {"skill_id": skill_id, "status": req.status, "updated_at": ""}


@router.post("/skills/{skill_id}/rollback")
async def rollback_skill(skill_id: str, req: RollbackRequest) -> dict[str, Any]:
    return {"skill_id": skill_id, "active_version": req.target_version, "rollback_from": "v2"}


# ── Agent 执行 API — SPEC 9.5 ──────────────────────────

@router.post("/agent/chat")
async def agent_chat(req: ChatRequest) -> dict[str, Any]:
    """发送消息并触发 Agent 执行 — 占位实现。"""
    # TODO: 接入 MainAgent + SessionManager
    return {
        "session_id": req.session_id or "new_session",
        "run_id": "placeholder",
        "reply": "",
        "steps": [],
        "usage": {},
    }


@router.get("/agent/runs/{run_id}")
async def get_agent_run(run_id: str) -> dict[str, Any]:
    raise HTTPException(status_code=404, detail="Run not found")


# ── 工具管理 API — SPEC 9.6 ────────────────────────────

@router.get("/tools")
async def list_tools(category: str | None = None) -> list[dict[str, Any]]:
    return []


@router.post("/tools/approvals/{approval_id}")
async def tool_approval(approval_id: str, req: ToolApprovalRequest) -> dict[str, Any]:
    return {"approval_id": approval_id, "decision": req.decision, "resumed_run_id": ""}


# ── 会话管理 API — SPEC 9.7 ────────────────────────────

@router.get("/sessions")
async def list_sessions(status: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
    return []


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    raise HTTPException(status_code=404, detail="Session not found")


@router.post("/sessions/{session_id}/close")
async def close_session(session_id: str) -> dict[str, Any]:
    return {"session_id": session_id, "status": "archived", "summary": ""}
