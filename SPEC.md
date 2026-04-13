# Self-Claw 规格说明（唯一事实来源）

状态：生效中
版本：0.6.0
最后更新：2026-04-13
负责人：产品 + 工程

## 1. 目的
本文档是 Self-Claw 项目的唯一事实来源。
所有实现工作必须严格遵循以下顺序：
1. 先更新本规格文档。
2. 评审并批准规格变更。
3. 再实施与已批准规格条目映射的代码变更。

除紧急热修复（见第 13 节）外，不允许在没有关联规格变更的情况下修改代码。

## 2. 产品目标
构建一个受 OpenClaw 启发、但更轻量且对业务人员更友好的 Agent 框架，降低部署与使用复杂度。

主要用户：
- 业务运营人员（非工程师）
- 内部运营/自动化团队

部署模式：
- 企业自托管、单租户

主要入口：
- Web 管理界面
- 可扩展消息渠道（待接入：企业微信、微信、QQ、飞书、Telegram 等）

## 3. 设计原则
- 以最小复杂度换取可验证价值。
- 约定优于配置。
- 面向非技术用户的人类可读输出。
- 安全默认值与优雅降级。
- 可观测性优先（日志、指标、状态可见）。
- 渐进增强（先 MVP，后高级能力）。

## 4. 范围
### 4.1 范围内（MVP）
- Agent 核心执行循环（意图理解、规划、执行、观察）。
- Skill 运行时定义与执行语义。
- 工具注册、发现与调用。
- 自然语言任务调度。
- 任务执行与失败通知。
- 多轮会话与上下文管理。
- Token/成本使用追踪。
- 混合记忆（文件型 + 向量数据库）。
- Skill（基于企业 SOP）创建、版本管理与启停。
- 主 Agent + 子 Agent 协作执行。
- LLM 模型集成与提示词管理。
- 可扩展消息渠道抽象层（首版不含具体渠道实现）。
- Web 看板用于任务与系统可视化。

### 4.2 范围外（MVP）
- 多租户架构。
- 插件市场/插件生态。
- 高级沙箱隔离。
- 复杂角色/权限矩阵。
- 具体消息渠道实现（企业微信、微信、QQ、飞书、Telegram 等，但抽象层首版必须就绪）。

## 5. 系统架构
分层架构：
1. 接入层
- 消息渠道抽象层（ChannelAdapter 接口，首版仅提供抽象与测试实现，具体渠道如企业微信/微信/QQ/飞书/Telegram 待后续接入）
- Web UI + HTTP API 网关
- 请求认证与速率限制

2. 应用层
- Agent 核心执行循环（意图识别 → 规划 → Skill/工具选择 → 执行 → 观察 → 回复）
- 主 Agent 任务拆解与路由（支持 LLM 驱动路由 + 规则回退）
- 子 Agent 执行器与结果回收
- 会话管理器（多轮对话、上下文积累与截断）
- 提示词编排器（system prompt + Skill prompt + 上下文组合）

3. 能力层
- Skill 注册、加载与运行时执行服务
- 工具注册表与工具调用协调器
- 调度服务
- 通知服务（通过渠道抽象层发送，不绑定具体渠道）
- 记忆服务（文件型 + 向量数据库）
- 成本核算服务

4. 模型集成层
- LLM 适配器（支持 OpenAI 兼容接口 + Anthropic）
- Embedding 适配器（用于向量记忆的文本向量化）
- 模型路由与降级（主模型不可用时自动切换备用模型）
- 上下文窗口管理（自动截断与摘要压缩）
- 流式/阻塞双模式输出
- Token 计数与成本钩子

5. 基础层
- 配置管理（分层：默认值 → 环境变量 → 部署覆盖）
- 存储（SQLite + 文件系统 + 向量数据库）
- 日志与遥测
- 健康检查

## 6. 功能需求
每条需求拥有稳定编号：FR-xxx。

### FR-001 自然语言调度
系统必须将业务语言的调度请求转换为结构化调度（cron/interval/once），并提供用户确认。
验收标准：
- 用户在最终确认前可看到解析后的调度结果与下次执行时间。
- 用户可以创建、暂停、恢复与取消任务。

### FR-002 人类可读任务状态
系统必须以非技术语言展示任务状态。
验收标准：
- 展示下次执行时间、最近一次成功/失败与当前状态。
- 默认界面输出应避免直接暴露原始内部字段。

### FR-003 失败通知
系统必须在任务失败时通过已配置的消息渠道发送通知。
验收标准：
- 通知包含原因分类、建议动作与重试入口。
- 单个任务失败不得导致全局调度循环停止。
- 通知通过渠道抽象层发送，不绑定具体渠道实现。

### FR-004 成本追踪
系统必须记录模型调用用量与成本。
验收标准：
- 记录输入 Token、输出 Token、预估成本、时间戳及关联任务/会话。
- 提供按天与按任务聚合统计。

### FR-005 混合记忆
系统必须支持文件型与向量数据库双模式记忆。
文件型记忆：
- 短期会话日志存储为 Markdown 文件，按日归档。
- 长期记忆存储为可编辑的 Markdown/JSON 文件，支持人工编辑。
- 支持基础全文检索。
向量记忆：
- 会话摘要、任务结果、Skill 执行产物自动写入向量数据库。
- 使用 Embedding 模型生成文本向量，存入本地向量库（默认使用 ChromaDB 或 LanceDB 等轻量方案）。
- 支持语义相似度检索（top-K）。
- Agent 执行循环中可自动检索相关记忆注入上下文。
验收标准：
- 全文检索与语义检索均可返回相关结果。
- 长期记忆支持人工编辑，编辑后向量索引自动重建。
- 向量写入失败不影响主流程（降级为仅文件型）。

### FR-006 Web 管理看板
系统必须为业务用户提供 Web 管理看板。
验收标准：
- 任务总览（状态、下次执行、失败情况）。
- 成本总览（每日趋势、按任务用量排行）。
- 轻量操作（暂停/恢复/重试）。

### FR-007 可扩展消息渠道抽象层
系统必须提供统一的消息渠道抽象接口，以便后续接入任意社交/IM 平台。
ChannelAdapter 接口约定：
- receive_message(raw_event) -> InboundMessage — 将平台原始事件解析为统一的入站消息结构。
- send_message(outbound: OutboundMessage) -> SendResult — 将统一出站消息结构转换为平台特定格式并发送。
- verify_callback(request) -> bool — 校验平台回调请求的合法性。
- refresh_credentials() — 刷新平台认证凭据（如 access_token）。
- get_user_identity(platform_uid) -> UserIdentity — 将平台用户 ID 映射为系统内操作者标识。
统一消息模型：
- InboundMessage: channel_type, platform_uid, message_type(text|event|image|file), content, timestamp, raw_payload
- OutboundMessage: channel_type, target_uid, format(text|markdown|card), content, reply_to(可为空)
- SendResult: success, message_id(可为空), error(可为空)
- UserIdentity: user_id, display_name, channel_type, platform_uid
首版实现要求：
- 提供 ChannelAdapter 抽象基类与完整类型定义。
- 提供 TestChannelAdapter（内存实现）用于开发、测试与演示。
- 渠道通过配置注册，运行时按 channel_type 动态加载对应适配器。
- 新增渠道时仅需实现 ChannelAdapter 接口并注册，不变动核心代码。
验收标准：
- TestChannelAdapter 可完成完整的发送/接收/回调校验流程。
- 新增一个渠道适配器无需修改 Agent、会话、通知等核心模块的代码。
- 未配置任何渠道时，系统仅通过 Web API 工作，不报错。

### FR-008 Skill 生命周期管理
系统必须支持将企业 SOP 以 Skill 的形式沉淀，并可持续迭代。
验收标准：
- 支持新建、更新、启用、停用 Skill。
- 每个 Skill 必须具备唯一标识、版本号、输入输出约定与适用场景描述。
- Skill 更新后需保留历史版本，支持回滚到指定版本。

### FR-009 主/子 Agent 上下文隔离
系统必须支持主 Agent 与子 Agent 的分层协作，以保证任务上下文干净、互不污染。
验收标准：
- 主 Agent 负责任务分解与子 Agent 调度，子 Agent 仅接收最小必要上下文。
- 子 Agent 会话上下文默认隔离，跨子 Agent 不共享临时上下文。
- 仅允许通过显式结构化产物（例如 JSON 结果）回传到主 Agent。
- 支持为每个子 Agent 记录独立执行日志与成本统计。

### FR-010 Agent 核心执行循环
系统必须实现完整的 Agent 推理-执行循环，作为所有任务处理的统一入口。
执行流程：
1. 接收输入（用户消息 / 定时任务触发 / 子 Agent 委派）。
2. 加载会话上下文与相关记忆。
3. 构造提示词（system prompt + Skill prompt + 上下文 + 工具描述）。
4. 调用 LLM 获取推理结果。
5. 解析 LLM 输出：判断是直接回复、调用工具、调用 Skill，还是委派子 Agent。
6. 执行动作并观察结果。
7. 决定是否需要继续循环（多步推理）或终止并回复用户。
验收标准：
- 推理策略采用 ReAct（Reasoning + Acting）模式，每步包含思考、动作、观察三阶段。
- 单次循环最大步数可配置（默认 10 步），超限后强制终止并返回中间结果。
- 每步推理的思考过程、动作选择与观察结果均需记录到执行日志。
- 支持流式输出模式（用于 Web/消息渠道实时反馈）与阻塞模式（用于定时任务）。
- 主 Agent 可在循环中决定自行处理或委派子 Agent，路由策略支持 LLM 驱动与规则回退双模式。

### FR-011 Skill 运行时定义
系统必须明确 Skill 的内容格式与运行时执行语义。
Skill 内容结构：
- skill_prompt: string — 该 Skill 的专用系统提示词模板，描述角色、目标、约束与输出格式。
- allowed_tools: string[] — 该 Skill 执行期间可调用的工具白名单。
- input_schema: object（JSON Schema）— 输入参数约定。
- output_schema: object（JSON Schema）— 输出结构约定。
- examples: object[]（可选）— 少量示例用于 few-shot 引导。
- max_steps: int（可选）— 覆盖全局默认最大步数。
执行语义：
- Skill 被调用时，Agent 循环将 skill_prompt 注入 system prompt，并将工具列表限定为 allowed_tools。
- Skill 执行完成后，输出必须经过 output_schema 校验，不合格则标记为 SCHEMA_VALIDATION_FAILED。
- Skill 不可嵌套调用其他 Skill（MVP 阶段），避免循环依赖复杂度。
验收标准：
- Skill 内容可通过 API 或文件导入创建。
- Skill 执行时仅能使用 allowed_tools 中声明的工具，未声明的工具调用请求被拒绝。
- 输出不符合 output_schema 时返回明确错误而非静默通过。

### FR-012 工具注册与调用
系统必须提供统一的工具注册、发现与调用机制。
工具描述格式（每个工具必须包含）：
- name: string — 唯一标识。
- display_name: string — 人类可读名称。
- description: string — 功能描述（会注入 LLM 提示词）。
- parameters: object（JSON Schema）— 输入参数定义。
- returns: object（JSON Schema）— 返回值定义。
- requires_approval: bool — 是否需要人工审批后执行（默认 false）。
- timeout_sec: int — 执行超时时间。
工具分类：
- 内置工具：web_fetch（网页抓取）、web_search（搜索）、exec（命令执行，MVP 阶段仅限白名单命令）。
- 自定义工具：通过 Python 函数 + 装饰器注册，运行时自动发现。
安全边界：
- exec 工具必须有命令白名单，禁止执行任意命令。
- 所有工具调用记录工具名、参数、返回值、耗时到日志。
- requires_approval=true 的工具调用暂停循环，通过消息渠道/Web 推送审批请求，审批通过后继续。
验收标准：
- 工具列表可通过 API 查询（GET /api/v1/tools）。
- 工具调用参数经过 JSON Schema 校验，不合格则拒绝调用。
- 工具执行超时后返回 TOOL_EXECUTION_FAILED 并记录日志。

### FR-013 会话与上下文管理
系统必须管理多轮对话的会话生命周期与上下文积累。
会话生命周期：
- 创建：用户首次发送消息或 API 显式创建时自动生成会话。
- 活跃：持续接收消息，按消息时间戳更新 last_active_at。
- 过期：超过可配置的空闲超时（默认 30 分钟）后自动标记为 expired。
- 归档：过期会话的上下文摘要写入长期记忆，原始消息保留到文件存档。
上下文管理：
- 上下文窗口由模型 max_tokens 决定，系统保留 20% 给输出。
- 当消息历史超出窗口时，自动执行压缩：保留最近 N 条原始消息 + 历史消息的 LLM 摘要。
- 压缩事件必须记录到日志，摘要内容存入 MemoryIndex。
验收标准：
- 同一用户连续消息属于同一会话，超时后新消息创建新会话。
- 上下文压缩后，Agent 仍能基于摘要回答历史相关问题。
- 会话归档后的摘要可被记忆检索命中。

## 7. 非功能需求
每条需求拥有稳定编号：NFR-xxx。

### NFR-001 可靠性
- 单个任务失败时，调度器应持续运行。
- 服务重启后可恢复已持久化任务。

### NFR-002 可观测性
- 结构化日志并包含关联 ID。
- 提供健康检查端点与最小运行指标。

### NFR-003 易用性
- 核心流程应可由非工程师在不懂 CLI 的前提下完成。
- 用户可见语言必须是人类可读表达。

### NFR-004 可维护性
- 服务边界模块化。
- 在 PR 流程中强制需求到代码的可追踪性。
- Skill 与 Agent 能力边界清晰，避免隐式耦合。

### NFR-005 安全性（单租户基线）
- 密钥通过环境变量或安全密钥源存储。
- 消息渠道回调必须通过 ChannelAdapter.verify_callback 校验。

### NFR-006 并发与限流
- 同时执行的子 Agent 数量可配置（默认最大 5 个并行）。
- 调度器同时触发的任务超过并发上限时，进入队列排序等待。
- LLM API 调用按模型提供商的速率限制进行令牌桶限流。
- 消息渠道推送按各平台频率限制进行限流（由 ChannelAdapter 实现方配置）。
- 超过限流的请求返回 RATE_LIMITED 错误码，不排队不重试。

## 8. 数据模型（初版）
- Task：id, title, skill_id(可为空), schedule_type, schedule_expr, status, next_run_at, last_run_at, last_result, created_at, updated_at
- RunLog：id, task_id, started_at, ended_at, status, error_category, error_detail
- UsageLog：id, task_id, session_id, agent_run_id, input_tokens, output_tokens, estimated_cost, model_name, created_at
- Session：id, user_id, channel_type(string), status(active|expired|archived), context_snapshot, summary, created_at, last_active_at, expired_at
- MemoryIndex：id, scope(short_term|long_term), session_id(可为空), ref_path, summary, embedding_id(可为空,关联向量记录), updated_at
- VectorRecord：id, content_hash, text_chunk, embedding(vector), source_type(session_summary|task_result|skill_output|long_term_memory), source_id, created_at
- Skill：id, name, version, status, sop_source, skill_prompt, allowed_tools, input_schema, output_schema, examples, max_steps, created_at, updated_at
- SkillVersion：id, skill_id, version, change_note, content_snapshot, created_at
- AgentRun：id, parent_run_id, agent_role(main|sub), skill_id(可为空), session_id, task_ref, context_ref, result_ref, started_at, ended_at, status, steps_count
- ToolCall：id, agent_run_id, tool_name, parameters, result, duration_ms, status, created_at
- AuditLog：id, operator, action, entity_type, entity_id, version, diff_summary, created_at
- ChannelConfig：id, channel_type, display_name, adapter_class, credentials_ref, rate_limit, enabled, created_at, updated_at

## 9. API 与接口约定（初版）
- 内部服务通过明确的服务接口通信。
- Web API 从 /api/v1 开始版本化。
- 破坏性 API 变更必须提升规格主版本号。

### 9.1 Skill 管理 API（对应 FR-008）
基础路径：`/api/v1/skills`

1. 新建 Skill
- 方法与路径：POST `/api/v1/skills`
- 请求体：
  - name: string（唯一）
  - display_name: string
  - scenario: string（适用场景）
  - sop_source: string（SOP 来源说明或引用）
  - input_schema: object（JSON Schema）
  - output_schema: object（JSON Schema）
  - content: string（首版指令内容）
- 响应体：
  - skill_id: string
  - version: string（初始为 `v1`）
  - status: string（`enabled` 或 `disabled`）

2. 更新 Skill（创建新版本）
- 方法与路径：POST `/api/v1/skills/{skill_id}/versions`
- 请求体：
  - change_note: string
  - content: string
  - input_schema: object（可选）
  - output_schema: object（可选）
- 响应体：
  - skill_id: string
  - version: string（例如 `v2`）
  - previous_version: string

3. 启用/停用 Skill
- 方法与路径：PATCH `/api/v1/skills/{skill_id}/status`
- 请求体：
  - status: string（仅允许 `enabled` 或 `disabled`）
- 响应体：
  - skill_id: string
  - status: string
  - updated_at: datetime

4. 回滚 Skill 版本
- 方法与路径：POST `/api/v1/skills/{skill_id}/rollback`
- 请求体：
  - target_version: string
  - reason: string
- 响应体：
  - skill_id: string
  - active_version: string
  - rollback_from: string

5. 查询 Skill 列表与详情
- 方法与路径：GET `/api/v1/skills`
- 查询参数：
  - status: enabled|disabled（可选）
  - keyword: string（可选）
- 方法与路径：GET `/api/v1/skills/{skill_id}`

6. Skill 审计要求
- 每次新建、更新、启停、回滚必须产生审计记录。
- 审计记录最少字段：operator, action, skill_id, version, timestamp, diff_summary。

### 9.2 主/子 Agent 协作协议（对应 FR-009）
1. 主 Agent -> 子 Agent 调用契约
- 接口：`SubAgentExecutor.run(request)`
- request 最小字段：
  - run_id: string
  - parent_run_id: string
  - sub_agent_role: string
  - goal: string
  - allowed_skills: string[]
  - context_pack: object（仅最小必要上下文）
  - timeout_sec: int

2. 子 Agent -> 主 Agent 回传契约
- 响应结构：
  - run_id: string
  - status: success|failed|partial
  - output: object（必须满足约定 output_schema）
  - artifacts: object[]（可选）
  - usage: object（input_tokens, output_tokens, estimated_cost）
  - error: object（失败时必填：code, message, category）

3. 上下文隔离规则
- 子 Agent 不可直接读取其他子 Agent 的临时上下文。
- 子 Agent 不可直接写入主 Agent 会话上下文。
- 跨 Agent 信息传递仅允许通过主 Agent 的结构化回传通道。
- 默认不继承全局记忆，仅可读取主 Agent 显式下发的 `context_pack`。

4. 子 Agent 生命周期状态
- 状态机：`queued -> running -> success|failed|timeout|cancelled`
- 任一子 Agent 失败时，主 Agent 可选择重试、降级或终止整链路。

### 9.3 错误码与重试约定（Skill 与 Agent 共享）
- `SKILL_NOT_FOUND`: Skill 不存在，禁止自动重试。
- `SKILL_DISABLED`: Skill 已停用，禁止自动重试。
- `SCHEMA_VALIDATION_FAILED`: 输入或输出不符合 schema，禁止自动重试。
- `SUBAGENT_TIMEOUT`: 子 Agent 超时，允许最多 2 次指数退避重试。
- `TOOL_EXECUTION_FAILED`: 工具执行失败，允许按策略重试。
- `TOOL_NOT_ALLOWED`: 工具未在 Skill 白名单中，禁止重试。
- `TOOL_APPROVAL_PENDING`: 工具需人工审批，循环暂停等待。
- `UPSTREAM_MODEL_ERROR`: 模型上游错误，允许重试并记录熔断计数。
- `MODEL_FALLBACK`: 主模型降级到备用模型，记录但不阻断。
- `CONTEXT_OVERFLOW`: 上下文超窗口限制，触发压缩后重试一次。
- `MAX_STEPS_EXCEEDED`: Agent 循环超最大步数，强制终止。
- `SESSION_EXPIRED`: 会话已过期，拒绝追加消息。
- `RATE_LIMITED`: 超过速率限制，拒绝请求。
- `VECTOR_WRITE_FAILED`: 向量写入失败，降级为文件型，记录但不阻断。
- `EMBEDDING_FAILED`: 向量化调用失败，允许重试一次。
- `CHANNEL_NOT_CONFIGURED`: 渠道未配置或已禁用，禁止重试。
- `CHANNEL_SEND_FAILED`: 渠道消息发送失败，允许重试一次。

### 9.4 接口验收检查
- FR-005 验收必须覆盖：全文检索、向量语义检索、索引重建、向量写入降级。
- FR-007 验收必须覆盖：ChannelAdapter 接口完整性、TestChannelAdapter 全流程、渠道动态加载、无渠道时仅 Web 可用。
- FR-008 验收必须覆盖：新建、更新、启停、回滚、审计记录完整性。
- FR-009 验收必须覆盖：上下文隔离、结构化回传、独立成本与日志记录。
- FR-010 验收必须覆盖：完整循环流转、最大步数限制、流式输出。
- FR-011 验收必须覆盖：prompt 注入、工具白名单限制、output_schema 校验。
- FR-012 验收必须覆盖：工具注册发现、参数校验、超时处理、审批流。
- FR-013 验收必须覆盖：会话创建与过期、上下文压缩、归档后检索。

### 9.5 Agent 执行 API（对应 FR-010）
1. 发送消息并触发 Agent 执行
- 方法与路径：POST `/api/v1/agent/chat`
- 请求体：
  - session_id: string（可选，为空则自动创建新会话）
  - message: string
  - stream: bool（默认 false）
- 响应体（阻塞模式）：
  - session_id: string
  - run_id: string
  - reply: string
  - steps: object[]（思考、动作、观察的列表）
  - usage: object
- 响应体（流式模式）：
  - SSE 事件流，事件类型：thinking / action / observation / reply / usage / done

2. 查询 Agent 执行记录
- 方法与路径：GET `/api/v1/agent/runs/{run_id}`
- 响应体：AgentRun 完整字段 + 关联的 ToolCall 列表 + UsageLog。

### 9.6 工具管理 API（对应 FR-012）
1. 查询已注册工具列表
- 方法与路径：GET `/api/v1/tools`
- 查询参数：
  - category: builtin|custom（可选）
- 响应体：工具描述列表（name, display_name, description, parameters, returns, requires_approval）。

2. 工具审批
- 方法与路径：POST `/api/v1/tools/approvals/{approval_id}`
- 请求体：
  - decision: approved|rejected
  - operator: string
- 响应体：
  - approval_id: string
  - decision: string
  - resumed_run_id: string（审批通过后恢复的 Agent 循环 run_id）

### 9.7 会话管理 API（对应 FR-013）
1. 查询会话列表
- 方法与路径：GET `/api/v1/sessions`
- 查询参数：
  - status: active|expired|archived（可选）
  - user_id: string（可选）
- 响应体：会话列表（id, user_id, channel, status, created_at, last_active_at, message_count）。

2. 查询会话详情与消息历史
- 方法与路径：GET `/api/v1/sessions/{session_id}`
- 响应体：Session 完整字段 + 消息列表（role, content, timestamp）+ 关联 AgentRun 列表。

3. 手动关闭会话
- 方法与路径：POST `/api/v1/sessions/{session_id}/close`
- 响应体：
  - session_id: string
  - status: archived
  - summary: string（自动生成的会话摘要）

## 10. 可追踪性规则
每个实现产物必须映射到需求编号。

示例：
- Commit 信息："feat(scheduler): FR-001 FR-002"
- PR 描述必须包含：
  - 规格版本
  - 覆盖的需求编号
  - 已执行的验收检查

未包含需求映射的 PR 不可进入评审。

## 11. 完成定义
变更只有在以下全部满足时才算完成：
1. 规格已更新并获批准。
2. 代码已实现并链接需求编号。
3. 测试已新增或更新。
4. 验收标准已验证。
5. 可观测性/日志影响已评估。
6. 文档/变更记录已更新。

## 12. 变更管理流程
### 12.1 常规变更流程
1. 在本文件提出规格变更。
2. 新增或修改需求编号与验收标准。
3. 递增规格版本号（语义化）：
   - MAJOR：破坏性契约变更或范围重置
   - MINOR：新增能力
   - PATCH：澄清或非破坏性更新
4. 在第 14 节添加变更记录。
5. 变更获批后，再实施与编号关联的代码。

### 12.2 顺序规则（严格）
规格变更必须先于关联代码 PR 合并。

### 12.3 需求状态标签
仅使用以下状态：
- proposed
- approved
- implemented
- verified
- deprecated

## 13. 紧急热修复例外
仅用于有用户影响的生产事故。
规则：
1. 可先实施热修复代码。
2. 必须在 24 小时内补齐规格回填。
3. 变更记录必须标注 HOTFIX。

## 14. 规格变更记录
- 0.6.0（2026-04-13）
  - FR-005 从“文件型记忆”升级为“混合记忆”，纳入向量数据库支持（Embedding + 语义检索 + 自动索引重建）。
  - FR-007 从“企业微信集成”重写为“可扩展消息渠道抽象层”，定义 ChannelAdapter 接口与统一消息模型。
  - 具体渠道实现（企业微信/微信/QQ/飞书/Telegram）移至范围外，保留抽象层与 TestChannelAdapter。
  - 架构层新增 Embedding 适配器、向量存储、渠道抽象层。
  - 数据模型新增 VectorRecord、ChannelConfig；MemoryIndex 关联 embedding_id；Session.channel 泛化为 string。
  - 错误码新增 VECTOR_WRITE_FAILED、EMBEDDING_FAILED、CHANNEL_NOT_CONFIGURED、CHANNEL_SEND_FAILED。
  - 目录结构新增 src/channels、src/services/vector。
  - 测试矩阵补充 FR-005 向量记忆测试、FR-007 渠道抽象层测试。
  - 解决 ODR-003（已泛化为渠道抽象）；新增 ODR-006 向量库选型。
- 0.5.0（2026-04-13）
  - 补全 Agent 框架核心缺失：新增 FR-010 Agent 核心执行循环（ReAct 模式）。
  - 新增 FR-011 Skill 运行时定义（内容格式、执行语义、工具白名单）。
  - 新增 FR-012 工具注册与调用（描述格式、安全边界、审批流）。
  - 新增 FR-013 会话与上下文管理（生命周期、压缩、归档）。
  - 新增 NFR-006 并发与限流策略。
  - 架构层补充：新增模型集成层、会话管理器、提示词编排器。
  - 数据模型补全：新增 Session、ToolCall、AuditLog；Task 关联 skill_id；AgentRun 关联 skill_id、session_id。
  - 新增 API 9.5 Agent 执行、API 9.6 工具管理、API 9.7 会话管理。
  - 错误码补充：TOOL_NOT_ALLOWED、TOOL_APPROVAL_PENDING、MODEL_FALLBACK、CONTEXT_OVERFLOW、MAX_STEPS_EXCEEDED、SESSION_EXPIRED、RATE_LIMITED。
  - 测试矩阵扩展覆盖 FR-010/011/012/013；里程碑由 M1-M4 调整为 M1-M5。
  - 澄清 Skill 与 Agent 关系。
- 0.4.0（2026-04-13）
  - 新增实现约束章节（Python 基线、编码规范、配置与密钥规范）。
  - 新增目录与模块规范，明确模块边界与分层调用限制。
  - 新增可观测性与审计规范（日志字段、指标、审计事件）。
  - 新增测试矩阵与里程碑门禁。
- 0.3.0（2026-04-13）
  - 新增第 9 节接口级规格补充：Skill 管理 API、主/子 Agent 协作协议、错误码与重试约定。
  - 明确 FR-008 与 FR-009 的可测试契约，作为后续实现基线。
- 0.2.0（2026-04-13）
  - 锁定 Python 为主运行时技术栈。
  - 新增 FR-008：Skill 生命周期管理（支持 SOP 沉淀、版本与回滚）。
  - 新增 FR-009：主/子 Agent 上下文隔离与结构化回传机制。
  - 扩展架构与数据模型以支撑 Skill 与多 Agent 协作。
- 0.1.0（2026-04-13）
  - 创建初始基线规格。
  - 建立“先规格、后代码”的严格流程。
  - 定义 MVP 范围、架构与需求编号。

## 15. 待定决策
- ODR-001：Python 作为主运行时技术栈（已锁定）。
- ODR-002：Web 管理看板一期认证模型。
- ODR-003：消息渠道回调校验策略（已泛化：各渠道通过 ChannelAdapter.verify_callback 自行实现，见 FR-007）。
- ODR-004：Skill 与 Agent 的关系模型（已解决：子 Agent 为通用执行器，通过 allowed_skills 分配 Skill，见 FR-009/FR-011）。
- ODR-005：主 Agent 路由策略（已解决：LLM 驱动 + 规则回退双模式，见 FR-010）。
- ODR-006：向量数据库选型（候选：ChromaDB 或 LanceDB，待实测后锁定）。

## 16. 评审节奏
- 在活跃开发期按周评审规格。
- 任何范围变更都必须同步更新第 4 节与第 14 节。

## 17. 实现约束（Python 基线）
### 17.1 运行时与依赖
- 主运行时版本：Python 3.11+。
- 包管理与锁定：使用 pyproject.toml 与锁文件管理依赖。
- 关键依赖原则：优先稳定、维护活跃、社区成熟的库。
- 禁止在未更新规格的情况下新增核心依赖类别（例如消息中间件）。

### 17.2 编码与工程规范
- 所有公共接口必须有类型标注与文档字符串。
- 跨模块契约必须通过 schema 或类型模型显式定义，禁止隐式字典协议。
- 所有 FR-008 与 FR-009 相关改动必须附带契约测试。
- 错误处理必须返回统一错误码（见 9.3），禁止仅返回裸异常文本。

### 17.3 配置与密钥
- 配置分层：默认值、环境变量、部署覆盖。
- 密钥仅允许来自环境变量或受控密钥源，不可写入仓库。
- 配置项新增或变更必须同步更新规格与部署模板。

## 18. 目录与模块规范（首版）
建议目录结构如下（实现时可在不破坏边界前提下微调）：

- src/app：启动、依赖装配、生命周期管理。
- src/api：HTTP 路由、请求校验、响应模型。
- src/agents/main：主 Agent 编排、任务拆解、路由。
- src/agents/sub：子 Agent 执行器、隔离上下文处理。
- src/agents/loop：Agent 核心执行循环（ReAct）、步骤管理。
- src/agents/prompt：提示词编排器、模板组装。
- src/skills：Skill 注册、版本、回滚、加载、运行时执行。
- src/tools：工具注册表、内置工具实现、自定义工具加载。
- src/models：LLM 适配器、模型路由、上下文窗口管理、Token 计数。
- src/sessions：会话生命周期、上下文压缩、归档。
- src/services/scheduler：调度与执行循环。
- src/services/notification：通知服务（通过渠道抽象层发送）。
- src/services/memory：短期/长期记忆与检索。
- src/services/cost：Token 与成本统计。
- src/storage：数据库访问层与文件存储适配。
- src/contracts：请求/响应模型、错误码、schema。
- tests/unit：单元测试。
- tests/integration：集成测试。
- tests/contracts：契约测试（重点覆盖 FR-008/FR-009/FR-011/FR-012）。

模块边界规则：
- api 层不得直接访问底层存储，必须通过 services。
- 子 Agent 不得绕过主 Agent 直接调用其他子 Agent。
- skills 模块不得依赖 web 视图逻辑。

## 19. 可观测性与审计规范
### 19.1 日志字段（最小集）
所有关键日志事件应包含以下字段：
- timestamp
- level
- trace_id
- run_id
- parent_run_id（如有）
- task_id（如有）
- skill_id（如有）
- agent_role（main|sub）
- event
- status
- error_code（失败时）

### 19.2 指标（最小集）
- 任务执行总数、成功率、失败率。
- 子 Agent 平均耗时、超时率。
- Skill 调用次数、失败率、回滚次数。
- 模型 token 使用量与成本趋势。

### 19.3 审计事件（强制）
以下动作必须记录审计：
- Skill 新建/更新/启停/回滚。
- 主 Agent 调度子 Agent。
- 人工重试、人工取消、配置变更。

## 20. 测试矩阵与里程碑门禁
### 20.1 测试矩阵（按需求）
- FR-005：
  - 文件型全文检索返回相关结果。
  - 向量语义检索返回 top-K 相关结果。
  - 人工编辑长期记忆后向量索引自动重建。
  - 向量写入失败时降级为文件型，不影响主流程。
  - Agent 循环中自动检索记忆并注入上下文。
- FR-007：
  - TestChannelAdapter 完成发送/接收/回调校验全流程。
  - 新增渠道适配器无需修改核心模块代码。
  - 未配置渠道时系统仅通过 Web API 工作。
  - 渠道配置动态加载正确。
- FR-008：
  - 新建 Skill 成功与重名失败。
  - Skill 更新生成新版本。
  - 停用后调用被拒绝。
  - 回滚到历史版本并生效。
- FR-009：
  - 子 Agent 只能读取下发 context_pack。
  - 子 Agent 回传必须满足 output_schema。
  - 子 Agent 失败不污染其他子 Agent 上下文。
  - usage 与 run log 能按子 Agent 维度聚合。
- FR-010：
  - 完整 ReAct 循环流转（思考→动作→观察）。
  - 超过最大步数后强制终止并返回中间结果。
  - 流式输出模式返回 SSE 事件流。
  - LLM 驱动路由失败时回退到规则路由。
- FR-011：
  - skill_prompt 正确注入 system prompt。
  - 未在 allowed_tools 中的工具调用被拒绝。
  - 输出不符合 output_schema 时返回 SCHEMA_VALIDATION_FAILED。
  - Skill 嵌套调用被拒绝。
- FR-012：
  - 工具注册后可通过 API 查询。
  - 工具参数不合法时拒绝调用。
  - exec 工具白名单外的命令被拒绝。
  - requires_approval 工具触发审批流，审批通过后恢复循环。
  - 工具执行超时返回 TOOL_EXECUTION_FAILED。
- FR-013：
  - 连续消息归属同一会话。
  - 超时后新消息创建新会话。
  - 上下文压缩后 Agent 仍能基于摘要回答历史问题。
  - 会话归档后摘要可被记忆检索命中。
  - 手动关闭会话后自动生成摘要。

### 20.2 里程碑门禁
- M1（骨架完成）：通过健康检查、基础日志、最小 Agent 循环链路（接收消息 → 调用 LLM → 返回回复）。
- M2（Skill + 工具可用）：FR-008、FR-011、FR-012 全部验收通过。
- M3（主/子 Agent 可用）：FR-009、FR-010 全部验收通过。
- M4（会话与记忆）：FR-005、FR-013 全部验收通过。
- M5（业务试运行）：渠道抽象层可用、Web 看板、成本统计打通（FR-003、FR-004、FR-006、FR-007）。

门禁规则：未通过当前里程碑门禁，不进入下一里程碑开发。
