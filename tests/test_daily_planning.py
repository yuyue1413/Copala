from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

from copala.agent.tools.daily_planning import DailyPlanningTool
from copala.calendar.service import CalendarEvent
from copala.planning.daily import DailyPlanningService


class FakeCalendar:
    def __init__(self, events=None) -> None:
        self.events = events or []

    async def list_events_async(self, **kwargs):
        return self.events


class FakeLifeEvents:
    def __init__(self, rows=None) -> None:
        self.rows = rows or []

    def list_events(self, limit=80):
        return self.rows[:limit]


class FakeMemory:
    def retrieve(self, query, top_k=4):
        return [SimpleNamespace(item=SimpleNamespace(text="用户不喜欢太赶，喜欢日程之间留一点缓冲。"))]


def _config(**kwargs):
    base = {
        "enabled": True,
        "default_travel_minutes": 30,
        "default_buffer_minutes": 15,
        "day_start_hour": 8,
        "day_end_hour": 22,
        "auto_replan": True,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_daily_plan_places_movable_tasks_between_fixed_events() -> None:
    async def run() -> None:
        day = date(2026, 5, 20)
        events = [
            CalendarEvent("meeting", "上午会议", datetime(2026, 5, 20, 9, 0), datetime(2026, 5, 20, 10, 0), "飞书会议"),
            CalendarEvent("gym", "健身", datetime(2026, 5, 20, 17, 20), datetime(2026, 5, 20, 18, 20), "健身房"),
            CalendarEvent("meal", "晚上聚餐", datetime(2026, 5, 20, 19, 0), datetime(2026, 5, 20, 21, 0), "餐厅"),
        ]
        service = DailyPlanningService(
            config=_config(),
            calendar_service=FakeCalendar(events),
            life_events=FakeLifeEvents(),
            memory_store=None,
        )

        result = await service.plan_day("我今天还要取快递、买药、回复邮件，帮我安排一下", target_date=day)

        assert "上午会议" in result
        assert "健身" in result
        assert "取快递" in result
        assert "买药" in result
        assert "回复邮件" in result
        assert "说明：这是规划建议，不会自动修改飞书日历" in result

    asyncio.run(run())


def test_daily_plan_marks_tasks_delayed_when_no_room() -> None:
    async def run() -> None:
        day = date(2026, 5, 20)
        events = [
            CalendarEvent("busy", "全天会议", datetime(2026, 5, 20, 8, 0), datetime(2026, 5, 20, 21, 50), "飞书会议")
        ]
        service = DailyPlanningService(
            config=_config(),
            calendar_service=FakeCalendar(events),
            life_events=FakeLifeEvents(),
            memory_store=None,
        )

        result = await service.plan_day("我今天还要取快递、买药、回复邮件，帮我安排一下", target_date=day)

        assert "建议延期" in result
        assert "取快递" in result
        assert "空档不足" in result

    asyncio.run(run())


def test_daily_plan_uses_memory_to_increase_buffer_note() -> None:
    async def run() -> None:
        day = date(2026, 5, 20)
        service = DailyPlanningService(
            config=_config(),
            calendar_service=FakeCalendar(),
            life_events=FakeLifeEvents(),
            memory_store=FakeMemory(),
        )

        result = await service.plan_day("今天帮我安排取快递", target_date=day)

        assert "个性化依据" in result
        assert "增加缓冲时间" in result

    asyncio.run(run())


def test_daily_plan_tool_returns_formatted_plan() -> None:
    async def run() -> None:
        day = date(2026, 5, 20)
        service = DailyPlanningService(
            config=_config(),
            calendar_service=FakeCalendar(),
            life_events=FakeLifeEvents(),
            memory_store=None,
        )
        tool = DailyPlanningTool(service)

        result = await tool.execute(
            action="plan_day",
            request="我今天还要取快递",
            date=day.isoformat(),
        )

        assert "主动规划" in result
        assert "取快递" in result

    asyncio.run(run())
