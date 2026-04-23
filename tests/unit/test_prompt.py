"""提示词编排器单元测试 — 对应 SPEC FR-005。"""

from __future__ import annotations

from src.agents.prompt import build_messages, compose_system_prompt
from src.models.llm import ChatMessage


# ── compose_system_prompt ───────────────────────────────


class TestComposeSystemPrompt:
    def test_default_base_prompt(self):
        result = compose_system_prompt()
        assert "AI 助手" in result
        assert "系统原则" not in result

    def test_custom_base_prompt(self):
        result = compose_system_prompt(base_prompt="You are a code reviewer.")
        assert result.startswith("You are a code reviewer.")

    def test_injection_order(self):
        result = compose_system_prompt(
            base_prompt="BASE",
            principle="PRINCIPLE",
            long_term_context="LONG_TERM",
            short_term_context="SHORT_TERM",
        )
        base_pos = result.index("BASE")
        principle_pos = result.index("PRINCIPLE")
        lt_pos = result.index("LONG_TERM")
        st_pos = result.index("SHORT_TERM")
        assert base_pos < principle_pos < lt_pos < st_pos

    def test_principle_section(self):
        result = compose_system_prompt(principle="Always be helpful.")
        assert "## 系统原则" in result
        assert "Always be helpful." in result

    def test_long_term_section(self):
        result = compose_system_prompt(long_term_context="Known facts.")
        assert "## 长期记忆" in result
        assert "Known facts." in result

    def test_short_term_section(self):
        result = compose_system_prompt(short_term_context="Recent activity.")
        assert "## 会话记忆" in result
        assert "Recent activity." in result

    def test_available_skills_catalog(self):
        catalog = [
            {"skill_name": "code-review", "description": "Review code"},
            {"skill_name": "deploy", "description": "Deploy to prod"},
        ]
        result = compose_system_prompt(available_skills_catalog=catalog)
        assert "## 可用 Skill 目录" in result
        assert "code-review: Review code" in result
        assert "deploy: Deploy to prod" in result

    def test_empty_skills_catalog_skipped(self):
        result = compose_system_prompt(available_skills_catalog=[])
        assert "## 可用 Skill 目录" not in result

    def test_activated_skills(self):
        skills = [
            {
                "skill_name": "code-review",
                "content": "Review the code carefully.",
                "resource_manifest": [
                    {"path": "rules/style.md"},
                    {"path": "rules/security.md"},
                ],
            }
        ]
        result = compose_system_prompt(activated_skills=skills)
        assert "## 已激活 Skills" in result
        assert "### code-review" in result
        assert "Review the code carefully." in result
        assert "rules/style.md" in result

    def test_activated_skills_empty_content(self):
        skills = [{"skill_name": "empty", "content": "", "resource_manifest": []}]
        result = compose_system_prompt(activated_skills=skills)
        assert "### empty" in result

    def test_tool_descriptions(self):
        tools = [
            {"name": "read_file", "description": "Read a file"},
            {"name": "write_file", "description": "Write a file"},
        ]
        result = compose_system_prompt(tool_descriptions=tools)
        assert "## 可用工具" in result
        assert "read_file: Read a file" in result

    def test_empty_tool_descriptions_skipped(self):
        result = compose_system_prompt(tool_descriptions=[])
        assert "## 可用工具" not in result

    def test_all_sections_together(self):
        result = compose_system_prompt(
            base_prompt="Base",
            principle="Principle",
            long_term_context="LT",
            short_term_context="ST",
            available_skills_catalog=[{"skill_name": "s1", "description": "d1"}],
            activated_skills=[{"skill_name": "s1", "content": "body"}],
            tool_descriptions=[{"name": "t1", "description": "desc"}],
        )
        assert "Base" in result
        assert "系统原则" in result
        assert "长期记忆" in result
        assert "会话记忆" in result
        assert "可用 Skill 目录" in result
        assert "已激活 Skills" in result
        assert "可用工具" in result


# ── build_messages ──────────────────────────────────────


class TestBuildMessages:
    def test_system_prompt_only(self):
        msgs = build_messages(system_prompt="You are helpful.")
        assert len(msgs) == 1
        assert msgs[0].role == "system"
        assert msgs[0].content == "You are helpful."

    def test_with_history(self):
        history = [
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Hi there"),
        ]
        msgs = build_messages(system_prompt="sys", history=history)
        assert len(msgs) == 3
        assert msgs[0].role == "system"
        assert msgs[1].role == "user"
        assert msgs[2].role == "assistant"

    def test_with_user_message(self):
        msgs = build_messages(system_prompt="sys", user_message="What is 2+2?")
        assert len(msgs) == 2
        assert msgs[1].role == "user"
        assert msgs[1].content == "What is 2+2?"

    def test_all_components(self):
        history = [ChatMessage(role="user", content="prev")]
        msgs = build_messages(system_prompt="sys", history=history, user_message="new")
        assert len(msgs) == 3
        assert msgs[0].role == "system"
        assert msgs[1] == history[0]
        assert msgs[2].role == "user"
        assert msgs[2].content == "new"
