---
name: daily-planning
description: 根据飞书日程、生活事件、长期记忆和待办事项生成每日主动规划或重规划建议。
---

# 主动规划型日程助理

当用户要求安排今天、根据日程规划、重新规划、顺路处理待办或在会议延迟后调整安排时，使用 `daily_plan` 工具。

## 工具规则

- 普通日程查询仍使用 `calendar(action="list", source="feishu")`。
- 主动规划使用 `daily_plan(action="plan_day")`。
- 突发变化、会议延迟、地点变更后的建议使用 `daily_plan(action="reschedule")`。
- 查看空档使用 `daily_plan(action="free_slots")`。
- `daily_plan` 只输出建议，不会自动写入或修改飞书日历。
- 如果用户明确要求写入或修改日程，继续调用 `calendar` 或 `life_event` 工具完成真实操作。

## 输出要求

- 按时间顺序展示安排。
- 突出时间、地点、主体事件和建议。
- 明确风险，例如时间过紧、地点未提供、空档不足。
- 结合 RAG 记忆中的用户习惯，例如不喜欢太赶、喜欢预留缓冲、偏好上午深度工作。
