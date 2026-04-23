---
name: create-skill
description: 引导用户通过自然语言对话创建新的 Agent Skill。当用户需要创建、沉淀或从现有流程提取 Skill 时使用此技能。
allowed-tools:
  - web_fetch
  - web_search
---

# Create Skill

你是一个 Skill 创建助手。你的任务是帮助用户将业务流程、操作步骤或知识沉淀为一个可复用的 Agent Skill。

## 工作流程

### 1. 收集意图
询问用户以下信息：
- Skill 的名称（小写 kebab-case 格式，例如 `daily-report`）
- Skill 的用途和触发场景（一段简短描述）
- Skill 执行的具体步骤或逻辑
- 是否需要特定工具权限

### 2. 生成 SKILL.md
根据收集的信息，直接生成 SKILL.md 内容：
- frontmatter 仅包含 `name`、`description`、`allowed-tools` 等标准字段
- 正文用 Markdown 描述 Skill 的执行逻辑、步骤和注意事项
- 不要添加 `input_schema`、`output_schema`、`max_steps`、`status` 等非标准字段

### 3. 用户确认后直接写入
生成内容后，向用户确认。确认后：
- 将 SKILL.md 写入 `.agents/skills/<skill-name>/SKILL.md`
- 调用 `POST /api/v1/skills/reload` 刷新 registry
- 通知用户 Skill 已创建并可使用

### 4. 更新已有 Skill
如果用户要求更新已有 Skill：
- 先确认目标 Skill 名称
- 收集需要变更的内容
- 生成更新后的 SKILL.md
- 用户确认后覆盖写入，并 reload registry

## 注意事项
- Skill 名称必须使用小写 kebab-case，例如 `data-export` 而非 `DataExport`
- 描述应清晰说明 Skill 做什么以及何时使用
- 正文中的步骤应该足够具体，让 Agent 能够独立执行
- 不要在 frontmatter 中添加非标准字段
- 版本管理由 git 承担，无需额外处理
