"""Skill 注册、版本管理与运行时执行 — 对应 SPEC FR-008 / FR-011。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from src.contracts.errors import ErrorCode

logger = structlog.get_logger()


class SkillDefinition:
    """Skill 运行时定义 — 对应 SPEC FR-011。"""

    def __init__(
        self,
        *,
        skill_id: str,
        name: str,
        version: str = "v1",
        status: str = "enabled",
        skill_prompt: str = "",
        allowed_tools: list[str] | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        examples: list[dict[str, Any]] | None = None,
        max_steps: int | None = None,
        sop_source: str = "",
    ) -> None:
        self.skill_id = skill_id
        self.name = name
        self.version = version
        self.status = status
        self.skill_prompt = skill_prompt
        self.allowed_tools = allowed_tools or []
        self.input_schema = input_schema or {}
        self.output_schema = output_schema or {}
        self.examples = examples or []
        self.max_steps = max_steps
        self.sop_source = sop_source


class SkillRegistry:
    """Skill 注册表 — 管理 Skill 的生命周期。"""

    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}

    def register(self, skill: SkillDefinition) -> None:
        self._skills[skill.skill_id] = skill
        logger.info("skill_registered", skill_id=skill.skill_id, name=skill.name, version=skill.version)

    def get(self, skill_id: str) -> SkillDefinition | None:
        return self._skills.get(skill_id)

    def get_by_name(self, name: str) -> SkillDefinition | None:
        for s in self._skills.values():
            if s.name == name:
                return s
        return None

    def list_skills(self, *, status: str | None = None) -> list[SkillDefinition]:
        skills = list(self._skills.values())
        if status:
            skills = [s for s in skills if s.status == status]
        return skills

    def disable(self, skill_id: str) -> bool:
        skill = self._skills.get(skill_id)
        if skill:
            skill.status = "disabled"
            return True
        return False

    def enable(self, skill_id: str) -> bool:
        skill = self._skills.get(skill_id)
        if skill:
            skill.status = "enabled"
            return True
        return False

    def validate_tool_access(self, skill_id: str, tool_name: str) -> bool:
        """检查工具是否在 Skill 的白名单中。"""
        skill = self._skills.get(skill_id)
        if not skill:
            return False
        if not skill.allowed_tools:
            return True  # 空白名单表示允许全部
        return tool_name in skill.allowed_tools
