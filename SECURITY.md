# 安全策略

## 支持范围

Copala 当前处于 Alpha 阶段。安全修复优先应用于默认分支的最新代码；历史 Commit 和第三方服务本身不在维护范围内。项目不承诺生产级 SLA。

## 私密报告漏洞

请优先使用 GitHub 仓库的 **Security → Report a vulnerability** 私密报告功能。若该功能不可用，请联系仓库维护者并明确标注“Security”，先请求私密沟通渠道。不要在公开 Issue、讨论区、PR、日志或截图中披露漏洞细节、真实凭证或个人数据。

报告建议包含受影响版本或 Commit、环境、最小复现、影响范围、脱敏日志，以及是否涉及日历、邮箱、Shell、MCP、Channel 或第三方 Token。维护者会尽快确认、评估并协调披露。

## 运行安全

- 配置位于 `~/.copala/config.json`；Windows 使用 `%USERPROFILE%\.copala\config.json`。工作区默认位于对应 `.copala/workspace`。
- 不要提交 `.env`、配置文件、OAuth Token、邮箱授权码、聊天记录、日历、数据库或日志。示例必须使用占位值。
- 将配置与 Token 文件权限限制为当前用户；怀疑泄露时立即吊销并轮换。
- 建议启用 `tools.restrictToWorkspace=true`，并使用权限受限的专用账户。该选项不能替代容器、虚拟机或操作系统隔离。
- 日历工具建议保持 `calendar.writeMode="draft"`。LifeEvent 另有 `lifeEvents.calendarWriteMode`，启用自动写入前需单独审查。
- Shell、MCP Server、网页内容、邮件正文和聊天消息都属于不可信输入。不要给 Agent 高权限账户或无限制文件访问。
- `gateway` 会运行后台 Cron/Heartbeat 和已启用通道；启动前确认通知目标、允许名单和副作用策略。
- 离线评测使用内存替身，不应读取真实日历、邮件或凭证。

## 依赖安全

仓库使用 Dependabot、依赖审查和 CodeQL 发现已知问题。第三方 SDK、模型和 API 仍受其各自安全策略约束。
