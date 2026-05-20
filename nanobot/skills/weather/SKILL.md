---
name: weather
description: 获取当前天气和天气预报（无需 API Key）。
homepage: https://wttr.in/:help
metadata: {"nanobot":{"emoji":"🌤️","requires":{"bins":["curl"]}}}
---

# 天气查询

两个免费服务，无需 API Key。

## wttr.in（首选）

快速一行命令：
```bash
curl -s "wttr.in/Beijing?format=3"
# 输出：Beijing: ⛅️ +25°C
```

紧凑格式（含湿度和风速）：
```bash
curl -s "wttr.in/Shanghai?format=%l:+%c+%t+%h+%w"
```

完整三日预报：
```bash
curl -s "wttr.in/Guangzhou?T"
```

常用格式码：`%c` 天气图标 · `%t` 温度 · `%h` 湿度 · `%w` 风速 · `%l` 城市 · `%m` 月相

使用技巧：
- 空格用 `+` 代替：`wttr.in/Hong+Kong`
- 机场代码：`wttr.in/PEK`
- 单位：`?m`（公制）`?u`（英制）
- 今天：`?1` · 当前：`?0`

## Open-Meteo（备用，返回 JSON）

免费、无需 Key，适合程序化调用：
```bash
curl -s "https://api.open-meteo.com/v1/forecast?latitude=39.9&longitude=116.4&current_weather=true"
```

先获取城市经纬度，再查询。返回 JSON 格式，含温度、风速、天气代码。

文档：https://open-meteo.com/en/docs
