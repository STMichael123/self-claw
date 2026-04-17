# Self-Claw 规格说明（唯一事实来源）

状态：生效中
版本：1.0.1
最后更新：2026-04-17
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
- 渐进式披露优先：默认先暴露目录、索引、摘要、snippet 等低成本上下文，仅在命中且确有必要时再加载正文、资源或历史细节，以控制 token 成本并减少上下文污染。

## 4. 范围
### 4.1 范围内（MVP）
- Agent 核心执行循环（意图理解、规划、执行、观察）。
- 项目级 Agent Skills 目录发现、激活与运行时执行语义。
- Skill 草稿自动生成、人工审核、文件化发布与回滚。
- 工具注册、发现与调用。
- 受控文件读写与沙箱工作区。
- 自然语言任务调度。
- 任务执行与失败通知。
- 多轮会话与上下文管理。
- Token/成本使用追踪。
- 分层混合记忆（Principle + 全局长期 + 会话短期 + 向量数据库）。
- 基于企业 SOP 的 Skill 沉淀、审核发布、启停与迁移。
- 主 Agent + 子 Agent 协作执行。
- 同一业务员多个顶层任务线程与多个主 Agent 并行处理。
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
- Agent 核心执行循环（意图识别 → 规划 → Skill catalog 披露/激活/工具选择 → 执行 → 观察 → 回复）
- 主 Agent 任务拆解与路由（支持 LLM 驱动路由 + 规则回退，支持多个顶层任务线程并行）
- 子 Agent 执行器与结果回收
- 会话管理器（多轮对话、上下文积累与截断，支持同一用户多个活跃顶层任务线程）
- 提示词编排器（base prompt + principle + available skills catalog + activated skill content + 上下文组合）

3. 能力层
- Skill 目录发现、catalog 索引、草稿审核、发布、回滚快照与运行时激活服务
- 工具注册表与工具调用协调器
- 调度服务
- 通知服务（通过渠道抽象层发送，不绑定具体渠道）
- 分层记忆服务（Principle + long-term + short-term + 向量数据库）
- 文件工作区服务（沙箱、锁与审计）
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
- 存储（SQLite + 项目文件系统 `.agents/skills` + 业务沙箱工作区 + 向量数据库）
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

### FR-005 分层混合记忆
系统必须支持 Principle、长期记忆、短期记忆三层结构，并同时提供文件型与向量型检索能力。
Principle 记忆：
- Principle 记忆是全局共享的系统级约束文档，类似 identity / soul 文档。
- Principle 记忆默认注入所有顶层 main Agent，优先级高于长期记忆与短期记忆。
- Principle 记忆仅允许通过显式管理入口更新，并产生审计记录。
长期记忆：
- 长期记忆是系统全局共享的业务知识层，可由人工沉淀、会话归档摘要或审核通过的 Skill 派生知识写入。
- 长期记忆存储为可编辑的 Markdown/JSON 文件，支持人工编辑与基础全文检索。
短期记忆：
- 短期记忆按 session_id 存储为 Markdown 文件，按日归档。
- 短期记忆仅对所属会话可见，不得被其他顶层会话命中或注入。
向量记忆：
- Principle、长期记忆、会话摘要、任务结果、Skill 执行产物可写入向量数据库。
- 向量记录必须带有 tier 与 session_id/source_id 元数据；短期记忆的向量检索必须按 session_id 过滤。
- Agent 执行循环中可按层级自动检索相关记忆注入上下文。
注入顺序：
- 默认注入顺序为：base prompt -> principle -> long-term -> session snapshot / short-term -> available skills catalog -> activated skill content -> user message。
- 记忆注入必须优先使用 summary/snippet/检索命中片段等轻量表示；仅当当前推理步骤明确需要更多细节时，才允许回源加载完整记忆正文，且不得默认批量注入完整 long-term 或 short-term 文件内容。
验收标准：
- 所有顶层 main Agent 都能看到最新 Principle 记忆。
- 不同会话的 short-term 记忆互不共享。
- long-term 记忆对所有顶层会话全局共享。
- 全文检索与语义检索均可按 tier 返回相关结果。
- Principle / long-term 文档编辑后向量索引自动重建。
- 向量写入失败不影响主流程（降级为仅文件型）。

### FR-006 Web 管理看板
系统必须为业务用户提供 Web 管理看板。
页面信息架构：
- 默认首页必须是“顶层会话入口页”，而不是直接进入对话框页面。
- 顶层会话入口页以气泡卡片/节点形式展示现有顶层会话，每个气泡代表一个顶层任务线程（可显示默认名称如 `Agent 1` 或线程标题）。
- 顶层会话入口页右侧必须提供明显的“+”气泡/卡片入口，用于新建顶层会话。
- 用户点击已有顶层会话气泡后，才进入对应的会话详情/对话页面。
- 左侧导航必须提供“状态管理”与“Skill 目录/审核”入口，前者用于可视化不同顶层会话以及其主 Agent / 子 Agent 的执行进度，后者用于查看项目级 Skill catalog、待审核草稿与审计信息。
- Skill 目录页必须展示从项目内 `.agents/skills` 发现到的正式 Skill catalog，最少展示 `name`、`description`、`compatibility`、`status`、`location`、`source` 与 `last_indexed_at`。
- Web 端不得提供正式 Skill 的手工新建、编辑、删除、启用、停用表单；正式 Skill 内容变更的主入口必须是与主会话 Agent 的自然语言对话，Web 仅承担只读目录、草稿审核与审计视图。
- 聊天页不得再通过下拉框预绑定 `skill_id` 作为主要交互方式；必须改为提示用户通过对话显式请求“创建 Skill / 更新 Skill / 停用 Skill / 从当前流程沉淀 Skill”。
状态管理页要求：
- 以线程为第一层级展示顶层会话。
- 在线程下展示当前 main Agent run 与关联 sub Agent runs 的状态、进度、最近事件、耗时与错误摘要。
- 状态管理页必须支持业务员快速识别：哪些线程正在运行、哪些线程已完成、哪些线程失败以及失败位置。
验收标准：
- 任务总览（状态、下次执行、失败情况）。
- 成本总览（每日趋势、按任务用量排行）。
- 轻量操作（暂停/恢复/重试）。
- 默认首页不直接显示聊天输入框，而是显示顶层会话气泡入口与“+”新建入口。
- 点击顶层会话气泡可进入对应会话页，并保留该会话独立上下文。
- 左侧导航包含“状态管理”与“Skill 目录/审核”，且状态管理页可展示顶层会话 > 主 Agent > 子 Agent 的层级进度。
- Skill 页面必须展示正式 Skill catalog 与待审核草稿，且不存在“新建 Skill”按钮、正式 Skill 编辑表单或聊天页 Skill 选择器。
- Skill 草稿的批准/拒绝操作必须可在审核页完成，并写入审计日志。

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
系统必须支持将企业 SOP 以项目级 Agent Skill 的形式沉淀，并通过“草稿 -> 审核 -> 发布 -> 回滚”的生命周期持续迭代。
正式 Skill 事实源：
- 正式 Skill 的单一事实源是项目内 `.agents/skills/<skill-name>/SKILL.md` 及其同目录资源文件。
- 运行时不得以数据库中的 `skills`/`skill_versions` 记录作为正式 Skill 指令内容的最终来源；数据库仅承担草稿、审核、审计、revision snapshot、catalog cache 与迁移状态的职责。
- 正式 Skill 的内容格式必须遵循 Agent Skills open standard；Self-Claw 不得再把 `input_schema`、`output_schema`、`max_steps`、`status` 等自定义字段写入正式 `SKILL.md`。
生命周期规则：
- 创建、更新、停用、回滚 Skill 的主入口是与顶层 main Agent 的自然语言对话，而不是 Web 表单或直接 API CRUD。
- 对话意图可直接生成草稿，也可从成功的 AgentRun 自动提取草稿；草稿必须记录 `source_run_id`、`source_session_id` 或人工意图摘要。
- 草稿批准后，由受控发布流程写入 `.agents/skills` 并刷新 registry；拒绝草稿不得修改任何正式 Skill 文件。
- 正式 Skill 的启用/停用状态由审核/索引层维护，可抑制 catalog 披露与激活，但不得通过向 `SKILL.md` 添加非标准状态字段实现。
- 正式 Skill 的版本历史由已批准 revision snapshot 组成；回滚通过把某个已批准 snapshot 重新发布到 `.agents/skills/<skill-name>/` 完成。
Web/API 约束：
- Web 仅允许查看正式 Skill catalog、待审核草稿、revision 历史、审计与迁移告警，不允许手工编辑正式 Skill 内容。
- API 不得提供接收“原始正式 Skill 内容”的直接新建/更新 CRUD 接口；正式内容变更必须经过草稿审核或正式 Skill 复审动作。
验收标准：
- 系统能从 `.agents/skills` 发现正式 Skill，并构建 catalog。
- 成功的 AgentRun 或明确的对话指令均可生成 Skill 草稿，并记录来源。
- Skill 草稿可编辑、可批准、可拒绝，且审核动作写入审计日志。
- 审批通过的 Skill 草稿会写入 `.agents/skills/<skill-name>/SKILL.md` 与附属资源，并触发 registry reload。
- 正式 Skill 可通过复审动作启用、停用与回滚；启停会影响 catalog 披露与激活结果。
- 被拒绝的草稿不会产生正式 Skill 文件变更。

### FR-009 主/子 Agent 上下文隔离
系统必须支持主 Agent 与子 Agent 的分层协作，以保证任务上下文干净、互不污染。
验收标准：
- 主 Agent 负责任务分解与子 Agent 调度，子 Agent 仅接收最小必要上下文。
- 子 Agent 默认不隐式继承 Principle、长期记忆、短期记忆或主 Agent 已激活的 Skill 内容，仅可读取主 Agent 显式下发的 `context_pack` 与 `allowed_skills`。
- 子 Agent 会话上下文默认隔离，跨子 Agent 不共享临时上下文。
- 仅允许通过显式结构化产物（例如 JSON 结果）回传到主 Agent。
- 支持为每个子 Agent 记录独立执行日志与成本统计。
- 子 Agent 的可用工具集合不得大于所属主 Agent 当前上下文允许的工具集合。
- 子 Agent 仅隶属于单个顶层主 Agent 运行，不得跨顶层任务线程复用、迁移或共享临时上下文。

### FR-010 Agent 核心执行循环
系统必须实现完整的 Agent 推理-执行循环，作为所有任务处理的统一入口。
顶层路由前置决策：
- 在进入当前 Agent 推理循环前，系统必须先判断该输入是继续当前顶层任务线程、创建新顶层任务线程，还是取消并重跑既有线程。
执行流程：
1. 接收输入（用户消息 / 定时任务触发 / 子 Agent 委派）。
2. 加载会话上下文与相关记忆。
3. 构造提示词（system prompt + available skills catalog + 已激活 Skill 内容 + 上下文 + 工具描述）。
4. 调用 LLM 获取推理结果。
5. 解析 LLM 输出：判断是直接回复、激活 Skill、调用工具，还是委派子 Agent。
6. 执行动作并观察结果。
7. 决定是否需要继续循环（多步推理）或终止并回复用户。
验收标准：
- 推理策略采用 ReAct（Reasoning + Acting）模式，每步包含思考、动作、观察三阶段。
- 单次循环最大步数可配置（默认 10 步），超限后强制终止并返回中间结果。
- 每步推理的思考过程、动作选择与观察结果均需记录到执行日志。
- 支持流式输出模式（用于 Web/消息渠道实时反馈）与阻塞模式（用于定时任务）。
- 主 Agent 可在循环中决定自行处理或委派子 Agent，路由策略支持 LLM 驱动与规则回退双模式。
- 主 Agent 路由必须先区分“继续当前任务 / 新建顶层任务 / 取消并重跑”，再进入当前线程内的直接回复、Skill 激活、工具、子 Agent 决策。
- 同一用户的多个顶层主 Agent 运行可并行存在，但各自上下文、运行状态与资源记账必须隔离。

### FR-011 Agent Skills 运行时定义
系统必须基于 Agent Skills open standard 明确正式 Skill 的目录结构、发现流程与运行时激活语义。
正式 Skill 目录结构：
- 正式 Skill 根目录固定为项目内 `.agents/skills/<skill-name>/`。
- 每个正式 Skill 必须包含 `SKILL.md` 作为入口文件，可选子目录为 `scripts/`、`references/`、`assets/`。
- `SKILL.md` 必须由 YAML frontmatter + Markdown 正文组成；frontmatter 仅允许 `name`、`description`、`license`、`compatibility`、`metadata`、`allowed-tools` 这些 Agent Skills 标准字段。
- `name` 必须与父目录名一致，并使用小写 kebab-case；`description` 必须描述 Skill 做什么以及何时使用。
渐进披露：
- 发现阶段：启动或 reload 时仅加载 `name`、`description`、`location`、`compatibility`、`status` 等 catalog 元数据。
- 激活阶段：当任务与某个 Skill 描述匹配，或用户显式请求某个 Skill 时，系统才通过内部 `activate_skill` 能力加载完整 `SKILL.md` 正文。
- 资源阶段：Skill 正文引用的 `scripts/`、`references/`、`assets/` 文件仅在需要时按相对路径加载，不得在 catalog 阶段整体预读。
- catalog 阶段必须只披露足以完成 Skill 选择的最小信息集；不得为了“省实现”而在推理前预读全部 `SKILL.md`、`references/` 或 `assets/`，以避免不必要的 token 开销与上下文噪音。
执行语义：
- available skills catalog 必须在推理前向主 Agent 披露，模型命中某个 Skill 时必须先激活，再继续后续推理与工具调用。
- `allowed-tools` 只允许为当前会话或子 Agent 已经具备的工具权限做预批准，不得突破全局权限、父子 Agent 工具上界或受保护目录策略。
- 一个 AgentRun 可以顺序激活多个 Skill；每次 Skill 激活都必须记录到运行日志与审计/指标中。
- 子 Agent 默认不继承主 Agent 已激活的 Skill；若确有需要，必须通过 `allowed_skills` 或 `context_pack` 显式下发。
验收标准：
- 系统能严格校验 `SKILL.md` 必填字段与支持字段；不合规 Skill 不得进入 catalog。
- 启动阶段仅加载 catalog 元数据，不预读完整正文。
- Draft Skill 未审批前不得被 Agent 作为可执行 Skill 载入。
- Skill 被命中后才加载完整正文，并可按需解析相对资源路径。
- `allowed-tools` 不得扩大既有工具权限，受保护目录规则始终优先。

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
- 内置工具：web_fetch（网页抓取）、web_search（搜索）、exec（命令执行，MVP 阶段仅限白名单命令）、list_dir（目录列表）、read_file（文件读取）、write_file（文件写入）、patch_file（补丁写入）、activate_skill（按名称激活已发现 Skill）。
- 自定义工具：通过 Python 函数 + 装饰器注册，运行时自动发现。
安全边界：
- exec 工具必须有命令白名单，禁止执行任意命令。
- read_file / list_dir / write_file / patch_file 只能访问单一配置化业务沙箱根目录内的路径。
- 项目控制目录 `.agents/skills` 不属于通用文件工具的读写表面；通用文件工具对其直接访问必须拒绝，并返回受保护目录错误。
- 文件工具必须执行路径规范化，拒绝路径越界、符号链接与未授权绝对路径。
- activate_skill 只能接受已发现且处于可激活状态的 Skill 名称；其返回内容应包含 Skill 正文与资源清单，而不是暴露原始目录遍历能力。
- write_file / patch_file 默认 requires_approval=true，除非规格明确豁免。
- 正式 Skill 的发布、停用与回滚必须由专门的 Skill 服务完成，不得通过通用 write_file / patch_file 直接改写 `.agents/skills`。
- 对同一沙箱文件的并发写入不得静默覆盖，必须返回 FILE_WRITE_CONFLICT 或等价错误。
- 所有工具调用记录工具名、参数、返回值、耗时到日志。
- 所有文件操作必须额外记录路径、操作类型、内容摘要以及校验和变化。
- requires_approval=true 的工具调用暂停循环，通过消息渠道/Web 推送审批请求，审批通过后继续。
验收标准：
- 工具列表可通过 API 查询（GET /api/v1/tools）。
- 工具调用参数经过 JSON Schema 校验，不合格则拒绝调用。
- activate_skill 仅接受已发现 Skill 名称，并在命中后返回结构化 Skill 内容。
- 工具执行超时后返回 TOOL_EXECUTION_FAILED 并记录日志。
- 沙箱外的文件读写请求与对 `.agents/skills` 的通用直接访问必须被拒绝。
- 多会话或多 Agent 对同一文件的并发写入不得静默覆盖。

### FR-013 会话与上下文管理
系统必须管理多轮对话会话（即顶层任务线程）的生命周期与上下文积累。
会话语义：
- 会话是业务员可见的顶层任务线程，而不是单个用户唯一活跃上下文。
- 同一用户可同时拥有多个 active 会话，每个会话围绕单一业务目标组织上下文。
- Principle 与 long-term 是全局共享层；short-term、context_snapshot 与 pending tool state 是会话私有层。
会话生命周期：
- 创建：用户首次发送消息、显式点击“新建任务”或 API 指定新任务模式时自动生成会话。
- 活跃：持续接收消息，按消息时间戳更新 last_active_at。
- 过期：超过可配置的空闲超时（默认 30 分钟）后自动标记为 expired。
- 归档：过期会话的上下文摘要写入全局 long-term 记忆，原始 short-term 消息保留到文件存档。
上下文管理：
- 上下文窗口由模型 max_tokens 决定，系统保留 20% 给输出。
- 当消息历史超出窗口时，自动执行压缩：保留最近 N 条原始消息 + 历史消息的 LLM 摘要。
- short-term 记忆检索与上下文注入必须以 session_id 为边界，不得跨会话复用。
- 压缩事件必须记录到日志，摘要内容存入 MemoryIndex。
验收标准：
- 同一用户可同时拥有多个 active 会话。
- 显式新建任务时，即使原会话尚未超时，也必须创建新会话。
- 指定 session_id 继续会话时，不得污染其他 active 会话及其 short-term 记忆。
- 上下文压缩后，Agent 仍能基于摘要回答历史相关问题。
- 会话归档后的摘要可进入全局 long-term，并被后续会话记忆检索命中。

### FR-014 多顶层任务与多主 Agent 并行
系统必须支持同一业务员同时打开多个顶层任务线程，并为每个线程分配独立的主 Agent 运行。
核心逻辑：
- 顶层任务线程是业务员视角的工作单元，每个线程围绕单一业务目标组织上下文与状态。
- 顶层主 Agent 运行定义为 `agent_role=main` 且 `parent_run_id` 为空的 AgentRun。
- 同一线程可存在多次主 Agent 运行（继续执行、人工重试、取消并重跑），但默认同一时刻仅允许一个 running 主 Agent。
- 运行中的线程收到新的独立业务目标时，系统必须将其作为新线程创建，而不是直接注入当前主 Agent 的子 Agent 树。
- 不同顶层线程共享 Principle 与 long-term，但不共享 short-term、context_snapshot 与 pending tool state。
- 顶层任务并行与子 Agent 并行属于两层不同并发模型，必须分别限流、记录和展示。
验收标准：
- 同一用户可同时拥有多个 active 顶层任务线程，且各自状态、摘要和最近运行结果可见。
- 新任务在原任务运行中发起时，原任务继续后台运行，新任务创建独立的 session_id 与顶层 main run。
- 取消并重跑必须在原线程内创建新的 main run，而不是覆盖既有运行记录。
- 多个顶层会话对同一沙箱文件并发写入时，系统必须返回冲突而不是静默覆盖。
- Web UI / API 至少可查询线程列表、当前主 Agent 状态、关联子 Agent 数量或运行摘要。
- 顶层会话入口页能够同时展示多个顶层会话气泡，并明确标识当前选中的会话。

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
- 通用文件读写工具只能访问单一配置化业务沙箱根目录，越界路径与符号链接必须拒绝。
- `.agents/skills` 是项目级受保护控制目录，不得通过通用文件工具直接读写；正式 Skill 访问必须走 Skill registry / activate_skill，正式 Skill 发布必须走专门的审核发布流程。
- Principle 记忆更新、自动生成 Skill 草稿的批准/拒绝、正式 Skill 发布/启停/回滚，以及写文件审批必须产生审计记录。

### NFR-006 并发与限流
- 同一用户同时活跃的顶层主 Agent 运行数量可配置（默认最大 3 个并行）。
- 同时执行的子 Agent 数量可配置（默认最大 5 个并行）。
- 顶层主 Agent 并发与子 Agent 并发必须分层限流，单个顶层任务不得独占全部子 Agent 槽位。
- 调度器同时触发的任务超过并发上限时，进入队列排序等待。
- LLM API 调用按模型提供商的速率限制进行令牌桶限流。
- 消息渠道推送按各平台频率限制进行限流（由 ChannelAdapter 实现方配置）。
- 同一沙箱文件同一时刻仅允许一个写者；后续写请求返回 FILE_WRITE_CONFLICT，不静默排队覆盖。
- 文件锁必须具备超时与清理机制，避免死锁或僵尸锁。
- 超过限流的请求返回 RATE_LIMITED 错误码，不排队不重试。

## 8. 数据模型（初版）
- Task：id, title, requested_skill_name(可为空), schedule_type, schedule_expr, status, next_run_at, last_run_at, last_result, created_at, updated_at
- RunLog：id, task_id, started_at, ended_at, status, error_category, error_detail
- UsageLog：id, task_id, session_id, agent_run_id, input_tokens, output_tokens, estimated_cost, model_name, created_at
- Session：id, user_id, title(可为空), channel_type(string), status(active|expired|archived), context_snapshot, summary, current_run_id(可为空), created_at, last_active_at, expired_at
- MemoryDocument：id, tier(principle|long_term), key, title, content, format(markdown|json), version, source_type(manual|session_archive|approved_skill_revision), source_ref, created_at, updated_at
- MemoryIndex：id, scope(principle|short_term|long_term), session_id(可为空), ref_path, summary, embedding_id(可为空,关联向量记录), updated_at
- VectorRecord：id, content_hash, text_chunk, embedding(vector), source_type(session_summary|task_result|skill_output|long_term_memory|principle_memory), source_id, created_at
- SkillCatalogEntry：skill_name, description, location, compatibility(可为空), status(enabled|disabled), source(project|legacy_migration), content_hash, discovered_at, indexed_at, last_approved_revision(可为空)
- SkillDraft：id, source_run_id(可为空), source_session_id(可为空), requested_action(create|update|disable|rollback), target_skill_name(可为空), proposed_name(可为空), draft_skill_md, draft_resources_manifest, review_status(draft|approved|rejected), reviewer, review_note, created_at, updated_at
- SkillRevision：id, skill_name, revision, source_draft_id(可为空), skill_md_snapshot, resources_manifest_snapshot, content_hash, created_at
- AgentRun：id, parent_run_id(顶层 main run 为空), agent_role(main|sub), activated_skills(json array,可为空), session_id, task_ref, context_ref, result_ref, started_at, ended_at, status, steps_count
- ToolCall：id, agent_run_id, tool_name, parameters, result, duration_ms, status, created_at
- FileLock：id, sandbox_path, lock_type(read|write), owner_run_id, created_at, expires_at
- FileOperation：id, agent_run_id, session_id, operation_type(list|read|write|patch), sandbox_path, status, content_preview, checksum_before, checksum_after, started_at, ended_at
- AuditLog：id, operator, action, entity_type, entity_id, version, diff_summary, created_at
- ChannelConfig：id, channel_type, display_name, adapter_class, credentials_ref, rate_limit, enabled, created_at, updated_at

语义约束：
- Session 表示业务员可见的顶层任务线程。
- AgentRun 中 `agent_role=main` 且 `parent_run_id` 为空的记录表示顶层主 Agent 运行。
- `.agents/skills` 中的文件是正式 Skill 内容的唯一事实源；`SkillCatalogEntry` 与 `SkillRevision` 仅承担索引/快照职责，不是运行时最终内容来源。
- `SkillDraft` 是审核工件，不得直接进入正式执行路径。

## 9. API 与接口约定（初版）
- 内部服务通过明确的服务接口通信。
- Web API 从 /api/v1 开始版本化。
- 破坏性 API 变更必须提升规格主版本号。

### 9.1 Skill 目录与审核 API（对应 FR-008）
基础路径：`/api/v1/skills`

1. 查询已发现 Skill catalog
- 方法与路径：GET `/api/v1/skills`
- 查询参数：
  - status: enabled|disabled（可选）
  - source: project|legacy_migration（可选）
  - keyword: string（可选）
- 响应体最少字段：skill_name, description, compatibility(可为空), status, location, source, last_indexed_at

2. 查询单个 Skill 详情
- 方法与路径：GET `/api/v1/skills/{skill_name}`
- 响应体最少字段：skill_name, frontmatter, location, resource_manifest, status, latest_revision, recent_audit

3. 刷新 Skill registry
- 方法与路径：POST `/api/v1/skills/reload`
- 响应体：
  - discovered_count: int
  - skipped_count: int
  - invalid_count: int
  - warnings: string[]

4. 生成 Skill 草稿
- 方法与路径：POST `/api/v1/skills/drafts`
- 请求体：
  - source_run_id: string（可选）
  - source_session_id: string（可选）
  - requested_action: create|update|disable|rollback
  - target_skill_name: string（可选）
  - proposed_name: string（可选）
  - operator: string
  - user_intent_summary: string（可选）
- 响应体：
  - draft_id: string
  - review_status: draft
  - requested_action: string

5. 查询与编辑 Skill 草稿
- 方法与路径：GET `/api/v1/skills/drafts`
- 查询参数：
  - review_status: draft|approved|rejected（可选）
  - requested_action: create|update|disable|rollback（可选）
  - target_skill_name: string（可选）
- 方法与路径：GET `/api/v1/skills/drafts/{draft_id}`
- 方法与路径：PATCH `/api/v1/skills/drafts/{draft_id}`

6. 审核 Skill 草稿
- 方法与路径：POST `/api/v1/skills/drafts/{draft_id}/approve`
- 请求体：
  - operator: string
  - change_note: string（可选）
- 响应体：
  - draft_id: string
  - skill_name: string
  - published_revision: string
  - registry_reloaded: bool
- 方法与路径：POST `/api/v1/skills/drafts/{draft_id}/reject`

7. 正式 Skill 复审动作
- 方法与路径：POST `/api/v1/skills/{skill_name}/actions`
- 请求体：
  - action: enable|disable|rollback
  - target_revision: string（仅 rollback 时必填）
  - operator: string
  - reason: string（可选）
- 响应体：
  - skill_name: string
  - action: string
  - status: string
  - active_revision: string

8. Skill 审计与迁移查询
- 方法与路径：GET `/api/v1/skills/audit`
- 查询参数：
  - skill_name: string（可选）
  - action: string（可选）
  - draft_id: string（可选）
- 方法与路径：GET `/api/v1/skills/migrations`

9. 正式 Skill 变更约束
- API 不得提供接收“原始正式 Skill 内容”的直接新建/更新/删除 CRUD 接口。
- 正式 Skill 内容变更只能来自草稿批准或正式 Skill 复审动作。
- 每次正式发布、启停、回滚必须产生审计记录。
- 每次 Skill 草稿生成、编辑、批准、拒绝必须产生审计记录。
- 审计记录最少字段：operator, action, skill_name, revision, timestamp, diff_summary。

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
- 默认不继承全局记忆或主 Agent 已激活 Skill，仅可读取主 Agent 显式下发的 `context_pack` 与 `allowed_skills`。

4. 子 Agent 生命周期状态
- 状态机：`queued -> running -> success|failed|timeout|cancelled`
- 任一子 Agent 失败时，主 Agent 可选择重试、降级或终止整链路。

### 9.3 错误码与重试约定（Skill 与 Agent 共享）
- `SKILL_NOT_FOUND`: Skill 不存在，禁止自动重试。
- `SKILL_DISABLED`: Skill 已停用，禁止自动重试。
- `SKILL_VALIDATION_FAILED`: `SKILL.md` 格式非法、缺少必填字段或包含不支持字段，禁止自动重试。
- `SKILL_ACTIVATION_FAILED`: Skill 激活失败或加载 `SKILL.md` / 资源失败，允许在 registry reload 后重试一次。
- `SKILL_RESOURCE_ACCESS_DENIED`: 访问未激活 Skill 资源或越过当前 Skill 根目录，禁止自动重试。
- `LEGACY_SKILL_MIGRATION_REQUIRED`: 旧数据库中心化 Skill 尚未完成迁移审核，禁止执行。
- `SCHEMA_VALIDATION_FAILED`: API、工具或子 Agent 的结构化输入/输出不符合 schema，禁止自动重试。
- `SUBAGENT_TIMEOUT`: 子 Agent 超时，允许最多 2 次指数退避重试。
- `TOOL_EXECUTION_FAILED`: 工具执行失败，允许按策略重试。
- `TOOL_NOT_ALLOWED`: 工具被当前全局策略、父子 Agent 工具上界或受保护目录规则拒绝，禁止重试。
- `TOOL_APPROVAL_PENDING`: 工具需人工审批，循环暂停等待。
- `SKILL_DRAFT_REVIEW_REQUIRED`: 自动生成的 Skill 草稿尚未通过人工审核，禁止执行。
- `UPSTREAM_MODEL_ERROR`: 模型上游错误，允许重试并记录熔断计数。
- `MODEL_FALLBACK`: 主模型降级到备用模型，记录但不阻断。
- `CONTEXT_OVERFLOW`: 上下文超窗口限制，触发压缩后重试一次。
- `MAX_STEPS_EXCEEDED`: Agent 循环超最大步数，强制终止。
- `SESSION_EXPIRED`: 会话已过期，拒绝追加消息。
- `RATE_LIMITED`: 超过速率限制，拒绝请求。
- `FILE_SANDBOX_VIOLATION`: 文件路径越出业务沙箱根目录或触达受保护控制目录，禁止执行。
- `FILE_WRITE_CONFLICT`: 同一沙箱文件存在并发写入冲突，禁止覆盖。
- `FILE_LOCK_TIMEOUT`: 文件锁获取或等待超时，允许按策略重试。
- `VECTOR_WRITE_FAILED`: 向量写入失败，降级为文件型，记录但不阻断。
- `EMBEDDING_FAILED`: 向量化调用失败，允许重试一次。
- `CHANNEL_NOT_CONFIGURED`: 渠道未配置或已禁用，禁止重试。
- `CHANNEL_SEND_FAILED`: 渠道消息发送失败，允许重试一次。

### 9.4 接口验收检查
- FR-005 验收必须覆盖：Principle / long-term / short-term 三层记忆、全文检索、向量语义检索、索引重建、向量写入降级。
- FR-007 验收必须覆盖：ChannelAdapter 接口完整性、TestChannelAdapter 全流程、渠道动态加载、无渠道时仅 Web 可用。
- FR-008 验收必须覆盖：`.agents/skills` catalog 发现、Skill 草稿生成与审核、正式 Skill 发布/启停/回滚、迁移告警与审计记录完整性。
- FR-009 验收必须覆盖：上下文隔离、结构化回传、独立成本与日志记录。
- FR-010 验收必须覆盖：完整循环流转、最大步数限制、流式输出。
- FR-011 验收必须覆盖：严格 `SKILL.md` 校验、catalog 披露、Skill 激活、相对资源解析、Draft Skill 不可执行、`allowed-tools` 不扩大权限。
- FR-012 验收必须覆盖：工具注册发现、`activate_skill`、参数校验、超时处理、审批流、业务沙箱与受保护 Skill 目录边界。
- FR-013 验收必须覆盖：会话创建与过期、上下文压缩、归档后检索、short-term 会话隔离。
- FR-014 验收必须覆盖：同一用户多顶层任务并行、新任务不污染旧任务、原线程内重跑、共享 Principle/long-term 与隔离 short-term、状态可见性。
- FR-006 验收必须覆盖：首页会话气泡入口、“+”新建会话入口、点击气泡进入会话页、状态管理页展示主/子 Agent 进度、Skill 目录/审核页只读化。

### 9.5 Agent 执行 API（对应 FR-010）
1. 发送消息并触发 Agent 执行
- 方法与路径：POST `/api/v1/agent/chat`
- 请求体：
  - session_id: string（可选；`task_mode=continue` 或 `cancel_and_rerun` 时必填；`task_mode=new_task` 时忽略）
  - message: string
  - task_mode: auto|continue|new_task|cancel_and_rerun（默认 `auto`）
  - session_title: string（可选，仅 `task_mode=new_task` 时作为新线程标题）
  - stream: bool（默认 false）
- 响应体（阻塞模式）：
  - session_id: string
  - run_id: string
  - session_action: continued|created_new|cancelled_and_reran
  - activated_skills: string[]
  - reply: string
  - steps: object[]（思考、动作、观察的列表）
  - usage: object
- 响应体（流式模式）：
  - SSE 事件流，事件类型：thinking / skill_activation / action / observation / reply / usage / done

任务模式约束：
- `continue`：继续指定的顶层任务线程，必须提供 `session_id`。
- `new_task`：无论当前是否存在 active 会话，都强制创建新会话与新的顶层 main run。
- `cancel_and_rerun`：取消指定线程中当前 running 的顶层 main run，并在同一线程内创建新的顶层 main run。
- `auto`：适用于消息渠道等弱交互入口；系统可自动判断继续当前线程或创建新线程，存在歧义时必须向用户确认。

2. 查询 Agent 执行记录
- 方法与路径：GET `/api/v1/agent/runs/{run_id}`
- 响应体：AgentRun 完整字段 + 关联的 ToolCall 列表 + UsageLog。
- 若查询对象为顶层 main run，则响应体还应返回其 child_runs 摘要（数量、状态分布、最近错误）。

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

3. 查询文件操作审计
- 方法与路径：GET `/api/v1/tools/file-operations`
- 查询参数：
  - session_id: string（可选）
  - run_id: string（可选）
  - status: pending|success|failed（可选）

4. 查询文件锁状态
- 方法与路径：GET `/api/v1/tools/file-locks`
- 查询参数：
  - sandbox_path: string（可选）

### 9.7 会话管理 API（对应 FR-013）
1. 新建顶层任务线程
- 方法与路径：POST `/api/v1/sessions`
- 请求体：
  - user_id: string
  - title: string（可选）
  - channel_type: string（可选，默认 `web`）
- 响应体：
  - session_id: string
  - status: active
  - created_at: datetime

2. 查询会话列表
- 方法与路径：GET `/api/v1/sessions`
- 查询参数：
  - status: active|expired|archived（可选）
  - user_id: string（可选）
- 响应体：会话列表（id, title, user_id, channel, status, current_run_status, active_child_runs, created_at, last_active_at, message_count）。

3. 查询会话详情与消息历史
- 方法与路径：GET `/api/v1/sessions/{session_id}`
- 响应体：Session 完整字段 + 消息列表（role, content, timestamp）+ 关联 main AgentRun 列表 + 当前 running main run 摘要。

4. 手动关闭会话
- 方法与路径：POST `/api/v1/sessions/{session_id}/close`
- 响应体：
  - session_id: string
  - status: archived
  - summary: string（自动生成的会话摘要）

### 9.8 状态管理 API（对应 FR-006 / FR-014）
1. 查询首页顶层会话气泡列表
- 方法与路径：GET `/api/v1/status/entry`
- 查询参数：
  - user_id: string（可选）
  - status: active|expired|archived（可选）
- 响应体：会话气泡列表（session_id, title, display_label, status, current_run_status, unread_events, last_active_at）。

2. 查询状态管理总览
- 方法与路径：GET `/api/v1/status/overview`
- 查询参数：
  - user_id: string（可选）
  - session_status: active|expired|archived（可选）
  - run_status: queued|running|success|failed|timeout|cancelled（可选）
- 响应体：
  - sessions: object[]
  - 每个 session 最小字段：session_id, title, status, current_main_run, child_run_summary, last_event_at

3. 查询单个顶层会话的运行树
- 方法与路径：GET `/api/v1/status/sessions/{session_id}`
- 响应体：
  - session_id: string
  - title: string
  - current_main_run: object
  - runs: object[]（树形或扁平结构，至少包含 run_id, parent_run_id, agent_role, display_name, status, progress_text, started_at, ended_at, latest_event, latest_error）

状态展示约束：
- 首页气泡列表用于会话入口，不承担完整运行树展示职责。
- 状态管理页必须调用状态总览或运行树接口，而不是依赖聊天页自行推断运行状态。

### 9.9 记忆管理 API（对应 FR-005 / FR-013）
1. 查询 Principle 记忆
- 方法与路径：GET `/api/v1/memory/principle`
- 响应体：当前 Principle 文档列表或聚合内容。

2. 更新 Principle 记忆
- 方法与路径：PUT `/api/v1/memory/principle`
- 请求体：
  - content: string
  - change_note: string
  - operator: string

3. 查询与写入 long-term 记忆
- 方法与路径：GET `/api/v1/memory/long-term`
- 方法与路径：POST `/api/v1/memory/long-term`

4. 分层记忆检索
- 方法与路径：GET `/api/v1/memory/search`
- 查询参数：
  - query: string
  - tiers: principle|long_term|short_term（可多选）
  - session_id: string（当 tiers 包含 short_term 时必填）

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
- 1.0.1（2026-04-17）
  - 将“渐进式披露优先”提升为全局设计原则，明确目录、索引、摘要、snippet 优先，正文与资源按需加载。
  - FR-005 增补记忆注入的轻量披露约束：默认使用 summary/snippet/命中片段，不得批量注入完整记忆正文。
  - FR-011 增补 Skill catalog 的最小披露约束，禁止在推理前预读全部 `SKILL.md` 与资源目录，以控制 token 成本并减少上下文噪音。
- 1.0.0（2026-04-17）
  - 正式 Skill 的单一事实源切换为项目内 `.agents/skills`，并明确数据库仅承担草稿、审核、审计、revision snapshot、catalog cache 与迁移状态职责。
  - FR-008 重写为“Agent Skills 草稿 -> 审核 -> 发布 -> 回滚”生命周期，移除正式 Skill 的直接 API/Web CRUD 语义。
  - FR-011 重写为严格 Agent Skills 运行时定义：`SKILL.md` + 标准 frontmatter + 渐进披露 + 按需资源读取。
  - FR-012 扩展内置 `activate_skill` 能力，并明确 `.agents/skills` 为受保护控制目录，通用文件工具不得直接访问。
  - FR-006 扩展为“Skill 目录/审核”只读页面，移除聊天页 Skill 下拉预绑定与正式 Skill 手工表单入口。
  - 数据模型从数据库中心化 Skill 改写为 `SkillCatalogEntry`、`SkillDraft`、`SkillRevision` 与 `activated_skills` 记录。
  - API 9.1 重写为目录、审核、审计与迁移接口；API 9.5 返回 `activated_skills` 并新增 `skill_activation` 流式事件。
  - 测试矩阵与 M2 门禁重写为 Agent Skills catalog、activation、受保护目录与迁移语义。
- 0.9.0（2026-04-16）
  - FR-005 从“混合记忆”扩展为“分层混合记忆”，明确 Principle 全局共享、long-term 全局共享、short-term 会话隔离的三层语义。
  - FR-008 增补自动生成 Skill 草稿与人工审核启用流程，要求自动沉淀的 Skill 不可直接上线执行。
  - FR-009 明确子 Agent 默认不隐式继承 Principle / long-term / short-term，且工具权限不得大于所属主 Agent。
  - FR-011 增补 Draft Skill 的运行时约束与自动提取 Skill 的 allowed_tools 来源规则。
  - FR-012 新增受控文件工具语义：list_dir、read_file、write_file、patch_file；明确固定沙箱目录、写操作审批、路径越界拒绝与并发写冲突规则。
  - FR-013 明确会话私有 short-term 与全局共享 Principle / long-term 的边界，归档摘要进入全局 long-term。
  - FR-014 增补多顶层线程共享与隔离规则，并要求跨会话同文件并发写入返回冲突而非静默覆盖。
  - NFR-005 / NFR-006 扩展为文件沙箱、安全审计、文件锁与写冲突控制。
  - 数据模型新增 MemoryDocument、SkillDraft、FileLock、FileOperation；API 新增 Skill draft、记忆管理与文件审计相关条目。
- 0.8.0（2026-04-16）
  - 扩展 FR-006 Web 管理看板的信息架构：默认首页改为顶层会话气泡入口页，不再直接显示对话框。
  - 新增“+”气泡/卡片用于新建顶层会话，明确点击会话气泡后才进入对应会话页。
  - 左侧导航新增“状态管理”，用于可视化不同顶层会话及主/子 Agent 的执行进度。
  - 新增 API 9.8 状态管理 API，覆盖首页会话入口气泡、状态总览与单线程运行树查询。
  - 扩充 FR-006 与 FR-014 的 Web UI 验收标准，要求页面层级与多主 Agent 语义保持一致。
- 0.7.0（2026-04-16）
  - 新增 FR-014：支持同一业务员多个顶层任务线程与多个主 Agent 并行处理。
  - 明确 Session 的语义为“业务员可见的顶层任务线程”，支持同一用户多个 active 会话并行存在。
  - FR-010 增补顶层路由前置决策：继续当前任务、创建新任务、取消并重跑。
  - FR-013 扩展为多会话并存语义，新增显式新建任务与指定 session_id 继续任务的规则。
  - NFR-006 扩展为两层并发控制：顶层 main Agent 并发与子 Agent 并发分别限流。
  - 数据模型扩展：Session 增加 title、current_run_id；AgentRun 明确顶层 main run 判定规则。
  - API 9.5 新增 task_mode / session_action 语义；API 9.7 新增创建顶层任务线程接口并扩展会话列表字段。
  - 测试矩阵与 M3 门禁扩展覆盖 FR-014。
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
- ODR-007：顶层任务线程语义（已解决：Session 即业务员可见的顶层任务线程，同一用户可并行多个 active Session，见 FR-013/FR-014）。
- ODR-008：Web 首页信息架构（已解决：默认首页为顶层会话气泡入口页；聊天页为会话详情页；状态管理承担多线程与主/子 Agent 进度可视化，见 FR-006/9.8）。
- ODR-009：记忆分层语义（已解决：Principle 全局共享、long-term 全局共享、short-term 会话隔离，见 FR-005/FR-013）。
- ODR-010：文件工具沙箱与项目控制目录边界（已解决：业务文件使用固定单一配置化沙箱目录，`.agents/skills` 作为受保护控制目录单独处理，见 FR-012/NFR-005）。
- ODR-011：自动生成 Skill 的上线策略（已解决：先生成草稿，再人工审核启用，见 FR-008/FR-011）。
- ODR-012：正式 Skill 的存储与格式（已解决：项目内 `.agents/skills` + 严格 Agent Skills，见 FR-008/FR-011）。
- ODR-013：Skill 激活机制（已解决：先披露 catalog，再通过 `activate_skill` 加载正文与资源，见 FR-011/FR-012）。

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
- src/skills：Skill 目录发现、catalog 索引、草稿审核、发布、回滚快照、运行时激活与迁移。
- src/tools：工具注册表、内置工具实现、自定义工具加载。
- src/models：LLM 适配器、模型路由、上下文窗口管理、Token 计数。
- src/sessions：会话/顶层任务线程生命周期、上下文压缩、归档。
- src/services/scheduler：调度与执行循环。
- src/services/notification：通知服务（通过渠道抽象层发送）。
- src/services/memory：Principle / long-term / short-term 记忆与检索。
- src/services/file_workspace：沙箱文件读写、锁与审计。
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
- session_id（如有）
- activated_skills（如有）
- agent_role（main|sub）
- event
- status
- error_code（失败时）

### 19.2 指标（最小集）
- 任务执行总数、成功率、失败率。
- 顶层主 Agent 活跃数、排队深度、平均执行时长。
- 子 Agent 平均耗时、超时率。
- Skill 激活次数、失败率、草稿批准率、发布/回滚次数。
- 模型 token 使用量与成本趋势。

### 19.3 审计事件（强制）
以下动作必须记录审计：
- Skill 正式发布、启用、停用、回滚。
- Skill 草稿生成、编辑、批准、拒绝。
- Principle / long-term 记忆的人工更新。
- 沙箱文件写入、补丁写入、写冲突与人工审批。
- 主 Agent 调度子 Agent。
- 人工新建顶层任务、任务切换、人工重试、人工取消、配置变更。

## 20. 测试矩阵与里程碑门禁
### 20.1 测试矩阵（按需求）
- FR-005：
  - Principle 记忆对所有顶层 main Agent 默认可见。
  - long-term 记忆可被不同顶层会话共享命中。
  - 不同会话的 short-term 记忆互不共享。
  - 文件型全文检索与 tier 过滤返回相关结果。
  - 向量语义检索返回 top-K 相关结果，short-term 检索必须按 session_id 过滤。
  - 人工编辑 Principle / long-term 后向量索引自动重建。
  - 向量写入失败时降级为文件型，不影响主流程。
  - Agent 循环中自动检索记忆并注入上下文。
- FR-007：
  - TestChannelAdapter 完成发送/接收/回调校验全流程。
  - 新增渠道适配器无需修改核心模块代码。
  - 未配置渠道时系统仅通过 Web API 工作。
  - 渠道配置动态加载正确。
- FR-006：
  - 默认首页显示顶层会话气泡入口，而不是聊天输入框。
  - “+”气泡可创建新的顶层会话。
  - 点击已有会话气泡后进入对应会话页，且会话上下文正确切换。
  - 左侧“状态管理”页可展示顶层会话、main Agent 与 sub Agent 的层级进度。
  - Skill 页面展示只读 catalog 与待审核草稿，且不存在正式 Skill 手工 CRUD 控件与聊天页 Skill 下拉框。
- FR-008：
  - `.agents/skills` 中的正式 Skill 可被发现并进入 catalog。
  - 非法 frontmatter 或缺少必填字段的 Skill 会被跳过并产生告警。
  - 成功的 AgentRun 或明确对话意图可生成 Skill 草稿。
  - Skill 草稿可编辑、可批准、可拒绝，且审核动作写审计日志。
  - 审批通过的 Skill 草稿会写入 `.agents/skills/<skill-name>/SKILL.md` 并触发 registry reload。
  - 停用后的 Skill 不再被披露或激活；回滚会把已批准 revision 重新发布到正式目录。
  - 旧数据库中心化 Skill 会进入 migration draft / 告警路径，而不是直接进入正式执行。
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
  - 启动阶段仅加载 Skill catalog 元数据，不预读完整正文。
  - 命中 Skill 后才激活 `SKILL.md` 正文，并将激活事件记录到运行日志。
  - Draft Skill 未审批前不得被加载执行。
  - 资源路径按 Skill 根目录相对解析，且不能越过当前 Skill 根目录。
  - `allowed-tools` 只能预批准已有权限，不能扩大工具能力或绕过受保护目录规则。
- FR-012：
  - 工具注册后可通过 API 查询。
  - 工具参数不合法时拒绝调用。
  - exec 工具白名单外的命令被拒绝。
  - activate_skill 仅接受已发现且可激活的 Skill 名称。
  - 沙箱外的目录列表、文件读取、文件写入请求以及对 `.agents/skills` 的通用直接访问都会被拒绝。
  - write_file / patch_file 默认触发审批流，审批通过后恢复循环。
  - 多会话或多 Agent 对同一文件并发写入返回冲突，不静默覆盖。
  - 文件操作审计可查询到路径、操作类型与校验和变化。
  - requires_approval 工具触发审批流，审批通过后恢复循环。
  - 工具执行超时返回 TOOL_EXECUTION_FAILED。
- FR-013：
  - 显式继续任务时消息归属指定会话。
  - 显式新建任务时，即使在超时窗口内也创建新会话。
  - 同一用户多个 active 会话互不污染，short-term 记忆也不跨会话共享。
  - 上下文压缩后 Agent 仍能基于摘要回答历史问题。
  - 会话归档后摘要进入全局 long-term，并可被后续会话记忆检索命中。
  - 手动关闭会话后自动生成摘要。
- FR-014：
  - 同一用户在已有 running 任务时可创建第二个顶层任务线程，两个 main run 可并行存在。
  - 新任务创建后不得注入旧任务的子 Agent 树，旧任务上下文不被污染。
  - 取消并重跑在原线程内创建新的顶层 main run，历史运行记录保留。
  - 多顶层线程共享 Principle / long-term，但不共享 short-term。
  - 不同顶层线程对同一沙箱文件并发写入时返回冲突并保留已成功写入结果。
  - 顶层 main Agent 并发限额与子 Agent 并发限额分别生效。
  - 状态管理页能同时展示多个顶层线程的运行状态，并可定位到各自 main/sub run 进度。

### 20.2 里程碑门禁
- M1（骨架完成）：通过健康检查、基础日志、最小 Agent 循环链路（接收消息 → 调用 LLM → 返回回复）。
- M2（Agent Skills + 工具可用）：FR-008、FR-011、FR-012 全部验收通过。
- M3（主/子 Agent 可用）：FR-009、FR-010、FR-014 全部验收通过。
- M4（会话与记忆）：FR-005、FR-013 全部验收通过。
- M5（业务试运行）：渠道抽象层可用、Web 看板、成本统计打通（FR-003、FR-004、FR-006、FR-007）。

门禁规则：未通过当前里程碑门禁，不进入下一里程碑开发。
