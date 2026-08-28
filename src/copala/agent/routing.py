"""Deterministic domain-route definitions used before the LLM tool loop."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Final


class RouteName(StrEnum):
    """Stable names for the ordered Agent decision stages."""

    CALENDAR = "calendar_query"
    OUTING = "outing_plan"
    SMART_REMINDER = "smart_reminder"
    DAILY_PLAN = "daily_plan"
    LIFE_EVENT = "life_event"
    LLM = "llm_slow"


@dataclass(frozen=True, slots=True)
class DirectRouteSpec:
    """A terminal domain route implemented by an AgentLoop handler."""

    name: RouteName
    handler_name: str


DIRECT_ROUTES: Final[tuple[DirectRouteSpec, ...]] = (
    DirectRouteSpec(RouteName.CALENDAR, "_direct_calendar_query"),
    DirectRouteSpec(RouteName.OUTING, "_direct_outing_planning"),
    DirectRouteSpec(RouteName.SMART_REMINDER, "_direct_smart_reminder"),
    DirectRouteSpec(RouteName.DAILY_PLAN, "_direct_daily_planning"),
)

ROUTE_PRIORITY: Final[tuple[RouteName, ...]] = (
    *(route.name for route in DIRECT_ROUTES),
    RouteName.LIFE_EVENT,
    RouteName.LLM,
)


def looks_like_outing_planning_query(text: str) -> bool:
    lowered = text.lower()
    outing_words = (
        "旅行", "旅游", "出游", "周末", "周末游", "周末去", "去商场", "逛商场",
        "临时外出", "前往", "路线规划", "行程", "玩", "逛",
    )
    destination_words = (
        "去", "到", "前往", "商场", "杭州", "苏州", "上海", "南京", "北京",
        "成都", "广州", "深圳",
    )
    write_words = ("写入日历", "创建日程", "加入日历", "改到")
    if any(word in text for word in write_words):
        return False
    has_destination = any(word in text for word in destination_words) or any(
        word in lowered for word in ("to ", "hangzhou", "suzhou", "mall")
    )
    return (
        any(word in text for word in outing_words)
        or any(word in lowered for word in ("trip", "travel", "outing", "mall", "itinerary"))
    ) and has_destination


def looks_like_smart_reminder_query(text: str) -> bool:
    lowered = text.lower()
    if looks_like_outing_planning_query(text):
        return False
    risk_words = ("风险", "预警", "提醒", "下雨", "天气", "交通", "堵车", "路线", "来得及", "冲突")
    action_words = ("检查", "看看", "有没有", "会不会", "影响", "调整", "预警")
    return (
        any(word in text for word in risk_words)
        or any(word in lowered for word in ("risk", "weather", "traffic"))
    ) and (
        any(word in text for word in action_words)
        or any(word in lowered for word in ("check", "warn", "alert"))
    )


def looks_like_daily_planning_query(text: str) -> bool:
    lowered = text.lower()
    plan_words = ("安排", "规划", "计划", "重新安排", "重新规划", "最优安排", "顺路", "空档")
    schedule_words = ("今天", "明天", "后天", "日程", "会议", "待办", "快递", "买药", "回复邮件")
    write_words = ("写入", "创建", "修改飞书", "加入日历", "记到日历")
    if any(word in text for word in write_words):
        return False
    return (
        any(word in text for word in plan_words)
        or any(word in lowered for word in ("plan", "schedule", "reschedule"))
    ) and any(word in text for word in schedule_words)


def looks_like_replanning_query(text: str) -> bool:
    return any(word in text for word in ("重新安排", "重新规划", "改期", "延迟", "推迟", "提前", "变更"))


def looks_like_calendar_status_query(text: str) -> bool:
    lowered = text.lower()
    has_calendar = any(word in text for word in ("日历", "日程", "飞书")) or "calendar" in lowered
    has_status = any(word in text for word in ("认证", "授权", "状态", "配置", "检查")) or "status" in lowered
    has_risk_intent = any(
        word in text for word in ("风险", "预警", "提醒", "天气", "交通", "堵车", "路线", "来得及", "冲突")
    )
    return (
        has_calendar
        and has_status
        and not has_risk_intent
        and not any(word in text for word in ("创建", "修改", "更改", "删除"))
    )


def looks_like_readonly_calendar_query(text: str) -> bool:
    lowered = text.lower()
    calendar_words = ("日程", "飞书日程", "飞书日历", "calendar", "schedule")
    date_words = ("今天", "今日", "明天", "本周", "这周", "today", "tomorrow", "week")
    query_words = ("查", "查看", "查询", "看看", "看下", "列出", "有什么", "有没有", "list", "show")
    write_or_plan_words = (
        "创建", "新建", "修改", "更改", "移动", "移到", "删除", "取消", "提醒", "规划",
        "建议", "根据", "安排写", "安排一个", "风险", "预警", "天气", "交通", "堵车",
        "路线", "来得及", "冲突",
    )
    if any(word in text for word in write_or_plan_words):
        return False
    has_calendar = any(word in text for word in calendar_words) or any(
        word in lowered for word in calendar_words
    )
    has_date = any(word in text for word in date_words) or any(word in lowered for word in date_words)
    has_query = any(word in text for word in query_words) or any(word in lowered for word in query_words)
    return has_calendar and has_date and (has_query or len(text) <= 20)


def calendar_query_range(text: str) -> tuple[date, date, str]:
    today = date.today()
    lowered = text.lower()
    if "明天" in text or "tomorrow" in lowered:
        tomorrow = today + timedelta(days=1)
        return tomorrow, tomorrow, "明天"
    if "本周" in text or "这周" in text or "week" in lowered:
        sunday = today + timedelta(days=6 - today.weekday())
        return today, sunday, "本周"
    return today, today, "今天"
