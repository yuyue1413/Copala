from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from copala.bus.events import OutboundMessage
from copala.calendar.service import CalendarEvent
from copala.life_events.service import LifeEventExtractor, LifeEventManager


class FakeProvider:
    name = "feishu"
    default_calendar_id = "primary"

    def __init__(self) -> None:
        self.created = []
        self.updated = []

    async def create_event(self, calendar_id, draft):
        self.created.append((calendar_id, draft))
        return CalendarEvent(
            id="evt-local",
            title=draft.title,
            start=draft.start,
            end=draft.end,
            location=draft.location,
            source="feishu",
            platform="feishu",
            calendar_id=calendar_id,
            event_id="evt-created",
        )

    async def update_event(self, calendar_id, event_id, patch):
        self.updated.append((calendar_id, event_id, patch))
        return CalendarEvent(
            id="evt-local",
            title=patch.title,
            start=patch.start,
            end=patch.end,
            location=patch.location,
            source="feishu",
            platform="feishu",
            calendar_id=calendar_id,
            event_id=event_id,
        )


class FakeCalendar:
    def __init__(self, events=None) -> None:
        self.provider = FakeProvider()
        self.events = events or []

    def _provider(self, platform):
        assert platform == "feishu"
        return self.provider

    async def list_events_async(self, **kwargs):
        return self.events


class FakeCron:
    def __init__(self) -> None:
        self.jobs = []

    def add_job(self, **kwargs):
        self.jobs.append(kwargs)
        return SimpleNamespace(id=f"job-{len(self.jobs)}")


def _config(**kwargs):
    base = {
        "enabled": True,
        "notify_channel": "qq",
        "notify_chat_id": "qq-user",
        "calendar_write_mode": "direct",
        "min_calendar_confidence": 0.82,
        "auto_create_todo": True,
        "auto_create_reminder": True,
        "calendar_lookahead_hours": 24,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_life_event_extractor_ignores_non_actionable_mentions() -> None:
    extractor = LifeEventExtractor()
    samples = (
        "请解释一下会议纪要应该怎么写",
        "我不参加2030-10-01 10:00的会议",
        "聚餐已经取消，不需要记录",
        "取消考虑2030-10-02 11:00的酒店",
        "航班信息只是示例，不是我的行程",
        "去年参加过一次项目会议",
    )

    for index, text in enumerate(samples):
        assert extractor.extract(text, source="test", source_ref=f"negative:{index}") == []


def test_life_event_creates_formatted_feishu_event_and_notifies(tmp_path: Path) -> None:
    async def run() -> None:
        sent: list[OutboundMessage] = []
        calendar = FakeCalendar()
        manager = LifeEventManager(
            workspace=tmp_path,
            config=_config(),
            calendar_service=calendar,
            cron_service=FakeCron(),
            send_callback=lambda msg: _capture(sent, msg),
        )

        await manager.process_text(
            "主题: 字节跳动后端岗位二面\n面试时间：2026年5月20日 下午2点\n地点：腾讯会议 123\n联系人：HR 小李",
            source="email",
            source_ref="email:1",
        )

        assert len(calendar.provider.created) == 1
        _, draft = calendar.provider.created[0]
        assert "【面试】" in draft.title
        assert draft.start == datetime(2026, 5, 20, 14, 0)
        assert draft.end == datetime(2026, 5, 20, 15, 0)
        assert "📨 来源：email" in draft.description
        assert "💡 建议：" in draft.description
        assert sent and "已写入飞书日历" in sent[0].content

    asyncio.run(run())


def test_life_event_missing_time_does_not_write_calendar(tmp_path: Path) -> None:
    async def run() -> None:
        sent: list[OutboundMessage] = []
        calendar = FakeCalendar()
        manager = LifeEventManager(
            workspace=tmp_path,
            config=_config(),
            calendar_service=calendar,
            send_callback=lambda msg: _capture(sent, msg),
        )

        await manager.process_text(
            "主题: 项目同步会\n地点：飞书会议",
            source="email",
            source_ref="email:2",
        )

        assert calendar.provider.created == []
        assert sent and "缺少开始时间" in sent[0].content

    asyncio.run(run())


def test_life_event_dedupes_same_source_ref(tmp_path: Path) -> None:
    async def run() -> None:
        calendar = FakeCalendar()
        manager = LifeEventManager(
            workspace=tmp_path,
            config=_config(),
            calendar_service=calendar,
            send_callback=lambda msg: _capture([], msg),
        )
        text = "主题: 项目同步会\n会议时间：2026年5月21日 10点\n地点：飞书会议"

        await manager.process_text(text, source="email", source_ref="email:3")
        await manager.process_text(text, source="email", source_ref="email:3")

        assert len(calendar.provider.created) == 1

    asyncio.run(run())


def test_life_event_uses_current_chat_channel_for_notifications(tmp_path: Path) -> None:
    async def run() -> None:
        sent: list[OutboundMessage] = []
        calendar = FakeCalendar()
        manager = LifeEventManager(
            workspace=tmp_path,
            config=_config(notify_channel="qq", notify_chat_id="qq-user"),
            calendar_service=calendar,
            send_callback=lambda msg: _capture(sent, msg),
        )

        await manager.process_text(
            "明天晚上7点项目同步会 地点：飞书会议",
            source="feishu",
            source_ref="feishu:chat:1",
            notify_channel="feishu",
            notify_chat_id="chat-1",
        )

        assert sent
        assert sent[0].channel == "feishu"
        assert sent[0].chat_id == "chat-1"

    asyncio.run(run())


def test_life_event_can_return_messages_without_sending_notification(tmp_path: Path) -> None:
    async def run() -> None:
        sent: list[OutboundMessage] = []
        calendar = FakeCalendar()
        manager = LifeEventManager(
            workspace=tmp_path,
            config=_config(),
            calendar_service=calendar,
            send_callback=lambda msg: _capture(sent, msg),
        )

        result = await manager.process_text(
            "明天下午3点项目同步会，地点：飞书会议，联系人：张三",
            source="qq",
            source_ref="qq:direct:1",
            notify_channel="qq",
            notify_chat_id="chat-1",
            send_notifications=False,
        )

        assert result.messages
        assert "已写入飞书日历" in result.messages[0]
        assert sent == []

    asyncio.run(run())


def test_life_event_cleans_chat_title_and_compares_aware_conflicts(tmp_path: Path) -> None:
    async def run() -> None:
        local_tz = timezone(timedelta(hours=8))
        conflict_start = (datetime.now(local_tz) + timedelta(days=1)).replace(
            hour=14, minute=30, second=0, microsecond=0
        )
        existing = CalendarEvent(
            id="existing",
            title="Existing",
            start=conflict_start,
            end=conflict_start + timedelta(hours=1),
            source="feishu",
            platform="feishu",
            calendar_id="primary",
            event_id="existing-event",
        )
        calendar = FakeCalendar(events=[existing])
        manager = LifeEventManager(
            workspace=tmp_path,
            config=_config(),
            calendar_service=calendar,
            send_callback=lambda msg: _capture([], msg),
        )

        result = await manager.process_text(
            "明天下午3点项目同步会，地点：飞书会议，联系人：张三",
            source="qq",
            source_ref="qq:title:1",
            send_notifications=False,
        )

        _, draft = calendar.provider.created[0]
        assert draft.title == "📌【会议】项目同步会"
        assert "地点" not in draft.title
        assert "联系人" not in draft.title
        assert result.messages and "⚠️ 注意" in result.messages[0]

    asyncio.run(run())


def test_personal_activity_with_time_creates_calendar_event(tmp_path: Path) -> None:
    async def run() -> None:
        calendar = FakeCalendar()
        manager = LifeEventManager(
            workspace=tmp_path,
            config=_config(),
            calendar_service=calendar,
            send_callback=lambda msg: _capture([], msg),
        )

        result = await manager.process_text(
            "后天18点我要去打乒乓球",
            source="qq",
            source_ref="qq:sport:1",
            send_notifications=False,
        )

        assert result.messages
        assert "已写入飞书日历" in result.messages[0]
        _, draft = calendar.provider.created[0]
        assert draft.title == "🏓【活动】打乒乓球"
        assert draft.start.hour == 18
        assert draft.end.hour == 20
        assert "💡 建议：建议提前确认路线和装备" in draft.description

    asyncio.run(run())


def test_calendar_patrol_handles_timezone_aware_events(tmp_path: Path) -> None:
    async def run() -> None:
        sent: list[OutboundMessage] = []
        local_tz = timezone(timedelta(hours=8))
        event = CalendarEvent(
            id="aware",
            title="📌【会议】项目同步会",
            start=datetime.now(local_tz) + timedelta(minutes=30),
            end=datetime.now(local_tz) + timedelta(minutes=90),
            location="飞书会议",
            source="feishu",
            platform="feishu",
            calendar_id="primary",
            event_id="aware-event",
        )
        manager = LifeEventManager(
            workspace=tmp_path,
            config=_config(),
            calendar_service=FakeCalendar(events=[event]),
            send_callback=lambda msg: _capture(sent, msg),
        )

        result = await manager.patrol_calendar()

        assert "日程" in result
        assert sent

    asyncio.run(run())


def test_life_event_exposes_planning_fields(tmp_path: Path) -> None:
    async def run() -> None:
        manager = LifeEventManager(
            workspace=tmp_path,
            config=_config(),
            calendar_service=FakeCalendar(),
            send_callback=lambda msg: _capture([], msg),
        )

        await manager.process_text(
            "2026-05-19 15:00 project meeting, location: Feishu",
            source="qq",
            source_ref="qq:planning-fields:1",
            send_notifications=False,
        )

        stored = manager.list_events(limit=1)[0]
        assert stored["duration_minutes"] == 60
        assert stored["priority"] == 8
        assert stored["fixed_time"] is True
        assert stored["movable"] is False

    asyncio.run(run())


def test_life_event_exposes_smart_reminder_fields(tmp_path: Path) -> None:
    async def run() -> None:
        manager = LifeEventManager(
            workspace=tmp_path,
            config=_config(),
            calendar_service=FakeCalendar(),
            send_callback=lambda msg: _capture([], msg),
        )

        await manager.process_text(
            "2026-05-19 15:00 outdoor meeting at park, location: People Park",
            source="qq",
            source_ref="qq:smart-fields:1",
            send_notifications=False,
        )

        stored = manager.list_events(limit=1)[0]
        assert stored["weather_sensitive"] is True
        assert stored["outdoor"] is True
        assert "weather" in stored["risk_tags"]
        assert stored["reminder_policy"]

    asyncio.run(run())


async def _capture(target: list[OutboundMessage], msg: OutboundMessage) -> None:
    target.append(msg)
