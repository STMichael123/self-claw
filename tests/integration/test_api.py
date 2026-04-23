"""API 路由集成测试。"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from src.app.main import create_app
from src.models.llm import ChatMessage, LLMAdapter, LLMResponse, StreamChunk, ToolCallRequest
from src.services.agent_service import AgentService
from src.storage.database import get_connection
from src.tools.registry import ToolDescriptor


class SlowToolLLM(LLMAdapter):
    def __init__(self) -> None:
        self._call_count = 0
        self.model = "test-slow-tool"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self._call_count += 1
        if self._call_count == 1:
            return LLMResponse(
                content="准备调用慢工具",
                tool_calls=[ToolCallRequest(id="tool-1", name="slow_tool", arguments="{}")],
                input_tokens=5,
                output_tokens=2,
                model=self.model,
                finish_reason="tool_calls",
            )
        return LLMResponse(
            content="慢工具已完成",
            input_tokens=2,
            output_tokens=3,
            model=self.model,
            finish_reason="stop",
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        response = await self.chat(messages, tools=tools, temperature=temperature, max_tokens=max_tokens)
        yield StreamChunk(delta=response.content, finish_reason=response.finish_reason, tool_calls=response.tool_calls)


class ImmediateReplyLLM(LLMAdapter):
    def __init__(self) -> None:
        self.model = "test-immediate"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        return LLMResponse(
            content="即时回复",
            input_tokens=3,
            output_tokens=2,
            model=self.model,
            finish_reason="stop",
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        response = await self.chat(messages, tools=tools, temperature=temperature, max_tokens=max_tokens)
        yield StreamChunk(delta=response.content, finish_reason=response.finish_reason)


class ApprovalLLM(LLMAdapter):
    def __init__(self) -> None:
        self._call_count = 0
        self.model = "test-approval"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self._call_count += 1
        if self._call_count == 1:
            return LLMResponse(
                content="需要执行审批工具",
                tool_calls=[ToolCallRequest(id="approval-tool-1", name="needs_approval_api", arguments='{"msg":"deploy"}')],
                input_tokens=4,
                output_tokens=3,
                model=self.model,
                finish_reason="tool_calls",
            )
        return LLMResponse(
            content="审批后的最终回复",
            input_tokens=2,
            output_tokens=2,
            model=self.model,
            finish_reason="stop",
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        response = await self.chat(messages, tools=tools, temperature=temperature, max_tokens=max_tokens)
        yield StreamChunk(delta=response.content, finish_reason=response.finish_reason, tool_calls=response.tool_calls)


class ForbiddenToolLLM(LLMAdapter):
    def __init__(self) -> None:
        self._call_count = 0
        self.model = "test-forbidden-tool"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self._call_count += 1
        if self._call_count == 1:
            return LLMResponse(
                content="尝试调用未授权工具",
                tool_calls=[ToolCallRequest(id="forbidden-tool-1", name="forbidden_tool", arguments="{}")],
                input_tokens=3,
                output_tokens=2,
                model=self.model,
                finish_reason="tool_calls",
            )
        return LLMResponse(
            content="未授权工具未执行",
            input_tokens=2,
            output_tokens=2,
            model=self.model,
            finish_reason="stop",
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        response = await self.chat(messages, tools=tools, temperature=temperature, max_tokens=max_tokens)
        yield StreamChunk(delta=response.content, finish_reason=response.finish_reason, tool_calls=response.tool_calls)


class BlockingLLM(LLMAdapter):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.model = "test-blocking"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.started.set()
        await self.release.wait()
        return LLMResponse(
            content="阻塞回复",
            input_tokens=2,
            output_tokens=2,
            model=self.model,
            finish_reason="stop",
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, object]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        response = await self.chat(messages, tools=tools, temperature=temperature, max_tokens=max_tokens)
        yield StreamChunk(delta=response.content, finish_reason=response.finish_reason)


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("FILE_SANDBOX_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("SKILL_ROOT", str(tmp_path / ".agents" / "skills"))
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _skill_markdown(name: str, description: str, *, allowed_tools: list[str] | None = None, body: str = "# Skill\n\n测试正文") -> str:
    tools = allowed_tools or []
    if tools:
        tool_block = "\n".join(f"  - {item}" for item in tools)
        allowed_tools_yaml = f"allowed-tools:\n{tool_block}"
    else:
        allowed_tools_yaml = "allowed-tools: []"
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "compatibility: self-claw@1.0\n"
        f"{allowed_tools_yaml}\n"
        "metadata:\n"
        "  owner: api-tests\n"
        "---\n\n"
        f"{body}\n"
    )


class TestHealthCheck:
    def test_health(self, client: TestClient) -> None:
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestSkillsAPI:
    def test_list_skills(self, client: TestClient) -> None:
        resp = client.get("/api/v1/skills")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_skill_file_discovery_and_catalog(self, client: TestClient, tmp_path) -> None:
        skill_dir = tmp_path / ".agents" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            _skill_markdown("test-skill", "测试 Skill", allowed_tools=["web_fetch"]),
            encoding="utf-8",
        )

        reload = client.post("/api/v1/skills/reload")
        assert reload.status_code == 200

        listing = client.get("/api/v1/skills")
        assert listing.status_code == 200
        names = [item["skill_name"] for item in listing.json()]
        assert "test-skill" in names

        detail = client.get("/api/v1/skills/test-skill")
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["frontmatter"]["name"] == "test-skill"
        assert payload["status"] == "enabled"

    def test_skill_enable_disable(self, client: TestClient, tmp_path) -> None:
        skill_dir = tmp_path / ".agents" / "skills" / "toggle-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            _skill_markdown("toggle-skill", "开关测试"),
            encoding="utf-8",
        )
        client.post("/api/v1/skills/reload")

        disabled = client.post(
            "/api/v1/skills/toggle-skill/actions",
            json={"action": "disable", "operator": "tester", "reason": "测试禁用"},
        )
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "disabled"

        enabled = client.post(
            "/api/v1/skills/toggle-skill/actions",
            json={"action": "enable", "operator": "tester", "reason": "测试启用"},
        )
        assert enabled.status_code == 200
        assert enabled.json()["status"] == "enabled"


class TestToolsAPI:
    def test_list_tools(self, client: TestClient) -> None:
        resp = client.get("/api/v1/tools")
        assert resp.status_code == 200
        names = {item["name"] for item in resp.json()}
        assert {"web_fetch", "web_search", "list_dir", "read_file", "write_file", "patch_file", "activate_skill", "exec"}.issubset(names)

    def test_memory_search_returns_recent_session_memory(self, client: TestClient) -> None:
        client.app.state.agent_service.model_router.get_primary = lambda: ImmediateReplyLLM()  # type: ignore[method-assign]

        chat = client.post(
            "/api/v1/agent/chat",
            json={
                "message": "记忆检索关键字 alpha-beta-gamma",
                "task_mode": "new_task",
                "stream": False,
                "user_id": "web-user",
            },
        )
        assert chat.status_code == 200

        result = client.get("/api/v1/memory/search", params={"query": "alpha-beta-gamma"})
        assert result.status_code == 200
        payload = result.json()
        assert "files" in payload
        assert payload["files"]

    def test_memory_search_can_scope_to_session(self, client: TestClient) -> None:
        client.app.state.agent_service.model_router.get_primary = lambda: ImmediateReplyLLM()  # type: ignore[method-assign]

        first = client.post(
            "/api/v1/agent/chat",
            json={
                "message": "shared-keyword only-from-session-a",
                "task_mode": "new_task",
                "stream": False,
                "user_id": "web-user",
            },
        )
        assert first.status_code == 200
        session_a = first.json()["session_id"]

        second = client.post(
            "/api/v1/agent/chat",
            json={
                "message": "shared-keyword only-from-session-b",
                "task_mode": "new_task",
                "stream": False,
                "user_id": "web-user",
            },
        )
        assert second.status_code == 200

        result = client.get(
            "/api/v1/memory/search",
            params={"query": "shared-keyword", "session_id": session_a},
        )
        assert result.status_code == 200
        payload = result.json()
        snippets = "\n".join(item.get("snippet", "") for item in payload["files"])
        assert "only-from-session-a" in snippets
        assert "only-from-session-b" not in snippets


class TestSessionsAPI:
    def test_create_and_list_sessions(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/sessions",
            json={"user_id": "web-user", "title": "Agent 1", "channel_type": "web"},
        )
        assert created.status_code == 200
        session_id = created.json()["session_id"]

        listing = client.get("/api/v1/sessions", params={"user_id": "web-user"})
        assert listing.status_code == 200
        assert any(item["id"] == session_id for item in listing.json())

        detail = client.get(f"/api/v1/sessions/{session_id}")
        assert detail.status_code == 200
        assert detail.json()["id"] == session_id

    def test_create_session_rejects_sixth_active_session(self, client: TestClient) -> None:
        for index in range(5):
            created = client.post(
                "/api/v1/sessions",
                json={"user_id": "web-user", "title": f"Agent {index + 1}", "channel_type": "web"},
            )
            assert created.status_code == 200

        rejected = client.post(
            "/api/v1/sessions",
            json={"user_id": "web-user", "title": "Agent 6", "channel_type": "web"},
        )
        assert rejected.status_code == 400
        assert "最多只能保留 5 个顶层会话" in rejected.json()["detail"]

    def test_status_entry_hides_archived_sessions(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/sessions",
            json={"user_id": "web-user", "title": "Will Archive", "channel_type": "web"},
        )
        assert created.status_code == 200
        session_id = created.json()["session_id"]

        closed = client.post(f"/api/v1/sessions/{session_id}/close", json={})
        assert closed.status_code == 200

        entry = client.get("/api/v1/status/entry", params={"user_id": "web-user"})
        assert entry.status_code == 200
        assert all(item["session_id"] != session_id for item in entry.json())


class TestTasksAPI:
    def test_create_list_and_run_task_now(self, client: TestClient) -> None:
        client.app.state.agent_service.model_router.get_primary = lambda: ImmediateReplyLLM()  # type: ignore[method-assign]

        created = client.post(
            "/api/v1/tasks",
            json={
                "title": "任务 API 测试",
                "prompt": "立即执行任务并返回结果",
                "schedule_text": "10分钟后",
            },
        )
        assert created.status_code == 200
        payload = created.json()
        assert payload["human_schedule"]
        assert payload["status"] == "active"

        listing = client.get("/api/v1/tasks")
        assert listing.status_code == 200
        assert any(item["id"] == payload["id"] for item in listing.json())

        trigger = client.post(f"/api/v1/tasks/{payload['id']}/run-now", json={})
        assert trigger.status_code == 200

        detail_payload = None
        for _ in range(40):
            detail = client.get(f"/api/v1/tasks/{payload['id']}")
            assert detail.status_code == 200
            detail_payload = detail.json()
            if detail_payload.get("last_result", {}).get("reply"):
                break
            time.sleep(0.05)

        assert detail_payload is not None
        assert detail_payload["session_id"]
        assert detail_payload["last_result"]["reply"] == "即时回复"

        cost = client.get("/api/v1/cost/summary", params={"task_id": payload["id"]})
        assert cost.status_code == 200
        cost_payload = cost.json()
        assert cost_payload["task_id"] == payload["id"]
        assert cost_payload["call_count"] >= 1

    def test_create_task_rejects_invalid_schedule(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/tasks",
            json={
                "title": "非法任务",
                "prompt": "不应该落库",
                "schedule_text": "61 25 * * *",
            },
        )
        assert created.status_code == 400

        listing = client.get("/api/v1/tasks")
        assert listing.status_code == 200
        assert listing.json() == []

    def test_task_approval_resume_updates_task_state(self, client: TestClient) -> None:
        llm = ApprovalLLM()
        service = client.app.state.agent_service
        service.model_router.get_primary = lambda: llm  # type: ignore[method-assign]

        async def approval_tool(msg: str) -> str:
            return f"approved: {msg}"

        service.tool_registry.register(
            ToolDescriptor(
                name="needs_approval_api",
                description="Approval-only tool",
                parameters={
                    "type": "object",
                    "properties": {"msg": {"type": "string"}},
                    "required": ["msg"],
                },
                requires_approval=True,
                handler=approval_tool,
            )
        )

        created = client.post(
            "/api/v1/tasks",
            json={
                "title": "审批任务",
                "prompt": "执行需要审批的工具",
                "schedule_text": "10分钟后",
            },
        )
        assert created.status_code == 200
        task_id = created.json()["id"]

        trigger = client.post(f"/api/v1/tasks/{task_id}/run-now", json={})
        assert trigger.status_code == 200

        detail_payload = None
        for _ in range(40):
            detail = client.get(f"/api/v1/tasks/{task_id}")
            assert detail.status_code == 200
            detail_payload = detail.json()
            if detail_payload.get("last_result", {}).get("pending_approval"):
                break
            time.sleep(0.05)

        assert detail_payload is not None
        approval_id = detail_payload["last_result"]["pending_approval"]["approval_id"]
        assert detail_payload["run_history"][0]["status"] == "waiting_approval"

        decision = client.post(
            f"/api/v1/tools/approvals/{approval_id}",
            json={"decision": "approved", "operator": "tester"},
        )
        assert decision.status_code == 200

        for _ in range(40):
            detail = client.get(f"/api/v1/tasks/{task_id}")
            assert detail.status_code == 200
            detail_payload = detail.json()
            if detail_payload.get("last_result", {}).get("status") == "success":
                break
            time.sleep(0.05)

        assert detail_payload is not None
        assert detail_payload["status"] == "completed"
        assert detail_payload["last_result"]["reply"] == "审批后的最终回复"
        assert detail_payload["last_result"]["status"] == "success"
        assert detail_payload["run_history"][0]["status"] == "success"


class TestAgentAndStatusAPI:
    def test_chat_stream_emits_realtime_events(self, client: TestClient) -> None:
        client.app.state.agent_service.model_router.get_primary = lambda: ImmediateReplyLLM()  # type: ignore[method-assign]

        with client.stream(
            "POST",
            "/api/v1/agent/chat",
            json={
                "message": "流式回复测试",
                "task_mode": "new_task",
                "stream": True,
                "user_id": "web-user",
            },
        ) as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())

        assert "event: thinking" in body
        assert "event: reply" in body
        assert "即时回复" in body
        assert "event: usage" in body
        assert "event: done" in body

    def test_chat_creates_run_and_status_views(self, client: TestClient) -> None:
        chat = client.post(
            "/api/v1/agent/chat",
            json={
                "message": "请分析这个任务并给出建议",
                "task_mode": "new_task",
                "stream": False,
                "user_id": "web-user",
            },
        )
        assert chat.status_code == 200
        payload = chat.json()
        assert payload["session_action"] == "created_new"
        assert payload["session_id"]
        assert payload["run_id"]
        assert payload["reply"]

        run = client.get(f"/api/v1/agent/runs/{payload['run_id']}")
        assert run.status_code == 200
        run_payload = run.json()
        assert run_payload["status"] == "success"
        assert "tool_calls" in run_payload
        assert "child_run_summary" in run_payload

        entry = client.get("/api/v1/status/entry", params={"user_id": "web-user"})
        assert entry.status_code == 200
        assert any(item["session_id"] == payload["session_id"] for item in entry.json())

        overview = client.get("/api/v1/status/overview", params={"user_id": "web-user"})
        assert overview.status_code == 200
        assert overview.json()["sessions"]

        session_tree = client.get(f"/api/v1/status/sessions/{payload['session_id']}")
        assert session_tree.status_code == 200
        runs = session_tree.json()["runs"]
        assert any(item["agent_role"] == "main" for item in runs)

    def test_invalid_task_mode_is_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/agent/chat",
            json={"message": "hello", "task_mode": "invalid_mode", "user_id": "web-user"},
        )
        assert resp.status_code == 422

    def test_auto_mode_rejects_ambiguous_multiple_sessions(self, client: TestClient) -> None:
        client.post("/api/v1/sessions", json={"user_id": "web-user", "title": "Agent 1"})
        client.post("/api/v1/sessions", json={"user_id": "web-user", "title": "Agent 2"})

        resp = client.post(
            "/api/v1/agent/chat",
            json={"message": "继续处理", "task_mode": "auto", "user_id": "web-user"},
        )
        assert resp.status_code == 400

    def test_continue_rejects_running_main_run(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/sessions",
            json={"user_id": "web-user", "title": "Busy Session", "channel_type": "web"},
        )
        session_id = created.json()["session_id"]
        db = client.app.state.db
        db.execute(
            """
            INSERT INTO agent_runs (id, parent_run_id, agent_role, skill_id, session_id, task_ref, context_ref, started_at, status, steps_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                None,
                "main",
                None,
                session_id,
                "busy",
                "{}",
                datetime.now(timezone.utc).isoformat(),
                "running",
                0,
            ),
        )
        db.commit()

        resp = client.post(
            "/api/v1/agent/chat",
            json={
                "session_id": session_id,
                "message": "继续处理这个线程",
                "task_mode": "continue",
                "user_id": "web-user",
            },
        )
        assert resp.status_code == 400

    def test_cancel_and_rerun_creates_new_main_run_in_same_session(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/sessions",
            json={"user_id": "web-user", "title": "Retry Session", "channel_type": "web"},
        )
        session_id = created.json()["session_id"]
        client.app.state.agent_service.model_router.get_primary = lambda: ImmediateReplyLLM()  # type: ignore[method-assign]

        old_run_id = str(uuid.uuid4())
        db = client.app.state.db
        db.execute(
            """
            INSERT INTO agent_runs (id, parent_run_id, agent_role, skill_id, session_id, task_ref, context_ref, started_at, status, steps_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                old_run_id,
                None,
                "main",
                None,
                session_id,
                "retry old run",
                "{}",
                datetime.now(timezone.utc).isoformat(),
                "running",
                0,
            ),
        )
        db.commit()

        resp = client.post(
            "/api/v1/agent/chat",
            json={
                "session_id": session_id,
                "message": "重跑这个线程",
                "task_mode": "cancel_and_rerun",
                "user_id": "web-user",
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["session_action"] == "cancelled_and_reran"
        assert payload["session_id"] == session_id
        assert payload["run_id"] != old_run_id

        old_run = client.get(f"/api/v1/agent/runs/{old_run_id}")
        new_run = client.get(f"/api/v1/agent/runs/{payload['run_id']}")
        session = client.get(f"/api/v1/sessions/{session_id}")

        assert old_run.status_code == 200
        assert old_run.json()["status"] == "cancelled"
        assert new_run.status_code == 200
        assert new_run.json()["status"] == "success"
        assert session.status_code == 200
        assert session.json()["current_run_id"] == payload["run_id"]

    def test_tool_approval_resume_flow(self, client: TestClient) -> None:
        llm = ApprovalLLM()
        service = client.app.state.agent_service
        service.model_router.get_primary = lambda: llm  # type: ignore[method-assign]

        async def approval_tool(msg: str) -> str:
            return f"approved: {msg}"

        service.tool_registry.register(
            ToolDescriptor(
                name="needs_approval_api",
                description="Approval-only tool",
                parameters={
                    "type": "object",
                    "properties": {"msg": {"type": "string"}},
                    "required": ["msg"],
                },
                requires_approval=True,
                handler=approval_tool,
            )
        )

        chat = client.post(
            "/api/v1/agent/chat",
            json={
                "message": "执行需要审批的工具",
                "task_mode": "new_task",
                "stream": False,
                "user_id": "web-user",
            },
        )
        assert chat.status_code == 200
        payload = chat.json()
        assert payload["pending_approval"]["tool_name"] == "needs_approval_api"
        approval_id = payload["pending_approval"]["approval_id"]

        approvals = client.get("/api/v1/tools/approvals", params={"status": "pending"})
        assert approvals.status_code == 200
        assert any(item["approval_id"] == approval_id for item in approvals.json())

        decision = client.post(
            f"/api/v1/tools/approvals/{approval_id}",
            json={"decision": "approved", "operator": "tester"},
        )
        assert decision.status_code == 200
        assert decision.json()["resumed_run_id"] == payload["run_id"]

        audit_row = client.app.state.db.execute(
            "SELECT action, diff_summary FROM audit_logs WHERE entity_id = ? ORDER BY created_at DESC LIMIT 1",
            (approval_id,),
        ).fetchone()
        assert audit_row is not None
        assert audit_row["action"] == "tool_approval_approved"
        assert "needs_approval_api -> approved" in audit_row["diff_summary"]

        run_payload = None
        for _ in range(40):
            run = client.get(f"/api/v1/agent/runs/{payload['run_id']}")
            assert run.status_code == 200
            run_payload = run.json()
            if run_payload["status"] != "running":
                break
            time.sleep(0.05)

        assert run_payload is not None
        assert run_payload["status"] == "success"
        assert any(item["tool_name"] == "needs_approval_api" for item in run_payload["tool_calls"])

        session = client.get(f"/api/v1/sessions/{payload['session_id']}")
        assert session.status_code == 200
        assert any(message["content"] == "审批后的最终回复" for message in session.json()["messages"])

    def test_skill_allowlist_blocks_unlisted_tool_execution(self, client: TestClient, tmp_path) -> None:
        llm = ForbiddenToolLLM()
        service = client.app.state.agent_service
        service.model_router.get_primary = lambda: llm  # type: ignore[method-assign]
        tool_calls: list[str] = []

        async def forbidden_tool() -> str:
            tool_calls.append("called")
            return "should not run"

        service.tool_registry.register(
            ToolDescriptor(
                name="forbidden_tool",
                description="Tool blocked by skill allowlist",
                handler=forbidden_tool,
            )
        )

        skill_dir = tmp_path / ".agents" / "skills" / "allowlist-only"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            _skill_markdown("allowlist-only", "不允许调用任何工具", allowed_tools=[]),
            encoding="utf-8",
        )
        client.post("/api/v1/skills/reload")

        chat = client.post(
            "/api/v1/agent/chat",
            json={
                "message": "尝试调用工具",
                "task_mode": "new_task",
                "stream": False,
                "user_id": "web-user",
                "requested_skill_name": "allowlist-only",
            },
        )
        assert chat.status_code == 200
        assert tool_calls == []

        run = client.get(f"/api/v1/agent/runs/{chat.json()['run_id']}")
        assert run.status_code == 200
        assert any(
            item["tool_name"] == "forbidden_tool" and item["status"] == "failed"
            for item in run.json()["tool_calls"]
        )

    @pytest.mark.asyncio
    async def test_task_failure_notification_uses_session_channel_and_user(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/sessions",
            json={"user_id": "notify-user", "title": "Notify Session", "channel_type": "test"},
        )
        assert created.status_code == 200
        session_id = created.json()["session_id"]

        adapter = client.app.state.channel_registry.get("test")
        assert adapter is not None

        await client.app.state.agent_service._notify_task_failure(
            task_id="task-notify",
            session_id=session_id,
            reply="boom",
        )

        assert adapter.sent_messages[-1].target_uid == "notify-user"
        assert adapter.sent_messages[-1].channel_type == "test"

    def test_cancelled_run_cannot_write_back_success(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/sessions",
            json={"user_id": "web-user", "title": "Cancellable Session", "channel_type": "web"},
        )
        session_id = created.json()["session_id"]
        service = client.app.state.agent_service
        run_id = service._create_run(
            session_id=session_id,
            agent_role="main",
            status="running",
            task_ref="late result",
            context_ref={"task_mode": "continue"},
        )

        service._cancel_running_main_runs(session_id)
        service._update_run(
            run_id,
            status="success",
            steps_count=1,
            result_ref={"reply": "should be ignored"},
        )
        message_id = service.sessions.add_message(
            session_id,
            "assistant",
            "should be ignored",
            run_id=run_id,
            guard_run_not_cancelled=True,
        )
        service.sessions.set_current_run(session_id, run_id)

        run = client.get(f"/api/v1/agent/runs/{run_id}")
        assert run.status_code == 200
        assert run.json()["status"] == "cancelled"
        assert message_id == ""

        session = client.get(f"/api/v1/sessions/{session_id}")
        assert session.status_code == 200
        assert session.json()["current_run_id"] is None
        assert session.json()["message_count"] == 0

    @pytest.mark.asyncio
    async def test_cancelled_run_stops_tool_execution_and_usage_persistence(self, tmp_path) -> None:
        db = get_connection(str(tmp_path / "cancel-runtime.db"))
        service = AgentService(db)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow_tool() -> str:
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return "done"

        service.tool_registry.register(
            ToolDescriptor(
                name="slow_tool",
                description="Slow tool for cancellation tests",
                handler=slow_tool,
                timeout_sec=30,
            )
        )
        service.model_router.get_primary = lambda: SlowToolLLM()  # type: ignore[method-assign]

        try:
            chat_task = asyncio.create_task(
                service.chat(
                    message="执行慢工具",
                    task_mode="new_task",
                    user_id="web-user",
                )
            )

            await asyncio.wait_for(started.wait(), timeout=1)
            running = db.execute(
                "SELECT id, session_id FROM agent_runs WHERE agent_role = 'main' AND status = ? LIMIT 1",
                ("running",),
            ).fetchone()
            assert running is not None

            service._cancel_running_main_runs(running["session_id"])

            result = await asyncio.wait_for(chat_task, timeout=2)
            await asyncio.wait_for(cancelled.wait(), timeout=1)

            assert result["run_id"] == running["id"]
            assert result["reply"] == "该运行已被取消，线程已切换到新的主运行。"

            run = db.execute("SELECT status FROM agent_runs WHERE id = ?", (running["id"],)).fetchone()
            tool_calls = db.execute(
                "SELECT COUNT(*) AS count FROM tool_calls WHERE agent_run_id = ?",
                (running["id"],),
            ).fetchone()
            usage_logs = db.execute(
                "SELECT COUNT(*) AS count FROM usage_logs WHERE agent_run_id = ?",
                (running["id"],),
            ).fetchone()
            assistant_messages = db.execute(
                "SELECT COUNT(*) AS count FROM messages WHERE run_id = ? AND role = 'assistant'",
                (running["id"],),
            ).fetchone()

            assert run is not None
            assert run["status"] == "cancelled"
            assert tool_calls["count"] == 0
            assert usage_logs["count"] == 0
            assert assistant_messages["count"] == 0
        finally:
            db.close()

    @pytest.mark.asyncio
    async def test_cancelled_run_before_artifact_persist_skips_runtime_writes(self, tmp_path) -> None:
        db = get_connection(str(tmp_path / "cancel-finalize.db"))
        service = AgentService(db)
        service.model_router.get_primary = lambda: ImmediateReplyLLM()  # type: ignore[method-assign]
        original_persist = service._persist_run_runtime_artifacts

        def cancel_before_persist(**kwargs):
            service._cancel_running_main_runs(kwargs["session_id"])
            return original_persist(**kwargs)

        service._persist_run_runtime_artifacts = cancel_before_persist  # type: ignore[method-assign]

        try:
            result = await service.chat(
                message="立即完成",
                task_mode="new_task",
                user_id="web-user",
            )

            run = db.execute("SELECT status, session_id FROM agent_runs WHERE id = ?", (result["run_id"],)).fetchone()
            usage_logs = db.execute(
                "SELECT COUNT(*) AS count FROM usage_logs WHERE agent_run_id = ?",
                (result["run_id"],),
            ).fetchone()
            assistant_messages = db.execute(
                "SELECT COUNT(*) AS count FROM messages WHERE run_id = ? AND role = 'assistant'",
                (result["run_id"],),
            ).fetchone()

            assert run is not None
            assert run["status"] == "cancelled"
            assert result["reply"] == "该运行已被取消，线程已切换到新的主运行。"
            assert usage_logs["count"] == 0
            assert assistant_messages["count"] == 0
        finally:
            db.close()


@pytest.mark.asyncio
async def test_concurrent_rejected_run_does_not_persist_extra_user_message(tmp_path) -> None:
    db = get_connection(str(tmp_path / "concurrency.db"))
    service = AgentService(db)
    llm = BlockingLLM()
    service.model_router.get_primary = lambda: llm  # type: ignore[method-assign]

    try:
        created = service.create_session(user_id="web-user", title="Concurrent Session")
        session_id = created["session_id"]

        first_chat = asyncio.create_task(
            service.chat(
                session_id=session_id,
                message="first message",
                task_mode="continue",
                user_id="web-user",
            )
        )
        await asyncio.wait_for(llm.started.wait(), timeout=1)

        with pytest.raises(ValueError):
            await service.chat(
                session_id=session_id,
                message="second message",
                task_mode="continue",
                user_id="web-user",
            )

        llm.release.set()
        await asyncio.wait_for(first_chat, timeout=1)

        messages = service.sessions.list_messages(session_id)
        assert [item["content"] for item in messages] == ["first message", "阻塞回复"]
    finally:
        db.close()
