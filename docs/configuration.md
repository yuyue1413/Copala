# 配置参考

配置模型定义在 `src/copala/config/schema.py`。JSON 同时接受 camelCase 和 snake_case；文档统一使用 `copala onboard` 生成的 camelCase。环境变量以 `COPALA_` 开头，嵌套层级使用双下划线。

## Agent 与 Provider

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.copala/workspace",
      "model": "openrouter/openai/gpt-4o-mini",
      "provider": "openrouter",
      "maxTokens": 8192,
      "temperature": 0.1,
      "maxToolIterations": 40,
      "memoryWindow": 100
    },
    "fallback": {
      "enabled": false,
      "model": "",
      "provider": "auto",
      "retryPrimaryNextDay": true
    }
  },
  "providers": {
    "openrouter": {"apiKey": "YOUR_API_KEY"}
  }
}
```

Provider 可使用 OpenAI-compatible API、LiteLLM 支持的供应商、Azure OpenAI，以及 OpenAI Codex/GitHub Copilot OAuth。不要同时在文档和仓库中保存真实密钥。

## 长期记忆

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

`local-hash` 是 192 维确定性 signed-hash 特征，不调用神经 Embedding 服务。`topK` 控制最终注入条数，`bm25K`/`denseK` 控制两路候选，`rrfK` 是融合常数。

## 日历与 LifeEvent

```json
{
  "calendar": {
    "enabled": true,
    "lookaheadDays": 30,
    "enterpriseEnabled": false,
    "writeMode": "draft",
    "providers": {
      "feishu": {"enabled": false, "authMode": "user"}
    }
  },
  "lifeEvents": {
    "enabled": true,
    "calendarWriteMode": "draft",
    "minCalendarConfidence": 0.82,
    "autoCreateTodo": true,
    "autoCreateReminder": true
  }
}
```

`calendar.writeMode` 约束通用日历工具。`lifeEvents.calendarWriteMode` 当前是预留字段，尚未被 LifeEvent 执行链消费，不能视为已生效的安全边界。真实账户启用 LifeEvent 前应先使用测试日历验证，并在需要时关闭 `lifeEvents.enabled`。

## 规划与提醒

`dailyPlanning` 控制工作时段、默认路程和缓冲时间；`outingPlanning` 控制攻略站点、预算币种和计划入库；`smartReminders.amap` 控制高德路线/天气调用。未提供高德或搜索凭证时，相关模块会按可用信息降级。

## 工具安全

```json
{
  "tools": {
    "restrictToWorkspace": true,
    "exec": {"timeout": 60},
    "web": {"search": {"provider": "auto", "apiKey": ""}},
    "mcpServers": {}
  }
}
```

`restrictToWorkspace=true` 可缩小文件工具边界，但不能替代操作系统权限隔离。MCP Server、Shell 和外部 URL 均应按不可信输入处理。

完整结构请运行 `copala onboard` 生成默认文件，或查看 [`examples/config.example.json`](../examples/config.example.json)。
