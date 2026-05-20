# Copala 安全说明

Copala 是一个个人智能助理 Agent，能够接入 QQ、飞书、邮件、飞书日历、高德地图、Tavily 搜索、RAG 长期记忆和本地 Skills。它会处理用户的聊天记录、日程、邮件摘要、偏好记忆和自动生成脚本，因此请把它视为一个拥有个人数据访问能力的本地应用来管理。

## 安全边界

Copala 默认运行在本机环境中，主要数据目录为：

```text
C:\Users\Weichu\.copala
```

该目录中可能包含 API Key、飞书 OAuth Token、QQ/飞书配置、RAG 记忆库、会话历史、自动学习的脚本和临时任务记录。不要把这个目录提交到 GitHub。

## 凭证管理

不要将以下内容写入代码、README、测试数据或 Git 仓库：

- 大模型 API Key
- QQ `appId` / `secret`
- 飞书 `appId` / `appSecret`
- 飞书 OAuth 用户 token
- Tavily Search Key
- 高德地图 Web 服务 Key
- 邮箱 IMAP / SMTP 密码
- GitHub Token

推荐配置方式：

```json
{
  "providers": {
    "deepseek": {
      "apiKey": "YOUR_API_KEY"
    }
  },
  "channels": {
    "qq": {
      "appId": "YOUR_QQ_APP_ID",
      "secret": "YOUR_QQ_SECRET"
    }
  }
}
```

配置文件应只放在本机：

```text
C:\Users\Weichu\.copala\config.json
```

提交前建议检查：

```powershell
rg -n "apiKey|appSecret|clientSecret|secret|token|tvly-|sk-" .
git status --short
```

## GitHub 提交安全

提交前确认仓库中不包含：

- `.copala/`
- `workspace/`
- OAuth token 文件，例如 `calendar/feishu_user_token.json`
- 本地会话、记忆数据库、日志文件
- 自动生成但未审查的脚本

当前项目 `.gitignore` 应继续忽略虚拟环境、缓存、构建产物和日志。若新增本地数据目录，也应先加入 `.gitignore` 再提交。

## 通道访问控制

Copala 支持 QQ、飞书、Telegram、Discord、钉钉、Slack、Email 等通道。生产或长期运行时，应限制允许访问的用户：

```json
{
  "channels": {
    "qq": {
      "enabled": true,
      "allowFrom": ["你的QQ用户ID或群ID"]
    },
    "feishu": {
      "enabled": true,
      "allowFrom": ["你的飞书open_id"]
    }
  }
}
```

不要在公网环境中使用无限制的 `allowFrom`。如果为了调试临时放开访问，测试后应立即恢复白名单。

## 日历与生活事件写入

Copala 可以读取和写入飞书私人日历，并能从聊天或邮件中识别会议、面试、体检、聚餐、航班、酒店等生活事件。

安全要求：

- 自动写入只应面向高置信、时间明确的事件。
- 缺少关键字段时应询问用户，不应直接写入。
- 修改或删除真实日程前应保留来源、摘要和 `event_id`。
- 低置信事件、账单、快递、普通待办应优先生成提醒，而不是直接写入日历。
- 写入失败时只向用户返回可理解提示，详细 API 错误留在后台日志。

## RAG 长期记忆隐私

Copala 的 RAG 记忆库会保存用户偏好、邮件事实、日程习惯、旅行偏好、购物偏好和历史规划结果。相关文件通常位于工作区或 `.copala` 目录下。

注意事项：

- 不要把 RAG 数据库或会话历史提交到 GitHub。
- 不要把包含私人信息的 demo 数据放进 `case/`。
- 删除记忆时应同时删除结构化记录、FTS 索引和 embedding。
- 面试演示时建议使用脱敏 demo 数据。

## Skills 与自动学习脚本

Copala 支持 Skills，并可以把机器人学到的脚本沉淀到工作区。这个能力很适合展示 Agent 自学习，但也需要额外审查。

安全要求：

- 自动生成的脚本运行前必须人工检查。
- 不要保存包含密钥、密码、Cookie、Token 的脚本。
- 不要让脚本默认访问用户主目录、系统目录或浏览器数据。
- 涉及文件删除、移动、批量修改的脚本必须限制在指定工作区。
- Skills 文档只描述能力和调用规则，不应包含真实凭证。

## Shell 与文件工具

Copala 的工具系统包含文件读写和命令执行能力。使用时应遵守：

- 不要以管理员权限长期运行 Copala。
- 不要在包含敏感资料的目录中开放 unrestricted 文件访问。
- 批量删除、移动、覆盖文件前必须确认目标路径。
- 命令执行应设置超时，并记录日志。
- 对用户不可见的底层异常不应直接发送到 QQ/飞书聊天中。

## 外部服务风险

Copala 会调用多个外部服务：

- LLM Provider：会接收提示词和上下文。
- 飞书 OpenAPI：用于消息和日历。
- 高德地图：用于天气、地理编码和路线规划。
- Tavily Search：用于公开网页搜索。
- 邮件服务器：用于 IMAP / SMTP。

不要向外部服务发送不必要的私人原文。邮件、日程和聊天内容进入 LLM 前应尽量压缩、摘要和脱敏。

## 日志与错误处理

日志中可能包含用户消息、工具参数、API 错误、日程标题或邮件摘要。建议：

- 不把日志提交到 Git。
- 长期运行时定期清理日志。
- 用户侧只展示友好错误提示。
- 后台日志保留必要的排查信息，但不打印完整密钥。

## 依赖安全

建议定期检查依赖：

```powershell
pip install pip-audit
pip-audit
```

更新项目依赖前先运行测试：

```powershell
D:\Environment\anaconda3\envs\py311\python.exe -m pytest tests\test_memory_rag.py tests\test_calendar_direct_intent.py tests\test_outing_planning.py tests\test_web_search_tool.py tests\test_smart_reminders.py tests\test_daily_planning.py
```

## 运行前检查清单

- [ ] `config.json` 未提交到仓库。
- [ ] `.copala/`、`workspace/` 未提交到仓库。
- [ ] QQ / 飞书通道配置了 `allowFrom`。
- [ ] 飞书 OAuth token 文件仅保存在本机。
- [ ] RAG 记忆库不包含未脱敏演示数据。
- [ ] 自动学习脚本已经人工审查。
- [ ] 高风险工具调用不会越过项目工作区。
- [ ] GitHub push 前执行过敏感词扫描。

## 安全事件处理

如果怀疑凭证泄露：

1. 立即撤销相关 API Key、OAuth Token 或 GitHub Token。
2. 检查 Git 历史中是否提交过敏感文件。
3. 检查 `.copala` 中的日志和会话记录。
4. 重新生成飞书、QQ、LLM、Tavily、高德、邮箱等凭证。
5. 必要时清空 RAG 记忆库和会话历史。

## 更新时间

最后更新：2026-05-20
