---
name: cron
description: 使用 cron 工具设置提醒和周期性任务。
---

# 定时任务（Cron）

使用内置 `cron` 工具来创建、查看、删除定时任务。

## 三种模式

1. **Reminder（提醒）** — 到时直接向用户发送消息
2. **Task（任务）** — 到时由 Agent 执行任务描述并回报结果
3. **One-time（一次性）** — 执行一次后自动删除

## 示例

固定提醒：
```
cron(action="add", message="该休息了！", every_seconds=1200)
```

动态任务（每次由 Agent 执行）：
```
cron(action="add", message="检查 GitHub 星标数并汇报", every_seconds=600)
```

一次性定时任务（用当前时间计算 ISO 时间）：
```
cron(action="add", message="提醒我开会", at="<ISO datetime>")
```

带时区的 Cron 表达式：
```
cron(action="add", message="每日早会", cron_expr="0 9 * * 1-5", tz="Asia/Shanghai")
```

查看 / 删除：
```
cron(action="list")
cron(action="remove", job_id="abc123")
```

## 时间表达式对照

| 用户说法 | 参数 |
|---------|------|
| 每 20 分钟 | `every_seconds: 1200` |
| 每小时 | `every_seconds: 3600` |
| 每天早上 8 点 | `cron_expr: "0 8 * * *"` |
| 工作日下午 5 点 | `cron_expr: "0 17 * * 1-5"` |
| 指定时间（一次性） | `at: ISO datetime 字符串` |

## 时区

`cron_expr` 配合 `tz` 参数可指定 IANA 时区（如 `Asia/Shanghai`）。不填 `tz` 则使用服务器本地时区。
