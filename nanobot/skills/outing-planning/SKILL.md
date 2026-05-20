# 出行/旅行智能规划

## 适用场景

当用户提出周末出游、城市旅行、去商场、临时外出、路线检查、预算控制、天气风险或日程冲突检查时，优先使用 `outing_plan` 工具。

## 工具规则

- 多日旅行使用 `outing_plan(action="plan_trip")`。
- 单次外出、商场、景点、临时出门使用 `outing_plan(action="plan_outing")`。
- 只检查路线耗时使用 `outing_plan(action="route_check")`。
- 只检查目标时间是否和飞书日程冲突使用 `outing_plan(action="calendar_fit")`。
- 不要凭空编造天气、路线、营业时间、攻略或预算依据；需要使用高德、飞书日历、RAG 和 web 搜索结果。
- Web 搜索支持 Tavily 和 Brave。若配置 `tools.web.search.provider="tavily"` 或 `apiKey` 以 `tvly-` 开头，会使用 Tavily Search。

## 输出要求

- 展示目的地、时间范围、预算、同行人和用户约束。
- 展示日程冲突、路线耗时、天气风险、外部攻略检索结果和备用方案。
- 多日旅行按天输出；单次外出突出推荐出发时间、路线、地点和返程建议。
- 结尾询问是否把出发提醒、天气提醒、订票/订酒店提醒或行程写入飞书日历。
- 默认只给建议，不自动修改飞书日历。
