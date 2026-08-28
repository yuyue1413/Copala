# 外部集成

所有示例凭证均为占位值。真实 Token 只应保存在本机配置、环境变量或密钥管理服务中。

## 消息通道

Copala 支持 Telegram、Discord、飞书、钉钉、Slack、QQ、Matrix、Email 和 Mochat 等通道。启用某通道前设置其 `enabled` 和凭证，并使用 `allowFrom` 或群组策略限制来源。

```bash
copala channels status
copala gateway
```

## 飞书日历

读取个人日历使用 `authMode: "user"`，在飞书开放平台配置应用凭证、日历权限和回调地址 `http://localhost:18790/feishu/oauth/callback`：

```json
{
  "channels": {
    "feishu": {"appId": "YOUR_APP_ID", "appSecret": "YOUR_APP_SECRET"}
  },
  "calendar": {
    "enabled": true,
    "enterpriseEnabled": true,
    "writeMode": "draft",
    "providers": {
      "feishu": {
        "enabled": true,
        "authMode": "user",
        "defaultCalendarId": "primary",
        "redirectUri": "http://localhost:18790/feishu/oauth/callback"
      }
    }
  }
}
```

```bash
copala calendar feishu-auth-login
copala calendar status
copala calendar list --source feishu --days 7
```

`tenant` 模式适合应用或共享日历，不等同于用户私人日历授权。

## 邮箱

`mailbox` 用于 LifeEvent 邮件巡检；`channels.email` 是收发消息通道，两者配置和副作用不同。只使用邮箱授权码，并确认账户所有者已明确同意访问。不要在公开日志中打印邮件正文或认证信息。

## 高德与 Web Search

高德 Web Service 用于天气和路线风险；Web Search 支持配置的搜索供应商。未配置密钥时规划会降级，但无法提供实时信息。离线评测不会访问这些服务。

## MCP

MCP Server 可通过 stdio、SSE 或 Streamable HTTP 接入。固定命令、参数、环境变量、URL 和允许的工具集合；来自 MCP 的内容应视为不可信外部输入。

## OAuth Provider

```bash
copala provider login openai-codex
copala provider login github-copilot
```

OAuth Token 保存在对应依赖的本地安全位置。不要将认证目录复制进仓库或评测制品。
