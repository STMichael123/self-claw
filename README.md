# Self-Claw

面向长链路任务和企业自动化场景的轻量 Agent 运行时与控制台。当前版本围绕三个核心能力展开：顶层会话隔离、多 Agent 运行树、Agent Skill 草稿审核发布。

## 核心能力

- 顶层会话隔离：每个线程维护独立上下文，适合并行处理不同任务。
- 主 Agent / 子 Agent 协作：运行树可追踪主执行链、子 Agent 分支和最近事件。
- Agent Skills 工作流：先生成草稿，再审核、发布、启停或回滚，避免直接改线上 Skill。
- 工具审批：高风险工具调用进入审批队列，支持人工通过或拒绝。
- 调度任务：用自然语言描述执行时间，复用同一套 Agent 聊天执行链路。
- 记忆与成本追踪：支持长期记忆检索和按任务、会话聚合的成本统计。
- Web 控制台：首页线程入口、状态树、当前会话、Skill 目录、任务调度、工具审批统一可视化。

## 当前工作流

### 1. 顶层线程

1. 从首页创建一个顶层线程。
2. 进入线程后发送消息，选择继续当前线程、启动新线程，或取消后重跑。
3. 需要专业流程时，在聊天或任务请求里填写 requested_skill_name。

### 2. Skill 草稿到发布

1. 通过 Skill 草稿接口或 Web 控制台提交草稿。
2. 审核通过后发布到项目 Skill 目录，并进入可启用状态。
3. 已发布 Skill 支持启用、停用和回滚。

说明：对外接口已统一使用 requested_skill_name，不再暴露旧的 skill_id 请求字段。

### 3. 调度与审批

1. 创建任务时填写标题、提示词、调度文本，以及可选 requested_skill_name。
2. 任务执行后会复用 Agent 执行链路，生成会话、运行、成本和最近结果。
3. 如果命中需审批工具，调用会进入审批队列，批准后继续执行。

## 控制台页面

- 会话入口：创建或进入顶层线程，快速查看活跃线程与运行中数量。
- 当前会话：在单线程上下文内聊天，支持流式步骤、Skill 选择和工具审批停点。
- 状态管理：查看线程总览、主 Agent / 子 Agent 运行树和最近错误。
- Skill 目录：过滤、启停 Skill，并审核待发布草稿。
- 任务调度：创建计划任务，查看最近执行结果、线程入口和累计成本。
- 工具列表：查看注册工具与待处理审批。

## 技术栈

| 组件 | 选型 |
|------|------|
| 运行时 | Python 3.11+ |
| Web 框架 | FastAPI + Uvicorn |
| 数据契约 | Pydantic v2 |
| 关系存储 | SQLite |
| 向量存储 | ChromaDB |
| 调度 | APScheduler |
| 日志 | Structlog |

## 项目结构

```text
src/
├── agents/          # 主 Agent / 子 Agent 编排、ReAct 循环、提示词
├── api/             # HTTP 路由（/api/v1）
├── app/             # 应用入口、生命周期、静态页面挂载
├── channels/        # 消息渠道抽象层
├── contracts/       # 跨模块数据契约
├── models/          # LLM 适配器与模型路由
├── services/        # Agent、Skill、Task、Memory、Cost 等服务
├── sessions/        # 会话状态与上下文管理
├── skills/          # Skill 目录、版本、审核与索引逻辑
├── storage/         # SQLite 持久化访问层
├── tools/           # 工具注册、审批与内置工具
└── web/             # 控制台前端静态资源
tests/
├── contracts/
├── integration/
└── unit/
```

## 快速开始

### 1. 安装依赖

```bash
py -3.11 -m pip install -e ".[dev]"
```

如果本机默认 Python 不是 3.11，请始终显式使用 py -3.11。

安装完成后，建议先进入项目虚拟环境，再运行服务和测试。Windows PowerShell 示例：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

### 2. 配置环境变量

```bash
copy .env.example .env
```

至少配置以下一组模型密钥：

- OPENAI_API_KEY
- 或 ANTHROPIC_API_KEY

其余常用变量见 .env.example，例如 APP_PORT、DATABASE_URL、VECTOR_DB_PATH。

### 3. 启动服务

```powershell
python -m uvicorn src.app.main:app --reload
```

如果不想手动激活虚拟环境，可以直接使用项目解释器：

```powershell
d:\self-claw\.venv\Scripts\python.exe -m uvicorn src.app.main:app --reload
```

注意：激活虚拟环境后不要再用 `py -3.11 -m uvicorn ...`，因为它会调用系统 Python，而不是 `.venv` 里的解释器；如果系统环境没有装全依赖，会报 `ModuleNotFoundError`。

启动后可访问：

- 控制台首页：http://localhost:8000/
- 健康检查：http://localhost:8000/api/v1/health
- OpenAPI 文档：http://localhost:8000/docs

### 4. 运行测试

```powershell
python -m pytest tests -v
```

如果未激活虚拟环境，也可以显式写成：

```powershell
d:\self-claw\.venv\Scripts\python.exe -m pytest tests -v
```

## 关键接口

### 健康与会话

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v1/health | 健康检查 |
| POST | /api/v1/sessions | 创建顶层线程 |
| GET | /api/v1/sessions | 查询会话列表 |
| GET | /api/v1/sessions/{session_id} | 查看会话详情 |
| POST | /api/v1/sessions/{session_id}/close | 关闭会话 |
| GET | /api/v1/status/entry | 首页线程入口数据 |
| GET | /api/v1/status/overview | 状态总览 |
| GET | /api/v1/status/sessions/{session_id} | 单线程运行树 |

### Agent 执行

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/agent/chat | 发送消息给 Agent，支持 stream=true |
| GET | /api/v1/agent/runs/{run_id} | 查询单次运行详情 |

请求体中的 Skill 选择字段为 requested_skill_name。它同时适用于聊天请求和调度任务创建。

### Skill 目录与审核

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v1/skills | 查询 Skill 目录 |
| POST | /api/v1/skills/reload | 重载 Skill 目录 |
| GET | /api/v1/skills/drafts | 查询 Skill 草稿 |
| POST | /api/v1/skills/drafts | 创建 Skill 草稿 |
| PATCH | /api/v1/skills/drafts/{draft_id} | 更新 Skill 草稿 |
| POST | /api/v1/skills/drafts/{draft_id}/approve | 批准并发布草稿 |
| POST | /api/v1/skills/drafts/{draft_id}/reject | 拒绝草稿 |
| POST | /api/v1/skills/{skill_name}/actions | 启用、停用或回滚 |
| GET | /api/v1/skills/{skill_name}/revisions | 查询修订历史 |
| GET | /api/v1/skills/audit | 查询审核与操作审计 |

说明：当前版本不再提供旧的“直接创建 Skill”公开接口，统一通过草稿审核流进入目录。

### 任务、工具与记忆

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v1/tasks | 查询任务列表 |
| POST | /api/v1/tasks | 创建任务 |
| POST | /api/v1/tasks/{task_id}/run-now | 立即执行 |
| POST | /api/v1/tasks/{task_id}/pause | 暂停任务 |
| POST | /api/v1/tasks/{task_id}/resume | 恢复任务 |
| POST | /api/v1/tasks/{task_id}/cancel | 取消任务 |
| GET | /api/v1/tools | 查询工具列表 |
| GET | /api/v1/tools/approvals | 查询审批队列 |
| POST | /api/v1/tools/approvals/{approval_id} | 批准或拒绝工具调用 |
| GET | /api/v1/cost/summary | 查看成本汇总 |
| GET | /api/v1/memory/search | 检索记忆 |

## 示例

### 创建顶层线程

```bash
curl -X POST http://localhost:8000/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "web-user",
    "title": "竞品日报线程",
    "channel_type": "web"
  }'
```

### 在线程里发起流式聊天

```bash
curl -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
    "session_id": "<session_id>",
    "message": "整理今天的竞品动态并给出风险判断",
    "task_mode": "continue",
    "stream": true,
    "requested_skill_name": "market_watch"
  }'
```

### 创建 Skill 草稿

```bash
curl -X POST http://localhost:8000/api/v1/skills/drafts \
  -H "Content-Type: application/json" \
  -d '{
    "requested_action": "create",
    "proposed_name": "market_watch",
    "draft_skill_md": "---\nname: market_watch\ndescription: 竞品监控\n---\n# market_watch\n",
    "operator": "web-user",
    "user_intent_summary": "把竞品监控流程固化为可复用 Skill"
  }'
```

### 创建调度任务

```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "每日竞品汇总",
    "prompt": "汇总昨天至今的竞品动态并写出日报",
    "schedule_text": "每天 09:30",
    "requested_skill_name": "market_watch"
  }'
```

## 规格文档

项目遵循先规格、后实现的工作方式。完整契约、数据模型和验收条件见 [SPEC.md](SPEC.md)。

## 许可证

Private - 内部使用