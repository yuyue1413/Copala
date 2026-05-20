---
name: memory
description: 两层记忆系统，支持 grep 式历史检索。
always: true
---

# 记忆系统

## 文件结构

- `memory/MEMORY.md` — 长期事实（偏好、项目背景、人际关系）。**始终加载到上下文中。**
- `memory/HISTORY.md` — 只追加的事件日志。**不自动加载**，需用工具搜索。每条记录以 `[YYYY-MM-DD HH:MM]` 开头。

## 搜索历史

根据文件大小选择方式：

- 文件较小：用 `read_file` 读取后在内存中过滤
- 文件较大：用 `exec` 工具执行命令搜索

```bash
# Linux/macOS
grep -i "关键词" memory/HISTORY.md

# Windows
findstr /i "关键词" memory\HISTORY.md

# 跨平台 Python
python -c "from pathlib import Path; text=Path('memory/HISTORY.md').read_text(encoding='utf-8'); print('\n'.join([l for l in text.splitlines() if '关键词' in l.lower()][-20:]))"
```

## 何时更新 MEMORY.md

遇到重要事实时立即用 `edit_file` 或 `write_file` 写入：
- 用户偏好（如"我喜欢深色模式"）
- 项目背景（如"API 使用 OAuth2"）
- 人际关系（如"Alice 是项目负责人"）

## 自动整合

当会话增长过大时，历史对话会被自动摘要并追加到 HISTORY.md，长期事实提取到 MEMORY.md。无需手动管理。
