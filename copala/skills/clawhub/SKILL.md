---
name: clawhub
description: 在 ClawHub 公共技能注册表中搜索并安装 Agent 技能。当用户提问"查找技能"、"搜索技能"、"安装技能"、"有哪些技能可用"或"更新技能"时使用。
homepage: https://clawhub.ai
metadata: {"copala":{"emoji":"🦞"}}
---

# ClawHub

AI Agent 公共技能注册表，支持自然语言（向量搜索）查找技能。

## 触发时机

当用户提出以下请求时使用：
- "帮我找一个 … 的技能"
- "搜索技能"
- "安装技能"
- "有哪些技能可用？"
- "更新我的技能"

## 搜索

```bash
npx --yes clawhub@latest search "网页抓取" --limit 5
```

## 安装

```bash
npx --yes clawhub@latest install <slug> --workdir ~/.copala/workspace
```

将 `<slug>` 替换为搜索结果中的技能名称。技能会安装到 `~/.copala/workspace/skills/`，copala 会从此处加载工作区技能。**务必加上 `--workdir`**。

## 更新

```bash
npx --yes clawhub@latest update --all --workdir ~/.copala/workspace
```

## 列出已安装

```bash
npx --yes clawhub@latest list --workdir ~/.copala/workspace
```

## 注意事项

- 需要 Node.js（`npx` 随 Node.js 附带）。
- 搜索和安装无需 API Key。
- 登录（`npx --yes clawhub@latest login`）仅在发布技能时需要。
- **`--workdir ~/.copala/workspace` 是关键参数**——缺少它，技能会安装到当前目录而非 copala 工作区。
- 安装完成后，提醒用户开启新会话以加载技能。
