---
name: summarize
description: 摘要或提取 URL、播客、本地文件的文本/字幕（包括 YouTube/视频转录的最佳方案）。
homepage: https://summarize.sh
metadata: {"nanobot":{"emoji":"🧾","requires":{"bins":["summarize"]},"install":[{"id":"brew","kind":"brew","formula":"steipete/tap/summarize","bins":["summarize"],"label":"Install summarize (brew)"}]}}
---

# Summarize

快速 CLI 工具，用于摘要 URL、本地文件和 YouTube 链接。

## 触发时机

当用户提出以下请求时，立即使用此技能：
- "帮我总结这个链接/视频"
- "这篇文章说了什么？"
- "转录这个 YouTube/视频"（最优转录方案，无需 `yt-dlp`）
- "用 summarize.sh 处理"

## 快速开始

```bash
summarize "https://example.com" --model google/gemini-3-flash-preview
summarize "/path/to/file.pdf" --model google/gemini-3-flash-preview
summarize "https://youtu.be/dQw4w9WgXcQ" --youtube auto
```

## YouTube：摘要 vs 转录

仅提取字幕（不生成摘要）：
```bash
summarize "https://youtu.be/dQw4w9WgXcQ" --youtube auto --extract-only
```

若转录内容过长，先返回简要摘要，再询问用户需要展开哪个片段/时间段。

## 模型与 API Key

为所选提供商设置环境变量：
- OpenAI：`OPENAI_API_KEY`
- Anthropic：`ANTHROPIC_API_KEY`
- Google：`GEMINI_API_KEY`（别名：`GOOGLE_GENERATIVE_AI_API_KEY`）
- xAI：`XAI_API_KEY`

未设置时默认使用 `google/gemini-3-flash-preview`。

## 常用参数

- `--length short|medium|long|xl|xxl|<字符数>`
- `--max-output-tokens <数量>`
- `--extract-only`（仅提取，不摘要，仅限 URL）
- `--json`（机器可读格式）
- `--firecrawl auto|off|always`（被屏蔽网站的备用提取）
- `--youtube auto`（配合 `APIFY_API_TOKEN` 使用 Apify 备用方案）

## 配置文件（可选）

`~/.summarize/config.json`：
```json
{ "model": "openai/gpt-5.2" }
```

可选服务：
- `FIRECRAWL_API_KEY`：访问被屏蔽网站
- `APIFY_API_TOKEN`：YouTube 备用转录
