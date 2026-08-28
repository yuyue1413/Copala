"""Compatibility and priority tests for the centralized Agent router."""

from copala.agent import AgentLoop
from copala.agent.routing import (
    DIRECT_ROUTES,
    ROUTE_PRIORITY,
    RouteName,
    calendar_query_range,
    looks_like_calendar_status_query,
    looks_like_daily_planning_query,
    looks_like_outing_planning_query,
    looks_like_readonly_calendar_query,
    looks_like_replanning_query,
    looks_like_smart_reminder_query,
)


def test_route_priority_is_explicit_and_complete():
    assert tuple(route.name for route in DIRECT_ROUTES) == (
        RouteName.CALENDAR,
        RouteName.OUTING,
        RouteName.SMART_REMINDER,
        RouteName.DAILY_PLAN,
    )
    assert ROUTE_PRIORITY == (
        RouteName.CALENDAR,
        RouteName.OUTING,
        RouteName.SMART_REMINDER,
        RouteName.DAILY_PLAN,
        RouteName.LIFE_EVENT,
        RouteName.LLM,
    )


def test_conflicting_outing_and_weather_intent_prefers_outing():
    text = "周末去杭州旅行，帮我看看天气和路线有没有风险"
    assert looks_like_outing_planning_query(text)
    assert not looks_like_smart_reminder_query(text)


def test_calendar_write_request_is_not_intercepted_by_read_fast_path():
    text = "把明天的会议创建到飞书日历"
    assert not looks_like_readonly_calendar_query(text)
    assert not looks_like_calendar_status_query(text)


def test_daily_plan_and_calendar_query_are_disjoint_for_plan_request():
    text = "根据明天的日程重新规划一下明天的安排"
    assert looks_like_daily_planning_query(text)
    assert not looks_like_readonly_calendar_query(text)


def test_agentloop_static_routing_wrappers_remain_compatible():
    samples = (
        (AgentLoop._looks_like_outing_planning_query, looks_like_outing_planning_query, "周末去苏州玩"),
        (AgentLoop._looks_like_smart_reminder_query, looks_like_smart_reminder_query, "检查明天会不会下雨"),
        (AgentLoop._looks_like_daily_planning_query, looks_like_daily_planning_query, "帮我规划今天的日程"),
        (AgentLoop._looks_like_replanning_query, looks_like_replanning_query, "下午会议推迟了"),
        (AgentLoop._looks_like_calendar_status_query, looks_like_calendar_status_query, "检查飞书日历认证状态"),
        (AgentLoop._looks_like_readonly_calendar_query, looks_like_readonly_calendar_query, "查看明天的飞书日程"),
    )
    for wrapper, implementation, text in samples:
        assert wrapper(text) == implementation(text)
    assert AgentLoop._calendar_query_range("查看明天日程") == calendar_query_range("查看明天日程")


def test_agentloop_output_guard_wrappers_remain_compatible():
    assert AgentLoop._sanitize_user_facing_content("Error: quota exceeded") == (
        "我这边的模型服务暂时额度不足或请求过于频繁，稍后再试一下就好。"
    )
    assert AgentLoop._looks_like_unverified_calendar_write_claim("日程已创建", [])
    assert not AgentLoop._looks_like_unverified_calendar_write_claim("日程已创建", ["calendar"])
