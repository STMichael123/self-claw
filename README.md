# Self-Claw

轻量级企业 Agent 框架 — 受 OpenClaw 启发，面向业务人员，低部署复杂度。

## 特性

- **ReAct Agent 循环** — 思考→动作→观察多步推理
- **主/子 Agent 架构** — 上下文隔离，结构化回传
- **Skill 管理** — 企业 SOP 沉淀为可版本化、可回滚的 Skill
- **工具系统** — 装饰器注册 + 内置工具（web_fetch / web_search / exec）+ 审批流
- **混合记忆** — 文件型全文检索 + 向量语义检索（ChromaDB）
- **可扩展渠道** — ChannelAdapter 抽象层，新增渠道无需改动核心代码
- **成本追踪** — 按任务/会话/天聚合 Token 用量与费用
- **Web 管理看板** — 任务总览、成本趋势、轻量操作

## 技术栈

| 组件 | 选型 |
|------|------|
| 运行时 | Python 3.11+ |
| Web 框架 | FastAPI + Uvicorn |
| 数据契约 | Pydantic v2 |
| 关系存储 | SQLite |
| 向量存储 | ChromaDB（可选 LanceDB） |
| LLM | OpenAI + Anthropic 双适配器 |
| 调度 | APScheduler |
| 日志 | Structlog |

## 项目结构

```
src/
├── agents/          # Agent 编排：ReAct 循环、主 Agent、子 Agent、提示词
├── api/             # HTTP 路由（/api/v1）
├── app/             # 应用入口与生命周期
├── channels/        # 消息渠道抽象层（ChannelAdapter）
├── contracts/       # 跨模块契约：Pydantic 模型、错误码
├── models/          # LLM 适配器、模型路由、Token 计数
├── services/        # 调度、通知、记忆、成本追踪
├── sessions/        # 会话生命周期与上下文管理
├── skills/          # Skill 注册、版本管理与运行时
├── storage/         # SQLite 数据库访问层
└── tools/           # 工具注册表、内置工具、装饰器
tests/
├── unit/            # 单元测试
├── integration/     # 集成测试
└── contracts/       # 契约测试
```

## 快速开始

### 1. 安装依赖

```bash
# 需要 Python 3.11+（确认版本: py -3.11 --version）
py -3.11 -m pip install -e ".[dev]"
```

> **注意**：系统默认 `python` 可能不是 3.11。请始终使用 `py -3.11` 调用正确版本。

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY 或 ANTHROPIC_API_KEY
```

### 3. 启动服务

```bash
py -3.11 -m uvicorn src.app.main:app --reload
```

访问 http://localhost:8000/api/v1/health 验证服务状态。

### 4. 运行测试

```bash
py -3.11 -m pytest tests/ -v
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/health` | 健康检查 |
| POST | `/api/v1/skills` | 新建 Skill |
| GET | `/api/v1/skills` | 查询 Skill 列表 |
| POST | `/api/v1/agent/chat` | 发送消息触发 Agent |
| GET | `/api/v1/tools` | 查询已注册工具 |
| GET | `/api/v1/sessions` | 查询会话列表 |

完整 API 定义见 [SPEC.md](SPEC.md) §9。

## 规格文档

本项目遵循"先规格、后代码"的工作流。所有需求、接口、数据模型的唯一事实来源：

→ [SPEC.md](SPEC.md)（v0.6.0）

## 许可证

Private — 内部使用
