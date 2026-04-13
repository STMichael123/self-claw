"""API 路由集成测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.app.main import create_app


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


class TestHealthCheck:
    def test_health(self, client: TestClient) -> None:
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestSkillsAPI:
    def test_list_skills(self, client: TestClient) -> None:
        resp = client.get("/api/v1/skills")
        assert resp.status_code == 200

    def test_create_skill(self, client: TestClient) -> None:
        resp = client.post("/api/v1/skills", json={"name": "test"})
        assert resp.status_code == 200
        assert "skill_id" in resp.json()


class TestToolsAPI:
    def test_list_tools(self, client: TestClient) -> None:
        resp = client.get("/api/v1/tools")
        assert resp.status_code == 200


class TestSessionsAPI:
    def test_list_sessions(self, client: TestClient) -> None:
        resp = client.get("/api/v1/sessions")
        assert resp.status_code == 200
