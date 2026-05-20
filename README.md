# Copala 个性化长期记忆 Agent

这是一个面向个人日常生活的 AI 助手项目。项目基于 copala 扩展，实现了长期记忆 RAG、邮件读取、QQ 对话、个人日历读取、飞书私人日历 OAuth 授权、旅行/购物规划，以及机器人自学习技能沉淀。

核心目标不是做一个普通聊天机器人，而是展示一个 Agent 如何持续积累用户偏好、日程习惯、邮件线索和任务上下文，并在后续对话中通过 RAG 动态召回，给出更个性化、更连续的回复。

## 核心亮点

- 长期记忆 RAG：使用 SQLite + FTS5 BM25 + Dense 向量召回 + RRF 融合排序。
- 个性化上下文注入：每轮对话自动检索用户偏好、日程、邮件和历史计划，作为上下文注入给 Agent。
- 邮件记忆增强：读取 IMAP 邮件，抽取联系人、deadline、偏好信号和待办线索。
- 日历集成：支持本地 JSON/ICS 日历、飞书私人日历 OAuth，并以飞书作为 QQ 日程查询默认来源。
- 安全写入日程：创建/修改日程默认先生成草案，用户确认后才真正写入。
- 生活事件管家：从邮件和聊天消息中识别面试、会议、体检、航班、酒店、账单等事件，高置信日程自动格式化写入飞书日历，并通过当前聊天渠道或后台默认渠道通知用户。
- 旅行/购物规划：结合长期偏好、日程限制和 Web 搜索结果生成规划。
- 技能自学习：机器人可把新学会的脚本和技能说明保存到工作区，方便下次复用。
- 可解释性：RAG 结果包含来源、类型、置信度和 metadata，便于说明技术实现。

## 技术栈

- Python 3.11
- SQLite / FTS5
- BM25 + Dense embedding + RRF
- Typer CLI
- Pydantic 配置模型
- LiteLLM / OpenAI-compatible provider
- QQ BotPy
- 飞书 OpenAPI / OAuth
- DingTalk Stream SDK（可选消息通道，不作为日历主线）
- IMAP / SMTP
- pytest

## 项目结构

```text
copala/
  agent/
    memory_rag/          # 长期记忆 RAG：store、schema、embedding、extractor
    tools/               # Agent 工具：memory、calendar、email、planning、learned
    context.py           # RAG 上下文注入
    loop.py              # Agent 主循环与工具注册
  calendar/
    service.py           # 统一日历服务：本地日历 + 企业日历 + 草案确认
    providers/
      feishu.py          # 飞书日历 Provider，支持 tenant/user 两种模式
  channels/
    qq.py                # QQ 机器人通道
    email.py             # 邮件通道
    feishu.py            # 飞书消息通道
  config/
    schema.py            # config.json 的配置模型，也是 onboard 模板来源
    loader.py            # 配置加载与保存
  templates/             # 工作区模板：SOUL、TOOLS、MEMORY 等
tests/                   # 单元测试
case/                    # 可选本地示例数据；默认只保留脱敏说明文件
```

## 环境准备

本项目推荐使用已有的 `py311` 环境：

```powershell
D:\Environment\anaconda3\envs\py311\python.exe --version
```

如果已经把 `py311` 激活到 PowerShell，可以直接使用：

```powershell
python --version
```

安装项目：

```powershell
pip install -e .
```

初始化配置和工作区：

```powershell
copala onboard
```

默认配置文件位置：

```text
C:\Users\Weichu\.copala\config.json
```

默认工作区位置：

```text
C:\Users\Weichu\.copala\workspace
```

注意：修改 JSON 文件时统一使用 UTF-8、2 空格缩进、文件末尾保留换行。

## 基础配置

`config.json` 中至少需要配置一个大模型 provider。示例：

```json
{
  "providers": {
    "deepseek": {
      "apiKey": "YOUR_API_KEY"
    }
  },
  "agents": {
    "defaults": {
      "model": "deepseek-ai/DeepSeek-R1-0528",
      "provider": "auto"
    },
    "fallback": {
      "enabled": true,
      "model": "deepseek-ai/DeepSeek-V3",
      "provider": "custom",
      "retryPrimaryNextDay": true
    }
  }
}
```

`defaults.model` 是首选模型，`fallback.model` 是备用模型。首选模型 API 调用失败时会自动切到备用模型；如果错误属于 429、quota、rate limit 等额度类问题，当天后续请求会直接走备用模型，次日再重新优先尝试首选模型。后台会记录完整错误，聊天用户只会看到友好的失败提示。

直接在 CLI 测试 Agent：

```powershell
copala agent -m "你好，介绍一下你现在支持哪些功能"
```

启动网关和聊天通道：

```powershell
copala gateway
```

## QQ 机器人配置

如果使用 QQ 作为主要交互入口，需要在 `config.json` 中配置：

```json
{
  "channels": {
    "qq": {
      "enabled": true,
      "appId": "YOUR_QQ_APP_ID",
      "secret": "YOUR_QQ_APP_SECRET",
      "allowFrom": []
    }
  }
}
```

启动：

```powershell
copala gateway
```

然后在 QQ 中向机器人提问，例如：

```text
帮我总结一下我最近的偏好和日程安排。
```

```text
帮我规划一个轻松一点的周末旅行，按我的习惯来。
```

## 长期记忆 RAG

长期记忆模块位于：

```text
copala/agent/memory_rag/
```

核心数据流：

```text
用户对话 / 邮件 / 日历 / 规划结果
        ↓
MemoryExtractor 抽取结构化记忆
        ↓
SQLite memory_items 持久化
        ↓
FTS5 BM25 索引 + Dense embedding
        ↓
BM25 topK + Dense topK
        ↓
RRF 融合排序
        ↓
ContextBuilder 注入当前对话上下文
```

常用命令：

```powershell
copala memory reindex
copala memory search "旅行偏好"
copala memory list --type travel_preference
```

配置项：

```json
{
  "memoryRag": {
    "enabled": true,
    "topK": 8,
    "bm25K": 20,
    "denseK": 20,
    "rrfK": 60,
    "maxContextChars": 4000,
    "embeddingModel": "local-hash",
    "autoExtract": true
  }
}
```

简历描述示例：

```text
基于 SQLite FTS5 与向量检索构建 Agent 长期记忆系统，支持邮件、日程、旅行与购物偏好的结构化沉淀；实现 BM25 + Dense 双路召回与 RRF 融合排序，在回复生成前动态注入个性化上下文，提升跨会话任务连续性和个性化准确率。
```

## 本地日历配置

本地日历支持 JSON、ICS 文件和 ICS URL。

示例配置：

```json
{
  "calendar": {
    "enabled": true,
    "localJsonFiles": [
      "path/to/your_calendar.json"
    ],
    "icsFiles": [],
    "icsUrls": [],
    "lookaheadDays": 30
  }
}
```

查看日程：

```powershell
copala calendar list --source local --days 7
```

写入 RAG：

```powershell
copala calendar ingest --source local
```

## 飞书私人日历配置

如果目标是读取“本人飞书账号的私人日历”，必须使用 OAuth 用户授权模式，也就是 `authMode: "user"`。

### 飞书开放平台需要做什么

1. 打开飞书开放平台：

```text
https://open.feishu.cn/
```

2. 创建或打开一个自建应用。

3. 获取应用凭证：

```text
App ID
App Secret
```

4. 在应用的 OAuth 或安全设置中添加回调地址：

```text
http://localhost:18790/feishu/oauth/callback
```

5. 在权限管理中开启日历权限：

```text
读取日历
读取日程
```

如果需要创建或修改日程，再开启写入日程相关权限。

6. 如果飞书后台要求发布版本，需要发布应用，让权限生效。

### 本地 config.json 配置

```json
{
  "calendar": {
    "enabled": true,
    "enterpriseEnabled": true,
    "defaultPlatform": "feishu",
    "writeMode": "draft",
    "providers": {
      "feishu": {
        "enabled": true,
        "defaultCalendarId": "primary",
        "authMode": "user",
        "redirectUri": "http://localhost:18790/feishu/oauth/callback",
        "tokenFile": "calendar/feishu_user_token.json"
      }
    }
  },
  "channels": {
    "feishu": {
      "appId": "YOUR_FEISHU_APP_ID",
      "appSecret": "YOUR_FEISHU_APP_SECRET"
    }
  }
}
```

### 本地授权流程

先确保没有运行占用 `18790` 端口的 `copala gateway`。

运行：

```powershell
copala calendar feishu-auth-login
```

命令会启动一个临时本地回调服务，并输出飞书授权链接。复制链接到浏览器打开，登录飞书并授权。

授权成功后，用户 token 会保存到：

```text
~/.copala/workspace/calendar/feishu_user_token.json
```

测试读取私人日历：

```powershell
copala calendar list --source feishu --days 7
```

写入 RAG：

```powershell
copala calendar ingest --source feishu
copala memory search "Feishu calendar"
```

如果浏览器显示 `localhost 拒绝连接`，说明没有启动本地回调服务。请先运行：

```powershell
copala calendar feishu-auth-login
```

如果失败页面地址栏中已经有 `code=...`，可以复制 code 手动换 token：

```powershell
copala calendar feishu-auth-code YOUR_CODE
```

## 飞书企业/机器人日历模式

企业日历或机器人日历可以使用 `authMode: "tenant"`。这种模式使用应用 tenant token，适合读取机器人拥有的日历或飞书企业共享日历。

```json
{
  "calendar": {
    "enabled": true,
    "enterpriseEnabled": true,
    "providers": {
      "feishu": {
        "enabled": true,
        "defaultCalendarId": "primary",
        "authMode": "tenant"
      }
    }
  }
}
```

诊断命令：

```powershell
copala calendar calendars --platform feishu
copala calendar list --source feishu --days 7
```

当前演示主线是“飞书私人日历 OAuth”。钉钉消息通道可以继续保留，但钉钉日历 Provider 已从日历运行路径中移除，避免干扰认证诊断。

## 日程写入草案机制

为了避免 Agent 误改真实日程，默认配置为：

```json
{
  "calendar": {
    "writeMode": "draft"
  }
}
```

创建或修改日程时，Agent 会先调用：

```text
calendar(action="draft_create")
calendar(action="draft_update")
```

用户确认后才调用：

```text
calendar(action="confirm")
```

## 个人生活事件管家

生活事件管家会从邮件、QQ/飞书/钉钉消息和日历巡检中识别生活事件，例如面试邀约、会议通知、体检预约、航班酒店确认、信用卡账单和快递提醒。

高置信且有明确开始时间的日程类事件会自动写入飞书日历，并通过触发它的聊天渠道通知用户：QQ 里触发就由 QQ 机器人回复，飞书里触发就由飞书机器人回复。邮件巡检、日历巡检这类后台任务才使用 `notifyChannel` / `notifyChatId` 作为默认通知目标。写入前会先整理标题、时间、地点、联系人、来源和原始摘要，避免把混乱邮件原文直接写入日程；日程标题和描述会加入轻量表情符号、重点字段和行动建议。

配置示例：

```json
{
  "lifeEvents": {
    "enabled": true,
    "notifyChannel": "qq",
    "notifyChatId": "YOUR_DEFAULT_CHAT_ID",
    "calendarWriteMode": "direct",
    "minCalendarConfidence": 0.82,
    "autoCreateTodo": true,
    "autoCreateReminder": true,
    "emailPatrolMinutes": 30,
    "calendarPatrolMinutes": 15,
    "calendarLookaheadHours": 24
  }
}
```

自动写入规则：

- 面试、会议、体检、聚餐、航班、酒店：有明确时间且置信度达标时自动写入飞书日历。
- 信用卡账单、快递、普通待办：默认只创建待办和提醒，不写入日历。
- 缺少开始时间：不写入飞书日历，改为通过当前聊天渠道或后台默认渠道询问缺失信息。
- 写入或修改成功：通过当前聊天渠道或后台默认渠道发送“已写入/已修改飞书日历”的通知。
- 标题示例：`📌【会议】项目同步会`、`🌟【面试】字节跳动后端岗位二面`。

日程查询也会使用同一套清晰格式，突出主体事件、时间、地点和建议：

```text
🗓️ 今天飞书日程

1. ⭐ 📌【会议】项目同步会
   🕒 时间：2026-05-19 15:00-16:00
   📍 地点：飞书会议
   💡 建议：提前 10 分钟准备议题、资料和会议链接。
```

查看生活事件：

```powershell
copala life-events list
copala life-events reprocess-email --limit 20
```

gateway 启动后会自动注册生活事件邮件巡检和飞书日历巡检 cron。日历巡检会读取未来 24 小时日程，并在日程即将开始前主动提醒。

可以对 QQ 或 CLI 这样说：

```text
帮我创建一个明天上午10点的一小时项目同步会，先给我确认草案。
```

```text
确认创建刚才那个日程。
```

## 邮件读取与记忆抽取

项目支持 IMAP 邮件读取，并可把邮件中的联系人、deadline、偏好信号写入 RAG。

示例配置：

```json
{
  "mailbox": {
    "enabled": true,
    "imapHost": "imap.qq.com",
    "imapPort": 993,
    "username": "YOUR_EMAIL",
    "password": "YOUR_EMAIL_AUTH_CODE",
    "fetchLimit": 20,
    "bodyMaxChars": 2000
  }
}
```

Agent 可通过邮件工具读取邮件摘要，RAG 自动抽取长期记忆。适合展示：

- 用户常联系的人
- 项目或客户上下文
- deadline 和待办事项
- 旅行、购物、报销、订票等偏好线索

## 旅行与购物规划

规划工具位于：

```text
copala/agent/tools/planning.py
```

支持：

```text
travel_plan(...)
shopping_plan(...)
```

示例问题：

```text
帮我规划一个周末去杭州的轻松旅行，不要太赶，预算控制一下。
```

```text
帮我挑一款适合写代码的显示器，按我的历史偏好来。
```

规划结果会结合：

- RAG 中的用户偏好
- 邮件中的 deadline 或旅行线索
- 日历中的可用时间
- Web 搜索得到的外部信息

最终计划也可作为 `plan_artifact` 写入长期记忆。

## 自学习技能

机器人可把新学会的脚本和技能说明保存到工作区，便于下次调用。

相关工具：

```text
copala/agent/tools/learned.py
```

保存位置：

```text
~/.copala/workspace/learned/scripts/<category>/<script>.py
~/.copala/workspace/skills/learned-<skill-name>/SKILL.md
~/.copala/workspace/learned/manifest.json
```

简历描述示例：

```text
实现 Agent 自学习技能沉淀机制，将运行中生成的可复用脚本按任务类型归档，并同步生成 SKILL.md 技能说明与 manifest 索引，使机器人能够在后续任务中检索、复用和迭代自身工具能力。
```

## 常用命令

```powershell
# 初始化
copala onboard

# CLI 对话
copala agent -m "你好"

# 启动 QQ/邮件/飞书等通道
copala gateway

# RAG 记忆
copala memory reindex
copala memory search "旅行偏好"
copala memory list --type travel_preference

# 日历
copala calendar list --days 7
copala calendar list --source local --days 7
copala calendar list --source feishu --days 7
copala calendar ingest --source feishu

# 生活事件
copala life-events list
copala life-events reprocess-email --limit 20

# 飞书 OAuth
copala calendar feishu-auth-login
copala calendar feishu-auth-code YOUR_CODE

# 企业日历诊断
copala calendar calendars --platform feishu
```

## 测试

推荐使用 `py311` 环境运行测试：

```powershell
D:\Environment\anaconda3\envs\py311\python.exe -m pytest tests\test_memory_rag.py tests\test_calendar_service.py tests\test_enterprise_calendar.py tests\test_feishu_provider.py tests\test_learned_skill_tool.py
```

如果当前环境没有启用 `pytest-asyncio`，可能出现：

```text
PytestConfigWarning: Unknown config option: asyncio_mode
```

这是测试环境插件提示，不影响已改成同步包装的测试运行。

## 常见问题

### `No calendar events found`

说明接口调用成功，但当前时间范围内没有可读取事件。可以拉长时间：

```powershell
copala calendar list --source feishu --days 30
```

### `localhost 拒绝连接`

说明浏览器 OAuth 回调时本地没有服务监听。请运行：

```powershell
copala calendar feishu-auth-login
```

### `app bot_id not found`

这是飞书 tenant 模式常见错误，说明应用没有启用机器人能力。若目标是读取本人私人日历，请使用：

```json
{
  "authMode": "user"
}
```

如果确实要用企业/机器人日历，请在飞书开放平台开启 Bot 能力并发布应用。

### QQ 中提示缺少飞书 App ID / App Secret，但 CLI 能查到日程

这通常说明 QQ 网关进程还在使用旧配置，或启动时使用了不同的 `--config` / `--workspace`。

先在 PowerShell 验证默认配置：

```powershell
copala calendar list --source feishu --days 1
```

如果 CLI 能查到，请重启网关：

```powershell
copala gateway --config C:\Users\Weichu\.copala\config.json --workspace C:\Users\Weichu\.copala\workspace
```

也可以在 QQ 中让机器人检查日历认证状态：

```text
检查日历认证状态
```

QQ 里的“查看今天的日程 / 查看我今天的飞书日程 / 检查日历认证状态”会优先直连真实日历服务，不让模型凭历史上下文猜测配置。工具会返回 workspace、`authMode`、是否存在 App ID/App Secret、以及 Feishu token 文件是否存在。

CLI 中也可以直接检查：

```powershell
copala calendar status
```

### QQ WebSocket 断开或心跳报错

常见于网络波动、网关重连或连接关闭时写心跳。通常不是业务代码错误，观察是否能自动重连即可。

## 与上游 copala 的关系

本项目基于 copala 扩展，保留了轻量 Agent 框架、多通道消息、工具调用、会话管理等基础能力。

## 主动规划型日程助理

主动规划工具位于：

```text
copala/planning/daily.py
copala/agent/tools/daily_planning.py
```

支持：

```text
daily_plan(action="plan_day")
daily_plan(action="reschedule")
daily_plan(action="free_slots")
```

示例问题：

```text
帮我根据今天的日程规划一下。
```

```text
我今天还要取快递、买药、回复邮件，帮我安排一下。
```

```text
下午会议延迟到 4 点了，重新规划一下今天。
```

规划逻辑：

- 读取飞书日历中的固定日程，默认不移动已有会议、健身、聚餐等时间明确的事项。
- 读取生活事件库中的待办，并从当前消息中识别取快递、买药、回复邮件、购物等可移动任务。
- 结合 RAG 中的时间偏好和节奏偏好，自动加入缓冲时间。
- 输出按时间排序的建议，突出时间、地点、主体事件、风险提醒和个性化依据。
- 默认只给规划建议，不自动修改飞书日历；用户明确要求写入或改期时，仍通过真实 `calendar` 或 `life_event` 工具完成。

配置示例：

```json
{
  "dailyPlanning": {
    "enabled": true,
    "defaultTravelMinutes": 30,
    "defaultBufferMinutes": 15,
    "dayStartHour": 8,
    "dayEndHour": 22,
    "autoReplan": true
  }
}
```

测试命令：

```powershell
D:\Environment\anaconda3\envs\py311\python.exe -m pytest tests\test_daily_planning.py tests\test_life_events.py tests\test_calendar_direct_intent.py
```

## 情境感知智能提醒与高德地图 API

本项目新增 `smart_reminder` 智能提醒能力，用于把固定提醒升级为场景化风险预警。系统会综合生活事件、飞书日程、RAG 记忆和高德地图 Web 服务，检查天气、交通、日程冲突、账单截止等风险，并给出可执行建议。它默认只提醒和建议，不会自动修改飞书日历；如果用户明确要求改期或写入日历，仍走真实 `calendar` 或 `life_event` 工具。

配置项位于 `config.json` 的 `smartReminders`：

```json
{
  "smartReminders": {
    "enabled": true,
    "riskPatrolMinutes": 30,
    "lookaheadHours": 48,
    "minRiskConfidence": 0.75,
    "notifyChannel": "",
    "notifyChatId": "",
    "amap": {
      "enabled": true,
      "apiKey": "",
      "apiBase": "https://restapi.amap.com",
      "defaultCity": "上海",
      "defaultOrigin": "",
      "routeMode": "driving",
      "defaultTravelBufferMinutes": 15,
      "timeoutSeconds": 8
    }
  }
}
```

高德配置步骤：

1. 打开高德开放平台，创建应用并添加 Web 服务 Key。
2. 将 Key 填入 `smartReminders.amap.apiKey`。
3. 将 `defaultCity` 设置为常用城市，例如 `上海`。
4. 可选填写 `defaultOrigin`，用于后续按常用出发地评估路线风险。

测试命令：

```powershell
copala smart-reminders status
copala smart-reminders check --days 2
copala smart-reminders patrol
```

聊天测试：

```text
明天下午4点我要去室外打羽毛球，地点：人民公园
帮我检查未来两天有没有天气或交通风险
明天如果下雨，我的运动安排怎么调整？
```

## 出行/旅行智能规划

新增 `outing_plan` 工具，用于处理周末出游、去某地旅行、前往商场和临时外出等需求。它会复用 RAG 长期记忆、飞书日历、高德路线/天气和 Web 搜索结果，生成行程、路线、预算、天气风险、日程冲突和提醒建议。

配置项位于 `config.json` 的 `outingPlanning`：

```json
{
  "outingPlanning": {
    "enabled": true,
    "defaultSearchCount": 5,
    "defaultBudgetCurrency": "CNY",
    "guideSites": ["xiaohongshu.com", "ctrip.com", "mafengwo.cn", "dianping.com"],
    "savePlanToMemory": true
  }
}
```

说明：

- 高德 Key、默认城市和默认出发地继续复用 `smartReminders.amap`。
- Web 攻略检索复用 `tools.web.search`。支持 `provider="tavily"` 或 `provider="brave"`，未配置时会降级提示，不编造实时攻略。
- 使用 Tavily 时，将 `tools.web.search.provider` 设为 `tavily`，`apiKey` 填写 `tvly-...` 开头的 Tavily API Key；系统也会在 `provider="auto"` 时自动识别 `tvly-` Key。
- 使用 Brave 时，将 `tools.web.search.provider` 设为 `brave`，`apiKey` 填写 Brave Search API 的订阅 Token。若日志出现 `SUBSCRIPTION_TOKEN_INVALID`，说明当前不是有效的 Brave Token。
- 规划结果默认只保存到 RAG，不自动写入飞书日历；用户确认后才调用真实日历工具。

Tavily 搜索配置示例：

```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "tavily",
        "apiKey": "tvly-你的TavilyKey",
        "apiBase": "https://api.tavily.com",
        "searchDepth": "basic",
        "includeAnswer": true,
        "maxResults": 5
      }
    }
  }
}
```

聊天测试：

```text
周末我想去杭州玩两天，预算控制一下，不要太赶
明天下午我想去万象城逛一下，帮我看看什么时候出发合适
这个周末去苏州，有没有天气或日程冲突风险？
把杭州旅行的出发提醒和天气提醒写入飞书日历
```
