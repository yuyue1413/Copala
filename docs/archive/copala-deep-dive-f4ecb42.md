# Copala 项目深度剖析文档

> [!NOTE]
> 本文是基于 Commit `f4ecb42` 及其当时本地工作树形成的时点归档。文中的文件路径、行号、测试数量和覆盖率不代表 `src/` 布局重构后的当前代码；当前入口请以仓库 README、`docs/` 专题文档和最新评测结果为准。

> 分析基线：<code>main</code> 分支，Commit <code>f4ecb42</code>（2026-05-20）
>
> 项目版本：<code>0.1.4.post4</code>；许可证：MIT；成熟度：Alpha
>
> 分析日期：2026-08-21；目标读者：开发者、技术负责人、架构师
>
> 分析方式：源码、配置、测试、README、SECURITY、Git 历史交叉取证，并执行完整测试与静态检查
>
> 范围约束：本文只分析当前仓库与本机可验证行为；没有连接真实 QQ、飞书、邮箱、高德、Tavily 或模型服务

## 证据标记约定

- **[事实]**：可由当前源码、配置、测试或实际运行结果直接确认。
- **[推断]**：根据代码结构推导的设计意图或运行后果，不能当成作者明示。
- **[建议]**：本文提出的优化方案，不代表当前项目已经实现。
- **[待验证]**：需要真实凭证、外部服务、生产数据或长期压测才能确认。
- 路径与行号均以 Commit <code>f4ecb42</code> 为准；后续代码变化可能使行号漂移。

---

# 0. 阅读指南

## 0.1 这是什么项目

**[事实]** Copala 是一个 Python 单进程、异步驱动的个人 AI Agent 框架。它把 QQ、飞书、Telegram、Discord、Slack、邮件等通道统一为消息事件，以 <code>AgentLoop</code> 编排规则型领域服务、LLM、工具、会话、长期记忆、日历与后台调度。项目定位见 <code>README.md:1-17</code>，包入口与命令入口见 <code>copala/__main__.py:5-8</code>、<code>pyproject.toml:64-65</code>。

它不是微服务平台，也不是一个对外提供业务 HTTP API 的 Web 服务。名为 <code>gateway</code> 的命令负责在一个进程内装配并运行通道、Agent、Cron 和 Heartbeat；<code>--port</code> 当前只参与输出，没有创建监听 socket，证据见 <code>copala/cli/commands.py:323-578</code>。真正使用 <code>18790</code> 监听的地方是飞书 OAuth 临时回调服务，见 <code>copala/cli/commands.py:1007-1096</code>。

## 0.2 阅读前置知识

读者只需理解 Python <code>asyncio</code>、消息队列、JSON/SQLite、LLM tool calling 和基础 OAuth。阅读代码时要特别区分：

1. **通道消息路径**：外部聊天平台经 <code>MessageBus</code> 进入 <code>AgentLoop</code>。
2. **领域规则阶段**：日历查询、智能提醒、出行/每日规划属于终止型快路径；LifeEvent 是 LLM 前预处理器，只有产生回复消息时短路，否则可能先写 TODO/Cron/RAG 再继续慢路径。
3. **通用慢路径**：未命中终止型路由，或 LifeEvent 预处理没有产生回复时，才构建上下文并进入 LLM—工具循环。
4. **前台与后台入口**：前台总线消息经过全局处理锁；普通 Cron job 与 Heartbeat 调用 <code>process_direct()</code> 并绕过该锁；只有 <code>Life Events Email Patrol (Auto)</code>、<code>Life Events Calendar Patrol (Auto)</code> 和 <code>Smart Reminders Patrol (Auto)</code> 三个特殊 job 由 Cron 回调直接调用领域服务。普通 <code>Email Patrol (Auto)</code> 仍走 <code>process_direct()</code>。

## 0.3 三条阅读路径

| 路径 | 适合读者 | 推荐章节 | 目标 |
|---|---|---|---|
| 快速认知 | 产品、初次接触者 | 1 → 3 → 4 → 16 | 10 分钟内说清项目价值、架构和一条主链 |
| 实现理解 | 维护开发者 | 4 → 5 → 6 → 7 → 8 → 14 | 能从入口追到状态和外部副作用，安全修改代码 |
| 架构评审 | 技术负责人、架构师 | 2 → 3 → 9 → 10 → 11 → 12 → 13 | 判断边界、权衡、上线风险和演进顺序 |

如果只有 10 分钟，优先阅读：1.1 的一句话、1.5 的全景图、3.2 的运行时架构、4.2 的锚点链路、12.2 的 P0 风险、13.1 的上线门槛。

## 0.4 本文的认知主线

本文始终围绕同一个锚点问题展开：

> QQ 用户说：“周末我想去杭州玩两天，预算控制一下，不要太赶。”

该场景来自 <code>README.md:804-853</code>，能够同时覆盖通道、总线、路由、RAG、日历、高德、Web 搜索、结果持久化和回复，是项目价值密度最高的代表场景。

---

# 1. 一页项目全景

## 1.1 一句话说明

**[事实]** Copala 是一个把多聊天通道、个人数据源、LLM 工具调用和确定性生活服务装配在本地单进程中的个人长期记忆 Agent。

## 1.2 核心问题、用户与输入输出

| 维度 | 结论 |
|---|---|
| 核心问题 | 普通聊天机器人缺少跨轮次记忆、个人日程与主动任务；Copala 试图把这些能力放入持续运行的个人 Agent |
| 目标用户 | 能自行配置模型及通道凭证、愿意在本机运行个人助手的开发者或高级个人用户 |
| 典型输入 | 聊天消息、邮件、日历事件、Cron/Heartbeat 任务、CLI 命令、文件与网页内容 |
| 典型输出 | 通道回复、规划文本、日历草案或真实事件、提醒、长期记忆、会话记录、工具副作用 |
| 核心约束 | 外部平台认证、个人隐私、高风险工具权限、跨轮次状态一致性、异步生命周期 |

## 1.3 五项核心能力

1. **多通道接入**：统一收发 QQ、飞书、Telegram、Discord、Matrix、钉钉、Slack、Email、Mochat 等通道，入口集中在 <code>copala/channels/manager.py:34-243</code>。
2. **Agent 与工具编排**：<code>AgentLoop</code> 注册文件、Shell、Web、消息、日历、邮件、规划、MCP 等工具，并执行多轮 tool calling，见 <code>copala/agent/loop.py:234-394</code>。
3. **长期记忆**：Markdown 长期摘要与 SQLite RAG 双层并存；RAG 使用 FTS5、192 维本地哈希向量和 RRF 融合，见 <code>copala/agent/memory.py:47-170</code>、<code>copala/agent/memory_rag/store.py:19-212</code>。
4. **个人领域服务**：日历查询、每日规划、智能提醒与出行规划采用终止型规则快路径；LifeEvent 则在 LLM 前预处理，只有产生回复消息时才短路，否则可能先写 TODO/Cron/RAG 再继续通用慢路径，见 <code>copala/agent/loop.py:533-566,902-920</code>、<code>copala/life_events/service.py:681-715</code>。
5. **主动运行**：普通 Cron job 与 Heartbeat 通过 <code>process_direct()</code> 触发 Agent；LifeEvent Email/Calendar 与 SmartReminder 三个具名特殊 patrol 由 Cron 回调直接调用领域服务，见 <code>copala/cli/commands.py:404-483,501-551</code>。

## 1.4 技术栈与核心模块

| 类别 | 采用技术 | 核心模块 |
|---|---|---|
| 语言与运行时 | Python ≥ 3.11、<code>asyncio</code> | <code>copala/agent/loop.py</code> |
| CLI/配置 | Typer、Pydantic Settings、JSON | <code>copala/cli/commands.py</code>、<code>copala/config</code> |
| 模型 | LiteLLM、OpenAI SDK、自定义 Codex OAuth、fallback | <code>copala/providers</code> |
| 消息平台 | 多个平台 SDK + 统一 Adapter | <code>copala/channels</code>、<code>copala/bus</code> |
| 本地状态 | JSON、JSONL、Markdown、SQLite FTS5 | <code>copala/session</code>、<code>copala/agent/memory*</code> |
| 外部服务 | 飞书日历、IMAP/SMTP、高德、Tavily、MCP | <code>copala/calendar</code>、工具与规划服务 |
| 测试与规范 | pytest、pytest-asyncio、Ruff | <code>tests</code>、<code>pyproject.toml</code> |

## 1.5 图 1-1：项目全景图

本图回答“外部输入如何穿过核心编排，最终变成回复或副作用”。

~~~mermaid
flowchart LR
    U[个人用户] --> C[QQ / 飞书 / Telegram / EmailChannel 等]
    C --> A[Channel Adapter]
    A --> B[MessageBus]
    B --> RUN[AgentLoop.run / /stop gate]
    RUN -- /stop --> STOP[AgentLoop._handle_stop]
    STOP -->|停止结果| B
    RUN -- 非 /stop --> FRONT[AgentLoop._dispatch]
    FRONT --> L[AgentLoop._process_message]

    CRON[CronService] --> ORD[普通 Cron 回调]
    ORD --> DIRECT[AgentLoop.process_direct]
    HB[HeartbeatService] --> DIRECT
    DIRECT --> L
    CRON --> PATROL[LifeEvent Email/Calendar + SmartReminder 特殊 patrol]
    MAIL[(Mailbox / Email)] --> PATROL
    FEISHU[(飞书日历)] --> PATROL
    MAPWEB[(高德 / Tavily / Web)] -->|高德数据| PATROL
    PATROL --> PD[巡检领域服务]

    L --> R{终止型领域路由命中?}
    R -- 是 --> D[终止型领域服务]
    R -- 否 --> LE[LifeEvent 预处理]
    LE -- 有 messages --> REPLY[回复文本]
    LE -- 无 messages，可能已有副作用 --> X[ContextBuilder + LLMProvider + ToolRegistry]
    D --> REPLY
    X --> REPLY
    D --> DS[(Session / RAG / Domain DB)]
    PD --> PS[(LifeEvent / Risk DB / Cron / RAG)]
    LE -. 可能写入 .-> LS[(Session / LifeEvent DB / Cron / RAG)]
    X --> XS[(Session / MEMORY / RAG / Draft / Tool stores)]
    D -. 按配置调用 .-> FEISHU
    D -. 按配置调用 .-> MAPWEB
    LE -. 可能直写 .-> FEISHU
    PD -. LifeEvent 可能直写 .-> FEISHU
    PD -. 领域通知回调 .-> B
    X -. Tool 调用 .-> FEISHU
    X -. Tool 调用 .-> MAIL
    X -. Tool 调用 .-> MAPWEB
    X -. MessageTool 可直接发送 .-> B

    REPLY --> MODE{本轮调用入口}
    MODE -- _dispatch --> B
    MODE -- process_direct --> RET[返回字符串给调用者]
    RET --> CDEL[普通 Cron: response 非空 + 未由 MessageTool 发送 + deliver + 非 IGNORE]
    RET --> HNOT[Heartbeat: response 非空 + on_notify + target 非 cli]
    RET --> CLIRET[CLI 调用者]
    CDEL -. 满足条件 .-> B
    HNOT -. 满足条件 .-> B
    B --> A
    A --> U

    G[CLI gateway 组合根] -. 装配 .-> A
    G -. 装配 .-> B
    G -. 装配 .-> RUN
    G -. 装配 .-> CRON
    G -. 装配 .-> HB
~~~

读图结论：

1. 前台主线是 <code>Channel → MessageBus → AgentLoop → 领域服务或 LLM/Tools → MessageBus → Channel</code>。
2. <code>gateway</code> 只以虚线表示装配和生命周期关系，不是 HTTP Server，也不是邮箱/日历数据流的中转站。
3. 日历查询、规划和提醒是终止型分支；LifeEvent 是条件短路的预处理器，无回复消息时仍会进入 LLM 慢路径。
4. <code>process_direct()</code> 只返回字符串：普通 Cron 回调仅在 <code>response</code> 非空、MessageTool 本轮未发送、<code>deliver</code> 开启且结果不是 <code>IGNORE</code> 时投递；Heartbeat 仅在 <code>response</code> 非空、配置了 <code>on_notify</code> 且目标不是 <code>cli</code> 时发布到 Bus；三个具名特殊 patrol 则直接调用领域服务并可用领域 <code>send_callback</code> 通知。
5. 文件、SQLite 与外部 SaaS 同时承载状态，且外部输入可触达高权限能力；事务、授权与隔离是首要评审面。

## 1.6 最重要的执行主线

<code>BaseChannel._handle_message()</code> 将平台事件归一化为 <code>InboundMessage</code> → <code>MessageBus.publish_inbound()</code> → <code>AgentLoop.run()</code> 先在创建 task 和获取锁之前拦截 <code>/stop</code>，其他消息由 <code>_dispatch()</code> 处理 → 命令或终止型领域路由可以直接返回；未终止时先执行 LifeEvent 预处理，有回复消息则短路，无回复消息则可能带着已产生的 TODO/Cron/RAG 副作用继续 → 此时才设置共享 Tool context、构建 Context 并运行 LLM/工具循环 → 按各分支保存 Session/Memory → 发布 <code>OutboundMessage</code> → <code>ChannelManager</code> 调用对应 Adapter 的 <code>send()</code>。

代码证据：<code>copala/channels/base.py:77-130</code>、<code>copala/bus/queue.py:16-34</code>、<code>copala/agent/loop.py:396-633,902-920</code>、<code>copala/life_events/service.py:681-715</code>、<code>copala/channels/manager.py:197-226</code>。

## 1.7 技术特点与首要风险

技术特点：

- 一个进程内同时容纳被动消息与主动调度，部署简单但故障域大。
- 规则快路径给个人领域请求提供确定性、低成本响应。
- Provider、Channel、Tool、Calendar Provider 均有适配或注册边界。
- 双层记忆兼顾人可读摘要与可检索事实。
- 文件优先的本地状态易检查，但并发、原子性与迁移能力有限。

最值得关注的风险：

1. **安全边界失效**：TLS 校验自动降级、Shell 沙箱可绕过、WebFetch/ICS SSRF、附件路径穿越。
2. **错误外部副作用**：生活事件忽略 <code>calendarWriteMode</code>，可以在 LLM 前直接写真实日历；确认流程也缺少幂等。
3. **跨会话并发与投递**：后台入口绕过全局锁；普通 Cron/HB 若进入慢路径还会竞争可变 Tool context，目标也可能被重新选择，存在串聊与隐私泄露风险。

## 1.8 必须记住的五个结论

1. Copala 是本地单进程异步 Agent，不是网络微服务。
2. <code>AgentLoop</code> 是控制中心，但并非所有请求都经过 LLM。
3. 本文锚点“杭州两日游”走 <code>OutingPlanningService</code> 快路径，绕过通用 LLM 工具循环。
4. Session、长期摘要、RAG、草案、Cron 与领域库分散持久化，没有统一事务。
5. 当前 Alpha 版本适合研究与受控演示；在多人或不可信输入环境上线前，必须完成第 13 章 P0。

## 1.9 核心术语速查

| 术语 | 本文含义 |
|---|---|
| Channel Adapter | 把平台 SDK 事件转换成统一消息，并把统一回复发回平台 |
| MessageBus | 进程内两个异步队列，传递入站与出站消息 |
| Turn | 一次用户输入及其工具调用、模型响应和持久化过程 |
| 快路径 | LLM 前由规则识别、领域服务直接处理并返回的路径 |
| 慢路径 | 由 ContextBuilder、LLMProvider 和 ToolRegistry 组成的通用 Agent 循环 |
| RAG | 从本地长期事实库检索与当前请求相关内容并注入上下文 |
| Draft | 日历外部写入前的本地待确认对象 |
| process_direct | CLI、普通 Cron（含普通 Email Patrol）与 Heartbeat 绕过 Bus/前台锁调用 Agent 的直接入口；三个具名特殊 patrol 不走此入口 |

后续导航：系统边界见第 2 章；架构见第 3 章；锚点逐步取证见第 4 章；模块地图见第 5～6 章；风险与落地计划见第 12～14 章。

---

# 2. 项目目标、边界与核心设计思想

## 2.1 项目为什么存在

**[事实-文档]** README 将目标描述为：持续积累用户偏好、日程习惯、邮件线索和任务上下文，并在后续对话通过 RAG 给出连续、个性化回复，见 <code>README.md:3-17</code>。

**[事实-代码]** 这一目标落在四类机制上：

- 通道持续接入：<code>copala/channels</code>。
- 会话与长期记忆：<code>copala/session</code>、<code>copala/agent/memory.py</code>、<code>copala/agent/memory_rag</code>。
- 个人领域行动：<code>copala/calendar</code>、<code>copala/life_events</code>、<code>copala/planning</code>、<code>copala/smart_reminders</code>。
- 主动任务：<code>copala/cron</code>、<code>copala/heartbeat</code>。

**[推断]** 当前设计优先追求“单人本地部署、功能可见、接入成本低”，而不是多租户隔离、横向扩容或严格事务。这解释了单进程、文件状态和共享工具实例，但不能成为忽略安全与一致性的理由。

## 2.2 系统负责与不负责什么

| 在边界内 | 在边界外或尚未实现 |
|---|---|
| 平台消息适配、权限白名单、会话路由 | 平台账号体系本身、DKIM/SPF 身份认证 |
| 模型请求、工具定义、工具执行循环 | 模型训练、推理托管、模型 SLA |
| 本地会话/记忆/草案/任务状态 | 云端多租户数据库、分布式事务 |
| 飞书日历、邮箱、高德、Tavily 等集成 | 对这些外部系统可用性的控制 |
| Cron/Heartbeat 进程内调度 | 多实例选主、耐久队列、跨进程抢占 |
| CLI 管理和持续运行 | 通用 HTTP API、管理控制台、标准健康检查端点 |

## 2.3 上下游与信任边界

- 上游：聊天平台回调/长连接、邮件服务器、CLI 用户、Cron/Heartbeat 文件。
- 下游：LLM Provider、飞书日历、高德、Tavily、MCP Server、IMAP/SMTP、宿主机文件系统和 Shell。
- 可信本地状态：工作区文件与 SQLite；但这些文件可能被用户、测试或异常中断破坏。
- 不可信内容：用户消息、邮件正文、网页、MCP 返回、附件名、模型工具参数。**[建议]** 它们都应按提示注入与路径注入输入处理。

## 2.4 核心业务约束

1. 通道消息必须携带 <code>channel</code> 与 <code>chat_id</code>，它们共同参与 Session 和回复路由。
2. 外部服务凭证可能缺失，领域能力必须能返回降级信息而非假装执行成功。
3. 只读建议与外部写入应有明确边界；当前通用日历草案做到了部分隔离，生活事件旁路没有做到。
4. 同一会话的消息顺序会影响上下文、记忆 offset 与工具目标，必须串行；不同会话不应被全局锁无差别阻塞。
5. 后台任务必须绑定稳定 owner 与投递目标；当前“最近会话”回退违反这一约束。

## 2.5 五条实际不变量

这里的“不变量”指当前主干代码在正常路径中努力维持的事实，不表示所有异常与旁路都已严格满足。

| 不变量 | 机制 | 代码证据 | 边界/例外 |
|---|---|---|---|
| 平台事件先归一化为统一消息 | <code>BaseChannel._handle_message()</code> 创建 <code>InboundMessage</code> | <code>channels/base.py:87-130</code>、<code>bus/events.py</code> | Cron/Heartbeat 不经过 Channel 入站 |
| 通道接入默认检查 allowlist | <code>is_allowed()</code> 与启动校验 | <code>channels/base.py:77-115</code>、<code>channels/manager.py:142-150</code> | Email sender 身份只信任可伪造的 From |
| 模型差异隐藏在 Provider 接口后 | <code>LLMProvider.chat()</code> 与注册表选择 | <code>providers/base.py:40-130</code>、<code>providers/registry.py:19-64,390-448</code> | Codex OAuth Provider 有特有 TLS 行为 |
| 通用工具通过注册表暴露和执行 | 定义收集、按名称查找、统一异常字符串 | <code>agent/tools/registry.py:18-59</code> | 快路径直接调用领域服务，不经过 ToolRegistry |
| 会话 key 决定连续上下文 | <code>SessionManager.get_or_create()</code> 及 JSONL 持久化 | <code>session/manager.py:73-180</code> | 后台目标推断可能切到最近的其他会话 |

## 2.6 设计原则与现实偏差

| 设计原则 | 已实现部分 | 当前偏差 |
|---|---|---|
| 适配器隔离外部平台 | Channel、Provider、Calendar Provider 边界清晰 | CLI 仍直接认识大量具体实现，装配层很重 |
| 本地优先、数据可检查 | Markdown、JSON、JSONL、SQLite 都可本地检查 | 明文凭证、非原子覆盖和敏感文件忽略规则不足 |
| 确定性优先处理明确领域意图 | 多个 LLM 前快路径 | 规则重复、路由优先级隐式、误判可直接产生副作用 |
| 高风险写入需要确认 | CalendarService 有 draft/confirm/discard | LifeEventManager 直接写飞书，配置开关没有生效 |
| 扩展能力通过注册/接口接入 | ToolRegistry、Provider Registry、ChannelManager | Channel 仍需在 Manager/Schema/CLI 多点手工修改 |

本章关键结论：项目边界是“本地个人 Agent 运行时”，而不是平台服务；最重要的架构约束来自个人数据、高权限工具和异步状态；当前代码在功能扩展上较积极，在能力授权、状态原子性与可运营性上尚未闭环。

读者应能回答：系统为何存在、信任边界在哪里、哪些能力不属于项目、快路径为何重要，以及“草案优先”在哪条旁路上被破坏。

---

# 3. 整体架构与系统分层

## 3.1 架构模式结论

**[事实]** 这是“模块化单体 + 事件队列 + Adapter/Registry + 规则路由 + Agent 工具循环”的混合架构。运行时只有一个 Python 进程和一个事件循环；文件与 SQLite 是本地持久化，外部 SaaS 是远端副作用边界。

## 3.2 图 3-1：系统上下文图

本图回答“Copala 与哪些人、平台和数据系统交互，信任边界在哪里”。

~~~mermaid
flowchart TB
    User[个人用户]
    Admin[本机维护者]
    Copala[Copala 单进程 Agent]
    IM[QQ / 飞书 / Telegram / Discord / Matrix / Slack / 钉钉]
    Mail[IMAP / SMTP]
    Model[LLM Providers / Codex OAuth]
    Calendar[飞书日历 / 本地 JSON / ICS]
    Search[高德 / Tavily / Web]
    MCP[MCP Servers]
    Host[宿主机文件系统 / Shell]

    User <--> IM
    IM <--> Copala
    User <--> Mail
    Mail <--> Copala
    Admin --> Copala
    Copala <--> Model
    Copala <--> Calendar
    Copala <--> Search
    Copala <--> MCP
    Copala <--> Host
~~~

读图结论：

1. Copala 同时连接不可信内容入口和高权限宿主机能力，提示注入不是单纯“回答质量”问题。
2. 外部平台各自拥有认证、速率和可用性边界；Copala 只能做超时、重试、降级与错误映射。
3. 本机维护者同时掌握配置、工作区与进程权限，是当前事实上的管理员。
4. 扩展点主要位于 Channel、Provider、Tool、Calendar Provider 和 MCP。

## 3.3 图 3-2：单进程运行时架构

本图回答“一个 gateway 进程中，哪些长期任务共同运行”。

~~~mermaid
flowchart TB
    CLI[Typer CLI gateway] --> CFG[Config + Workspace]
    CLI --> BUS[MessageBus]
    CLI --> AGENT[AgentLoop]
    CLI --> CM[ChannelManager]
    CLI --> CRON[CronService]
    CLI --> HB[HeartbeatService]

    CM -->|publish inbound| BUS
    BUS -->|consume inbound| RUN[AgentLoop.run / /stop gate]
    AGENT --> RUN
    RUN -->|/stop| STOP[AgentLoop._handle_stop]
    STOP -->|停止结果| BUS
    RUN -->|非 /stop| FRONT[AgentLoop._dispatch]
    BUS -->|consume outbound| CM
    CRON --> ORD[普通 Cron job 回调]
    ORD --> DIRECT[AgentLoop.process_direct]
    HB --> DIRECT
    AGENT --> DIRECT
    CRON --> PATROL[LifeEvent Email/Calendar + SmartReminder 特殊回调]
    PATROL --> PDOMAIN[巡检领域服务]

    FRONT --> RULES[AgentLoop._process_message 内联规则链]
    DIRECT --> RULES
    RULES --> TERMINAL{终止型领域路由命中?}
    TERMINAL -- 是 --> DOMAIN[终止型领域服务]
    TERMINAL -- 否 --> LIFE[LifeEvent 预处理]
    LIFE -- 有 messages --> REPLY[本轮回复]
    LIFE -- 无 messages --> LLM[ContextBuilder / Provider / ToolRegistry]
    LIFE -. 可能先写入 .-> STATE
    DOMAIN --> REPLY
    LLM --> REPLY
    LLM -. MessageTool .-> BUS
    DOMAIN --> STATE[(Workspace files + SQLite)]
    PDOMAIN --> STATE
    PDOMAIN -. send_callback .-> BUS
    LLM --> STATE

    REPLY --> MODE{本轮调用入口}
    MODE -- _dispatch --> BUS
    MODE -- process_direct --> RET[返回字符串]
    RET --> CDEL[普通 Cron: response 非空 + 未由 MessageTool 发送 + deliver + 非 IGNORE]
    RET --> HNOT[Heartbeat: response 非空 + on_notify + target 非 cli]
    CDEL -. 满足条件 .-> BUS
    HNOT -. 满足条件 .-> BUS
~~~

读图结论：

1. 通道消息由 <code>_dispatch()</code> 发布最终 Outbound；<code>process_direct()</code> 只返回字符串，普通 Cron/HB 再按各自条件投递；特殊 patrol 绕过 Agent，领域服务仍可用 <code>send_callback</code> 发布通知。
2. 图中的“内联规则链”是对 <code>AgentLoop._process_message()</code> 分支的概念命名，源码中不存在独立 <code>Fast-path Router</code> 组件。
3. LifeEvent 只有在产生 <code>messages</code> 时终止本轮；无消息时可能已经写状态，然后继续 LLM 慢路径。
4. Channel SDK、模型、外部 HTTP、Cron/Heartbeat 与 Bus 消费是异步的，但 JSON/JSONL、Markdown 和 SQLite RAG 的大量读写同步发生在事件循环线程。
5. 单进程崩溃会停止全部通道与后台任务；外部日历成功与本地落盘之间也没有事务。MCP 初始连接还在 Agent 消费前串行执行且部分连接无总超时，见 <code>agent/loop.py:396-400</code>、<code>agent/tools/mcp.py:83-147</code>。

## 3.4 图 3-3：核心模块依赖图

本图回答“源码模块之间的主要依赖方向及扩展点”。

~~~mermaid
flowchart LR
    CLI[cli.commands] --> Config[config]
    CLI --> Bus[bus]
    CLI --> Channels[channels]
    CLI --> Agent[agent.loop]
    CLI --> Cron[cron]
    CLI --> Heartbeat[heartbeat]

    Channels --> Bus
    Agent --> Bus
    Agent --> Session[session]
    Agent --> Context[agent.context]
    Agent --> Providers[providers]
    Agent --> Tools[agent.tools]
    Agent --> Domains[calendar / life_events / planning / smart_reminders]
    Context --> RAG[agent.memory_rag]
    Tools --> Domains
    Domains --> RAG
    Cron -. CLI 注入运行时回调 .-> Agent
    Heartbeat -. CLI 注入运行时回调 .-> Agent
~~~

读图结论：

1. <code>bus</code> 是 Channel 与 Agent 的稳定协议层；<code>bus/events.py</code> 的字段变化影响所有适配器。
2. <code>providers</code> 和 <code>agent/tools</code> 是明确扩展点。
3. <code>ContextBuilder</code> 不 import Session；它接收 <code>AgentLoop</code> 取得的 history，因此图中只保留 Agent → Session 与 Agent → Context 两条源码依赖。
4. 领域服务既可被 Tool 调用，也被 <code>AgentLoop</code> 或特殊巡检回调直接调用，形成多入口行为差异。
5. Cron/Heartbeat 到 Agent 的虚线表示 CLI 注入的运行时回调，不是源码 import 依赖；<code>cli.commands</code> 与 <code>agent.loop</code> 仍是依赖面最大的两个文件，当前未发现源码级循环 import 证据。

## 3.5 控制流、数据流与事务边界

| 边界 | 控制流 | 数据流 | 事务/恢复 |
|---|---|---|---|
| Channel ↔ Bus | 异步 publish/consume | <code>InboundMessage</code>/<code>OutboundMessage</code> | 仅内存队列，进程退出丢失 |
| Bus → Agent | 每条消息创建 task，随后全局锁 | 文本、媒体路径、metadata | 无持久队列和重放 |
| Agent → LLM | 多轮 await | messages、tools、tool results | Provider 错误转成响应或 fallback |
| Agent → 本地状态 | 同步文件/SQLite | 会话、记忆、草案、任务 | 多数无原子 replace 或跨文件事务 |
| Agent → 外部服务 | HTTP/SDK/IMAP | 日历、天气、路线、搜索、邮件 | 各模块自行处理，幂等/重试不统一 |
| 普通 Cron/HB → Agent | 直接 await <code>process_direct</code>，调用方再条件投递 | 合成 prompt、目标信息、返回字符串 | 绕过总线入站和前台锁；仅进入 LLM 慢路径时设置共享 Tool context；目标可能重选，投递条件分散在回调 |
| 三个具名特殊 patrol → 领域服务 | Cron 回调直接 await LifeEvent/SmartReminder 服务 | Mailbox/Calendar 数据与领域结果 | 绕过 Agent 与 Bus 入站、且不设置共享 Tool context；仍与前台并发，领域 <code>send_callback</code> 可向出站 Bus 发布通知 |

## 3.6 声明架构与实际代码的差异

1. <code>gateway.host/port</code> 和 CLI <code>--port</code> 给人以网络网关印象，但没有运行时监听，见 <code>config/schema.py:408-414</code>、<code>cli/commands.py:323-578</code>。
2. README 将 RAG 描述为 BM25 + Dense；实际 Dense 是固定 192 维 signed hash bag-of-token，不读取 <code>embeddingModel</code>，见 <code>copala/agent/memory_rag/embeddings.py:18-58</code>、<code>copala/agent/memory_rag/store.py:22-27</code>。
3. README 的“每轮 RAG”只适用于通用慢路径；多个快路径在 <code>ContextBuilder</code> 之前返回，见 <code>agent/loop.py:533-566,591-623</code>。
4. README 中通用日历“默认先草案”成立于 <code>CalendarService</code>，但 LifeEvent 自动处理直接调用 Provider，见 <code>calendar/service.py:148-271</code>、<code>life_events/service.py:672-770</code>。
5. 架构图片/遗留命令提及 WhatsApp，但 Schema、ChannelManager 和 bridge 源码不完整；<code>channels status/login</code> 仍访问不存在的 <code>config.channels.whatsapp</code>，见 <code>cli/commands.py:1175-1192,1267-1354</code>。

本章关键结论：系统的真实“中心”是 <code>AgentLoop</code> 而非网络 Gateway；快/慢双路径和前台/后台双入口是理解所有行为差异的钥匙；最大的结构风险是装配与业务编排集中、同步状态写入异步循环，以及缺少统一副作用协议。

读者应能回答：该项目属于什么架构、Gateway 是否监听 HTTP、快慢路径在哪里分叉、扩展点在哪里、事务和故障边界在哪里。

---

# 4. 锚点场景的完整生命周期

## 4.1 场景与结论

场景：QQ 用户发送“周末我想去杭州玩两天，预算控制一下，不要太赶。”

**核心结论**：该请求不是“LLM 决定调用 outing 工具”，而是 <code>AgentLoop</code> 在 LLM 之前通过最终生效的字符串规则命中 <code>OutingPlanningService.plan_from_request()</code>。服务按配置顺序尝试读取无类型过滤的相关 RAG 记忆、企业日历、高德路线/天气和 Web 搜索，再用确定性模板组装结果；仅在启用记忆保存时写 <code>plan_artifact</code>，最终回到 QQ。它绕过 <code>ContextBuilder</code>、<code>LLMProvider</code>、<code>ToolRegistry</code> 和共享 Tool context 设置。

锚点也暴露了解析边界：<code>plan_from_request()</code> 只提取目的地和 trip/outing 类型，把整句原文同时传给 <code>time_range/preferences/constraints</code>；周末窗口随后由 <code>_window()</code> 解析。<code>budget</code> 与 <code>companions</code> 保持为空；用户原文中的出发地也不会被解析，但 <code>_plan()</code> 可能用 Python 属性 <code>AmapConfig.default_origin</code>（JSON 配置 <code>smartReminders.amap.defaultOrigin</code>）补齐 <code>origin</code>。因此“预算控制一下”不会成为结构化预算，输出会显示“未指定”并给通用预算建议；“不要太赶”只会随原文进入展示和 RAG 查询，不是独立约束字段，也不驱动模板分支——当前多日模板本来就固定使用轻量、留缓冲的安排。

## 4.2 可追踪调用表

| 步骤 | 调用关系 | 输入 | 核心处理 | 状态变化 | 异常/降级 | 代码证据 |
|---|---|---|---|---|---|---|
| 1 | QQ SDK → <code>QQChannel</code> → <code>BaseChannel._handle_message</code> | 平台 sender、chat、text、media、metadata | 校验 allowlist，构造统一消息 | 无持久化 | 未授权直接丢弃；空 allowlist 启动即失败 | <code>channels/base.py:77-130</code>、<code>channels/qq.py</code> |
| 2 | Channel → <code>MessageBus.publish_inbound</code> | <code>InboundMessage</code> | 放入无界 <code>asyncio.Queue</code> | 内存队列增加 | 无持久重放/背压 | <code>bus/queue.py:16-34</code> |
| 3 | <code>AgentLoop.run</code> → <code>_dispatch</code> | 入站消息 | <code>/stop</code> 在建 task/取锁前拦截；本例不是 stop，故创建 task 并获取全局锁 | 活动 task 表变化 | task 异常记录；所有会话争同一锁 | <code>copala/agent/loop.py:396-437</code> |
| 4 | <code>_process_message</code> | channel/chat/text | 记录日志并只取得 Session；本例依次未命中 <code>/new</code>、<code>/help</code> 和先行 QQ 日历直达 | Session 尚未追加消息，Tool context 尚未设置 | 日志记录文本前 80 字 | <code>copala/agent/loop.py:467-538</code> |
| 5 | 内联规则链 → <code>_direct_outing_planning</code> | 原始中文请求 | 在 LLM 前按最终生效谓词判断出行意图 | 无 | 同名谓词定义三次，只有最后一版生效 | <code>copala/agent/loop.py:540-545,717-727,730-808</code> |
| 6 | <code>plan_from_request</code> → <code>_plan</code> | 请求文本 | 只提取杭州和 trip 类型；原文复用为 time/preferences/constraints，<code>_window</code> 再解析周末；budget/companions 为空，origin 输入为空但可由 <code>AmapConfig.default_origin</code> 配置补齐 | 创建内存 <code>OutingWindow</code> | 目的地/时间规则失败时用默认值；预算显示“未指定” | <code>copala/planning/outing.py:107-168,398-419</code> |
| 7 | <code>_memories</code> | 含原文的宽查询 | 若 Memory Store 存在则检索相关事实，不按 <code>mem_type</code> 过滤 | 可选只读 SQLite | 无 Store/异常/无结果都用空列表 | <code>copala/planning/outing.py:170-178</code> |
| 8 | <code>_calendar_events</code> | 周末时间窗 | 通过 CalendarService 请求 <code>source="feishu"</code>；仅 enterprise/provider 配置完整时访问远端 | 条件性外部只读 | 未启用、未配置或异常时降级为空事件 | <code>copala/planning/outing.py:180-185</code>、<code>copala/calendar/service.py:386-421</code> |
| 9 | <code>_route</code>、<code>_weather</code> | 配置补齐后的出发地/杭州 | 按配置尝试高德；若 <code>default_origin</code> 仍为空则路线不发请求，若已配置则可发起路线请求；天气/地理编码也可能因凭证缺失降级 | 条件性外部只读 | 缺配置或错误转成可渲染状态 | <code>copala/planning/outing.py:146-150,187-229</code> |
| 10 | <code>_web_brief</code> | 目的地与规划类型 | 调用 WebSearchTool；仅有可用搜索配置时形成真实远端请求 | 条件性外部只读 | 无 key/异常返回降级文本 | <code>copala/planning/outing.py:231-244</code> |
| 11 | <code>_render_plan</code> | 上述可用/降级结果 | 确定性模板生成行程、通用预算、风险和建议 | 形成回复文本 | 局部数据缺失不阻断整体模板 | <code>copala/planning/outing.py:246-369</code> |
| 12 | <code>_save_plan</code> | 规划文本 | 仅 Memory Store 存在且 <code>save_plan_to_memory=true</code> 时写 <code>plan_artifact</code> | 条件性 SQLite 写入 | 写失败只记 debug，不影响回复 | <code>copala/planning/outing.py:372-389</code> |
| 13 | <code>AgentLoop</code> 快路径收尾 | user/assistant 内容 | 规划返回后才依次追加 user 与 assistant，并直接保存 Session；不调用 <code>_save_turn()</code> | JSONL 整文件覆盖 | 保存失败由 <code>_dispatch</code> 捕获并返回通用错误 | <code>copala/agent/loop.py:540-545</code>、<code>copala/session/manager.py:163-180</code> |
| 14 | Bus → <code>ChannelManager</code> → QQ <code>send</code> | <code>OutboundMessage</code> | 按 channel 找 Adapter 发回 | 外部消息副作用 | SDK 异常记录；无持久重投 | <code>copala/channels/manager.py:197-226</code> |

## 4.3 图 4-1：锚点快路径流程

本图回答“为什么这条请求没有进入 LLM”。

~~~mermaid
flowchart TD
    M[QQ 文本消息] --> P[BaseChannel 权限检查与归一化]
    P --> B[MessageBus inbound]
    B --> Z{/stop 前置拦截?}
    Z -- 否 --> D[AgentLoop dispatch 全局锁]
    D --> S[加载 Session]
    S --> X[/new /help / QQ 日历均未命中]
    X --> R{最后定义的 outing 谓词命中?}
    R -- 否 --> L[进入其他快路径或通用 LLM 慢路径]
    R -- 是 --> O[OutingPlanningService.plan_from_request]
    O -. Memory 可用 .-> K[相关 RAG 记忆 无类型过滤]
    O -. Enterprise 配置可用 .-> C[CalendarService / 飞书远端]
    O -. 高德配置可用 .-> A[路线与天气]
    O -. 搜索配置可用 .-> W[Web 搜索]
    K --> T[模板渲染]
    C --> T
    A --> T
    W --> T
    O --> T
    T -. savePlanToMemory .-> V[保存 plan_artifact]
    T --> H[追加 user + assistant 并保存 Session]
    V --> H
    H --> Q[Outbound → QQ]
~~~

读图结论：

1. 快路径分支位于上下文构建和 LLM 调用之前。
2. 规划服务按配置尝试多个来源；虚线表示可能根本不发远端请求，模板仍能用降级值返回。
3. RAG 检索没有类型过滤；它不等同于 ContextBuilder 的每轮注入，也不能保证结果都是偏好。
4. <code>plan_artifact</code> 是条件性结果，Session user/assistant 则在规划完成后一起追加；锚点不设置共享 Tool context。
5. 可选 RAG 写与 Session 保存没有统一事务，二者同时启用时仍可能只成功一个。

## 4.4 图 4-2：关键交互时序

本图回答“一次请求在各组件之间的先后顺序和状态落点”。

~~~mermaid
sequenceDiagram
    actor User as QQ 用户
    participant QQ as QQChannel
    participant Bus as MessageBus
    participant Loop as AgentLoop
    participant Session as SessionManager
    participant Outing as OutingPlanningService
    participant RAG as MemoryRAGStore
    participant Cal as CalendarService
    participant Feishu as Feishu API
    participant Amap as Amap
    participant Web as Web Search

    User->>QQ: 周末杭州两日游请求
    QQ->>QQ: allowlist + 归一化
    QQ->>Bus: publish_inbound
    Bus->>Loop: consume
    Loop->>Session: get_or_create
    Loop->>Loop: /new /help / QQ日历未命中
    Loop->>Loop: outing 最终谓词命中
    Loop->>Outing: plan_from_request
    opt Memory Store 可用
        Outing->>RAG: retrieve 相关记忆 无类型过滤
    end
    Outing->>Cal: list_events_async(source=feishu)
    opt enterprise/provider 配置完整
        Cal->>Feishu: list events
        Feishu-->>Cal: events
    end
    opt default origin 与高德配置可用
        Outing->>Amap: route
    end
    opt 高德请求前提可用
        Outing->>Amap: weather
    end
    opt 搜索 Key 可用
        Outing->>Web: destination brief
    end
    opt Memory 存在且 savePlanToMemory
        Outing->>RAG: upsert plan_artifact
    end
    Outing-->>Loop: 渲染后的规划文本
    Loop->>Session: add user + assistant, save
    Loop->>Bus: publish_outbound
    Bus->>QQ: dispatch send
    QQ-->>User: 规划回复
~~~

读图结论：

1. QQ Adapter 与规划服务不直接依赖，二者由 Bus 和 AgentLoop 隔开。
2. RAG 可同时作为相关记忆来源和规划结果写入目标，但两次操作都受配置约束，检索也无类型过滤。
3. CalendarService 调用总会发生，真实飞书、高德与 Web 网络访问只在相应配置可用时发生；已启用的调用按顺序执行并占用前台全局锁。
4. user/assistant 在规划结束后才一起加入 Session；本路径既不设置 Tool context，也不调用通用 <code>_save_turn()</code>。

## 4.5 数据变化与状态变化

输入数据依次从平台原始事件变成 <code>InboundMessage</code>、内存中的目的地/类型/<code>OutingWindow</code>、按配置取得的外部或降级结果、规划 Markdown；规划完成后才把 user/assistant 一起加入 Session，并构造 <code>OutboundMessage</code>。<code>plan_artifact</code> 不是必然状态，而是受 Memory Store 和 <code>save_plan_to_memory</code> 控制的可选写入。

- <code>workspace/memory/rag/memory.sqlite</code>：若启用保存，规划结果成为可检索事实。
- <code>workspace/sessions/*.jsonl</code>：用户输入与助手回复。

**[推断]** 当可选 RAG 写启用时，如果 RAG 写成功而 Session 保存失败，或反之，后续检索与会话历史会不一致；当前没有跨存储事务或补偿。

## 4.6 异常、资源与可观测性

- 通道权限失败在入口终止；Channel 发送错误通常被记录，不进入耐久重试队列。
- 出行服务对缺失数据倾向降级渲染；真实外部错误类型没有统一错误码。
- 普通路径在 <code>_dispatch</code> 的全局锁下，因此任一已启用且变慢的日历/高德/Web 请求会阻塞其他用户。
- Loguru 记录部分入口、工具和异常；没有 trace ID、指标、持久化日志 sink 或跨组件链路追踪。
- 本场景不显式打开长期流资源；共享 HTTP client 和 Provider 的关闭依赖 Gateway 生命周期，部分 Calendar client 未纳入统一关闭。

## 4.7 与通用 LLM 慢路径的对照

### 图 4-3：通用 LLM 工具循环

本图回答“未命中快路径时，模型如何在工具结果与新一轮推理之间循环”。

~~~mermaid
flowchart LR
    U[未被命令、终止型路由或 LifeEvent 消息终止的请求] --> C[ContextBuilder.build_messages]
    C --> P[LLMProvider.chat]
    P --> Q{有 tool calls?}
    Q -- 否 --> R[清洗并返回模型文本]
    Q -- 是 --> T[ToolRegistry.execute]
    T --> M[追加 tool result]
    M --> P
    R --> S[Session save + memory extraction]
~~~

读图结论：

1. 只有前置命令、终止型领域路由和 LifeEvent 条件短路都未结束本轮时，才进入此循环。
2. ContextBuilder 只负责构造初始 messages；模型是否调用工具以及调用几轮由 Provider 响应和最大迭代共同决定。
3. ToolRegistry 把执行结果追加成 tool message 后再次调用 Provider，当前多个 tool call 仍顺序执行。
4. 最终文本会经过统一净化、Session 保存和当轮记忆提取，这些收尾与锚点快路径不同。

通用慢路径证据为 <code>agent/loop.py:317-394,591-623</code>。主要差异：

| 维度 | 锚点快路径 | 通用慢路径 |
|---|---|---|
| 决策者 | Python 规则 | LLM |
| ContextBuilder | 不经过 | 经过 |
| ToolRegistry | 不经过 | 经过 |
| RAG 使用 | Memory 存在时由领域服务宽查询，未按类型过滤 | ContextBuilder 按当前消息自动注入 |
| 输出确定性 | 较高 | 受模型影响 |
| 自动 turn memory 提取 | 快路径提前返回，通常跳过 | 正常执行 |
| 用户错误净化 | 依各领域分支 | 统一走 <code>_sanitize_user_facing_content</code> |

本章关键结论：锚点请求的核心价值来自领域编排而非 LLM；快路径提高确定性和成本可控性，却造成上下文、记忆、错误处理与工具治理不一致；理解 <code>_process_message()</code> 中分支顺序，比只看工具定义更重要。

读者应能从 QQ 入口逐步说到规划写入 RAG 和回复，并解释它为什么绕过 LLM、发生哪些外部访问、留下哪些状态、失败时哪里可能不一致。

---

# 5. 目录结构与代码地图

## 5.1 经过裁剪的目录树

~~~text
Copala/
├─ pyproject.toml                 依赖、构建、入口、pytest 与 Ruff 配置
├─ README.md / SECURITY.md       产品说明与安全说明
├─ copala/
│  ├─ __main__.py                python -m copala 入口
│  ├─ cli/commands.py            Typer 命令与运行时组合根
│  ├─ config/                    Pydantic schema、JSON 加载与路径
│  ├─ bus/                       统一消息对象和内存队列
│  ├─ channels/                  各聊天/邮件平台 Adapter 与管理器
│  ├─ agent/
│  │  ├─ loop.py                 核心编排、路由与快/慢路径
│  │  ├─ context.py              System prompt、Session、RAG 上下文
│  │  ├─ memory.py               MEMORY/HISTORY 合并
│  │  ├─ memory_rag/             SQLite FTS5、hash dense、RRF
│  │  ├─ tools/                  Tool 抽象、注册表与具体能力
│  │  ├─ skills.py               内置/工作区 Skill 加载
│  │  └─ subagent.py             子 Agent 生命周期
│  ├─ providers/                 模型适配、注册与 fallback
│  ├─ session/                   Session 对象与 JSONL 持久化
│  ├─ calendar/                  本地/企业日历、草案、飞书 Provider
│  ├─ life_events/               事件抽取、去重、日历/提醒副作用
│  ├─ planning/                  每日和出行规划
│  ├─ smart_reminders/           风险聚合与提醒去重
│  ├─ cron/ / heartbeat/         主动任务与周期触发
│  ├─ context_awareness/         上下文感知支撑
│  ├─ templates/                 初始化工作区模板
│  └─ skills/                    内置 Skill 文档和脚本
├─ tests/                        38 个 pytest 文件
└─ case/                         静态个性化演示材料
~~~

## 5.2 组织方式评价

**[事实]** 目录同时按技术层（<code>channels</code>、<code>providers</code>、<code>session</code>）和业务域（<code>calendar</code>、<code>life_events</code>、<code>planning</code>）划分，是混合组织。它便于快速找到具体能力，但运行时边界没有完全跟随目录：

- <code>AgentLoop</code> 在一个文件里创建所有业务服务与工具并决定路由。
- <code>OutingPlanningService</code> 反向依赖 Agent 工具层的 <code>WebSearchTool</code>，见 <code>planning/outing.py:12-15,49-57</code>。
- <code>CalendarService</code> 内部直接创建 RAG Store，见 <code>calendar/service.py:318-354</code>。
- <code>LifeEventManager</code> 调用 <code>CalendarService._provider()</code> 私有方法，绕过公开草案边界，见 <code>life_events/service.py:717-745</code>。

**[推断]** 目录体现了希望逐步业务化的方向，但依赖注入与领域端口尚未稳定，因此“文件夹分开”还没有形成严格分层。

新增代码的一般放置规则：

- 新平台接入放入 <code>channels</code>，同时修改 Schema、Manager、CLI 状态与测试。
- 新模型放入 <code>providers</code> 并注册到 Registry。
- 新通用能力放入 <code>agent/tools</code>，通过 <code>ToolRegistry</code> 暴露。
- 具有明确业务语义和确定性流程的能力放入独立领域目录，由 Router/Tool 通过公开接口调用。
- 新持久状态应优先明确唯一事实来源与迁移策略，不再随意增加一个无锁 JSON 文件。

## 5.3 概念到代码定位表

| 项目概念 | 负责模块 | 入口文件/函数 | 关键配置 | 代表测试 |
|---|---|---|---|---|
| 命令启动 | CLI | <code>cli/commands.py::gateway/agent</code> | <code>--config</code>、<code>--workspace</code> | <code>tests/test_commands.py</code> |
| 通道归一化 | Channel | <code>channels/base.py::_handle_message</code> | <code>channels.*</code> | Matrix/QQ/Telegram 等通道测试 |
| 消息协议 | Bus | <code>bus/events.py</code>、<code>bus/queue.py</code> | 无 | 间接由 Agent/Channel 测试覆盖 |
| 核心路由 | Agent | <code>agent/loop.py::_process_message</code> | <code>agents</code>、各领域开关 | <code>tests/test_calendar_direct_intent.py</code> 等 |
| LLM 循环 | Agent/Provider | <code>copala/agent/loop.py::_run_agent_loop</code> | model、provider、fallback | Provider 与工具参数测试 |
| 工具执行 | Tools | <code>copala/agent/tools/registry.py::execute</code> | <code>tools.*</code> | <code>tests/test_tool_validation.py</code> |
| 会话 | Session | <code>session/manager.py</code> | workspace | Session/记忆合并测试 |
| RAG | Memory | <code>copala/agent/memory_rag/store.py::retrieve</code> | <code>memoryRag</code> | <code>tests/test_memory_rag.py</code> |
| 日历草案 | Calendar | <code>calendar/service.py:draft_*/confirm</code> | <code>calendar</code> | <code>tests/test_calendar_service.py</code> |
| 生活事件 | Life Events | <code>life_events/service.py::process_text</code> | <code>lifeEvents</code> | <code>tests/test_life_events.py</code> |
| 出行规划 | Planning | <code>planning/outing.py::plan_from_request</code> | <code>outingPlanning</code> | <code>tests/test_outing_planning.py</code> |
| 主动任务 | Cron/Heartbeat | <code>cron/service.py</code>、<code>heartbeat/service.py</code> | <code>heartbeat</code>、jobs.json | Cron/Heartbeat 测试 |

本章关键结论：定位功能时先找“运行入口和路由”，再看目录；<code>agent/loop.py</code> 和 <code>cli/commands.py</code> 是多数改动的真实汇合点；业务域之间已有目录隔离，但公开端口和依赖方向仍需治理。

读者应能回答：新通道、新工具、新领域服务分别放在哪里；为什么只浏览目录不足以理解实际调用；哪些文件是变更热点。

---

# 6. 核心模块逐一剖析

## 6.1 CLI、配置与运行时组合根（A级）

### 模块定位、输入输出

<code>copala/cli/commands.py</code> 是组合根：接收命令行参数和配置文件，创建 Provider、Bus、Session、Agent、Channel、Cron、Heartbeat，输出终端结果或启动常驻进程。配置由 <code>config/loader.py:13-76</code> 加载成 <code>config/schema.py:464-565</code> 的 Pydantic 对象。

公开入口包括 <code>copala gateway</code>、<code>copala agent</code>、日历/记忆/通道管理等 Typer 命令；安装入口为 <code>pyproject.toml:64-65</code>，模块入口为 <code>copala/__main__.py:5-8</code>。

### 内部实现、依赖与生命周期

Gateway 初始化顺序是：加载配置与工作区模板 → 创建 Bus/Provider/Session/Cron → 创建 AgentLoop → 创建 ChannelManager → 注册后台回调与巡检 → 启动 Cron/Heartbeat → 并行运行 Agent 与 Channels，见 <code>cli/commands.py:344-568</code>。关闭时依次关闭 MCP、停止 Heartbeat/Cron/Agent/Channels，见 <code>:571-576</code>。

配置来源以显式 <code>--config</code> 或默认 <code>~/.copala/config.json</code> 为主；<code>--workspace</code> 可覆盖运行时 workspace。解析失败会记录后退到默认 Config，见 <code>config/loader.py:26-48</code>。这提高首跑容错，但安全配置写错也可能静默失效。

### 状态、异常与权衡

- 配置保存为明文 JSON，见 <code>config/loader.py:59-66</code>。
- Pydantic 基类未设置 <code>extra="forbid"</code>，拼错字段可能被忽略，见 <code>config/schema.py:11-14</code>。
- <code>gateway.host/port</code> 是死配置；<code>--port</code> 仅打印。
- 组合集中让启动逻辑可读，但 <code>copala/cli/commands.py</code> 同时承载 OAuth 回调和大量业务命令，职责过重。

### 修改指南

修改装配前先画出对象所有权和关闭顺序；新增长期资源必须加入 <code>finally</code> 并等待关闭。新增配置字段必须同时有 Schema、真实消费点、默认值安全测试、未知字段/范围测试和 README。至少运行 <code>tests/test_commands.py</code>、配置相关测试及目标功能测试。

**三个结论**：CLI 是组合根而非薄壳；配置错误可能退回默认值；Gateway 名称不能被解释为 HTTP 服务。

**应能回答**：进程怎样启动/退出、配置从哪里来、为什么 <code>--port</code> 无效。

**待验证**：真实平台 SDK 在 Windows 信号退出时的完整资源回收。

## 6.2 Channel Adapter 与 MessageBus（A级）

### 模块定位、接口与数据

<code>BaseChannel</code> 定义 <code>start()</code>、<code>stop()</code>、<code>send()</code> 与统一 <code>_handle_message()</code>，将平台字段映射为 <code>InboundMessage</code>。<code>ChannelManager</code> 硬编码实例化启用通道、启动/停止它们并消费出站队列；<code>MessageBus</code> 提供两个进程内 <code>asyncio.Queue</code>。

输入是平台消息/统一出站消息；输出是统一入站消息/平台 API 调用。关键协议在 <code>bus/events.py</code>，队列在 <code>bus/queue.py:8-44</code>。

### 实现、状态与异常

- allowlist 在 <code>BaseChannel.is_allowed()</code> 和 Manager 启动时双重检查，见 <code>channels/base.py:77-115</code>、<code>channels/manager.py:142-150</code>。
- Manager 并行启动通道，逐个停止；出站按 <code>msg.channel</code> 找 Adapter，见 <code>channels/manager.py:152-226</code>。
- 队列无 <code>maxsize</code>、ACK、重放或持久化；发送失败只记录日志。
- 媒体处理由各 Adapter 自行实现，导致 Matrix 有完善 containment，而 Feishu/Discord 存在路径穿越差异。

### 设计原因与代价

统一消息模型有效隔离 SDK 差异，Channel 与 Agent 可以独立测试。代价是平台特有语义被塞入 <code>metadata</code>，且通道注册不是动态插件；新增通道需要多点修改。进程内 Bus 简单、延迟低，但不能承担可靠消息语义。

### 修改指南

新增/修改通道必须验证：身份来源、allowlist、消息 ID 去重、群聊与私聊 key、附件 basename/大小/containment、typing/progress、启动失败隔离、发送失败和关闭。应补 Adapter 单测、Manager 生命周期测试和一条 Bus 往返集成测试。

**三个结论**：Adapter 边界清晰；Bus 不是消息中间件；安全行为目前因通道而异。

**应能回答**：平台消息如何归一化、回复如何路由、进程崩溃后队列会怎样。

**待验证**：各 SDK 的真实重连、限流和重复投递语义。

## 6.3 AgentLoop 与规则路由（A级）

### 定位、输入输出与对外能力

<code>AgentLoop</code> 是核心编排器，输入 <code>InboundMessage</code> 或 <code>process_direct()</code> 参数，输出 <code>OutboundMessage</code> 或直接返回文本。它公开运行/停止、直接处理和 MCP 关闭能力，同时内部创建领域服务、注册工具、路由意图、管理 Session 和处理错误。

### 内部实现与关键分支

运行主线见 <code>agent/loop.py:396-633</code>。实际优先级：

0. <code>run()</code> 在创建 task 和获取全局锁之前拦截 <code>/stop</code>；
1. system 内部消息；
2. <code>/new</code>、<code>/help</code>；
3. QQ 只读日历；
4. QQ/飞书出行规划；
5. QQ/飞书智能提醒；
6. QQ/飞书每日规划；
7. 非 CLI/System 的 LifeEvent 预处理：<code>result.messages</code> 非空时短路，否则可能保留已产生的 TODO/Cron/RAG 副作用并继续；
8. 仅对尚未终止的普通消息设置共享 Tool context，再执行通用 Context → LLM → Tools。

证据分别位于 <code>agent/loop.py:408-410,474-586,902-920</code>、<code>life_events/service.py:681-715</code>。出行谓词定义三次、智能提醒谓词定义两次，Python 只保留最后定义，见 <code>agent/loop.py:686-808</code>。

### 并发、状态与错误

- 前台每消息创建 task，但 <code>_dispatch()</code> 使用单个全局锁，见 <code>:402-437</code>。
- <code>process_direct()</code> 不获取该锁，见 <code>:967-979</code>。
- <code>_set_tool_context()</code> 修改共享 Tool 实例，见 <code>:292-297</code>。
- 正常 LLM 输出经过用户错误净化与虚假日历写入声明检测，见 <code>:607-619,982-1037</code>；部分快路径把原始异常直接拼给用户。

### 设计原因、优势与代价

中心化路由让功能迭代快、单步调试直观；规则快路径能避免模型幻觉并控制成本。但它混合装配、路由、并发、状态和业务逻辑，新增一个条件可能改变所有后续优先级；快/慢路径在记忆、净化、工具治理上出现语义分裂。

### 修改指南

修改前必须列出完整路由顺序和“是否经过 Context/LLM/Tool/Memory”的矩阵。新增快路径应通过独立 Router 注册，明确优先级、适用通道、只读/写副作用及统一收尾。必须测试相似语句冲突、否定/假设、快慢路径一致性、不同 session 并发和 <code>/stop</code>。

**三个结论**：AgentLoop 是系统最大耦合点；规则优先级就是业务行为；当前锁既限制吞吐又未覆盖后台。

**应能回答**：锚点为什么不调用 LLM、同名谓词哪版生效、后台为何可能串聊。

**待验证**：真实突发流量下的延迟和 task/内存增长。

## 6.4 Provider、ToolRegistry 与通用 Agent 循环（A级）

### 定位和接口

<code>LLMProvider.chat()</code> 统一模型调用与 <code>LLMResponse</code>；Provider Registry 根据配置/model 选择 LiteLLM、Azure、Codex 等实现；<code>FallbackLLMProvider</code> 包装主备切换。Tool 通过 JSON Schema 描述参数，由 <code>ToolRegistry</code> 注册、枚举和执行。

输入是 messages、工具定义、模型参数；输出是文本或 tool calls。工具输入为模型生成的 dict，输出统一转成字符串再追加上下文。

### 实现路径

<code>_run_agent_loop()</code> 在每轮调用 Provider；若返回 tool calls，则顺序调用 <code>ToolRegistry.execute()</code>，把 assistant/tool 消息加入列表后继续，见 <code>agent/loop.py:317-394</code>。注册表负责查找、参数转换、schema 校验与异常包装，见 <code>agent/tools/registry.py:18-59</code>、<code>agent/tools/base.py:55-170</code>。

默认工具在 <code>agent/loop.py:234-268</code> 硬编码注册，包括文件、Shell、WebFetch、Message、Spawn、Cron、Memory、Calendar 等。MCP 是运行时外部扩展。

### 异常、安全和权衡

- 工具失败变成文本交给模型，模型可尝试修正；优点是循环韧性，代价是错误分类丢失。
- 多个工具顺序执行，保证副作用顺序但降低只读工具并行度。
- fallback 能在主 Provider 额度/错误时切换，状态见 <code>providers/fallback_provider.py:15-123</code>。
- Codex Provider 在证书错误后 <code>verify=False</code> 重试，是严重安全偏差。
- LiteLLM 把 API key 放入环境，而 Shell 继承完整环境，形成凭证泄露链。

### 修改指南

新 Provider 必须覆盖消息清洗、工具调用格式、错误映射、超时/取消、凭证传递、关闭和 fallback。新 Tool 必须声明 capability（只读/写/外发/执行）、参数边界、工作区限制、目标绑定和幂等性。需要主 Agent 与子 Agent 双路径测试。

**三个结论**：Provider 与 Tool 接口是最成熟的扩展边界；工具异常被文本化；权限模型尚未成为一等概念。

**应能回答**：tool result 如何回到模型、fallback 何时触发、为什么 Shell 能读到密钥。

**待验证**：不同真实 Provider 对空 content、并行 tool calls 和取消的兼容性。

## 6.5 Session、Context、长期摘要与 RAG（A级）

### 定位、数据与实现

<code>SessionManager</code> 以 session key 保存消息；<code>ContextBuilder</code> 组合身份、bootstrap 文件、Skill、长期摘要、运行时信息、历史和当轮 RAG；<code>MemoryStore</code> 把旧消息总结到 <code>MEMORY.md/HISTORY.md</code>；<code>MemoryRAGStore</code> 保存结构化事实。

通用路径中 <code>ContextBuilder.build_messages()</code> 先构造 system prompt，再清理历史并把 RAG 作为 metadata 文本合入当前 user content，见 <code>agent/context.py:30-169</code>。Session 历史会去除 orphan tool result、运行时 metadata 并截断大型 base64，见 <code>session/manager.py:35-64</code>。

RAG 写入同时更新主表、FTS5 与 JSON embedding；检索用 FTS5 BM25 和全表 hash dense，再 RRF 融合并考虑 priority/confidence，见 <code>copala/agent/memory_rag/store.py:43-212,305-344</code>。<code>embeddingModel</code> 不生效，实际总是 <code>HashEmbeddingProvider</code>。

### 状态、异常与代价

- Session 每次保存用 <code>"w"</code> 整文件重写 JSONL，见 <code>session/manager.py:163-180</code>。
- 某一坏行导致加载返回 <code>None</code>；后续可能以新会话覆盖损坏文件。
- 记忆合并异步使用活 Session；await 模型期间新消息可能被 offset 跳过，见 <code>agent/memory.py:84-165</code>。
- 本地 hash embedding 零网络、隐私好且确定，但语义弱；dense 全表扫描和同步 IO 随数据线性退化。
- 快路径通常不执行 ContextBuilder 和自动 turn memory 提取。

### 修改指南

修改消息格式须考虑旧 JSONL、Provider tool-call 配对和 offset 迁移；修改 RAG 要同时处理主表/FTS/embedding 原子性、重建、过期和检索评估。至少运行记忆 RAG、记忆合并、Session 清理和目标快路径测试。

**三个结论**：长期记忆是双层而非单库；RAG 的 dense 不是神经语义向量；快路径和慢路径的记忆行为不同。

**应能回答**：RAG 注入在哪里、事实来源是什么、数据增长后瓶颈在哪里。

**待验证**：真实个人数据上的召回率、误召回率与长期库规模拐点。

## 6.6 日历、生活事件、规划与智能提醒（A级）

### 模块定位与入口

- <code>CalendarService</code>：本地 JSON/ICS 与企业日历读写、RAG 摄取、草案状态。
- <code>LifeEventManager</code>：从聊天/邮件/日历巡检用规则抽取事件、去重、写飞书、建提醒和记忆。
- <code>DailyPlanningService</code>/<code>OutingPlanningService</code>：聚合 RAG、日历、路线、天气、搜索并模板渲染。
- <code>SmartReminderService</code>：聚合风险信号并去重提醒。

Calendar、规划和提醒能力既可通过 Tools 暴露，也会被 AgentLoop 的终止型分支直接调用；LifeEvent 则作为 LLM 前预处理器，并可由 Email/Calendar patrol 直接触发。

### 核心实现、状态与外部依赖

Calendar 草案公开流程为 <code>draft_create/update → confirm/discard</code>，见 <code>calendar/service.py:148-271</code>；状态保存在 <code>workspace/calendar/drafts.json</code>。确认先调用远端，再标记本地 confirmed 和写 RAG，缺少锁、executing 状态和幂等键。

LifeEvent 的规则分类、时间解析、置信度位于 <code>life_events/service.py:86-353</code>；高置信日历事件直接从私有 Provider 创建/更新，见 <code>:672-770</code>。<code>calendar_write_mode</code> 和 <code>auto_create_todo</code> 没有生产消费点。

规划服务的优势是外部来源局部失败仍能模板化返回；代价是领域模块直接依赖具体工具/Provider，且串行查询拉长全局锁占用。

### 设计原因与权衡

确定性服务适合日程、冲突和预算等可解释任务，也更便于测试；但规则无法可靠理解否定、引用和假设。只读聚合可以接受“最佳努力”，真实日历写入则必须升级为确认、幂等和对账协议。

### 修改指南

日历修改必须区分本地快照、草案与飞书真实状态；规则修改必须增加否定/疑问/条件句测试；新增外部源需要超时、降级、速率限制和脱敏。任何真实副作用都应统一经过公开 Application Service，不得调用 <code>_provider()</code> 私有方法。

**三个结论**：终止型领域快路径是项目产品价值核心；Calendar 草案边界方向正确；LifeEvent 条件预处理及其直写旁路是当前最危险的业务一致性缺陷。

**应能回答**：草案状态如何变化、生活事件为何可直写、规划为何不依赖 LLM。

**待验证**：真实中文自由文本中的规则精度、飞书重复写入与分页边界。

## 6.7 Cron、Heartbeat 与主动 Agent（A级）

### 定位、输入输出与实现

<code>CronService</code> 持久化定时任务并在到期时调用回调。普通 job（包括 LifeEvent 未启用时注册的普通 <code>Email Patrol (Auto)</code>）由 Gateway 组装提示后调用 <code>process_direct()</code>；只有两个 LifeEvent patrol 和一个 SmartReminder patrol 按精确 job name 直接调用领域服务。<code>HeartbeatService</code> 周期读取工作区 Heartbeat 内容，让 Provider 判断是否执行，再通过 <code>process_direct()</code> 调用 Agent，见 <code>cli/commands.py:404-483,501-551</code>。

Cron 的任务事实来源是配置目录下 <code>cron/jobs.json</code>；支持 <code>at/every/cron</code>。同批任务逐个 await，见 <code>cron/service.py:227-276</code>。Heartbeat 为内存循环。

### 状态、故障与权衡

- <code>every</code> 从当前时刻重算，重启不补跑，慢任务造成 cadence 漂移。
- MCP/Agent/外部服务无统一 job 总超时，一个任务可阻塞后续任务。
- 未指定目标时普通 Cron 回退到最近会话；Heartbeat 执行和通知会各重新选择一次目标，见 <code>cli/commands.py:388-483</code>。
- 普通 Cron 与 Heartbeat 调用 <code>process_direct()</code> 并绕过前台锁；只有请求未被前置规则终止、进入 LLM 慢路径时，才设置并共享可变 Tool context。特殊 patrol 同样不受前台锁保护，但完全不设置 Agent Tool context。
- stop 取消 task 但不 await 完成。

主动能力提升了个人助手价值，但 owner、target、capabilities、idempotency 尚未成为任务的一等字段。

### 修改指南

新任务必须持久化 owner、固定投递目标、能力集合、幂等键、超时、重试、misfire 策略与执行记录。修改调度后应测试时区、重启、错过触发、并发同点任务、慢任务、取消与跨用户隔离。

**三个结论**：普通 Cron/HB 与特殊 patrol 是两类不同入口；当前目标选择不稳定；Cron 不是耐久分布式调度器。

**应能回答**：后台如何触发 Agent、为什么可能投错人、重启后错过任务怎样处理。

**待验证**：系统休眠/唤醒、时钟回拨和 DST 下的行为。

## 6.8 B/C 级支撑模块索引

| 等级 | 模块 | 作用 | 评审重点 |
|---|---|---|---|
| B | <code>agent/skills.py</code> | 合并内置和工作区 Skill 到提示 | 同名覆盖、prompt 体积、脚本可信度 |
| B | <code>agent/subagent.py</code> | 独立异步子 Agent | 权限继承、取消、结果路由、日志敏感信息 |
| B | <code>agent/tools/mcp.py</code> | 连接外部 MCP 并注册工具 | 初始连接超时、失败隔离、关闭 |
| B | <code>context_awareness</code> | 上下文信号支撑 | 触发规则、数据来源、隐私 |
| B | <code>utils/helpers.py</code> | 工作区与模板辅助 | 路径、默认文件、升级兼容 |
| C | <code>templates</code>/<code>skills</code> | 初始工作区内容和操作知识 | 包构建是否包含、内容版本 |
| C | <code>case</code> | 静态演示 | 不应误认为可执行 E2E 示例 |

## 6.9 A 级模块统一交接核验矩阵

本表集中补齐各 A 级模块的公开接口/扩展点、第三方依赖、测试覆盖与独立证据索引；它与 6.1～6.7 的输入输出、内部实现、状态异常、权衡和修改指南共同构成统一模块模板。

| 模块 | 公开接口与扩展点 | 主要第三方依赖 | 代表测试/覆盖缺口 | 独立证据索引 |
|---|---|---|---|---|
| CLI/Config | Typer <code>app</code> 与子命令；<code>load_config/save_config</code>；Pydantic Config classes | Typer、Rich、prompt-toolkit、Pydantic Settings | <code>tests/test_commands.py</code>、<code>tests/test_config_paths.py</code>；缺 OAuth 回调和完整配置字段消费测试 | <code>pyproject.toml:64-65</code>、<code>copala/cli/commands.py:299-578</code>、<code>copala/config/loader.py:13-76</code> |
| Channel/Bus | <code>BaseChannel.start/stop/send</code>、<code>_handle_message</code>；<code>Inbound/OutboundMessage</code>；新增 Adapter 是扩展点 | 各平台 SDK、websocket/socketio；Bus 仅用 asyncio | <code>tests/test_base_channel.py</code>、QQ/Telegram/DingTalk/Matrix/Email 测试；缺 Slack/Discord/Mochat 与 Manager 集成 | <code>copala/channels/base.py:12-135</code>、<code>copala/channels/manager.py:16-245</code>、<code>copala/bus/queue.py:8-44</code> |
| AgentLoop/Router | <code>run/stop/process_direct/close_mcp</code>；当前快路径通过私有谓词扩展，目标应改 Router 注册 | asyncio、Loguru | <code>tests/test_calendar_direct_intent.py</code>、<code>tests/test_loop_save_turn.py</code>、<code>tests/test_task_cancel.py</code>、<code>tests/test_user_error_sanitization.py</code>；缺跨 session 并发 | <code>copala/agent/loop.py:44-145,317-633,967-1037</code> |
| Provider/Tools | <code>LLMProvider.chat</code>、Provider Registry；<code>Tool</code>、<code>ToolRegistry.register/execute</code>；MCP 外部扩展 | LiteLLM、OpenAI、httpx、mcp、readability-lxml | <code>tests/test_azure_openai_provider.py</code>、<code>tests/test_fallback_provider.py</code>、<code>tests/test_tool_validation.py</code>、<code>tests/test_mcp_tool.py</code>；缺 Shell/WebFetch 安全 | <code>copala/providers/base.py:32-131</code>、<code>copala/agent/tools/base.py:7-180</code>、<code>copala/agent/tools/registry.py:8-69</code> |
| Session/Context/Memory | <code>SessionManager</code>、<code>ContextBuilder.build_messages</code>、<code>MemoryStore.consolidate</code>、<code>MemoryRAGStore</code>；Store/embedding 是潜在替换点 | sqlite3/FTS5、JSON/Path 标准库；无外部向量服务 | <code>tests/test_memory_rag.py</code>、<code>tests/test_consolidate_offset.py</code>、<code>tests/test_memory_consolidation_types.py</code>、<code>tests/test_context_prompt_cache.py</code> | <code>copala/session/manager.py:16-213</code>、<code>copala/agent/context.py:17-219</code>、<code>copala/agent/memory.py:47-170</code> |
| Calendar/LifeEvent/Planning | Calendar Provider base；<code>draft_create/update/confirm/discard</code>；<code>process_text</code>；plan/remind 服务方法 | httpx、飞书 SDK/API、高德/Tavily、croniter | <code>tests/test_calendar_service.py</code>、<code>tests/test_enterprise_calendar.py</code>、<code>tests/test_feishu_provider.py</code>、<code>tests/test_life_events.py</code>、<code>tests/test_outing_planning.py</code>；缺真实服务 E2E/幂等故障注入 | <code>copala/calendar/service.py:42-627</code>、<code>copala/life_events/service.py:86-1005</code>、<code>copala/planning/outing.py:26-438</code> |
| Cron/Heartbeat | <code>CronService.add/list/remove/enable/run</code>；<code>HeartbeatService.start/stop/trigger_now</code>；Gateway 回调注入 | croniter、Loguru、asyncio | <code>tests/test_cron_service.py</code>、<code>tests/test_heartbeat_service.py</code>；缺睡眠/DST/多实例/目标漂移 | <code>copala/cron/service.py:63-375</code>、<code>copala/heartbeat/service.py:40-173</code>、<code>copala/cli/commands.py:388-559</code> |

---

# 7. 关键流程与状态变化

## 7.1 图 7-1：Gateway 生命周期

本图回答“常驻进程从装配到关闭依次发生什么”。

~~~mermaid
stateDiagram-v2
    [*] --> LoadConfig
    LoadConfig --> SyncWorkspace
    SyncWorkspace --> BuildRuntime
    BuildRuntime --> RegisterCallbacks
    RegisterCallbacks --> StartBackground
    StartBackground --> Running
    Running --> Stopping: Ctrl+C / exception
    Stopping --> CloseMCP
    CloseMCP --> StopHeartbeat
    StopHeartbeat --> StopCron
    StopCron --> StopAgent
    StopAgent --> StopChannels
    StopChannels --> [*]
~~~

读图结论：

1. 配置和依赖装配都先于任何通道开始消费消息。
2. Cron/Heartbeat 与 Channel/Agent 共享同一进程和关闭故障域。
3. 关闭顺序先断 MCP/后台，再停 Agent 与通道，但不是结构化 task group。
4. 活动消息、子 Agent、记忆合并、Cron/Heartbeat 取消完成和 Feishu HTTP client 没有全部 await/close。

证据：<code>copala/cli/commands.py:344-576</code>。

## 7.2 配置加载与覆盖

入口是所有 CLI 命令的运行时加载辅助。流程为：解析路径 → 读取 JSON → Pydantic 验证 → 失败则默认 Config → 应用 CLI workspace 覆盖 → 各组件构造时读取一次。除 Cron jobs 外没有通用热更新。

性能不敏感；风险在“安全字段拼错或整体解析失败后静默回默认”。应改成：未知字段拒绝、字段范围验证、生产模式 fail-closed、敏感值仅引用环境/凭证库。

## 7.3 通用 LLM 请求

入口：未命中任何快路径的 <code>_process_message()</code>。参与者：Session、ContextBuilder、RAG、Skills、Provider、ToolRegistry。关键分支是无 tool call 结束、存在 tool calls 继续、超出最大迭代、Provider error、MessageTool 已发消息。

性能敏感点：RAG 同步全表 dense、system prompt/历史长度、模型网络、顺序工具。状态变化：Session 添加 user/assistant/tool，自动抽取记忆，必要时后台合并旧消息。证据：<code>agent/loop.py:317-394,568-633</code>。

## 7.4 记忆合并

达到窗口后，Agent 创建后台 consolidation task；<code>MemoryStore.consolidate()</code> 选旧消息、调用模型总结、写 <code>MEMORY.md</code>/<code>HISTORY.md</code> 并推进 <code>last_consolidated</code>，见 <code>agent/loop.py:568-584,960-965</code>、<code>agent/memory.py:71-170</code>。

异常通常只记录、不阻断当前回复。竞态在于它引用活 Session：模型等待期间新增消息后，结束时用最新长度推进 offset，可能跳过未被总结的消息。应冻结消息快照和固定 end offset，以 CAS 提交。

## 7.5 图 7-2：日历草案状态

本图回答“公开 CalendarService 的写入保护如何工作，以及缺哪一态”。

~~~mermaid
stateDiagram-v2
    [*] --> pending: draft_create / draft_update
    pending --> confirmed: confirm + 远端成功
    pending --> discarded: discard
    pending --> expired: confirm 时检测 expires_at
    confirmed --> [*]
    discarded --> [*]
    expired --> [*]
~~~

读图结论：

1. <code>expired</code> 不是后台自动清理，而是 <code>confirm_draft()</code> 在确认时检查 <code>expires_at</code> 后写入。
2. <code>confirmed</code> 只表示远端调用返回成功且本地随后落盘；两步不原子。
3. 当前没有 <code>executing</code>，两个确认者可以同时读到 pending。
4. **[建议]** 目标状态机应为 <code>pending → executing → confirmed/failed_unknown</code>，由事务 CAS、幂等键和对账任务支撑。

证据：<code>copala/calendar/service.py:148-271</code>。

## 7.6 生活事件自动处理

触发条件是非 CLI/System 通道文本，且位于所有终止型规划分支之后、LLM 之前。Extractor 用关键词/正则判类、解析时间和置信度；Manager 查 dedupe，日历型事件可能直写飞书，其他类型可写本地 TODO、创建 Cron 提醒并写 RAG。前台入口明确传入 <code>send_notifications=False</code>，因此这里不会由 Manager 主动另发通知，见 <code>agent/loop.py:561-566,902-920</code>、<code>life_events/service.py:645-715</code>。

该流程是“预处理并条件短路”，不是纯快路径：若 <code>result.messages</code> 非空，AgentLoop 将其拼成正常回复、写入 user/assistant Session 后返回；若为空，则可能带着已经产生的 TODO/Cron/RAG 副作用继续设置 Tool context、构建 Context 并进入 LLM。缺时间等需要用户处理的消息属于前一种情况；真实日历写入无论成功或失败也都会追加 message 并短路。低置信、本地 TODO、重复事件等无回复结果才可能属于继续慢路径的情况。

关键异常路径：外部写成功、本地记录失败会重复；疑问/否定可能误识别；失败回复可能包含原始异常。该流程应被看成“高风险自动化”，不能仅按便利功能评审。

## 7.7 Cron/Heartbeat 与通知

Cron 到期依次执行 job。普通 job（包括普通 Email Patrol）由 Gateway 将 prompt 交给 <code>process_direct()</code>，若 <code>deliver</code> 则通过 MessageTool 或 Bus/Channel 发出；两个 LifeEvent patrol 与一个 SmartReminder patrol 由同一 Cron 回调按精确名称分支，直接调用领域服务，不进入 Agent，但领域服务可通过自身 <code>send_callback</code> 发布通知。Heartbeat 先用 Provider 判断内容，再选择目标通过 <code>process_direct()</code> 调用 Agent，完成后再次选择目标通知。

状态变化包括 job 的 next/last run 和错误、普通 Cron/HB 产生的 Session/记忆，以及特殊 patrol 直接产生的领域状态或外部副作用。主要性能风险是慢 job 串行阻塞；普通 Cron/HB 存在目标漂移，并在进入慢路径时竞争共享 Tool context；特殊 patrol 的风险则是绕过统一 turn/授权/收尾。证据：<code>cron/service.py:175-276</code>、<code>heartbeat/service.py:85-165</code>、<code>cli/commands.py:404-483</code>。

## 7.8 异常传播与恢复矩阵

| 位置 | 当前处理 | 恢复能力 | 主要缺口 |
|---|---|---|---|
| Tool 参数/执行 | 转字符串给模型 | 模型可修正重试 | 无结构化错误类别 |
| LLM Provider | error response / fallback | 可切备用模型 | TLS 降级不安全；重试策略不统一 |
| 领域外部查询 | 局部空结果/提示 | 规划仍可返回 | 真实错误与“无数据”混淆 |
| Channel 发送 | 日志后丢弃 | 无 | 无重试、outbox、去重 |
| JSON/JSONL 损坏 | 部分模块回空状态 | 表面可继续 | 可能覆盖现场并丢全量数据 |
| SQLite | 局部事务 | 单库可回滚 | 多库/远端无事务 |
| 任务取消 | 主 task 可 cancel | <code>/stop</code> | Shell 子进程树、后台 task 清理不全 |

本章关键结论：生命周期、消息、记忆、日历和后台任务各有独立状态机，但没有统一运行时监督与提交协议；最危险窗口出现在“远端副作用成功、本地状态未提交”；取消不等于资源已经清理。

读者应能回答：启动/关闭顺序、通用请求何时结束、草案缺少什么状态、记忆 offset 为什么可能跳过、后台任务怎样投递。

---

# 8. 数据、状态、配置与外部依赖

## 8.1 数据与状态流向表

| 数据/状态 | 创建位置 | 修改者 | 使用者 | 生命周期 | 持久化 | 一致性要求 |
|---|---|---|---|---|---|---|
| Config | <code>config/loader.py</code> | CLI/save_config | 全部组件 | 进程级 | <code>~/.copala/config.json</code> | 安全字段应 fail-closed |
| Inbound/Outbound | Channel/Agent | Bus 队列 | Agent/Channel | 瞬时 | 无 | 至少一次/至多一次未定义 |
| Session | <code>SessionManager</code> | AgentLoop | Context/目标选择 | 跨进程 | <code>sessions/*.jsonl</code> | 同 session 顺序、原子写 |
| 长期摘要 | <code>MemoryStore</code> | consolidation | ContextBuilder | 长期 | <code>MEMORY.md/HISTORY.md</code> | offset 与摘要快照一致 |
| RAG Item | Memory/Calendar/Planning/LifeEvent | 多模块 | Context/领域服务 | 长期/可过期 | <code>memory.sqlite</code> | 主表/FTS/embedding 同步 |
| Calendar Draft | CalendarService | Calendar 工具/CLI | 用户确认 | 短期状态机 | <code>drafts.json</code> | 单次确认、远端幂等 |
| 飞书 Event | 飞书 API | Calendar/LifeEvent | 日历查询/规划 | 外部长期 | 远端 | 与 draft/life event 对账 |
| OAuth Token | 飞书 Provider | 授权/刷新 | Provider | 到期刷新 | token JSON | 保密、原子写、权限收紧 |
| Life Event | Extractor/Manager | Manager | Patrol/Reminder/RAG | 长期 | <code>life_events.db</code> | dedupe 与远端事件一致 |
| Risk dedupe | SmartReminder | Service | Service | 长期 | <code>risks.db</code> | 防重复但允许过期 |
| Cron Job | CronService | Tool/CLI/Gateway | Scheduler | 长期 | <code>cron/jobs.json</code> | cadence、租约、幂等 |
| Tool turn context | AgentLoop | <code>_set_tool_context</code> | Message/Spawn/Cron/Email Tool | 单 turn 理想，当前实例级 | 内存 | 不得跨 turn 污染 |

## 8.2 唯一事实来源与不一致面

1. 会话正文以 JSONL 为事实来源；内存 Session 是运行缓存。
2. 个人事实没有单一来源：<code>MEMORY.md</code> 和 RAG SQLite 可能不同步。
3. 飞书事件以远端为最终事实；草案、生活事件库和 RAG 是本地投影。
4. Cron 以 <code>jobs.json</code> 为事实；执行中的 task 没有耐久租约。
5. 运行配置以进程内 Pydantic 对象为事实；磁盘更新通常需重启。

最可能不一致的组合是：飞书 ↔ drafts.json、飞书 ↔ life_events.db ↔ RAG、Session ↔ MEMORY offset、RAG 主表 ↔ FTS/embedding、Cron job ↔ 已创建提醒副作用。

## 8.3 配置来源、优先级和真实生效情况

优先级可概括为：CLI 指定 config path → 默认 config path；加载文件后 CLI <code>--workspace</code> 覆盖 workspace。部分凭证还可能由 Provider/LiteLLM 从环境变量读取。配置在构造组件时生效，普通文件变化不热更新。

| 配置 | 是否生效 | 证据/问题 |
|---|---|---|
| <code>channels.*</code> | 是 | Manager 按字段构造 Adapter |
| <code>agents.defaults/model</code> | 是 | Provider/Agent 构造 |
| <code>memoryRag.enabled/topK</code> 等 | 部分 | 检索参数生效；<code>embeddingModel</code> 不生效 |
| <code>lifeEvents.calendarWriteMode</code> | 否 | 仅 Schema/README；生产无读取 |
| <code>lifeEvents.autoCreateTodo</code> | 否 | 生产无读取 |
| <code>gateway.host/port</code> | 否 | Gateway 不监听 |
| <code>agents.fallback.retryPrimaryNextDay</code> | 否 | 恢复逻辑固定 |
| <code>tools.restrictToWorkspace</code> | 是但防护不足 | 默认 false；working_dir 可改变限制根 |
| <code>calendar.enabled</code> | 实际边界不完整 | 具体 Provider 开关更关键 |

其他静态发现的死/弱配置包括 <code>dailyPlanning.defaultTravelMinutes</code>、<code>autoReplan</code>、<code>outingPlanning.defaultBudgetCurrency</code>、<code>slack.webhookPath</code>、<code>userTokenReadOnly</code>，详见 <code>config/schema.py:99-461</code>。

## 8.4 敏感数据治理

敏感数据包括模型 API key、Codex OAuth token、飞书 token、邮箱凭证、聊天与邮件正文、日历、RAG 和 Shell 环境。当前明文写入与访问位置见 <code>config/loader.py:59-66</code>、<code>calendar/providers/feishu.py:289-309</code>、<code>session/manager.py:163-180</code>。

**[事实]** <code>.gitignore</code> 未覆盖 SECURITY 要求的 <code>.copala/</code>、<code>workspace/</code>、<code>config.json</code>、token、<code>*.db</code>、<code>*.sqlite*</code>、<code>*.jsonl</code> 和通用日志；反而忽略 <code>docs/</code>。已跟踪文本扫描未发现真实高置信凭证，但这不代表运行期目录安全。

## 8.5 外部依赖行为矩阵

| 外部依赖 | 用途 | 超时/重试/降级 | 幂等 | 主要风险 |
|---|---|---|---|---|
| LLM/LiteLLM | 回复与工具决策 | Provider 各异，可 fallback | 只读请求通常可重试 | 凭证环境泄露、错误格式差异 |
| Codex OAuth API | 模型调用 | 证书失败会不安全重试 | 无关 | MITM 窃取 token/context |
| 飞书日历 | 读写事件 | httpx/Provider 局部处理 | 当前无稳定幂等键 | 重复写、分页、client 未关闭 |
| IMAP/SMTP | 邮件读取/发送 | 模块自行处理 | 发送无统一幂等 | From 伪造、敏感内容 |
| 高德 | 路线/天气 | 规划服务降级 | 读请求 | 配额、延迟、隐私 |
| Tavily/Brave/Web | 搜索/抓取 | 搜索降级；WebFetch 风险大 | 读请求 | SSRF、重定向、超大响应 |
| MCP | 外部工具 | 单次工具有 timeout；初连可无限 | 由外部工具决定 | 启动挂住、能力越权 |
| QQ/飞书等通道 | 收发消息 | SDK 自有重连；出站无耐久重试 | 平台消息 ID 局部去重 | 丢消息、重复、附件 |

## 8.6 数据版本与迁移

当前文件/SQLite 结构缺少统一 schema version 和迁移框架。<code>LifeEventStore</code> 有列存在性补齐，RAG 在初始化时建表，JSON/JSONL 则依赖兼容读取。**[建议]** 每个持久 store 明确 <code>schema_version</code>、向前迁移、备份/回滚和损坏隔离；升级前生成 manifest 并验证飞书等派生状态可重建。

本章关键结论：项目不是“一个数据库”，而是多个本地事实与远端事实的集合；配置声明不等于运行生效；跨存储一致性、敏感数据和 schema 迁移是交接后必须建立的治理面。

读者应能回答：核心状态各在哪里、谁能修改、哪个是真实日历事实、哪些配置无效、外部服务失败后系统如何降级。

---

# 9. 技术亮点与完整实现路径

本章只选取具有项目辨识度、且能由代码完整追踪的设计。每项亮点同时说明边界，避免把普通库用法包装成创新。

## 9.1 亮点一：确定性领域快路径与通用 Agent 慢路径并存

| 维度 | 分析 |
|---|---|
| 要解决的问题 | 明确的日历/规划请求若完全交给模型，可能幻觉、成本高且难测试；长尾请求又需要模型灵活性 |
| 困难 | 同一入口既要保持领域行为确定，又要保留工具型 Agent 的开放能力 |
| 核心思路 | <code>AgentLoop._process_message()</code> 先执行命令与终止型领域规则，再做 LifeEvent 条件预处理；只有尚未终止的消息才构建 LLM 上下文 |
| 完整路径 | Channel → Bus → AgentLoop → 终止型 predicate → Domain Service → 回复；未终止 → LifeEvent，若有 messages 则回复，否则可能带副作用继续 ContextBuilder → Provider → ToolRegistry |
| 关键实现 | <code>agent/loop.py:467-633,635-923</code> |
| 模式 | Chain of Responsibility 的简化形式 + Application Service + Agent Loop |
| 采用理由 | **[推断]** 项目先以较少代码保证核心演示场景稳定，再用通用工具覆盖长尾 |
| 替代方案 | 全部让 LLM 选工具；或把每个意图做成显式命令/独立 API |
| 实际收益 | 锚点场景无需模型，输出可预测、可局部降级、易写单测 |
| 代价/边界 | 路由重复、优先级隐式；终止型分支绕过 Context、统一净化和自动记忆；LifeEvent 的副作用还可能发生在 LLM 之前却不终止本轮 |
| 可迁移经验 | 有真实副作用的 Agent 应优先把高价值流程做成确定性 Application Service |
| 证据 | <code>tests/test_outing_planning.py:94-156</code>、<code>agent/loop.py:540-545,591-623</code> |

“亮”在于项目没有把所有能力都伪装成 LLM 智能；真正不足在于缺少统一 Router 和公共 turn 收尾协议。最佳演进不是删除快路径，而是让快慢路径共享授权、上下文、错误和持久化管线。

## 9.2 亮点二：零外部向量服务的轻量混合 RAG

| 维度 | 分析 |
|---|---|
| 要解决的问题 | 个人部署需要可搜索长期事实，又不希望依赖昂贵/泄露隐私的 embedding API |
| 困难 | 关键词对同义表达弱，纯向量又增加模型与索引依赖 |
| 核心思路 | SQLite FTS5 BM25 + 本地 192 维 signed hash 向量 + RRF 融合 |
| 完整路径 | upsert 主表/FTS/embedding → query 两路召回 → RRF + priority/confidence → format_context |
| 关键实现 | <code>copala/agent/memory_rag/store.py:43-212,305-373</code>、<code>copala/agent/memory_rag/embeddings.py:18-58</code> |
| 模式/机制 | CQRS 式派生索引、Rank Fusion、本地确定性特征哈希 |
| 采用理由 | **[推断]** 零网络、零模型下载、结果稳定，适合小规模个人数据 |
| 替代方案 | 仅 FTS5；本地 sentence-transformer + HNSW；远端 embedding/vector DB |
| 实际收益 | 安装和隐私成本低；数据可单文件携带；检索源、类型、置信度可解释 |
| 代价/边界 | 不是神经语义检索；dense 全表扫描；<code>embeddingModel</code> 配置无效 |
| 可迁移经验 | 小数据先用可解释混合检索，确认规模与质量需求后再引入 ANN |
| 证据 | <code>tests/test_memory_rag.py:8-58</code> |

与替代方案比较：

| 方案 | 隐私/运维 | 语义质量 | 规模 | 本项目适配 |
|---|---|---|---|---|
| 仅 FTS5 | 最优 | 低到中 | 中 | 更简单，但损失模糊匹配 |
| 当前 hash + FTS5 | 优 | 中低 | 小 | 当前 Alpha 的合理折中 |
| 本地神经 embedding + ANN | 优 | 高 | 中大 | 数据增长后的候选 |
| 远端 embedding + Vector DB | 较差 | 高 | 大 | 对单人本地场景偏重 |

## 9.3 亮点三：多适配边界统一到同一 Agent 协议

| 维度 | 分析 |
|---|---|
| 要解决的问题 | 平台 SDK、模型厂商和工具协议差异大 |
| 困难 | 消息字段、tool call 格式、生命周期与错误语义不一致 |
| 核心思路 | Channel Adapter + <code>Inbound/OutboundMessage</code>；Provider 接口；Tool Registry |
| 完整路径 | 平台事件归一化 → Bus → Agent；Provider 统一 <code>LLMResponse</code>；Tool schema 统一执行 |
| 关键实现 | <code>channels/base.py:12-135</code>、<code>providers/base.py:32-131</code>、<code>copala/agent/tools/registry.py:18-59</code> |
| 模式 | Adapter、Registry、Strategy |
| 采用理由 | 新平台/模型/工具可以在边界内吸收差异 |
| 替代方案 | AgentLoop 直接调用各 SDK；完整插件发现系统 |
| 实际收益 | 核心流程不依赖具体 QQ/飞书 SDK；Provider 可 fallback；工具可枚举 |
| 代价/边界 | Channel 注册和默认工具仍硬编码；metadata 与实例可变 context 形成隐式协议 |
| 可迁移经验 | 先稳定跨实现数据协议，再追求动态插件化 |
| 证据 | <code>tests/test_matrix_channel.py</code>、<code>tests/test_azure_openai_provider.py</code>、<code>tests/test_tool_validation.py</code>、<code>tests/test_commands.py</code> |

亮点不在“用了接口”，而在三个变化最频繁的外部边界都建立了统一模型。下一步应给协议加 capability、结构化 error 和资源所有权。

## 9.4 亮点四：规划服务的多源、局部降级编排

| 维度 | 分析 |
|---|---|
| 要解决的问题 | 个性化出行建议要同时理解偏好、空闲时间、路线、天气和公开信息 |
| 困难 | 多个来源延迟和可用性不同，任何一个失败都不应让整次规划失败 |
| 核心思路 | 顺序查询独立来源，把缺失/错误转换为局部状态，再由确定性模板组装 |
| 完整路径 | request parsing → RAG → Calendar → Amap route/weather → Web → render → RAG artifact |
| 关键实现 | <code>planning/outing.py:107-389</code> |
| 模式 | Aggregator / Orchestrator、Graceful Degradation |
| 采用理由 | **[推断]** 演示场景需要稳定、有证据、可复现，而非完全生成式回答 |
| 替代方案 | LLM 自由调用多个工具；并行 DAG 工作流 |
| 实际收益 | 单个依赖失败仍有可用结果；输出包含预算、冲突与风险 |
| 代价/边界 | 顺序调用增加延迟；模板扩展成本；领域层依赖具体 Web Tool |
| 可迁移经验 | 多源只读聚合应先定义降级契约，再考虑并行优化 |
| 证据 | <code>tests/test_outing_planning.py:94-156</code> |

## 9.5 亮点五：日历草案—确认边界

| 维度 | 分析 |
|---|---|
| 要解决的问题 | 自然语言可能被误解，创建/修改真实日历属于用户可见且难以无痕回滚的副作用 |
| 困难 | 需要在“生成意图”和“执行远端写入”之间保存可检查状态，还要处理过期、放弃和不同 Provider |
| 核心思路 | 把 create/update 先序列化为本地 pending draft，用户 confirm 后才调用远端；也可 discard |
| 完整路径 | Calendar Tool/CLI → <code>draft_create/update</code> → <code>drafts.json</code> → <code>confirm_draft</code> → Provider → confirmed → RAG ingest |
| 关键实现 | <code>copala/calendar/service.py:148-271,458-478</code> |
| 模式/机制 | Command、显式确认门、有限状态机 |
| 采用理由 | **[推断]** 把模型/规则的建议与外部真实写入分离，保持本地部署下的可检查性 |
| 替代方案 | 直接写入；仅在聊天中二次询问但不落草案；事务 outbox + 审批工作流 |
| 实际收益 | 用户能在远端写入前查看、确认或丢弃；草案可跨 turn/进程保存 |
| 代价/边界 | 多一步交互；JSON 无事务/CAS；confirm 远端成功与本地落盘不原子；LifeEvent 当前绕过此边界 |
| 可迁移经验 | Agent 的不可逆副作用应形成独立、可审计、可过期的命令对象，确认不能只是一句提示词 |
| 证据 | <code>tests/test_calendar_service.py</code>、<code>tests/test_feishu_provider.py</code>；当前时间相关用例有一项失败，见 11.2 |

候选方案比较：

| 方案 | 安全性 | 实现复杂度 | 失败恢复 | 当前适用性 |
|---|---|---|---|---|
| 直接写远端 | 低 | 低 | 差 | 不适合不可信自然语言 |
| 只做对话二次确认 | 中 | 低 | 对话丢失后难恢复 | 优于直写，但缺耐久审计 |
| 当前耐久草案 | 中到高 | 中 | 仍缺幂等/对账 | 正确基础，需完成 P0-7 |
| 事务 Store + outbox/审批 | 高 | 高 | 最好 | 生产化目标方案 |

另一个值得保留的支撑性正向控制是上下文卫生：Session/Provider 会清理 orphan tool result、空 content、runtime metadata 和大型 base64，见 <code>copala/session/manager.py:35-64</code>、<code>copala/providers/base.py:45-103</code>、<code>copala/agent/loop.py:925-956</code>。它能减少 Provider 400 和上下文膨胀，但不单列为技术亮点，也不能替代完整数据契约。

本章关键结论：真正值得学习的是快慢双路径、多源降级和轻量混合 RAG；Adapter/Registry 为其提供扩展骨架；所有亮点都已显露规模或一致性边界，不能直接按“生产成熟能力”宣传。

读者应能回答：哪些能力真正构成项目亮点、它们的完整实现路径是什么、为什么当前 RAG 既有价值又不能被描述为神经语义检索。

---

# 10. 关键设计决策与方案权衡

以下 ADR 区分 **[事实]** 当前选择与 **[推断]** 可能动机；仓库未提供正式 ADR，因此作者意图均不作确定陈述。

## ADR-01：采用单进程模块化单体

- **状态**：当前事实。
- **背景/约束**：个人本地助手、部署门槛低、平台长连接与后台任务数量有限。
- **候选**：单进程；多进程服务；微服务。
- **当前方案**：Gateway 在一个事件循环装配全部模块，证据 <code>cli/commands.py:323-578</code>。
- **收益**：安装、调试、数据本地化简单；函数级调用低延迟。
- **代价**：共同故障域、全局可变状态、无法横向扩容。
- **重新评估条件**：多用户、需要高可用、任务必须耐久、单进程资源隔离不够。

| 方案 | 优点 | 缺点 | 适用条件 |
|---|---|---|---|
| 单进程 | 简单、低开销 | 故障域大 | 单人本地、Alpha |
| 模块化多进程 | 资源/权限隔离 | 运维复杂 | Shell/邮件等需隔离 |
| 微服务 | 独立伸缩 | 对当前规模过度设计 | 多租户平台化后 |

## ADR-02：用内存 Bus 解耦 Channel 与 Agent

- **状态/当前决策**：已采用；两个进程内 <code>asyncio.Queue</code> 分别承载入站和出站。
- **背景与约束**：单进程、多个平台 Adapter、希望 Channel 不直接认识 Agent。
- **候选方案**：直接函数回调；内存 Bus；SQLite/Redis Stream；外部消息中间件。
- **直接证据**：<code>copala/bus/queue.py:8-44</code>、<code>copala/channels/manager.py:197-226</code>、<code>copala/agent/loop.py:396-413</code>。
- **选择理由**：**[推断]** 内存 Bus 能以最低部署成本建立统一消息协议，并支持通道与 Agent 独立生命周期。
- **未选原因**：直接回调会耦合 Channel/Agent；外部 MQ 对当前单人 Alpha 过重；SQLite 队列需要额外 ACK/恢复设计。
- **收益**：协议简单、延迟低、测试容易、无需外部基础设施。
- **代价**：无界、无 ACK/背压/重放/持久化，出站失败也没有 outbox。
- **重新评估条件**：消息不能丢、突发流量、无损升级、多 worker 或跨进程隔离。

## ADR-03：领域规则优先于 LLM

- **状态/当前决策**：已采用混合路由；明确领域请求先走规则，长尾再进入 LLM。
- **背景与约束**：日历、规划与提醒需要稳定、低成本、可测试，开放聊天又不能枚举所有意图。
- **候选方案**：全部由 LLM 选择 Tool；全部显式命令；规则/分类器 + LLM 混合。
- **直接证据**：<code>copala/agent/loop.py:533-633</code>；锚点测试 <code>tests/test_outing_planning.py:94-156</code>。
- **选择理由**：**[推断]** 用确定性服务保证 README 主推场景，同时保留通用 Agent 的长尾覆盖。
- **未选原因**：全 LLM 容易幻觉、成本和波动高；全显式命令损失自然语言体验。
- **收益**：核心行为稳定、成本低、可解释且便于局部降级。
- **代价**：意图冲突、重复谓词、优先级隐式；提前 return 造成 Context/Memory/Error 收尾不一致。
- **重新评估条件**：规则继续增长、跨语言、冲突率升高或误判产生真实副作用；建议保留混合思想但抽取 Router + TurnPipeline。

## ADR-04：本地文件与 SQLite 作为状态层

- **状态/当前决策**：已采用多种本地 Store；JSON/JSONL、Markdown 和多个 SQLite 并存。
- **背景与约束**：个人数据本地优先、零服务运维、用户需要能直接检查和备份。
- **候选方案**：纯文件；统一 SQLite；服务端 PostgreSQL；事件日志 + 派生视图。
- **直接证据**：<code>copala/session/manager.py:73-180</code>、<code>copala/agent/memory.py:47-170</code>、<code>copala/agent/memory_rag/store.py:19-212</code>。
- **选择理由**：**[推断]** 不同功能按最短实现路径选了人可读文件或 SQLite，无需额外数据库。
- **未选原因**：服务端数据库增加安装与隐私负担；统一 SQLite 需要先统一 schema、迁移和所有权。
- **收益**：本地优先、可携带、可手工检查、部署简单。
- **代价**：跨文件/远端事务、并发、schema migration、备份、ACL 和损坏恢复薄弱。
- **重新评估条件**：多进程、频繁写入、数据规模增长、远端对账要求；近期优先统一 SQLite + 原子文件导出，而非直接上服务端数据库。

## ADR-05：RAG 使用本地 hash dense + FTS5

- **状态/当前决策**：FTS5 BM25 与固定 192 维 signed hash dense 用 RRF 融合。
- **背景与约束**：个人事实规模较小，避免远端 embedding 的成本与隐私暴露。
- **候选方案**：仅 FTS5；当前混合；本地神经 embedding + ANN；远端 Vector DB。
- **直接证据**：<code>copala/agent/memory_rag/embeddings.py:18-58</code>、<code>copala/agent/memory_rag/store.py:163-212,305-344</code>。
- **选择理由**：**[推断]** 零网络、零模型下载、跨机器结果稳定，仍比纯关键词多一路模糊相似度。
- **未选原因**：本地神经模型和 ANN 增加包体/索引运维；远端方案违背本地隐私方向；纯 FTS5 可能降低模糊召回。
- **收益**：隐私好、部署简单、可解释、确定性强。
- **代价**：不是神经语义向量；全表扫描线性退化；<code>embeddingModel</code> 配置无效。
- **重新评估条件**：离线评测召回不足或规模基准超过延迟预算；先建评测集，再决定 BM25-only 或本地神经 + ANN。

## ADR-06：工具调用顺序执行

- **状态/当前决策**：同一轮返回的多个 tool call 按数组顺序逐个 await。
- **背景与约束**：当前 Tool 没有只读/副作用/幂等元数据，盲目并行可能改变写入顺序。
- **候选方案**：全部顺序；全部并行；基于 capability 的安全分组并行。
- **直接证据**：<code>copala/agent/loop.py:364-371</code>。
- **选择理由**：**[推断]** 最简单地保持副作用顺序，避免共享 Tool context 进一步竞态。
- **未选原因**：全部并行在 Message/Calendar/Shell 等写 Tool 上不安全；安全分组需要先扩展 Tool 契约。
- **收益**：顺序确定，错误定位简单，现有 Tool 无需声明并发属性。
- **代价**：多个独立只读远端调用延迟相加。
- **重新评估条件**：常见请求包含三个以上独立只读 Tool，且 immutable TurnContext/capability 已落地；届时只并行 read_only + idempotent 组。

## ADR-07：默认拒绝空 allowlist

- **状态/当前决策**：空 allowlist 为 deny-all，且启用通道会在启动时 fail fast。
- **背景与约束**：Agent 能读文件、执行 Shell、发消息和写日历，公开访问的失误成本极高。
- **候选方案**：空列表公开；空列表拒绝；首次配对/一次性注册码；按平台默认。
- **直接证据**：<code>copala/channels/base.py:77-85</code>、<code>copala/channels/manager.py:142-150</code>。
- **选择理由**：当前代码明确 fail closed；作者没有正式 ADR，安全意图只能由实现推断。
- **未选原因**：空列表公开会把配置遗漏变成远程高权限入口；首次配对机制尚未实现。
- **收益**：避免误配置后公开 Agent，启动时即可发现。
- **代价**：README 示例和 Schema 注释与实现相反，按文档首跑失败。
- **重新评估条件**：若需要易用 onboarding，应实现安全配对/注册码，而不是改变空列表为公开。

本章关键结论：单进程、本地状态、混合路由和轻量 RAG均符合个人 Alpha 项目约束；真正应改变的是不明确的权限、提交和生命周期协议，而不是为了“架构感”拆微服务。

读者应能回答：每个关键选择的约束、收益和代价是什么，在哪些条件下才应重新评估单进程、本地状态或顺序工具。

---

# 11. 工程化、质量与运行保障

## 11.1 当前工程画像

| 能力 | 状态 | 证据与评价 |
|---|---|---|
| 依赖管理 | 部分实现 | <code>pyproject.toml</code> 完整，但无 lockfile；多通道 SDK 为强制依赖 |
| 本地安装 | 已实现 | Hatchling，<code>pip install -e .</code>；入口 <code>copala</code> |
| 代码规范 | 部分实现 | Ruff 配置存在，但当前源码有 69 项 |
| 单元测试 | 较丰富 | 38 文件、约 281 个静态测试函数；大多使用 Fake/Mock |
| 集成/E2E | 薄弱 | 无真实平台矩阵、无可运行完整 demo |
| CI/CD | 未实现 | 无 <code>.github/workflows</code> 或其他 CI/发布流水线 |
| 类型检查 | 未实现 | 无 mypy/pyright 配置 |
| 覆盖率门槛 | 未实现 | 无 coverage 配置与阈值 |
| 构建可复现 | 未实现 | 无锁文件，部分依赖无稳定上界 |
| 部署 | 本地 CLI | 无 Dockerfile、服务管理模板、健康端点 |
| 日志 | 部分实现 | 广泛使用 Loguru，但无统一 sink/rotation/retention/trace ID |
| 指标/追踪/告警 | 未实现 | 未发现 metrics、tracing 或 alerting |
| 安全扫描 | 文档级 | SECURITY 较详细；无依赖/秘密/静态安全自动扫描 |
| 版本兼容 | 部分实现 | 版本双处维护；Python 声明与测试矩阵不明确 |

## 11.2 实际验证记录

验证于 2026-08-21（Asia/Shanghai）在独立依赖环境执行，环境快照如下：

| 项目 | 版本/环境 |
|---|---|
| 操作系统 | Windows 11 <code>10.0.26100</code> |
| Python | <code>3.13.14</code> |
| uv | <code>0.11.26</code> |
| pytest / Ruff | <code>9.1.1</code> / <code>0.16.4</code> |
| matrix-nio / nh3 / mistune | <code>0.26.0</code> / <code>0.3.6</code> / <code>3.3.4</code> |
| pytest-asyncio | <code>1.4.0</code> |

项目没有 lockfile；以下结果是无锁依赖解析下的时间点快照，不保证未来解析出相同依赖组合，也不应脱离本环境解释为可重复生产基线。

~~~text
uv run --isolated --extra dev pytest -q
结果：278 passed, 3 failed

uv run --isolated --extra dev ruff check copala --statistics
结果：69 项源码问题
~~~

测试运行产生的 <code>.pytest_cache</code>、<code>.ruff_cache</code> 已被 Git 忽略；最终 <code>git status --short</code> 在生成本文前为空。测试不是“全绿”，不可把 278 个通过用例解释为生产可用。

### 三个失败用例

| 失败 | 现象 | 定性 | 修复方向 |
|---|---|---|---|
| <code>tests/test_calendar_service.py:7-30</code> | 测试固定 2026-05-18，当前日期 2026-08-21，ingest 时间窗过滤后为 0 | 测试时钟设计缺陷 | 注入 clock 或冻结时间；不要写死过期绝对日期 |
| <code>tests/test_life_events.py:212-246</code> | 输入“明天下午3点”解析到当前明天，但冲突日程固定 2026-05-19 | 测试时钟设计缺陷 | 统一 fake clock 与相对 fixture |
| <code>tests/test_matrix_channel.py:1268-1281</code> | 期望保留 <code>mxc://</code> 图片 src，实际清洗移除 src | 真实依赖兼容/功能缺陷 | 固定/升级 sanitization 依赖并加协议白名单契约测试 |

### Ruff 问题分布

| 规则 | 数量 | 含义 |
|---|---:|---|
| W293 | 29 | 空白行含空格 |
| I001 | 11 | import 排序 |
| F401 | 7 | 未使用 import |
| E701 | 6 | 一行多语句 |
| F541 | 3 | 无占位符 f-string |
| F821 | 2 | 未定义名称 |
| 其他 | 11 | 少量风格/命名问题 |

关键正确性问题：<code>channels/slack.py:81-85</code> 使用未定义 <code>use_thread</code>，异常又在 <code>:105-106</code> 被吞并，导致 Slack 所有发送路径可能只记录错误而丢消息。另一个 Telegram 注解 <code>Any</code> 未导入，但因 <code>from __future__ import annotations</code> 通常不造成当前运行时故障。

## 11.3 测试覆盖结构

测试较多的区域包括 Matrix、记忆合并、工具参数、CLI 和 Azure Provider；薄弱区域包括 Discord/Slack/Mochat、ChannelManager、配置 load/save、文件/Shell 安全、OAuth 回调、WebFetch、Calendar client 关闭、跨服务集成和真实构建。

大多数测试是单元/契约级 Fake Provider 或 MockTransport。正向案例包括用户错误清洗、工具参数校验、Matrix 生命周期和规划降级。关键缺口是：

- TLS 证书失败不得降级；
- SSRF/重定向/DNS rebinding；
- Shell cwd 绕过和环境密钥；
- 附件路径穿越与出站任意文件；
- 跨 turn 串聊、后台目标漂移；
- 日历并发确认、响应丢失和崩溃恢复；
- 损坏 JSON/JSONL 不得覆盖；
- 配置“声明即消费”。

## 11.4 依赖、构建与发布

项目有 27 个运行依赖、6 个开发依赖；Matrix 被拆为 extra，但其他通道 SDK 全部强制安装。建议按 <code>telegram</code>、<code>feishu</code>、<code>qq</code>、<code>email</code> 等拆 extras，基础核心只保留必要依赖。

无 lockfile 且 <code>openai>=2.8.0</code>、<code>ruff>=0.1.0</code> 等边界较宽；Python <code>>=3.11</code> 实际允许未来版本，但 classifier 只列 3.11/3.12。sdist 不包含 tests/SECURITY；元数据缺 readme、项目 URL；版本在 <code>pyproject.toml</code> 与 <code>copala/__init__.py</code> 双处维护。

## 11.5 可观测性与运行保障

Gateway 的 <code>verbose</code> 配置标准库 logging，而主要代码用 Loguru，见 <code>cli/commands.py:340-342</code>。工具参数、消息片段、Web 错误正文可能进入日志，见 <code>agent/loop.py:364-368,493-494</code>、<code>agent/subagent.py:166-169</code>。没有文件 sink、rotation、retention、结构化字段、trace ID、队列长度、任务耗时和外部依赖指标。

最低运行保障建议：

1. 每 turn 生成 <code>trace_id</code>，日志只记录 channel/session hash、长度、tool 名和耗时。
2. 指标覆盖队列深度、活跃/等待 task、LLM/工具延迟、fallback、外部错误、发送失败、Cron lag。
3. 提供本地 health/status 命令，报告通道、MCP、Provider、日历凭证、队列和存储。
4. 统一错误码与用户消息映射，原始异常仅进入脱敏诊断日志。
5. CI 至少覆盖 Python 3.11/3.12、完整 pytest、Ruff、构建安装、依赖审计和秘密扫描。

本章关键结论：项目有可观的单测资产，但当前不是全绿；没有 CI、可复现构建和运行可观测闭环；测试数量不能覆盖高权限 Agent 的安全与并发风险。

读者应能回答：当前实际测试结果、三个失败的性质、Ruff 的正确性问题，以及上线还缺哪些工程门禁。

---

# 12. 问题、风险与技术债务

## 12.1 评级口径

- **P0/高严重度**：在启用相应能力并接触不可信输入时，可能导致凭证/隐私泄露、任意主机操作、错误真实副作用或核心通道不可用；上线前处理。
- **P1/中高严重度**：可靠性、数据完整性、性能或运维缺口；近期处理。
- **P2/中低严重度**：可维护性、边缘兼容与文档清理；计划治理。
- 概率按当前默认配置和典型个人长期运行判断；“条件性 P0”表示只有启用该通道/能力时触发，但启用前必须修。

## 12.2 P0：上线阻断风险

| ID / 问题 | 代码证据 | 触发条件与影响 | 严重度 / 概率 | 短期修复 / 长期治理 | 回滚或安全降级 | 成本、副作用与验证 |
|---|---|---|---|---|---|---|
| P0-1 TLS 失败后关闭校验 | <code>copala/providers/openai_codex_provider.py:63-70,92-111</code> | Codex OAuth 遇证书错误；MITM 可读取 token、上下文并伪造工具响应 | 高 / 中 | 删除 <code>verify=False</code>；企业代理仅允许显式 CA bundle / 统一 TLS policy | 禁用 Codex Provider 或恢复到经验证的可信 CA；绝不回退到跳过校验 | 小；旧代理环境会硬失败；用伪证书集成测试确认绝不发第二次不校验请求 |
| P0-2 Tool sandbox 可绕过且 Shell 继承密钥 | <code>copala/config/schema.py:455-461</code>、<code>copala/agent/tools/shell.py:26-36,66-83,125-150</code>、<code>copala/providers/litellm_provider.py:53-87</code> | 默认 unrestricted，或模型传任意 working_dir/绕过正则；提示注入可读写主机和外传 key | 高 / 高 | 默认工作区限制、固定 cwd、最小环境、禁用任意 Shell；长期按 capability 在低权限进程/容器执行 | 出现兼容问题时仅按主体临时放开单项 capability，或整体禁用高风险 Tool；不得恢复全局 unrestricted | 大；会限制现有自动化；用 PowerShell/符号链接/解释器/环境泄露攻击集验证 |
| P0-3 WebFetch/远程 ICS SSRF 与大响应 | <code>copala/agent/tools/web.py:34-44,248-283</code>、<code>copala/calendar/service.py:497-509</code> | 模型/配置给内网、loopback、元数据或重定向 URL；泄露内部信息或耗尽内存 | 高 / 中 | 拒绝私网/保留地址、逐跳和 DNS 后校验、流式字节上限；长期受控出口代理/域名策略 | 禁用 WebFetch/远程 ICS，或只允许审核过的公网域名；不恢复宽松 URL 校验 | 中；可能阻断本地 URL；测试 IPv4/IPv6、DNS rebinding、redirect 和超大 body |
| P0-4 普通 Cron/HB 绕锁，慢路径共享 Tool context | <code>copala/agent/loop.py:292-297,431-434,540-586,967-979</code>、<code>copala/agent/tools/message.py:20-37,82-105</code>、<code>copala/cli/commands.py:404-475</code> | 普通 Cron/HB 通过 <code>process_direct()</code> 与前台并发；若请求进入 LLM 慢路径，目标或 <code>_sent_in_turn</code> 可互相覆盖，产生串聊/漏回复。三个具名特殊 patrol 也绕前台锁，但不设置共享 Tool context | 高 / 中 | 所有入口进统一调度；turn context 参数化或 ContextVar；长期 per-session lock + global semaphore；特殊 patrol 也纳入统一 owner/capability/trace | 可退到“所有入口共用同一锁”的安全串行模式，或禁用 Cron/HB 与自动 patrol；不可退到旧的 direct 绕锁 | 中到大；并发语义改变；用可控 barrier 同时跑两 session、普通 job 与特殊 patrol，断言目标和状态隔离 |
| P0-5 后台重新选择最近目标 | <code>copala/cli/commands.py:388-402,423-426,462-513</code> | 长任务期间另一用户变成最近会话；邮件摘要/提醒可能发错人 | 高 / 中 | 任务创建时固定 owner/channel/chat_id；无目标敏感任务拒绝投递 | 暂停/禁用无 owner 的旧任务，人工补齐目标后恢复；不能回退到“最近会话”猜测 | 中；旧无 owner job 需迁移；两用户交错 E2E 验证始终投原目标 |
| P0-6 LifeEvent 忽略安全配置并直写日历 | <code>copala/config/schema.py:145-154</code>、<code>copala/life_events/service.py:86-197,672-770</code>、<code>copala/agent/loop.py:561-566</code> | 普通疑问/假设/否定文本被高置信规则命中；在 LLM 前修改真实飞书 | 高 / 高 | 实现 <code>calendar_write_mode</code>、默认 draft、否定/假设检查和明确确认；长期统一副作用 policy | 关闭 <code>lifeEvents.enabled</code> 或强制 draft；不得恢复自动 direct | 中；自动便利性下降；测试 draft/direct、enabled=false、否定/疑问、重复与用户确认 |
| P0-7 Calendar/LifeEvent 无幂等提交 | <code>copala/calendar/service.py:212-258</code>、<code>copala/life_events/service.py:681-760</code> | 并发确认、远端成功后崩溃或响应丢失；重复创建真实事件 | 高 / 中 | 在 SQLite/带锁原子 Store 中 CAS <code>pending→executing</code>，加稳定幂等键和失败对账；长期 outbox/saga | 暂停自动写，保留草案/只读并以单 worker 人工确认；先对账再重试未知结果 | 大；需远端字段和旧数据迁移；故障注入每个提交点，断言至多一个远端事件 |
| P0-8 媒体路径穿越/任意文件外传 | <code>copala/channels/feishu.py:691-763</code>、<code>copala/channels/discord.py:295-310</code>、<code>copala/agent/tools/message.py:48-105</code> | 恶意附件名或模型 media path；覆盖工作区外文件或发送主机敏感文件 | 高 / 中 | basename + safe filename + resolve containment + 字节上限；出站只允许白名单目录和当前目标 | 关闭受影响通道的附件收发或禁用该通道；绝不恢复不安全路径拼接 | 中；合法外部文件发送受限；Windows/Unix separator、symlink、超大文件和任意目标测试 |
| P0-9 Email allowlist 信任可伪造 From | <code>copala/channels/email.py:79-95,272-280</code>、<code>copala/channels/base.py:77-115</code> | 攻击者伪造白名单邮箱，正文进入高权限 Agent | 高 / 中 | 邮件只读隔离、验证可信 Authentication-Results/签名；长期来源级 capability policy | 禁用 Email Channel/自动巡检，或只将邮件送入无 Tool 的隔离只读摘要器 | 中到大；部分邮件无法自动处理；构造伪 From 与认证头 E2E |
| P0-10 Slack 发送使用未定义变量 | <code>copala/channels/slack.py:81-85,105-106</code> | 只要启用 Slack 并发送；<code>NameError</code> 被吞，所有消息丢失 | 高 / 高（启用时） | 定义并传递 thread policy，不能吞异常；加发送路径契约测试 | 若回归，禁用 Slack 并保留其他通道；不静默吞发送失败 | 小；无显著副作用；Ruff F821 清零且 DM/thread 测试通过 |
| P0-11 Feishu OAuth 默认 state 可预测且非一次性，错误 HTML 未转义 | <code>copala/cli/commands.py:977-1064</code>、<code>copala/calendar/providers/feishu.py:60-68</code> | 命令允许覆盖 <code>--state</code>，但默认值固定、无过期/消费记录；localhost 回调被诱导或错误参数注入 HTML | 高 / 低（条件性 P0） | 每次随机一次性 state、恒时校验、HTML escape、短时过期；长期统一 OAuth 会话存储 | 禁用浏览器回调并改用人工 code exchange，或暂停新授权；随机 state/转义本身无需旧 token 重登 | 小到中；手工流程略变；测试默认随机性、CSRF、重放、过期与恶意 error_description |

## 12.3 P1：近期可靠性与工程风险

| 问题 | 证据 | 触发/影响 | 严重度/概率 | 修复与长期治理 | 成本/副作用/验证 |
|---|---|---|---|---|---|
| 非原子覆盖，损坏后静默清空 | <code>copala/session/manager.py:131-180</code>、<code>copala/cron/service.py:78-180</code>、<code>copala/calendar/service.py:461-478</code> | 断电、磁盘满、并发写；整库丢失并覆盖现场 | 高/中 | temp+fsync+atomic replace、锁、.corrupt；核心状态迁 SQLite | 中；迁移复杂；逐字节故障注入和恢复测试 |
| 记忆合并 offset 竞态 | <code>copala/agent/loop.py:568-584</code>、<code>copala/agent/memory.py:84-165</code> | **[推断]** 特定并发时序下，await 前的旧快照不含新消息，但结束时 offset 按最新长度推进，可能越过这些消息，使其不再进入后续归档或上下文 | 中/中 | 固定 snapshot/end offset + CAS | 中；冲突需重试；barrier 并发测试 |
| 无界队列/task + 全局串行 | <code>copala/bus/queue.py:16-30</code>、<code>copala/agent/loop.py:402-434</code> | 慢请求与突发流量；跨用户头阻塞和内存放大 | 高/中 | 有界队列、per-session serial、全局限流/公平调度 | 大；需定义繁忙响应；负载/公平性测试 |
| RAG/Session 同步线性热点 | <code>copala/agent/memory_rag/store.py:94-140,325-344</code>、<code>copala/session/manager.py:163-180</code> | 记忆/会话增长；事件循环阻塞、延迟线性上升 | 中/高（长期） | WAL/batch/thread offload、增量 append、候选/ANN | 中到大；索引迁移；10²～10⁶ 规模基准 |
| Shell 取消不清进程树 | <code>copala/agent/tools/shell.py:85-98</code>、<code>copala/agent/loop.py:415-429</code> | <code>/stop</code>、超时或 cancel；子孙进程继续执行 | 高/中 | process group/Windows Job Object，所有退出路径统一回收 | 中；跨平台差异；启动孙进程后 cancel 验证 |
| MCP 初连可无限挂 Gateway | <code>copala/agent/loop.py:396-400</code>、<code>copala/agent/tools/mcp.py:83-147</code> | MCP 不响应；全部通道无法开始消费 | 高/中 | 分阶段 timeout、并行隔离、后台懒连接 | 中；启动后短暂 degraded；假 MCP hang 测试 |
| Cron 漂移、漏跑、慢任务阻塞 | <code>copala/cron/service.py:20-29,175-276</code> | 重启/睡眠/慢 job；missed tick 和后续全阻塞 | 中/高 | cadence anchor、misfire、worker pool、job timeout/lease | 大；行为迁移；fake clock + restart 测试 |
| 错误/日志泄露隐私 | <code>copala/agent/loop.py:493-494,635-654</code>、<code>copala/life_events/service.py:764-770</code>、<code>copala/agent/subagent.py:166-169</code> | 外部错误含路径/token/正文；发送给用户或日志留存 | 高/中 | 统一 error mapper、字段级脱敏、默认不记正文 | 中；诊断信息减少；canary secret 测试 |
| 来源不明的持久化记忆可形成跨轮提示注入 | <code>copala/agent/loop.py:885-900</code>、<code>copala/agent/memory_rag/extractor.py:20-44</code>、<code>copala/agent/tools/email.py:121-143</code>、<code>copala/agent/context.py:157-171</code> | 用户/邮件内容可自动进入 RAG，之后标为 metadata 再注入模型；“metadata only”不是强指令隔离，恶意内容可能跨轮影响 Tool 决策 | 高/中 | 保存 provenance/trust、隔离低信任来源、确认后晋升；系统策略明确数据不可覆盖指令，敏感 Tool 仍由 capability 决策 | 中；自动记忆召回减少；用持久化 prompt-poison corpus 验证不能越权且可按来源删除 |
| OAuth/token/敏感文件缺忽略与权限治理 | <code>.gitignore</code>、<code>SECURITY.md:59-69</code> | 用户把 workspace/config 定向到仓库内，或手工复制运行数据后提交；可泄露 token、会话和日历 | 高/中 | 补 ignore、启动权限检查、凭证库/最小 ACL | 小到中；docs 当前被忽略需调整；git check-ignore 回归 |
| 配置死字段和 silent fallback | <code>copala/config/schema.py:11-14,99-461</code>、<code>copala/config/loader.py:38-48</code> | 字段拼错/无消费；安全预期与行为不符 | 中/高 | extra forbid、范围校验、声明即消费测试、生产 fail-fast | 中；旧配置可能报错；配置 corpus 迁移测试 |
| 资源关闭不完整 | <code>copala/calendar/providers/feishu.py:56-58</code>、<code>copala/cli/commands.py:571-576</code> | 重启/短命 CLI/取消；连接警告与未完成副作用 | 中/中 | AsyncExitStack 统一拥有资源并 await stop | 中；需整理所有权；资源泄露与取消测试 |
| ICS 忽略 TZID | <code>copala/calendar/service.py:549-615</code> | 导入带 <code>TZID</code> 或 DST 的 ICS；日历查询、冲突、规划和提醒可能静默错时 | 中/中 | 短期保留参数并按 zoneinfo 解析；长期采用成熟 ICS parser、统一 aware datetime | 中；旧 naive 时间迁移后显示可能变化；多时区、DST 前后、全天事件 fixture |
| 飞书 list_events 不分页 | <code>copala/calendar/providers/feishu.py:97-114</code> | 查询窗口事件超过单页；规划、冲突检查和 RAG 摄取漏项 | 中/中 | 短期消费 page token 并设最大页/事件数；长期统一 Provider 异步分页接口和配额预算 | 小到中；请求数和限流概率上升；MockTransport 三页、限流与截断测试 |
| 工程闭环缺失 | 仓库无 CI/lock/coverage/type/security scan | 依赖升级、合并或发布；全仓正确性、供应链和制品不可稳定复现 | 中到高/高 | 先接入 pytest、Ruff、build 与 Python 矩阵；长期锁定依赖并加入覆盖率、类型、安全门禁和发布签名 | 中；初期暴露大量存量失败；同 Commit 重复构建与完整门禁验证 |

## 12.4 P2：维护性与边缘兼容

| 问题 | 代码证据 | 触发条件 | 影响范围 | 严重度 | 概率 | 短期修复 | 长期治理 | 成本 | 可能副作用 | 验证 |
|---|---|---|---|---|---|---|---|---|---|---|
| 意图谓词重复定义 | <code>copala/agent/loop.py:686-808</code> | 维护者修改了较早定义，或新增相似 route | 出行/提醒路由和所有通道快路径 | 中 | 高 | 删除被覆盖的死定义并锁定当前行为 | 声明式 Router/Policy + 优先级表 | 小到中 | 清理时可能暴露依赖旧误行为的测试 | route table 覆盖命中、冲突和否定语句 |
| WhatsApp/上游遗留 | <code>copala/cli/commands.py:1175-1192,1267-1354</code>、架构图片 | 执行 channels status/login 或按图片配置 | CLI 首跑、文档可信度 | 中 | 高（调用时） | 删除无 Schema/Adapter 的引用并给迁移说明 | 以正式插件契约重新引入或永久移除 | 小 | 旧用户脚本可能失效 | 全部 CLI 子命令 smoke + Schema 快照 |
| README 与源码漂移 | <code>README.md:142-152,398-437</code>、第 3.6 节证据 | 用户照示例启动或评估安全边界 | onboarding、运维和安全预期 | 中 | 高 | 修 QQ allowlist、Gateway、RAG、LifeEvent 文案 | 文档示例作为可执行契约进入 CI | 中 | 文档需随行为版本化 | 干净环境按 README smoke，示例配置逐个解析 |
| 模块过大/私有方法跨域 | <code>copala/agent/loop.py</code>、<code>copala/cli/commands.py</code>、<code>copala/life_events/service.py:717-745</code> | 新增 route、后台服务或日历写逻辑 | 核心编排、测试和变更扩散 | 中 | 高 | 禁止新增私有跨域调用，先加 characterization tests | Router、TurnPipeline、Application Port 分离 | 大 | 重构可能改变隐式路由顺序 | 依赖规则 + 快慢路径行为矩阵 + E2E 回放 |

## 12.5 风险组合链

### 图 12-1：不可信输入到高权限副作用的组合攻击链

本图回答“为什么多个看似独立的小缺陷会组合成凭证泄露、内网访问或真实日历事故”。

单个问题会互相放大：

~~~mermaid
flowchart LR
    I[不可信消息/邮件/网页] --> P[提示注入]
    P --> T[默认高权限 Tool]
    T --> E[Shell 继承环境密钥]
    T --> F[任意文件读取/外发]
    T --> S[SSRF 内网访问]
    E --> X[凭证泄露]
    F --> X
    S --> X
    P --> C[LifeEvent 规则误判]
    C --> W[真实日历写入]
    W --> D[无幂等导致重复]
~~~

这说明 P0 不能按“逐个小 bug”处理；必须同时收紧入口身份、capability、执行隔离和副作用确认。

读图结论：

1. 消息、邮件和网页都属于不可信内容来源，来源白名单不等于内容可信。
2. Shell、文件外发、SSRF 和真实日历写入共享同一上游提示注入风险。
3. 只修一个 Tool 不能切断所有路径，必须同时治理身份、capability 和目标绑定。
4. 无幂等会把一次误判放大为重复真实副作用。

本章关键结论：首要风险不是模型“答错”，而是不可信输入借高权限能力造成真实外部影响；P0 之间存在组合链，必须按安全协议成组治理。

读者应能回答：每个 P0 的触发条件、影响、验证和回滚方式，以及为何 LifeEvent、后台路由和 Tool sandbox 必须一起评审。

---

# 13. 优化与重构路线图

## 13.1 P0：上线门槛

| 改造流 | 目标与涉及模块 | 推荐步骤/前置 | 风险与回滚 | 验证标准 | 收益/工作量 |
|---|---|---|---|---|---|
| 安全传输与 URL 出口 | 消除 TLS 降级、SSRF；Provider/Web/Calendar | 先加攻击测试 → 删除 verify false → IP/redirect/size policy | 企业代理/本地 URL 受阻；用显式 allowlist 临时回滚，不回滚到跳过 TLS | 伪证书、私网、IPv6、redirect、超大响应全部 fail closed | 阻断凭证/内网泄露；中 |
| Capability 执行隔离 | Tool、Shell、Filesystem、Message、Subagent | 定义 capability → 默认最小权限 → 固定 cwd/min env → 低权限 worker | 现有 Skill 失效；按用户显式授权临时开放单能力 | 攻击 corpus 无法越界/读取 key/任意外发 | 建立 Agent 安全底座；大 |
| Turn/Session 并发隔离 | AgentLoop、Message/Spawn/Cron/Email Tool | characterization test → immutable TurnContext → 所有入口统一调度 → per-session lock + semaphore | 顺序变化；只可退到“统一入口后的全局串行”，immutable context 与目标隔离不可回退 | 两用户+Cron 并发 1000 次无串聊/漏回 | 安全与吞吐同时改善；大 |
| 稳定 owner/target | CLI callbacks、Cron model、Heartbeat | 为任务加 owner/channel/chat_id → 迁移旧 job → 禁止猜最近用户 | 旧任务无目标停止投递；可人工补 owner | 运行中活跃会话变化不改变目标 | 消除跨用户泄露；中 |
| 副作用确认与幂等 | Calendar、LifeEvent、Reminder、RAG | 以现有 <code>enabled=false</code>、<code>draft</code>、<code>direct</code> 明确 policy → 把 CAS executing 放入 SQLite/带锁原子 Store → idempotency key → reconcile；若新增 <code>off</code>，须先扩展 Schema 与迁移 | 迁移中的 pending 状态；保留只读/草案模式回滚 | 故障注入和并发确认至多一个远端对象，可对账恢复 | 保护真实日历/提醒；大 |
| 通道身份与媒体 | Email/Feishu/Discord/Slack/Matrix | 修 Slack → 统一 safe media utility → Email 认证/只读 policy | 部分附件/邮件拒绝；回滚只能禁用附件/自动邮件或受影响通道，不能恢复旧路径拼接 | 路径/大小/伪 From/发送契约测试全绿 | 消除入口与外传风险；中 |
| OAuth 与秘密治理 | Feishu OAuth、Config、.gitignore | random state/escape → ignore/ACL → 密钥不入环境/明文最小化 | state 随机化和 HTML 转义无需重登；仅 token 存储格式迁移可能要求重登，应保留旧 token 只读备份和迁移脚本 | CSRF/重放失败，git check-ignore 与权限检查通过 | 减少凭证泄露；中 |

P0 完成定义：所有 P0 安全回归进入 CI；OAuth state 随机、一次性、可过期且错误 HTML 已转义；Email 来源已验证或只进入隔离只读处理器；后台 owner/target 在任务创建后保持不变；LifeEvent 默认不直写；任何 Shell、媒体文件外发和出站目标都有显式 capability 与 containment；并发隔离测试通过；Slack 启用时可正常回复；没有 TLS/SSRF/路径穿越已知绕过。

## 13.2 P1：可靠性、性能与可运营

| 改造流 | 目标/模块 | 实施步骤 | 前置/风险/回滚 | 验证标准 | 收益/工作量 |
|---|---|---|---|---|---|
| 统一本地持久化 | Session/Cron/Draft/Fallback/Token | 原子文件 helper → 损坏隔离 → schema version → 关键状态迁 SQLite | 先备份与双读；失败回滚只读旧格式 | crash point 测试无数据丢失 | 数据可靠；大 |
| 记忆一致性 | Memory/Session/RAG | snapshot offset → CAS → 批量事务/WAL → 重建命令 | 固定旧数据 manifest，并用旧格式 shadow/dual-write 或 delta journal 保留迁移后增量；回滚须先冻结写入、回放 delta、核验 offset 与三索引一致，再原子切回旧 reader/writer | 并发归档无跳过，三索引一致 | 长期记忆可信；中 |
| 背压与调度 | Bus/Agent/Cron | queue maxsize → session partition → fair semaphore → busy/timeout → job pool | 需要 P0 TurnContext；回滚先冻结/限流新入口并排空在途队列，再切到“有界全局串行 + busy 响应”，继续保留 immutable TurnContext、稳定 owner/target 和统一入口；不得恢复无界队列或旧 direct 绕路 | 峰值内存有界、不同用户公平 | 稳定吞吐；大 |
| RAG/Session 性能 | Store/Context/Session | 建基准 → batch/upsert → offload IO → 增量 session → 候选/ANN | 不先选技术；保留重建/dual index | P95 和事件循环 lag 达预算 | 支撑长期数据；大 |
| 生命周期监督 | CLI/MCP/Provider/Cron/HB/Subagent | AsyncExitStack → structured task group → 分阶段 timeout → process tree cleanup | 明确所有权；可关闭新的监督编排，但进程树回收、资源所有权和 await close 不得回退；兼容故障时禁用 Shell 或受影响后台能力 | 退出无 pending task/client/child | 可预测重启；中 |
| 可观测性 | 全局 | trace ID、结构化脱敏日志、metrics、status/health、诊断包 | 先制定隐私字段；日志级别可回滚 | 故障可按 trace 定位，canary 不泄露 | 运维闭环；中 |
| 工程门禁 | pyproject/tests/CI | 固定依赖 → Python matrix → pytest/Ruff/build → coverage/type/audit | 先修 3 fail/F821；门禁渐进收紧 | 每提交自动验证且制品可复现 | 降低回归；中 |
| 日历正确性 | CalendarService/Feishu Provider/Planning | 保留 ICS <code>TZID</code> 并用 zoneinfo 正确解析 → 消费飞书 page token 并设置页数/事件上限 → 补 DST/多页故障测试 | 保留原始 ICS、分页响应 fixture 与迁移前已验证投影；异常时暂停受影响 TZID 导入/多页摄取并切只读投影，不能恢复已知错时或漏页实现 | TZID/DST/全天事件和三页以上事件均不静默错时/漏项 | 修复数据完整性；中 |

## 13.3 P2：架构与体验演进

| 改造 | 步骤 | 依赖/回滚 | 验证 | 收益/工作量 |
|---|---|---|---|---|
| 拆 Router 与 TurnPipeline | characterization → 声明式 route → 公共 pre/post hooks → 从 AgentLoop 移出领域装配 | 依赖 P0 并发/副作用协议；按 route feature flag 恢复原内联判定实现，但必须继续运行在已落地的 immutable TurnContext、capability 与公共 pre/post 安全包络内 | 快慢路径行为矩阵全绿 | 降耦合；大 |
| 建领域公开端口 | CalendarPort、MemoryPort、SearchPort；禁止私有 Provider 跨域 | 先适配现有实现；保留旧适配器与双实现，异常时恢复旧 DI 绑定 | dependency test 无反向 import | 可替换、可测试；中 |
| Provider/Channel 插件化 | entry point/注册 manifest、按 extra 安装 | 保留静态内置注册与原依赖 extras，可关闭发现机制回到静态注册 | 插件契约测试 | 减小基础依赖；大 |
| 日历模型进一步标准化 | 在 P1 正确性修复上引入成熟 ICS parser、统一 aware datetime 与 Provider 异步分页接口 | 依赖 P1 日历正确性；回滚只切回仍正确处理 TZID/page token 的 P1 适配层和已验证投影，不回到原缺陷实现 | 跨 Provider DST/TZID/多页契约测试 | 统一扩展语义；中 |
| 文档与演示产品化 | 可运行 E2E demo、配置 schema 文档、ADR/CHANGELOG | 依赖 CI；错误文档直接回退，E2E 不稳定时只关闭非阻断 job，不绕过核心门禁 | fresh machine smoke | 交接与采用；中 |

## 13.4 图 13-1：推荐实施依赖

~~~mermaid
flowchart TD
    T[先补 characterization 与安全攻击测试] --> A[Transport / SSRF / OAuth]
    T --> C[Capability Sandbox]
    T --> X[Immutable TurnContext]
    X --> O[稳定 Owner/Target]
    X --> B[Per-session 并发与背压]
    C --> S[副作用确认与幂等]
    O --> S
    S --> P[统一持久化与对账]
    B --> R[性能优化]
    P --> M[Router / TurnPipeline 重构]
    R --> M
    A --> G[CI 安全门禁]
    C --> G
    S --> G
~~~

读图结论：推荐顺序不是按文件，而是按风险依赖：

1. 先冻结当前行为并建立攻击/故障测试。
2. 再收紧传输、URL、Tool 和通道入口。
3. 将 turn/owner 变成不可变上下文，随后修并发与投递。
4. 建立副作用状态机和对账，再迁移持久化。
5. 最后拆大模块和优化检索，避免在不稳定语义上重构。

本章关键结论：P0 的核心是“权限、身份、上下文、幂等”四个协议，不是零散补丁；P1 才处理规模与运维；微服务化不在当前推荐路线中。

读者应能回答：推荐实施顺序、各阶段前置依赖、完成标准，以及为什么 Router 拆分不能先于安全与行为 characterization。

---

# 14. 常见需求的变更影响指南

本章从维护者视角给出“从哪里开始、会波及哪里、怎样证明没有改坏”。所有场景都应先写行为测试，再修改实现。

## 14.1 新增一种聊天通道

1. **修改入口**：实现 <code>copala/channels/base.py:12-135</code> 的生命周期和发送契约；在 <code>copala/channels/manager.py:34-150</code> 注册。
2. **涉及模块**：<code>copala/config/schema.py:323-337</code>、CLI status/login、依赖 extras、Bus events。
3. **数据/接口**：明确 sender_id、chat_id、message_id、thread/group、media 和 metadata；不要把平台对象直接传入 Agent。
4. **调用链影响**：平台 → Adapter → BaseChannel → Bus → Agent；Outbound 反向。
5. **测试**：allowlist、重复消息、群/私聊、附件路径/大小、typing、发送失败、重连、stop。
6. **常见风险**：信任错误身份字段、session key 冲突、附件穿越、SDK task 泄露。
7. **验证**：Fake SDK 契约 + Manager 生命周期 + 一条入站到出站 E2E；Ruff F821 必须为零。
8. **回滚**：先在配置中禁用新通道，再撤销 <code>ChannelManager</code> 注册和依赖 extra；保留已接收数据及配置快照，避免删除用户状态。

## 14.2 新增一个 Agent 工具

1. **入口**：继承 <code>copala/agent/tools/base.py</code>，在 <code>AgentLoop._register_default_tools()</code> 或 MCP/插件注册。
2. **涉及模块**：ToolRegistry、Context/Provider tool schema、Subagent 权限、配置和 Skill 文档。
3. **接口**：当前 <code>Tool.execute()</code>、<code>ToolRegistry.execute()</code> 与 Context/Provider 消息链要求工具结果最终为字符串，见 <code>copala/agent/tools/base.py:42-53</code>、<code>copala/agent/tools/registry.py:38-59</code>、<code>copala/agent/context.py:195-201</code>。若引入结构化 <code>ToolResult</code>，必须同步修改统一 Tool、Registry、Context 和所有 Provider 的序列化契约；另应声明 read_only、side_effect、external_send 等 capability。
4. **链路**：LLM tool call → 参数转换/校验 → execute → tool result → 下一轮 LLM。
5. **测试**：缺字段、类型转换、边界值、超时/取消、权限拒绝、敏感日志、幂等。
6. **风险**：模型参数是不可信输入；工作区、目标和凭证泄露；主/子 Agent 权限不一致。
7. **验证**：Registry 契约测试 + capability 攻击测试 + Provider tool-call 回放。
8. **回滚**：通过 feature flag 或 capability policy 禁用新工具，并从 Registry unregister；若动过结果协议，恢复旧字符串适配层后再下线新类型。

## 14.3 接入新的 LLM Provider

1. **入口**：实现 <code>copala/providers/base.py::LLMProvider</code>，加入 <code>copala/providers/registry.py</code>。
2. **涉及模块**：Schema、CLI provider 构造、fallback、OAuth/凭证、依赖和 README。
3. **接口**：messages/tools/max_tokens/temperature/reasoning；返回文本、tool calls、finish_reason。
4. **链路**：Config/model 解析 → Registry → Provider → AgentLoop。
5. **测试**：空 content、system/tool 消息、tool_call_id、超时、429/5xx、取消、fallback、client close。
6. **风险**：不同 API 消息语义、重复副作用重试、key 写环境、TLS。
7. **验证**：MockTransport 契约矩阵 + 一次可选真实 smoke；错误不得原样到用户。
8. **回滚**：恢复旧默认 Provider、Registry 解析规则和依赖版本；保留新 Provider 配置但停止选择，避免破坏用户凭证。

## 14.4 新增一个领域快路径

1. **入口**：当前在 <code>copala/agent/loop.py:533-566</code> 插分支；目标架构应注册到独立 Router。
2. **涉及模块**：领域 Application Service、Tool（如也需 LLM 调用）、配置、Session/Memory 公共收尾。
3. **接口**：结构化 IntentMatch（score/reason/read_only）与领域请求/响应；禁止只返回 bool 后隐式抢优先级。
4. **链路**：pre-turn → Router → Domain → common post-turn；明确是否允许 LLM fallback。
5. **测试**：命中、近似不命中、与现有路由冲突、否定/疑问/引用、多通道、异常和记忆。
6. **风险**：提前 return 绕过净化/记忆；高风险误触发；重复定义。
7. **验证**：全路由表 parameterized test，记录每条语句命中唯一 route 和副作用等级。
8. **回滚**：关闭该 route 的 feature flag，使请求回到原通用 LLM 路径；保留行为测试和领域服务，避免直接删除后无法对照。

## 14.5 新增日历 Provider

1. **入口**：实现 <code>copala/calendar/providers/base.py</code> 契约，在 <code>CalendarService._build_providers()</code> 注册。
2. **涉及模块**：Schema、OAuth/Token、CLI auth/list、CalendarTool、LifeEvent（应只依赖公开 Calendar Port）。
3. **数据**：统一 aware datetime、calendar_id/event_id、分页 token、幂等 key、错误类型。
4. **链路**：CalendarService → Provider → 外部 API；写入必须走 draft/confirm。
5. **测试**：认证刷新、分页、时区/DST、速率限制、创建/更新幂等、关闭。
6. **风险**：远端成功本地失败、ID 映射、时区错位、token 明文。
7. **验证**：MockTransport 多页与故障注入；对账命令能重建本地投影。
8. **回滚**：恢复旧 Calendar Provider 和原 DI 选择；保留 token、远端 ID 映射与本地投影，先对账确认未知结果再恢复写入。

## 14.6 新增或修改配置项

1. **入口**：<code>copala/config/schema.py</code> 与实际构造/消费点必须同一改动提交。
2. **涉及模块**：loader、CLI、组件、默认模板、README、迁移。
3. **数据**：明确类型、范围、默认、是否敏感、是否可热更新、弃用策略。
4. **链路**：JSON 字段 → Pydantic alias → 组件参数 → 可观察行为。
5. **测试**：默认/显式值、非法范围、未知拼写、加载失败、旧配置迁移、“声明即消费”。
6. **风险**：像 <code>calendarWriteMode</code> 一样只声明不生效；不安全默认。
7. **验证**：改变字段必须让一个行为测试可观测；静态引用和配置快照通过。
8. **回滚**：弃用窗口内保持新旧字段双读；前向运行时显式新字段优先，冲突必须 fail-fast 或告警，安全字段不得静默恢复旧的不安全值。回滚前先反向迁移并回填旧字段，校验后原子切换到旧 Schema 与配置快照。

## 14.7 修改 Session、Memory 或 RAG

1. **入口**：<code>copala/session/manager.py</code>、<code>copala/agent/context.py</code>、<code>copala/agent/memory.py</code>、<code>copala/agent/memory_rag</code>。
2. **涉及模块**：Provider 消息格式、快/慢路径、规划/日历/生活事件、CLI memory 命令。
3. **数据**：JSONL schema、offset、MemoryItem、FTS 与 embedding 版本。
4. **链路**：写 turn → 检索/注入 → consolidate → reindex。
5. **测试**：旧文件兼容、orphan tool、并发 consolidate、损坏恢复、检索质量和规模基准。
6. **风险**：历史不可读、offset 跳过、索引不一致、上下文膨胀。
7. **验证**：迁移前后固定语料召回排名、消息数和摘要覆盖一致；可从主数据重建索引。
8. **回滚**：先停止新写；只有旧格式 dual-write 已核验追平，或 delta journal 可完整 replay 时，才切回旧 reader/writer。回放迁移后 turn、核对消息数与 offset，再从主数据重建 FTS/embedding；不得直接恢复会丢增量的旧备份。

## 14.8 调整 Cron/Heartbeat 调度

1. **入口**：<code>copala/cron/types.py</code>、<code>copala/cron/service.py:20-376</code>、<code>copala/heartbeat/service.py</code>。
2. **涉及模块**：CLI 回调、Agent direct、Message Tool、LifeEvent reminder、jobs 持久化。
3. **数据**：owner、target、schedule anchor、timezone、misfire、lease、attempt、idempotency key。
4. **链路**：load → calculate next → lease/run → persist result → deliver。
5. **测试**：fake clock、重启补偿、DST、同时到期、慢/失败任务、取消、两个实例。
6. **风险**：重复/漏跑、投错目标、阻塞全部任务。
7. **验证**：给定事件时间线可精确预测执行次数与目标；每次执行有耐久记录。
8. **回滚**：先停止发放新 lease 并排空在途执行，导出迁移后新增/修改/删除的 job 定义增量，反向迁移到旧格式并核验执行账本，再原子切回旧调度器；未知执行禁止自动重复补跑，不能只恢复迁移前快照。

## 14.9 修改权限或工作区规则

1. **入口**：<code>copala/config/schema.py:436-461</code>、Shell/Filesystem/Message/MCP/Subagent。
2. **涉及模块**：所有不可信输入源和所有外部副作用，不只 ExecTool。
3. **接口**：Principal、Source、Capability、Resource、Action、Decision/Reason。
4. **链路**：turn 创建 principal → Router/LLM 请求能力 → policy decision → sandbox execute。
5. **测试**：路径规范化、symlink、Windows 分隔符、PowerShell、环境、目标绑定、邮件低权限。
6. **风险**：黑名单绕过、一个工具规则与另一工具不一致、旧 Skill 破坏。
7. **验证**：集中攻击 corpus；默认拒绝且显式最小授权可以完成正常场景。
8. **回滚**：若新策略误阻断，只对明确主体临时放开单一 capability 并记录到期时间；不得回到全局 <code>unrestricted</code>。

## 14.10 增加数据字段或新持久 Store

1. **入口**：先写数据所有权 ADR，再改对应 dataclass/Pydantic/SQLite schema。
2. **涉及模块**：创建者、修改者、查询、序列化、迁移、备份、删除/隐私。
3. **接口**：schema_version、主键/幂等键、时间语义、nullable/default。
4. **链路**：create/read/update/delete/rebuild/expire 必须全列出。
5. **测试**：旧版本迁移、未知字段、部分写入、并发、崩溃、回滚。
6. **风险**：又增加一个事实来源、无法对账、隐私无法删除。
7. **验证**：从备份恢复、升级/降级演练、唯一事实来源文档和数据流表同步更新。
8. **回滚**：停止新 writer；依靠已验证的 dual-write/CDC 或 delta journal 把迁移后增量回放到旧格式，核验旧 reader 可见性、记录数和校验和后再原子切换。保留迁移 manifest 与新字段原始数据；不得只恢复备份后立即恢复服务。

本章关键结论：大多数变更都不止改一个文件；影响分析必须沿“入口—协议—状态—副作用—测试—生命周期”展开；<code>AgentLoop</code>、Schema、持久化和安全策略是最常见的连锁点。

读者应能回答：任一典型变更从哪里开始、会改变哪条链、需要哪些迁移/测试，以及怎样安全回滚。

---

# 15. 项目细节问答与答辩手册

## 15.1 基础认知

| 问题 | 参考答案（结论—机制—证据） | 进一步追问 |
|---|---|---|
| 这个项目解决什么问题？ | **[事实]** 它让个人助手跨通道积累会话、偏好、日程和邮件线索，并能执行规划与主动提醒。机制是 Channel + Agent + Session/RAG + Domain/Cron。证据：<code>README.md:1-17</code>、<code>agent/loop.py:44-145</code>。 | 哪些能力是规则，哪些是 LLM？ |
| 核心用户是谁？ | **[事实/推断]** 元数据面向开发者，运行又要求自行配置大量凭证，因此当前是开发者/高级个人用户，而非零配置大众 SaaS。证据：<code>pyproject.toml:1-18</code>、README 配置章节。 | 多用户会暴露什么缺陷？ |
| 输入和输出是什么？ | **[事实]** 输入是平台消息、邮件、日历、CLI、Cron/HB；输出是回复、规划、记忆、日历/提醒及文件/Shell 副作用。证据：图 1-1。 | 哪些输出不可逆？ |
| 系统边界在哪里？ | **[事实]** Copala 负责编排与本地状态，不拥有模型、聊天平台、飞书和邮箱服务；也不提供通用 HTTP API。证据：<code>cli/commands.py:323-578</code>。 | Gateway 为什么不是 HTTP gateway？ |

## 15.2 架构理解

| 问题 | 参考答案 | 进一步追问 |
|---|---|---|
| 项目采用什么架构？ | **[事实]** 单进程模块化单体，内部用内存事件队列、Adapter/Registry、终止型领域快路径、LifeEvent 条件预处理和 LLM 工具慢路径。证据：第 3 章与 <code>agent/loop.py:396-633,902-920</code>。 | 故障域是什么？ |
| 核心模块有哪些？ | **[事实]** CLI/Config、Channel/Bus、AgentLoop、Context/Session/Memory、Provider/Tools、领域服务、Cron/HB。证据：第 5.3 节。 | 最大耦合点是哪两个文件？ |
| 模块怎样协作？ | **[事实]** Channel 和 Agent 通过 Bus；Agent 直接依赖 Session/Context/Provider/Tools/Domain；普通 Cron/HB（含普通 Email Patrol）直接调用 Agent，三个具名特殊 patrol 直接调用领域服务。证据：图 3-2、图 3-3。 | 哪些依赖绕过公开端口？ |
| 为什么这样划分？ | **[推断]** 外部变化用 Adapter 隔离，通用能力用 Tool/Provider 扩展，稳定个人流程做领域服务。收益是迭代快，代价是组合根和 AgentLoop 膨胀。 | 为什么不拆微服务？ |
| 同步与异步边界是什么？ | **[事实]** 平台/网络/Bus/模型是 async；很多文件与 SQLite 操作仍同步在事件循环。证据：<code>copala/agent/memory_rag/store.py:325-344</code>、<code>session/manager.py:163-180</code>。 | 数据增长后会怎样？ |

## 15.3 实现细节

| 问题 | 参考答案 | 进一步追问 |
|---|---|---|
| 程序入口在哪里？ | **[事实]** 安装入口 <code>pyproject.toml:64-65</code> 指向 <code>copala.cli.commands:app</code>；<code>python -m</code> 走 <code>copala/__main__.py:5-8</code>。 | agent 与 gateway 有何不同？ |
| Gateway 如何启动？ | **[事实]** 配置/模板 → Bus/Provider/Session/Cron → Agent/Channels → 注册回调 → 启动 Cron/HB → 并行 Agent/Channels。证据：<code>copala/cli/commands.py:344-568</code>。 | 关闭漏了哪些资源？ |
| 一次普通消息如何执行？ | **[事实]** Adapter 归一化 → Bus → <code>/stop</code> 前置拦截或 Agent 全局锁 → 命令/终止型路由 → LifeEvent 条件预处理 → 必要时 Context/LLM/Tools → 分支式 Session 收尾 → Outbound。证据：第 6.3、7.6 节。 | 队列有没有重放？ |
| 杭州规划为什么不调用 LLM？ | **[事实]** 在 <code>_process_message()</code> 中先命中最终 outing 谓词，直接调用 <code>plan_from_request()</code>。证据：<code>agent/loop.py:540-545,717-808</code>。 | 它如何获得个性化？ |
| 通用工具循环如何结束？ | **[事实]** Provider 无 tool calls 时结束；有则顺序执行并把 tool result 追加后继续；还受最大迭代/错误影响。证据：<code>agent/loop.py:317-394</code>。 | 多工具是否并行？ |
| 核心状态在哪里？ | **[事实]** Session JSONL、MEMORY/HISTORY、RAG SQLite、Draft JSON、LifeEvent/Risk SQLite、Cron JSON 和飞书远端。证据：第 8.1 节。 | 哪个是日历事实来源？ |
| 配置如何加载生效？ | **[事实]** JSON 经 Pydantic 在启动时加载并注入组件，CLI 可覆盖 workspace；多数不热更新。证据：<code>config/loader.py:13-76</code>。 | 哪些字段不生效？ |
| RAG 算法是什么？ | **[事实]** FTS5 BM25 + 192 维 hash dense 全表 cosine + RRF，再加 priority/confidence。证据：<code>copala/agent/memory_rag/store.py:163-212,305-344</code>。 | 为何不能叫神经语义 embedding？ |
| 异常怎样传播恢复？ | **[事实]** Tool 错误文本化给模型，Provider 可 fallback，领域查询局部降级，Channel 发送只记日志。证据：第 7.8 节。 | 哪些错误会泄露给用户？ |

## 15.4 技术权衡

| 问题 | 参考答案 | 进一步追问 |
|---|---|---|
| 最重要的技术亮点是什么？ | **[事实/评价]** 快慢双路径、多源降级规划、轻量混合 RAG最有辨识度。证据：第 9 章。 | 亮点的共同边界是什么？ |
| 为什么用本地文件/SQLite？ | **[推断]** 适合个人本地、零运维和可检查；代价是原子性、迁移与并发。证据：第 8 章。 | 是否应立即换 PostgreSQL？ |
| 为什么用全局锁？ | **[推断]** 简单保护共享 Tool context 与会话顺序；代价是全用户串行，而且后台仍绕过。证据：<code>agent/loop.py:431-434,967-979</code>。 | 更好的并发模型是什么？ |
| 为什么规则先于 LLM？ | **[推断]** 终止型领域规则降低核心场景幻觉和成本，LifeEvent 则先捕获主动事项；代价是规则冲突、误判，以及 LifeEvent 可能在继续 LLM 前已产生副作用。 | 怎样保留优势又减耦合？ |
| 当前设计最大代价是什么？ | **[评价]** 安全/状态协议落后于功能：高权限入口、分散状态、无幂等、共享 context 和生命周期不完整。证据：第 12 章。 | 哪项应最先改？ |

## 15.5 性能与稳定性

| 问题 | 参考答案 | 进一步追问 |
|---|---|---|
| 主要性能瓶颈在哪里？ | **[推断有证据]** 全局锁、网络串行、RAG dense 全表扫描、Session 整文件覆盖和同步 IO。证据：<code>copala/agent/loop.py:431-434</code>、<code>copala/agent/memory_rag/store.py:325-344</code>。 | 应先优化哪一个？ |
| 并发安全如何保证？ | **[事实]** 前台靠全局锁近似串行；普通 Cron/HB 的 direct 入口与特殊 patrol 都不受它保护，前者在进入慢路径后还共享可变 Tool context，所以并未完整保证。 | per-session lock 还需什么？ |
| 外部服务失败会怎样？ | **[事实]** 规划多为局部降级；模型可 fallback；发送无重试；写副作用可能处于未知结果。 | 怎样处理 unknown outcome？ |
| 项目如何定位故障？ | **[事实]** 主要依赖 Loguru 文本日志和 CLI status；无 trace/metrics。 | 最小指标集是什么？ |
| 消息会丢吗？ | **[事实]** 会。Bus 仅内存且发送失败无 outbox/retry，进程退出或 SDK 失败可丢。 | 是否需要 Kafka？ |
| 如何取消任务？ | **[事实]** <code>/stop</code> 取消 session 主 task 和子 Agent；Shell 子进程树与其他后台资源未必完全清理。证据：<code>copala/agent/loop.py:415-429</code>。 | Windows 如何清进程树？ |

## 15.6 扩展与维护

| 问题 | 参考答案 | 进一步追问 |
|---|---|---|
| 新增功能通常改哪里？ | **[事实]** 按类型改 Channel/Provider/Tool/Domain，再检查 Schema、AgentLoop/CLI、状态和测试。证据：第 14 章。 | 如何避免继续膨胀 AgentLoop？ |
| 哪些模块最易连锁影响？ | **[事实]** <code>agent/loop.py</code>、<code>cli/commands.py</code>、<code>config/schema.py</code>、Session/Context 和 Bus events。 | 怎样做 characterization？ |
| 最应优先重构什么？ | **[建议]** 先 capability、TurnContext、owner/target 和副作用幂等；之后才拆 Router。 | 为什么不先重写 AgentLoop？ |
| 哪些设计可复用？ | **[评价]** 确定性领域服务、多源局部降级、Adapter/Registry、草案确认、轻量混合检索。 | 哪些不能照搬？ |
| 如何证明真正掌握项目？ | 能从任一入口画到状态/外部副作用，指出快慢路径差异，并为变更列出连锁测试。 | 用第 17.9 节自测。 |

## 15.7 一分钟项目介绍

Copala 是一个本地运行的个人 AI Agent 框架。它把 QQ、飞书、邮件等通道统一成消息，交给单进程里的 AgentLoop。日历查询、每日/出行规划和智能提醒等明确请求走终止型领域快路径；LifeEvent 在 LLM 前预处理，只有产生回复消息时短路，否则可能先写 TODO/Cron/RAG 再继续带长期记忆的 LLM 工具循环。普通 Cron（含普通 Email Patrol）与 Heartbeat 通过 <code>process_direct()</code> 主动调用 Agent，两个 LifeEvent patrol 和一个 SmartReminder patrol 则直接调用领域服务。项目以 JSON、JSONL、Markdown 和 SQLite 保存会话、RAG、草案与任务。它的亮点是多通道、多源规划和轻量本地 RAG；当前主要短板是高权限工具隔离、日历副作用幂等、后台跨会话并发以及工程运维闭环，所以适合受控个人演示，生产化前必须完成安全与一致性整改。

## 15.8 五分钟架构介绍

**第 0～1 分钟：目标与边界。** Copala 不是 Web 后端或微服务，而是本地个人助手运行时。外部是聊天平台、模型、飞书日历、邮箱、高德、搜索和宿主机；内部负责接入、编排、记忆、规划与主动任务。

**第 1～2 分钟：主架构。** CLI Gateway 是组合根，它在一个 asyncio 进程创建 ChannelManager、MessageBus、AgentLoop、Cron 和 Heartbeat。Channel 把平台事件标准化后发布到内存 Bus；Agent 处理后再经 Bus 发回 Channel。普通 Cron/HB（含普通 Email Patrol）直接调用 Agent，两个 LifeEvent patrol 与一个 SmartReminder patrol 直接调用领域服务。Bus 只做进程内解耦，没有持久化或重放。

**第 2～3 分钟：规则与模型路径。** AgentLoop 先判断日历、出行、提醒和每日规划等终止型规则；未命中后执行 LifeEvent 预处理，有回复消息则短路，无回复消息则可能保留已有副作用并继续。只有尚未终止的请求才由 ContextBuilder 组合 Session、Skills、MEMORY 与 RAG，再让 LLM 通过 ToolRegistry 多轮调用工具。杭州两日游示例走终止型快路径，读取无类型过滤的相关 RAG 记忆，并按配置尝试飞书日历、高德路线/天气和 Web 搜索，再用模板输出。

**第 3～4 分钟：数据和状态。** 会话在 JSONL，长期摘要在 Markdown，结构化记忆在 SQLite FTS5/hash RAG，日历草案和 Cron 在 JSON，生活事件和风险去重各有 SQLite；真实日历在飞书。它们没有统一事务，所以远端成功、本地失败是关键故障窗口。

**第 4～5 分钟：评价。** 项目的强项是 Adapter/Registry、确定性核心流程、轻量混合 RAG和局部降级；短板是 AgentLoop/CLI 集中、快慢路径行为不一致、默认工具权限过宽、后台进入慢路径时竞争共享 context、日历直写与无幂等、无 CI/指标。演进应先安全和提交协议，再做并发、持久化和模块拆分。

## 15.9 十分钟核心实现介绍

**0:00～1:00，入口。** <code>pyproject.toml</code> 把 <code>copala</code> 指向 Typer app。<code>gateway()</code> 读取 Config 和 workspace，创建运行时组件。这里要特别澄清：port 没有监听；Gateway 是常驻宿主。

**1:00～2:00，通道与 Bus。** 每个 Adapter 继承 BaseChannel，入口先做 allowlist，再生成 InboundMessage。ChannelManager 同时消费 OutboundMessage。Bus 是两个无界 asyncio Queue；所以它低成本但不可靠。

**2:00～3:30，AgentLoop。** <code>run()</code> 消费消息，先在创建 task 和获取锁之前拦截 <code>/stop</code>；其他消息由使用全局锁的 <code>_dispatch()</code> 处理。<code>_process_message()</code> 先处理命令和终止型领域规则，再执行 LifeEvent 条件预处理，只有仍未终止时才设置共享 Tool context 并进入 LLM。文件里重复定义的意图函数只有最后一个生效。

**3:30～5:00，锚点。** QQ 的杭州请求由 outing 谓词命中，<code>OutingPlanningService</code> 只结构化解析目的地、trip 类型和周末窗口；预算与同行仍为“未指定”，用户出发地不解析但可由 <code>AmapConfig.default_origin</code>（JSON 的 <code>defaultOrigin</code>）配置补齐。“不要太赶”没有独立约束字段，只进入原文展示和 RAG 查询，当前轻量模板并不是由这句话触发。服务读取无类型过滤的相关 RAG 记忆，并按配置尝试飞书日历、高德路线/天气和 Web 信息，局部失败可降级；仅在启用记忆保存时写 <code>plan_artifact</code>。规划结束后 AgentLoop 才一起追加 user/assistant Session；该路径不设置共享 Tool context，也不经过 ContextBuilder、LLMProvider 或 ToolRegistry。

**5:00～6:00，慢路径。** ContextBuilder 把身份、模板、Skills、MEMORY、Session 历史、当轮 RAG和媒体整理为 messages。Provider 返回 tool calls 时，Registry 顺序校验和执行，把结果追加后再问模型，直到返回最终文本或错误。

**6:00～7:00，记忆。** Session JSONL保存原始对话；MEMORY/HISTORY 是总结；RAG 用 SQLite FTS5 与本地 192 维 hash 融合。优点是零外部 embedding，缺点是 dense 全表扫描和快路径注入不一致。

**7:00～8:00，副作用。** CalendarService 有 draft/confirm/discard，但 confirm 缺 executing 和幂等。LifeEvent 在 LLM 前预处理，高置信日历事件可直接调用飞书 Provider，配置的 draft 模式没有消费；只有结果含 messages 时才终止本轮，无消息结果可能已经写 TODO/Cron/RAG 后继续 LLM。

**8:00～9:00，后台与并发。** 普通 Cron job（含普通 Email Patrol）与 Heartbeat 调 <code>process_direct()</code>，绕过前台锁；只有进入 LLM 慢路径时才设置并竞争 MessageTool 等可变 context。两个 LifeEvent patrol 与一个 SmartReminder patrol 由 Cron 回调直接调用领域服务，同样绕锁但不设置共享 Tool context。普通后台还可能在执行和通知时重新选择最近会话。

**9:00～10:00，结论和路线。** 当前 278 个测试通过、3 个失败，Ruff 有 69 项；没有 CI/lock/指标。接手第一阶段应删除 TLS 降级，限制 URL/Tool/媒体，固定 TurnContext/owner，日历默认草案并加幂等；之后再治理持久化、背压、性能和 Router 拆分。

---

# 16. 总结与学习建议

## 16.1 整体技术评价

Copala 已经越过“聊天机器人示例”阶段：它拥有真实多通道、会话、长期记忆、模型工具循环、日历/邮件/地图集成、确定性规划和主动任务，且 38 个测试文件提供了一定回归资产。其架构与单人本地 Alpha 的目标基本匹配，最有价值的是把个性化数据与具体生活流程贯通。

但它尚未达到长期无人值守或不可信多人输入环境的生产门槛。功能增长快于安全、并发、幂等、状态迁移和可观测性建设；特别是高权限 Tool、LifeEvent 直写、后台目标漂移与远端副作用未知结果，会把普通软件缺陷升级为隐私或真实日程事故。

综合评价：

- **产品与演示价值：较高。**
- **模块可理解性：中等；核心路径可追，但中心文件过重。**
- **扩展性：接口层中等偏好，运行时插件化有限。**
- **可靠性：中等偏低；本地状态和外部副作用协议不足。**
- **安全性：当前不适合暴露给不可信来源。**
- **工程成熟度：Alpha，与项目元数据一致。**

## 16.2 最值得学习的五个设计

1. 用领域快路径约束高价值 Agent 行为，而不是全部交给 LLM。
2. 多源规划按来源局部降级，最终仍提供有用结果。
3. Channel、Provider、Tool 三类变化边界使用 Adapter/Registry 隔离。
4. 小规模个人数据用 FTS5 + 本地 hash + RRF，在隐私、部署和可解释性间折中。
5. Calendar draft 和上下文卫生体现了正确安全方向，应推广成全局协议。

## 16.3 最需要警惕的五个问题

1. 不可信内容可以触达默认高权限 Tool，而所谓工作区限制不足以成为沙箱。
2. 快路径提前返回导致授权、Context、Memory、错误与持久化行为分裂。
3. 远端副作用与本地状态没有幂等/对账，异常可能重复或未知。
4. 前台全局串行，后台又绕锁，且普通后台进入慢路径时会共享实例 context，既慢又不安全。
5. 配置、README 与源码漂移，声明存在不代表能力生效或安全边界成立。

## 16.4 可迁移与不宜照搬的经验

可迁移：

- 明确区分“生成建议”与“执行副作用”。
- 高价值流程用确定性服务，LLM 负责长尾和自然语言适配。
- 多外部来源先定义降级契约和证据，再优化并发。
- 小数据先用本地可解释检索，建立评测后再上复杂向量设施。
- 适配器边界应包含生命周期、错误、身份和 capability，而不只方法签名。

不宜照搬：

- 全局共享 Tool 实例携带 turn context。
- 用正则黑名单当 Shell sandbox。
- 远端写成功后才无锁标记本地状态。
- 损坏 JSON 返回空并随后覆盖。
- 在一个编排器里用大量 if 和重复谓词管理业务优先级。

## 16.5 如果从零复刻，推荐顺序

1. 定义 <code>Principal/TurnContext/Capability</code>、统一消息协议与错误模型。
2. 做 CLI 组合根、Channel Adapter、Bus 和 per-session 调度。
3. 实现 Session 的原子持久化、schema version 和可重建索引。
4. 实现 Provider/Tool 接口与低权限 sandbox，先只开放只读 Tool。
5. 建 Context/Memory/RAG，并用固定评测集验证召回。
6. 选择一个领域场景，做确定性 Application Service 和统一 TurnPipeline。
7. 为任何外部写入建立 draft、幂等键、outbox/对账和审计。
8. 最后增加 Cron/HB；任务模型必须先有 owner、target、lease、misfire。
9. 在扩大平台/能力前补 CI、指标、脱敏日志和故障注入。

## 16.6 不同角色的掌握重点

| 角色 | 必须掌握 |
|---|---|
| 初学者 | 图 1-1、快/慢路径、锚点 14 步、主要状态位置 |
| 开发者 | AgentLoop 路由、Tool/Provider/Channel 契约、Session/RAG、变更指南与测试 |
| 技术负责人 | 状态事实来源、并发/生命周期、副作用幂等、P0/P1 依赖 |
| 架构师 | 信任边界、ADR、领域端口、持久化/调度演进和生产门槛 |

## 16.7 图 16-1：项目知识地图

~~~mermaid
flowchart TB
    Goal[目标: 连续、个性化、可行动的个人 Agent]
    Goal --> Scene[核心场景: QQ 杭州两日游]
    Goal --> Entry[入口: Typer CLI / Channel / Cron / Heartbeat]
    Entry --> ChannelIn[Channel → MessageBus]
    Entry --> Direct[普通 Cron/HB 含普通 Email Patrol → process_direct]
    Entry --> Patrol[三个具名特殊 patrol]
    ChannelIn --> Agent[AgentLoop]
    Direct --> Agent
    Agent --> Rules[内联规则阶段]
    Rules -->|命中| Fast[终止型快路径: Calendar / Planning / Reminder]
    Rules -->|未命中| Life[LifeEvent 条件预处理]
    Life -->|messages 非空| Reply[本轮回复]
    Life -->|无回复| Slow[慢路径: Context + Provider + ToolRegistry]
    Fast --> Reply
    Slow --> Reply
    Patrol --> Domain[领域服务]
    Domain --> PatrolEffect[领域状态 / 条件出站通知]
    Scene --> Fast

    Goal --> Data[关键数据全集: Session / MEMORY / RAG / Draft / Domain DB]
    Goal --> Ext[外部边界全集: 飞书 / 邮箱 / 高德 / Web / LLM]
    Agent --> Highlights[亮点: 确定性路由 / 多源降级 / 混合 RAG]
    Data --> Risks[风险: 权限 / 并发 / 幂等 / 原子性]
    Risks --> Roadmap[路线: P0 安全协议 → P1 可靠性 → P2 解耦]
    Goal --> Extension[扩展: Channel / Provider / Tool / Domain Port]
~~~

读图结论：

1. Channel 入站与普通 Cron/HB direct 在 AgentLoop 汇合，但只有 Channel 经过入站 Bus；三个具名特殊 patrol 绕过 AgentLoop 直接调用领域服务。
2. LifeEvent 有 <code>messages</code> 时终止并回复，无消息时才继续慢路径；锚点则在终止型快路径结束。
3. “关键数据全集”和“外部边界全集”是知识分类节点，不表示每条执行路径都会读写其中每一项。
4. 风险治理横切终止型、条件预处理、慢路径和巡检入口；P0、P1、P2 的顺序是先稳定安全语义，再优化可靠性，最后解耦。

最终必须形成的心智模型是：

> Copala 的价值不是“让模型会更多工具”，而是把个人数据、确定性领域编排和通用 Agent 放进同一运行时；它的下一阶段重点也不是再加功能，而是把身份、权限、turn、状态提交和资源生命周期升级为全局不变量。

---

# 17. 附录

## 17.1 图 17-1：实际部署图

本图回答“实际需要部署多少进程、状态和外部网络连接在哪里”。它明确纠正把 Gateway 当 HTTP Server 的误解。

~~~mermaid
flowchart TB
    subgraph Host[用户本机 / 单个 Python 进程]
        CLI[Copala Typer Runtime]
        Tasks[Channel tasks + Agent task + Cron/HB tasks]
        FS[(workspace: JSON / JSONL / Markdown / SQLite)]
        CLI --> Tasks
        Tasks <--> FS
    end

    Tasks <--> IM[聊天平台长连接 / API]
    Tasks <--> LLM[LLM APIs]
    Tasks <--> Feishu[飞书日历 API]
    Tasks <--> Mail[IMAP / SMTP]
    Tasks <--> Map[高德 / Tavily / Web]
    Tasks <--> MCP[MCP Server]
    OAuth[临时 OAuth localhost:18790] -. 仅授权命令启动 .-> Feishu
~~~

读图结论：

1. 正常 Gateway 没有业务 HTTP 监听端口。
2. 进程、状态和高权限文件/Shell在同一主机信任域。
3. 单进程退出会同时停止所有通道、Agent 和后台任务。
4. OAuth localhost server 是临时、独立命令路径，不应画入常驻 Gateway。

## 17.2 图 17-2：核心数据流

~~~mermaid
flowchart TB
    Raw[平台原始事件] --> In[InboundMessage]
    In --> Stop{/stop 前置拦截?}
    Stop -- 是 --> Cancel[取消当前 session 主处理任务 + 子 Agent]
    Stop -- 否 --> Current[取得或创建内存 Session]
    Session[(sessions JSONL)] -->|加载或创建| Current
    Current --> Command{Slash command?}
    Command -- /new --> New[归档旧消息后清空 Session]
    Command -- /help --> Reply[回复文本]
    Command -- 否 --> Route[终止型领域规则]
    Route -- 命中 --> Fast[领域服务结果]
    Fast --> Reply
    Route -- 未命中 --> Life[LifeEvent 预处理]
    Life -- messages 非空 --> Reply
    Life -- messages 为空 --> ToolCtx[设置共享 Tool 实例上下文]
    ToolCtx --> Context[ContextBuilder]
    Session -->|读取 history| Context
    Memory[(MEMORY/HISTORY)] -->|读取摘要| Context
    RAG[(memory.sqlite)] -->|当轮检索| Context
    Context --> Slow[LLMProvider ↔ ToolRegistry]
    Slow --> Reply
    Cancel --> Reply
    New --> Reply
    Reply --> Out[OutboundMessage]
    Out --> Platform[平台发送]

    New -->|保存清空状态| Session
    New -. 归档时写入 .-> Memory
    Fast -. 保存 user/assistant .-> Session
    Life -. 有 messages 时保存 user/assistant .-> Session
    Slow -->|保存慢路径 turn| Session
    Fast -. 出行且启用 savePlanToMemory .-> RAG
    Life -. TODO / 日历成功事件记忆 .-> RAG
    Life -. 本地事件/TODO/去重状态 .-> LifeStore[(life_events SQLite)]
    Life -. 提醒 .-> CronStore[(cron/jobs JSON)]
    Slow -. 当轮自动记忆抽取 .-> RAG
    Slow -. CalendarTool 等写入 .-> ToolState[(calendar drafts / tool domain stores)]
    Fast -. 按配置调用 .-> Feishu[(飞书日历)]
    Fast -. 按配置调用 .-> MapWeb[(高德 / Web 搜索)]
    Life -. 可能直写 .-> Feishu
    Slow -. CalendarTool 等 .-> Feishu
    Slow -. EmailTool .-> Mail[(邮箱)]
    Slow -. 规划/Web Tool .-> MapWeb
    Session -->|后台 consolidation 读取| Consolidate[MemoryStore]
    Consolidate -->|写入摘要| Memory
~~~

读图结论：

1. <code>/stop</code> 在创建处理 task 和取得 Session 之前被拦截，并取消该 session 的主处理任务与子 Agent；<code>/help</code> 不写 turn，<code>/new</code> 是归档后清空，不能与终止型领域收尾合并描述。
2. 只有命令和终止型领域规则都未结束、且 LifeEvent 的 <code>messages</code> 为空时，才设置共享 Tool context 并构建 LLM messages；当前仍没有不可变 <code>TurnContext</code>。
3. LifeEvent 空消息继续路径可已写 TODO、Cron、RAG 或低置信/去重本地状态；真实日历写入无论成功或失败都会产生 message，因此会在本轮短路。
4. 图中补出了两类常被遗漏的 RAG 写：出行快路径按配置写 <code>plan_artifact</code>，慢路径在完成后自动抽取当轮记忆；CalendarTool 等慢路径工具才可能写 Calendar Draft。
5. <code>MEMORY.md/HISTORY.md</code> 由 ContextBuilder 读取，写入来自 Session consolidation；Session、RAG、摘要、领域库和远端服务之间仍没有统一事务。

## 17.3 核心类、函数与接口索引

| 名称 | 位置 | 作用 |
|---|---|---|
| <code>gateway()</code> | <code>cli/commands.py:323-578</code> | 常驻运行时组合根 |
| <code>BaseChannel</code> | <code>channels/base.py:12-135</code> | 通道抽象、权限与消息归一化 |
| <code>ChannelManager</code> | <code>channels/manager.py:16-245</code> | 通道装配、生命周期和出站路由 |
| <code>MessageBus</code> | <code>bus/queue.py:8-44</code> | 进程内双队列 |
| <code>AgentLoop</code> | <code>agent/loop.py:44-1037</code> | 核心编排器 |
| <code>_run_agent_loop()</code> | <code>agent/loop.py:317-394</code> | LLM/Tools 多轮循环 |
| <code>_process_message()</code> | <code>agent/loop.py:467-633</code> | 命令、终止型路由、LifeEvent 预处理与慢路径 |
| <code>process_direct()</code> | <code>agent/loop.py:967-979</code> | CLI、普通 Cron/HB 的单次直接入口；特殊 patrol 不经过 |
| <code>ContextBuilder</code> | <code>agent/context.py:17-219</code> | System/历史/RAG/媒体上下文 |
| <code>ToolRegistry</code> | <code>agent/tools/registry.py:8-69</code> | 工具注册、定义与执行 |
| <code>LLMProvider</code> | <code>providers/base.py:32-131</code> | 模型统一接口 |
| <code>FallbackLLMProvider</code> | <code>providers/fallback_provider.py:15-123</code> | 主备模型切换 |
| <code>Session/SessionManager</code> | <code>session/manager.py:16-213</code> | 会话对象与 JSONL |
| <code>MemoryStore</code> | <code>agent/memory.py:47-170</code> | 长期摘要与历史 |
| <code>MemoryRAGStore</code> | <code>agent/memory_rag/store.py:19-407</code> | 结构化记忆、索引与检索 |
| <code>CalendarService</code> | <code>calendar/service.py:42-627</code> | 日历读取、草案、确认与摄取 |
| <code>LifeEventManager</code> | <code>life_events/service.py:617-1005</code> | 事件处理与真实副作用 |
| <code>OutingPlanningService</code> | <code>planning/outing.py:26-438</code> | 锚点规划 |
| <code>CronService</code> | <code>cron/service.py:63-375</code> | 本地定时任务 |
| <code>HeartbeatService</code> | <code>heartbeat/service.py:40-173</code> | 周期主动判断 |

## 17.4 配置项索引

| 配置分组 | 关键字段 | 真实作用/注意 |
|---|---|---|
| <code>channels</code> | 各平台 enabled/凭证/allowFrom | 通道装配；空 allowlist 实际 deny-all |
| <code>agents.defaults</code> | model、maxTokens、temperature、maxToolIterations | 通用 Agent/Provider 行为 |
| <code>agents.fallback</code> | enabled/provider/model | 主备；<code>retryPrimaryNextDay</code> 不生效 |
| <code>providers</code> | apiKey/apiBase/extraHeaders | 模型凭证和端点 |
| <code>memoryRag</code> | enabled、topK、maxContextChars 等 | 检索；<code>embeddingModel</code> 不生效 |
| <code>calendar</code> | sources/daysAhead/enterprise | 日历读取和 Provider；enabled 边界不完整 |
| <code>lifeEvents</code> | enabled/minConfidence/patrol | 部分生效；writeMode/autoCreateTodo 不生效 |
| <code>dailyPlanning</code> | enabled 等 | 部分字段无消费点 |
| <code>outingPlanning</code> | enabled/origin/city | 规划；defaultBudgetCurrency 无消费 |
| <code>smartReminders</code> | enabled/阈值/通知 | 风险提醒 |
| <code>heartbeat</code> | enabled/interval | 后台周期 |
| <code>gateway</code> | host/port | 当前不生效 |
| <code>tools.exec</code> | timeout、pathAppend | Shell 行为 |
| <code>tools</code> | restrictToWorkspace/mcpServers | 默认 false；现有限制不足 |

完整定义见 <code>copala/config/schema.py:18-565</code>。交接时应把“有字段”与“有生产消费点”分开审查。

## 17.5 外部依赖及用途

| 依赖/库 | 用途 |
|---|---|
| Typer / Rich / prompt-toolkit | CLI、终端展示与交互 |
| Pydantic / pydantic-settings | 配置模型 |
| LiteLLM / openai | 多模型调用 |
| httpx / websockets / websocket-client / socketio | HTTP 与长连接 |
| lark-oapi / qq-botpy / dingtalk-stream / slack-sdk / python-telegram-bot | 平台通道 |
| matrix-nio / mistune / nh3 | Matrix 与 Markdown/HTML 清洗，属于 extra |
| croniter | Cron 表达式 |
| mcp | 外部工具协议 |
| readability-lxml / chardet | Web 内容抽取与编码 |
| sqlite3 / FTS5（标准库/系统能力） | RAG 与领域状态 |

精确版本约束见 <code>pyproject.toml:19-67</code>。

## 17.6 术语表

| 术语 | 定义 |
|---|---|
| Adapter | 把外部平台接口转换为内部稳定协议 |
| Application Service | 编排一个完整业务用例的确定性服务 |
| Backpressure | 下游处理不过来时限制上游继续生产 |
| Capability | 某主体在特定资源上被允许的动作 |
| CAS | Compare-And-Set，基于期望旧状态的原子更新 |
| Dense | 本项目指 hash 向量相似度，不等于神经语义 embedding |
| FTS5 | SQLite 全文检索扩展 |
| Idempotency | 同一请求重复执行不会产生多个副作用 |
| Misfire | 定时任务错过计划时间后的处理策略 |
| Outbox | 先耐久记录待发送/待执行事件，再可靠投递 |
| RRF | Reciprocal Rank Fusion，多路排序结果融合 |
| Saga | 跨服务事务的步骤与补偿模式 |
| TurnContext | 一次处理内不可变的主体、来源、目标、trace 与能力 |

## 17.7 测试、静态检查与复核命令

已实际执行：

~~~text
uv run --isolated --extra dev pytest -q
# 278 passed, 3 failed

uv run --isolated --extra dev ruff check copala --statistics
# 69 项；包括 2 个 F821
~~~

文档完成时建议复核：

~~~text
git status --short
rg -n "[ \t]+$" 项目深度剖析文档.md
rg -n "^# ([0-9]+)\." 项目深度剖析文档.md
~~~

<code>git diff --check</code> 和普通 <code>git diff</code> 不覆盖未跟踪文件；本文仍为未跟踪交付物时，应直接检查行尾空格、围栏和表格，并以 <code>git status --short</code> 确认没有源码改动。

测试规模统计：89 个源码 Python 文件、约 16,639 行；38 个测试文件、约 5,257 行。数字由当前工作树静态统计，注释/空行口径随工具而异。

## 17.8 未确认问题、分析范围与证据局限

### 待真实环境验证

1. 各聊天 SDK 的断线重连、限流、重复事件和发送幂等。
2. 飞书真实 OAuth、token refresh、分页、日历并发和远端幂等字段能力。
3. 高德/Tavily/模型真实超时、配额与错误响应兼容。
4. Windows/Linux/macOS 下 Shell 进程树、路径/符号链接和文件权限差异。
5. 长期个人 RAG 数据的召回质量、库规模和同步 IO 拐点。
6. 进程休眠、时钟回拨、DST 和断电下 Cron/Heartbeat 行为。
7. Wheel/sdist 在干净 Python 3.11/3.12 环境的安装与各 optional extra 组合。

### 已覆盖

- 当前 Commit 的源码、配置、测试、README、SECURITY、构建元数据和 Git 状态。
- 包入口、Gateway/Agent、主消息链、锚点、LLM 慢路径、状态和外部副作用。
- 静态安全、并发、一致性、性能、生命周期、工程化和文档差异。
- 完整 pytest 与源码 Ruff 的实际执行结果。

### 证据局限

- 没有使用真实个人凭证连接外部服务，故没有改变日历、邮件或聊天平台。
- 测试通过只能证明既有断言，不能否定未建模的攻击与竞态。
- README 表达作者公开目标，但源码和实际运行证据优先。
- 所有行号绑定 Commit <code>f4ecb42</code>。

## 17.9 重要结论与代码位置对照

| 重要结论 | 主要证据 |
|---|---|
| Gateway 不是 HTTP Server | <code>cli/commands.py:323-578</code>；OAuth 才在 <code>:1007-1096</code> 监听 |
| 锚点不经过 LLM | <code>agent/loop.py:540-545,717-808</code> |
| LifeEvent 是条件短路预处理 | <code>agent/loop.py:561-566,902-920</code>、<code>life_events/service.py:681-715</code> |
| 通用慢路径是 Context → LLM ↔ Tools | <code>agent/loop.py:317-394,591-623</code> |
| 前台全局串行；普通 Cron/HB direct 与特殊 patrol 均绕锁 | <code>agent/loop.py:431-434,967-979</code>、<code>cli/commands.py:404-475</code> |
| Tool context 是共享可变实例状态 | <code>agent/loop.py:292-297</code>、<code>copala/agent/tools/message.py:20-37</code> |
| RAG dense 是 192 维 hash 全表扫描 | <code>copala/agent/memory_rag/embeddings.py:28-44</code>、<code>copala/agent/memory_rag/store.py:325-344</code> |
| LifeEvent 绕过草案直写 | <code>life_events/service.py:672-770</code> |
| 日历确认无幂等/执行态 | <code>calendar/service.py:212-258</code> |
| Session 非原子整文件覆盖 | <code>session/manager.py:163-180</code> |
| TLS 可降级为不校验 | <code>providers/openai_codex_provider.py:63-70,92-111</code> |
| 默认 Tool 边界过宽 | <code>config/schema.py:455-461</code>、<code>agent/loop.py:234-249</code> |
| Slack 发送存在 F821 | <code>channels/slack.py:81-85</code> |

## 17.10 读者掌握度自检

读者应先闭卷回答，再按“答案位置”复核：

| 自检问题 | 合格答案要点 | 答案位置 |
|---|---|---|
| 1. 项目为什么存在？ | 连续个性化、个人数据、可行动 Agent | 1.1～1.3、2.1 |
| 2. 项目边界是什么？ | 本地编排；外部平台/模型；非 HTTP/多租户 | 2.2～2.3 |
| 3. 程序从哪里启动？ | pyproject entry / __main__ / commands | 6.1、17.3 |
| 4. 一次请求如何执行？ | Channel→Bus→Agent→终止型路由/LifeEvent 条件预处理/LLM 慢路径→State→Channel | 第 4 章、6.3、7.6 |
| 5. 核心模块如何协作？ | 图 3-3 和 A 级模块 | 3.4、第 6 章 |
| 6. 数据和状态如何流动？ | Session/RAG/Draft/远端多事实源 | 第 8 章、图 17-2 |
| 7. 异常如何处理？ | 工具文本化、Provider fallback、领域降级、发送丢弃 | 7.8、11.5 |
| 8. 最重要亮点是什么？ | 确定性终止型路径与通用 Agent 并存、多源降级、轻量 RAG | 第 9 章 |
| 9. 为什么采用当前设计？ | 单人本地 Alpha 的部署/隐私/确定性约束 | 第 10 章 |
| 10. 主要代价是什么？ | 权限、中心耦合、分散状态、无幂等、并发 | 第 12 章 |
| 11. 新增功能修改哪里？ | 按类型找扩展点并检查 Schema/State/Test | 第 14 章 |
| 12. 最优先优化什么？ | P0 权限/身份/TurnContext/副作用协议 | 第 13 章 |

掌握度分级：

- **初步理解**：能准确回答 1～5，不把 Gateway 说成 HTTP 服务。
- **可参与开发**：能回答 1～9，并追踪锚点和通用慢路径到函数级。
- **可独立维护**：能回答全部 12 题，并为第 14 章任一变更列出测试和回滚。
- **可做架构交接**：还能解释 P0 之间的组合攻击链、状态事实来源和路线依赖。

## 17.11 最终自检结论

- 主线从目标、边界、架构、锚点、模块、状态、决策一直连到风险和变更，没有用目录清单代替运行链路。
- 每个关键技术名都映射到真实实现；“Dense”“Gateway”“每轮 RAG”“草案”均说明了源码事实与文档差异。
- 关键建议均有触发条件、影响、实施步骤、回滚或验证标准。
- 图示与源码一致：Gateway 只表示单进程装配，LifeEvent 与终止型路由已分开，普通 Cron/HB 与特殊 patrol 已分流，外部写入没有被画成原子事务。
- 已明确区分 [事实]、[推断]、[建议]、[待验证]。
- 本文能够支持项目交接、代码定位、架构评审、风险整改和技术答辩。
