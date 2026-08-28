import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from copala.calendar.service import CalendarEvent
from copala.smart_reminders.service import SmartReminderService


class FakeAmap:
    async def geocode(self, address: str, city: str = ""):
        coordinates = {
            "Office A": "121.4700,31.2300",
            "Office B": "121.4800,31.2400",
        }
        return {
            "location": coordinates.get(address, "121.4900,31.2500"),
            "city": city or "Shanghai",
            "adcode": "310000",
        }

    async def weather(self, city_or_adcode: str, extensions: str = "base"):
        return {"city": "Shanghai", "weather": "rain", "temperature": "20"}

    async def driving(self, origin: str, destination: str, strategy: int = 10):
        return {"mode": "driving", "duration_seconds": 40 * 60, "distance_meters": 12000}

    async def transit(self, origin: str, destination: str, city: str, cityd: str | None = None):
        return {"mode": "transit", "duration_seconds": 50 * 60, "distance_meters": 12000}


class SunnyAmap(FakeAmap):
    async def weather(self, city_or_adcode: str, extensions: str = "base"):
        return {"city": "Shanghai", "weather": "sunny", "temperature": "24"}


class FakeLifeEvents:
    def __init__(self, rows):
        self.rows = rows

    def list_events(self, limit: int = 100):
        return self.rows[:limit]


class FakeCalendar:
    def __init__(self, events=None):
        self.events = events or []

    async def list_events_async(self, **kwargs):
        return self.events


def _config(**kwargs):
    amap = SimpleNamespace(
        enabled=True,
        api_key="key",
        api_base="https://restapi.amap.com",
        default_city="Shanghai",
        default_origin="",
        route_mode="driving",
        default_travel_buffer_minutes=15,
        timeout_seconds=8,
        outdoor_weather_keywords=["rain", "storm"],
    )
    base = {
        "enabled": True,
        "lookahead_hours": 48,
        "min_risk_confidence": 0.75,
        "notify_channel": "",
        "notify_chat_id": "",
        "amap": amap,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_outdoor_event_with_rain_triggers_weather_risk(tmp_path: Path) -> None:
    row = {
        "id": "life-1",
        "type": "personal_activity",
        "title": "Outdoor badminton",
        "start": (datetime.now() + timedelta(days=1)).isoformat(),
        "location": "People Park",
        "outdoor": True,
        "weather_sensitive": True,
        "source_ref": "qq:1",
    }
    service = SmartReminderService(
        workspace=tmp_path,
        config=_config(),
        calendar_service=FakeCalendar(),
        life_events=FakeLifeEvents([row]),
    )
    service.amap = FakeAmap()

    risks = asyncio.run(service.check_risks(days=2))

    assert any(risk.risk_type == "weather" for risk in risks)


def test_indoor_event_does_not_trigger_weather_risk(tmp_path: Path) -> None:
    row = {
        "id": "life-2",
        "type": "meeting",
        "title": "Indoor meeting",
        "start": (datetime.now() + timedelta(days=1)).isoformat(),
        "location": "Office",
        "outdoor": False,
        "weather_sensitive": False,
        "source_ref": "qq:2",
    }
    service = SmartReminderService(
        workspace=tmp_path,
        config=_config(),
        calendar_service=FakeCalendar(),
        life_events=FakeLifeEvents([row]),
    )
    service.amap = SunnyAmap()

    risks = asyncio.run(service.check_risks(days=2))

    assert not any(risk.risk_type == "weather" for risk in risks)


def test_tight_adjacent_calendar_events_trigger_route_risk(tmp_path: Path) -> None:
    day = date.today() + timedelta(days=1)
    events = [
        CalendarEvent(id="a", title="Meeting A", start=datetime.combine(day, datetime.min.time()).replace(hour=9), end=datetime.combine(day, datetime.min.time()).replace(hour=10), location="Office A", source="feishu", platform="feishu", event_id="a"),
        CalendarEvent(id="b", title="Meeting B", start=datetime.combine(day, datetime.min.time()).replace(hour=10, minute=30), end=datetime.combine(day, datetime.min.time()).replace(hour=11), location="Office B", source="feishu", platform="feishu", event_id="b"),
    ]
    service = SmartReminderService(
        workspace=tmp_path,
        config=_config(),
        calendar_service=FakeCalendar(events),
        life_events=FakeLifeEvents([]),
    )
    service.amap = FakeAmap()

    risks = asyncio.run(service.check_risks(days=2))

    assert any(risk.risk_type == "traffic" for risk in risks)


def test_virtual_locations_skip_route_risk(tmp_path: Path) -> None:
    day = date.today() + timedelta(days=1)
    events = [
        CalendarEvent(id="a", title="Online A", start=datetime.combine(day, datetime.min.time()).replace(hour=9), end=datetime.combine(day, datetime.min.time()).replace(hour=10), location="飞书会议", source="feishu", platform="feishu", event_id="a"),
        CalendarEvent(id="b", title="Online B", start=datetime.combine(day, datetime.min.time()).replace(hour=10, minute=20), end=datetime.combine(day, datetime.min.time()).replace(hour=11), location="腾讯会议", source="feishu", platform="feishu", event_id="b"),
    ]
    service = SmartReminderService(
        workspace=tmp_path,
        config=_config(),
        calendar_service=FakeCalendar(events),
        life_events=FakeLifeEvents([]),
    )
    service.amap = FakeAmap()

    risks = asyncio.run(service.check_risks(days=2))

    assert not any(risk.risk_type == "traffic" for risk in risks)


def test_bill_deadline_generates_risk(tmp_path: Path) -> None:
    row = {
        "id": "bill-1",
        "type": "bill",
        "title": "Credit card bill",
        "deadline": (datetime.now() + timedelta(hours=24)).isoformat(),
        "source_ref": "email:bill",
    }
    service = SmartReminderService(
        workspace=tmp_path,
        config=_config(),
        calendar_service=FakeCalendar(),
        life_events=FakeLifeEvents([row]),
    )

    risks = asyncio.run(service.check_risks(days=2))

    assert any(risk.risk_type == "deadline" for risk in risks)


def test_risk_store_dedupes_notified_risk(tmp_path: Path) -> None:
    service = SmartReminderService(
        workspace=tmp_path,
        config=_config(),
        calendar_service=FakeCalendar(),
        life_events=FakeLifeEvents([]),
    )
    risk = service._bill_risks(1)
    assert risk == []
    row = {
        "id": "bill-2",
        "type": "bill",
        "title": "Utility bill",
        "deadline": (datetime.now() + timedelta(hours=12)).isoformat(),
        "source_ref": "email:utility",
    }
    service.life_events = FakeLifeEvents([row])
    item = service._bill_risks(1)[0]

    assert not service.store.was_notified(item)
    service.store.mark_notified(item)
    assert service.store.was_notified(item)
