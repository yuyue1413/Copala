from __future__ import annotations

from datetime import date, timedelta

from copala.agent.loop import AgentLoop


def test_readonly_calendar_query_intent() -> None:
    assert AgentLoop._looks_like_readonly_calendar_query("查看我今天的飞书日程")
    assert AgentLoop._looks_like_readonly_calendar_query("看看明天日程")
    assert AgentLoop._looks_like_readonly_calendar_query("列出本周飞书日历")


def test_calendar_query_intent_excludes_planning_and_writes() -> None:
    assert not AgentLoop._looks_like_readonly_calendar_query("帮我根据今天日程安排写代码时间")
    assert not AgentLoop._looks_like_readonly_calendar_query("创建明天上午10点的一小时项目同步会")
    assert not AgentLoop._looks_like_readonly_calendar_query("把明天的会议改到下午2点")
    assert not AgentLoop._looks_like_readonly_calendar_query("检查一下今日日程冲突会不会影响我的安排")


def test_daily_planning_intent() -> None:
    assert AgentLoop._looks_like_daily_planning_query("帮我根据今天的日程规划一下")
    assert AgentLoop._looks_like_daily_planning_query("我今天还要取快递、买药、回复邮件，帮我安排一下")
    assert AgentLoop._looks_like_replanning_query("下午会议延迟到4点了，重新规划一下今天")
    assert not AgentLoop._looks_like_daily_planning_query("查看今天的飞书日程")


def test_smart_reminder_intent() -> None:
    assert AgentLoop._looks_like_smart_reminder_query("检查未来两天有没有天气或交通风险")
    assert AgentLoop._looks_like_smart_reminder_query("明天如果下雨会不会影响我的安排")
    assert AgentLoop._looks_like_smart_reminder_query("check weather risk")
    assert not AgentLoop._looks_like_smart_reminder_query("查看今天的飞书日程")


def test_outing_planning_intent() -> None:
    assert AgentLoop._looks_like_outing_planning_query("周末我想去杭州玩两天，预算控制一下")
    assert AgentLoop._looks_like_outing_planning_query("明天下午我想去万象城逛一下")
    assert AgentLoop._looks_like_outing_planning_query("plan a weekend trip to Hangzhou")
    assert not AgentLoop._looks_like_outing_planning_query("查看今天的飞书日程")


def test_calendar_query_range() -> None:
    today = date.today()

    start, end, label = AgentLoop._calendar_query_range("查看今天的日程")
    assert (start, end, label) == (today, today, "今天")

    start, end, label = AgentLoop._calendar_query_range("查看明天的日程")
    assert (start, end, label) == (today + timedelta(days=1), today + timedelta(days=1), "明天")

    start, end, label = AgentLoop._calendar_query_range("查看本周的日程")
    assert start == today
    assert end == today + timedelta(days=6 - today.weekday())
    assert label == "本周"


def test_calendar_status_intent() -> None:
    assert AgentLoop._looks_like_calendar_status_query("检查日历认证状态")
    assert AgentLoop._looks_like_calendar_status_query("查看飞书日历授权配置")
    assert not AgentLoop._looks_like_calendar_status_query("创建一个日历事件")
    assert not AgentLoop._looks_like_calendar_status_query("检查一下今日日程冲突会不会影响我的安排")
    assert AgentLoop._looks_like_smart_reminder_query("检查一下今日日程冲突会不会影响我的安排")
