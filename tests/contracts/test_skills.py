"""Skill 注册表测试 — 对应 SPEC FR-008。"""

from __future__ import annotations

import pytest

from src.skills.registry import SkillDefinition, SkillRegistry


@pytest.fixture
def registry() -> SkillRegistry:
    reg = SkillRegistry()
    reg.register(SkillDefinition(
        skill_id="s1",
        name="test_skill",
        skill_prompt="你是测试 Skill",
        allowed_tools=["echo", "web_fetch"],
    ))
    return reg


class TestSkillRegistry:
    def test_register_and_get(self, registry: SkillRegistry) -> None:
        skill = registry.get("s1")
        assert skill is not None
        assert skill.name == "test_skill"

    def test_get_by_name(self, registry: SkillRegistry) -> None:
        skill = registry.get_by_name("test_skill")
        assert skill is not None
        assert skill.skill_id == "s1"

    def test_list_skills(self, registry: SkillRegistry) -> None:
        assert len(registry.list_skills()) == 1

    def test_disable_enable(self, registry: SkillRegistry) -> None:
        assert registry.disable("s1")
        assert registry.get("s1").status == "disabled"
        assert len(registry.list_skills(status="enabled")) == 0

        assert registry.enable("s1")
        assert registry.get("s1").status == "enabled"

    def test_validate_tool_access(self, registry: SkillRegistry) -> None:
        assert registry.validate_tool_access("s1", "echo")
        assert not registry.validate_tool_access("s1", "exec")
