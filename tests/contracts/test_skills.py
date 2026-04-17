"""Agent Skills 文件注册表测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.contracts.errors import ErrorCode
from src.skills.registry import SkillRegistry, SkillRegistryError


def _write_skill(
    root: Path,
    *,
    name: str,
    description: str,
    allowed_tools: list[str] | None = None,
    body: str = "# Skill\n\n默认正文",
    resource_path: str | None = None,
    resource_content: str = "",
) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    tools = allowed_tools or []
    if tools:
        tool_block = "\n".join(f"  - {item}" for item in tools)
        allowed_tools_yaml = f"allowed-tools:\n{tool_block}"
    else:
        allowed_tools_yaml = "allowed-tools: []"
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "compatibility: self-claw@1.0\n"
        f"{allowed_tools_yaml}\n"
        "metadata:\n"
        "  owner: tests\n"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    if resource_path:
        target = skill_dir / resource_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(resource_content, encoding="utf-8")


@pytest.fixture
def registry(tmp_path: Path) -> SkillRegistry:
    skill_root = tmp_path / ".agents" / "skills"
    _write_skill(
        skill_root,
        name="test-skill",
        description="测试 Skill",
        allowed_tools=["web_fetch", "read_file"],
        body="# Test Skill\n\n执行测试流程。",
        resource_path="references/guide.md",
        resource_content="参考资料",
    )
    registry = SkillRegistry(skill_root)
    registry.reload()
    return registry


class TestSkillRegistry:
    def test_reload_catalog_reads_metadata(self, registry: SkillRegistry) -> None:
        items = registry.list_catalog()
        assert len(items) == 1
        assert items[0].skill_name == "test-skill"
        assert items[0].description == "测试 Skill"

    def test_activate_returns_body_and_manifest(self, registry: SkillRegistry) -> None:
        activated = registry.activate("test-skill")
        assert activated.skill_name == "test-skill"
        assert "执行测试流程" in activated.body
        assert activated.allowed_tools == ["web_fetch", "read_file"]
        assert activated.resource_manifest == [
            {"path": "references/guide.md", "kind": "references", "size": len("参考资料".encode("utf-8"))}
        ]

    def test_activate_can_load_selected_resource(self, registry: SkillRegistry) -> None:
        activated = registry.activate("test-skill", resource_paths=["references/guide.md"])
        assert activated.resources == [{"path": "references/guide.md", "content": "参考资料"}]

    def test_resource_path_cannot_escape_skill_root(self, registry: SkillRegistry) -> None:
        with pytest.raises(SkillRegistryError) as exc_info:
            registry.activate("test-skill", resource_paths=["../secrets.txt"])
        assert exc_info.value.code == ErrorCode.SKILL_RESOURCE_ACCESS_DENIED

    def test_validate_rejects_directory_name_mismatch(self, tmp_path: Path) -> None:
        registry = SkillRegistry(tmp_path / ".agents" / "skills")
        with pytest.raises(SkillRegistryError) as exc_info:
            registry.validate_skill_text(
                "expected-name",
                "---\nname: another-name\ndescription: bad\nallowed-tools: []\nmetadata: {}\n---\n\n# bad\n",
            )
        assert exc_info.value.code == ErrorCode.SKILL_VALIDATION_FAILED
