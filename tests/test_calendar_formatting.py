from __future__ import annotations

from datetime import datetime

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.calendar import CalendarTool
from nanobot.calendar.formatting import format_calendar_events
from nanobot.calendar.service import CalendarEvent


def _event() -> CalendarEvent:
    return CalendarEvent(
        id="1",
        title="📌【会议】项目同步会",
        start=datetime(2026, 5, 19, 15, 0),
        end=datetime(2026, 5, 19, 16, 0),
        location="飞书会议",
    )


def test_calendar_query_format_highlights_core_fields() -> None:
    text = format_calendar_events([_event()], label="今天飞书日程")

    assert "⭐ 📌【会议】项目同步会" in text
    assert "🕒 时间：2026-05-19 15:00-16:00" in text
    assert "📍 地点：飞书会议" in text
    assert "💡 建议：" in text


def test_direct_calendar_query_uses_friendly_format() -> None:
    text = AgentLoop._format_direct_calendar_events([_event()], "今天")

    assert "🗓️ 今天飞书日程" in text
    assert "🕒 时间：" in text
    assert "📍 地点：" in text


def test_calendar_tool_format_uses_friendly_format() -> None:
    text = CalendarTool._format([_event()])

    assert "🗓️ 日程查询结果" in text
    assert "💡 建议：" in text
