# nanobot 技能

这个目录包含 nanobot 的内置技能，用于扩展机器人的能力。

## 技能格式

每个技能都是一个独立目录，其中包含一个 `SKILL.md` 文件，内容包括：

- YAML frontmatter，例如名称、描述和元数据
- 面向 Agent 的 Markdown 操作说明

## 来源说明

这些技能改编自 [OpenClaw](https://github.com/openclaw/openclaw) 的技能系统。
技能格式和元数据结构遵循 OpenClaw 的约定，以保持兼容性。

## 可用技能

| 技能 | 描述 |
|-------|-------------|
| `github` | 使用 `gh` CLI 与 GitHub 交互 |
| `weather` | 使用 wttr.in 和 Open-Meteo 获取天气信息 |
| `summarize` | 总结 URL、文件和 YouTube 视频 |
| `tmux` | 远程控制 tmux 会话 |
| `clawhub` | 从 ClawHub 注册表搜索并安装技能 |
| `skill-creator` | 创建新技能 |
| `daily-planning` | 根据飞书日程、生活事件、长期记忆和待办事项生成每日主动规划 |

| `context-aware-reminders` | 结合高德天气/路线、飞书日程和生活事件生成情境感知风险预警 |
| `outing-planning` | 结合 RAG、飞书日历、高德路线/天气和 Web 搜索生成出行、旅行和商场外出规划 |
