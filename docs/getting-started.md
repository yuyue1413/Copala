# 快速开始

## 环境要求

- Python 3.11、3.12 或 3.13
- Git
- 至少一个受支持的 LLM Provider；仅运行离线测试和评测时不需要凭证

## 安装

```bash
git clone https://github.com/yuyue1413/Copala.git
cd Copala
python -m venv .venv
```

激活环境：

```bash
# Linux / macOS
source .venv/bin/activate

# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

安装项目：

```bash
python -m pip install --upgrade pip
python -m pip install -e .
copala onboard
```

`onboard` 会创建配置和工作区：

| 系统 | 配置 | 默认工作区 |
| --- | --- | --- |
| Linux / macOS | `~/.copala/config.json` | `~/.copala/workspace` |
| Windows | `%USERPROFILE%\.copala\config.json` | `%USERPROFILE%\.copala\workspace` |

## 最小配置

将占位值替换为自己的凭证，不要把真实配置提交到 Git：

```json
{
  "providers": {
    "openrouter": {"apiKey": "YOUR_API_KEY"}
  },
  "agents": {
    "defaults": {
      "model": "openrouter/openai/gpt-4o-mini",
      "provider": "openrouter"
    }
  },
  "tools": {
    "restrictToWorkspace": true
  }
}
```

也可从 [`examples/config.example.json`](../examples/config.example.json) 复制完整脱敏示例。

## 首次运行

```bash
copala --help
copala status
copala agent -m "你好，请介绍当前可以使用的能力"
```

交互模式：

```bash
copala agent
```

配置消息通道后启动后台运行入口：

```bash
copala gateway
```

该命令启动消息 Channel、Agent Loop、Cron 与 Heartbeat；`--port` 目前主要用于相关运行配置和 OAuth 回调语境，不表示 Copala 提供普通 Web 首页。

## 下一步

- 按[配置参考](configuration.md)启用长期记忆、日历或 LifeEvent。
- 按[外部集成](integrations.md)配置飞书、QQ、邮箱、高德或搜索服务。
- 遇到问题先运行 `copala status` 和 `copala calendar status`，再查看[排障指南](troubleshooting.md)。
