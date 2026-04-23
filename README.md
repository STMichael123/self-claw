# Self-Claw

轻量级企业 Agent 框架，面向长链路任务与自动化场景。基于 SPEC v1.2.0 实现，提供 ReAct 循环、多 Agent 协作、Skill 管理、工具审批、任务调度与分层记忆。

## 核心特性

- **ReAct Agent 循环** — 意图理解 → 规划 → 工具执行 → 观察，支持并行安全工具并发执行
- **主 / 子 Agent 协作** — 自动识别复杂任务并派生子 Agent（FR-006），运行树可追踪
- **Skill 工作流** — 通过 `/skill` 斜杠命令创建草稿 → 审核 → 发布 → 启停 → 回滚（FR-015）
- **工具审批** — 高风险工具调用进入审批队列，人工通过或拒绝后继续执行
- **自然语言调度** — "每天 09:30" 即可创建计划任务，复用 Agent 执行链路
- **分层记忆** — Principle（全文）→ 长期记忆（索引摘要）→ 短期记忆 → 语义检索（FR-008）
- **Token 压缩** — 累计 token 达模型窗口 80% 时自动压缩上下文（FR-010）
- **LLM 重试与熔断** — 指数退避（3 次，1s–30s）+ 熔断器（5 次/分钟）+ fallback 模型（NFR-001）
- **Hook 扩展** — `.agents/hooks/*.py` 自动发现，5 个 hook 点，5 秒超时（FR-016）
- **Web 控制台** — 线程管理、运行树、Skill 目录、任务调度、工具审批、成本追踪
- **配置分层** — 默认值 → `.agents/settings.d/*.json` 深度合并 → 环境变量（Section 17.3）

## 技术栈

| 组件 | 选型 |
|------|------|
| 运行时 | Python 3.11+ |
| Web 框架 | FastAPI + Uvicorn |
| 数据契约 | Pydantic v2 |
| 关系存储 | SQLite |
| 向量存储 | ChromaDB |
| LLM SDK | OpenAI / Anthropic |
| 调度 | APScheduler |
| 日志 | Structlog |

## 项目结构

```
src/
├── agents/          # 主 Agent / 子 Agent 编排、ReAct 循环、提示词编排
├── api/             # HTTP 路由（/api/v1）
├── app/             # 应用入口与生命周期管理
├── channels/        # 消息渠道抽象层
├── contracts/       # 错误码、数据模型等跨模块契约
├── models/          # LLM 适配器、重试熔断、模型路由、token 计数
├── services/        # Agent、Skill、Task、Memory、Cost、Hook 等服务
├── sessions/        # 会话状态、上下文滑动窗口、JSONL 归档
├── skills/          # Skill 目录、版本、审核与索引
├── storage/         # SQLite 持久化访问层
├── tools/           # 工具注册、审批、内置工具（web_fetch/list_dir/read_file 等）
└── web/             # 控制台前端
tests/
├── integration/     # 集成测试（API 端到端）
└── unit/            # 单元测试（125 个）
.agents/
├── skills/          # Skill 定义目录
├── hooks/           # Hook 扩展目录
├── memory/          # 长期记忆目录
├── principle.md     # Agent 行为原则
└── settings.d/      # 配置覆盖 JSON
```

## 快速开始

### 1. 安装依赖

```bash
python -m pip install -e ".[dev]"
```

需要 Python 3.11+。Windows 下可使用 `py -3.11 -m pip install -e ".[dev]"`。

激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

至少配置一组模型密钥：

- `OPENAI_API_KEY` — OpenAI 或兼容 API
- 或 `ANTHROPIC_API_KEY` — Anthropic Claude

### 3. 启动服务

```bash
python -m uvicorn src.app.main:app --reload
```

启动后访问：

| 入口 | 地址 |
|------|------|
| 控制台 | http://localhost:8000/ |
| 健康检查 | http://localhost:8000/api/v1/health |
| OpenAPI 文档 | http://localhost:8000/docs |

### 4. 运行测试

```bash
python -m pytest tests/ -v
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENAI_API_KEY` | — | OpenAI API 密钥 |
| `ANTHROPIC_API_KEY` | — | Anthropic API 密钥 |
| `LLM_MODEL` | `gpt-4o` | 默认 LLM 模型 |
| `DATABASE_PATH` | `data/self_claw.db` | SQLite 数据库路径 |
| `FILE_SANDBOX_ROOT` | `data/workspace` | 文件沙箱根目录 |
| `SKILL_ROOT` | `.agents/skills` | Skill 目录路径 |
| `SESSION_TIMEOUT_MINUTES` | `30` | 会话超时（分钟） |
| `MAX_PARALLEL_MAIN_RUNS` | `3` | 最大并行主 Agent 数 |
| `MAX_PARALLEL_SUB_AGENTS` | `5` | 最大并行子 Agent 数 |
| `DEFAULT_MAX_STEPS` | `10` | Agent 默认最大步数 |
| `DEFAULT_WEB_USER_ID` | `web-user` | 默认 Web 用户 ID |
| `EXEC_WHITELIST` | `ls,dir,echo,...` | exec 命令白名单 |
| `SUB_AGENT_TRIGGER_KEYWORDS` | `分析,调研,...` | 子 Agent 触发关键词 |

完整变量列表见 `.env.example`。配置优先级：默认值 < `.agents/settings.d/*.json` < 环境变量。

## API 概览

### 会话与状态

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/sessions` | 创建会话 |
| GET | `/api/v1/sessions` | 查询会话列表 |
| GET | `/api/v1/sessions/{id}` | 查看会话详情 |
| POST | `/api/v1/sessions/{id}/close` | 关闭会话 |
| GET | `/api/v1/status/entry` | 首页线程入口 |
| GET | `/api/v1/status/overview` | 状态总览 |
| GET | `/api/v1/status/sessions/{id}` | 单线程运行树 |

### Agent 执行

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/agent/chat` | 发送消息（支持 `stream=true`） |
| GET | `/api/v1/agent/runs/{id}` | 查询运行详情 |

`task_mode` 支持 `auto`、`continue`、`new_task`、`cancel_and_rerun`。通过 `requested_skill_name` 指定 Skill。

### Skill 目录

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/skills` | 查询 Skill 目录 |
| GET | `/api/v1/skills/{name}` | 查看 Skill 详情 |
| POST | `/api/v1/skills/reload` | 重载目录 |
| POST | `/api/v1/skills/{name}/actions` | 启用 / 停用 |
| GET | `/api/v1/skills/audit` | 审核日志 |

### 任务调度

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/tasks` | 查询任务列表 |
| POST | `/api/v1/tasks` | 创建任务 |
| POST | `/api/v1/tasks/{id}/run-now` | 立即执行 |
| POST | `/api/v1/tasks/{id}/pause` | 暂停 |
| POST | `/api/v1/tasks/{id}/resume` | 恢复 |
| POST | `/api/v1/tasks/{id}/cancel` | 取消 |

### 工具与审批

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/tools` | 工具列表 |
| GET | `/api/v1/tools/approvals` | 审批队列 |
| POST | `/api/v1/tools/approvals/{id}` | 批准 / 拒绝 |

### 记忆与成本

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/memory/search` | 检索记忆 |
| GET | `/api/v1/memory/principle` | 获取 Agent 原则 |
| PUT | `/api/v1/memory/principle` | 更新 Agent 原则 |
| GET | `/api/v1/memory/long-term` | 长期记忆列表 |
| POST | `/api/v1/memory/long-term` | 写入长期记忆 |
| GET | `/api/v1/cost/summary` | 成本汇总 |

## 示例

### 创建会话并聊天

```bash
# 创建会话
curl -X POST http://localhost:8000/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"title": "数据分析线程"}'

# 发送消息
curl -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "<session_id>",
    "message": "分析本周销售数据趋势",
    "task_mode": "continue"
  }'
```

### 使用斜杠命令激活 Skill

```bash
curl -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "/create-skill 帮我创建一个竞品监控 Skill"}'
```

### 创建调度任务

```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "title": "每日竞品汇总",
    "prompt": "汇总竞品动态并写出日报",
    "schedule_text": "每天 09:30"
  }'
```

## 架构概要

```
┌─────────────────────────────────────────────┐
│  Web Console / API / Channels               │
├─────────────────────────────────────────────┤
│  AgentService  →  SessionManager            │
│       ↓                                     │
│  MainAgent (ReAct Loop)                     │
│       ├── ToolExecutor (parallel safe)      │
│       ├── SubAgentExecutor                  │
│       └── HookRegistry (pre/post)           │
├─────────────────────────────────────────────┤
│  SkillService  MemoryService  FileWorkspace │
│  TaskService   CostService    Scheduler     │
├─────────────────────────────────────────────┤
│  LLMRetryWrapper → LLMAdapter               │
│       (retry + circuit breaker + fallback)  │
├─────────────────────────────────────────────┤
│  SQLite  │  ChromaDB  │  File System        │
└─────────────────────────────────────────────┘
```

## 规格文档

完整功能规格、数据模型和验收条件见 [SPEC.md](SPEC.md)（v1.2.0）。

## 许可证

Private — 内部使用
