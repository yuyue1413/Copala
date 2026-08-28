from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

from copala.agent.tools.outing_planning import OutingPlanningTool
from copala.calendar.service import CalendarEvent
from copala.context_awareness.amap import AmapRouteUnavailable
from copala.planning.outing import OutingPlanningService


class FakeCalendar:
    def __init__(self, events=None) -> None:
        self.events = events or []

    async def list_events_async(self, **kwargs):
        return self.events


class FakeMemory:
    def __init__(self) -> None:
        self.saved = []

    def retrieve(self, query, top_k=6):
        return [SimpleNamespace(item=SimpleNamespace(text="用户不喜欢太赶，偏好轻松行程和预算可控。"))]

    def upsert(self, item):
        self.saved.append(item)


class FakeAmap:
    async def geocode(self, address: str, city: str = ""):
        return {"location": f"{len(address)}.0,31.0", "city": city or "上海", "adcode": "310000"}

    async def weather(self, city_or_adcode: str, extensions: str = "base"):
        return {"city": "上海", "weather": "晴", "temperature": "25"}

    async def driving(self, origin: str, destination: str, strategy: int = 10):
        return {"mode": "driving", "duration_seconds": 3600, "distance_meters": 52000}

    async def transit(self, origin: str, destination: str, city: str, cityd: str | None = None):
        return {"mode": "transit", "duration_seconds": 4800, "distance_meters": 50000}


class FailingRouteAmap(FakeAmap):
    async def driving(self, origin: str, destination: str, strategy: int = 10):
        raise AmapRouteUnavailable("no route")


class FakeWeb:
    async def execute(self, query: str, count: int | None = None):
        return "Results for guide\n1. 杭州攻略\n   https://example.com\n   西湖、餐饮、避坑信息"


def _config(**kwargs):
    base = {
        "enabled": True,
        "default_search_count": 5,
        "default_budget_currency": "CNY",
        "guide_sites": ["xiaohongshu.com", "ctrip.com", "mafengwo.cn", "dianping.com"],
        "save_plan_to_memory": True,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _amap_config(**kwargs):
    base = {
        "api_key": "key",
        "api_base": "https://restapi.amap.com",
        "default_city": "上海",
        "default_origin": "上海虹桥站",
        "route_mode": "driving",
        "timeout_seconds": 8,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _service(calendar=None, memory=None, amap=None, amap_config=None):
    service = OutingPlanningService(
        config=_config(),
        calendar_service=calendar or FakeCalendar(),
        memory_store=memory,
        amap_config=amap_config or _amap_config(),
        web_api_key="key",
    )
    service.amap = amap or FakeAmap()
    service.web = FakeWeb()
    return service


def test_plan_trip_uses_memory_calendar_amap_web_and_saves_plan() -> None:
    async def run() -> None:
        memory = FakeMemory()
        event = CalendarEvent(
            id="meeting",
            title="周六上午会议",
            start=datetime(2026, 5, 23, 9, 0),
            end=datetime(2026, 5, 23, 10, 0),
            location="飞书会议",
            source="feishu",
            platform="feishu",
        )
        service = _service(calendar=FakeCalendar([event]), memory=memory)

        result = await service.plan_trip(destination="杭州", time_range="周末两天", budget="1000元")

        assert "多日旅行智能规划" in result
        assert "杭州" in result
        assert "周六上午会议" in result
        assert "用户不喜欢太赶" in result
        assert "杭州攻略" in result
        assert "是否需要我把出发时间" in result
        assert memory.saved and memory.saved[0].type == "plan_artifact"

    asyncio.run(run())


def test_plan_outing_for_mall_includes_route_and_reminder_prompt() -> None:
    async def run() -> None:
        service = _service()

        result = await service.plan_outing(destination="万象城", time_range="明天下午", budget="300元")

        assert "临时外出智能规划" in result
        assert "万象城" in result
        assert "路线：driving 约 60 分钟" in result
        assert "预算：300元" in result

    asyncio.run(run())


def test_missing_origin_degrades_without_failure() -> None:
    async def run() -> None:
        service = _service(amap_config=_amap_config(default_origin=""))

        result = await service.plan_outing(destination="万象城", time_range="明天")

        assert "未配置出发地" in result
        assert "是否需要我把出发时间" in result

    asyncio.run(run())


def test_route_unavailable_does_not_break_plan() -> None:
    async def run() -> None:
        service = _service(amap=FailingRouteAmap())

        result = await service.plan_outing(destination="很远的地方", time_range="明天")

        assert "高德暂时无法计算该路线" in result
        assert "建议行程" in result

    asyncio.run(run())


def test_outing_tool_returns_plan() -> None:
    async def run() -> None:
        tool = OutingPlanningTool(_service())

        result = await tool.execute(action="plan_trip", destination="苏州", time_range="周末")

        assert "苏州" in result
        assert "智能规划" in result

    asyncio.run(run())
