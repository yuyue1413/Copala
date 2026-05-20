from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path

import pytest

from copala.calendar.service import CalendarEvent, CalendarService


class FakeProvider:
    name = "feishu"
    default_calendar_id = "primary"

    def __init__(self) -> None:
        self.created = 0
        self.updated = 0

    async def list_calendars(self):
        return []

    async def list_events(self, calendar_id: str, start: datetime, end: datetime):
        return [
            CalendarEvent(
                id="fake-1",
                title="Enterprise Sync",
                start=datetime(2026, 5, 18, 10, 0),
                end=datetime(2026, 5, 18, 11, 0),
                source="feishu",
                platform="feishu",
                calendar_id=calendar_id,
                event_id="evt-1",
            )
        ]

    async def create_event(self, calendar_id: str, draft):
        self.created += 1
        return CalendarEvent(
            id="created-1",
            title=draft.title,
            start=draft.start,
            end=draft.end,
            source="feishu",
            platform="feishu",
            calendar_id=calendar_id,
            event_id="created-event",
        )

    async def update_event(self, calendar_id: str, event_id: str, patch):
        self.updated += 1
        return CalendarEvent(
            id="updated-1",
            title=patch.title or "Updated",
            start=patch.start,
            end=patch.end,
            source="feishu",
            platform="feishu",
            calendar_id=calendar_id,
            event_id=event_id,
        )

    async def get_freebusy(self, start: datetime, end: datetime, user_ids=None):
        return []


class CalendarConfig:
    enterprise_enabled = True
    default_platform = "feishu"
    write_mode = "draft"


def test_enterprise_list_and_ingest(tmp_path: Path) -> None:
    async def run() -> None:
        service = CalendarService(
            tmp_path,
            calendar_config=CalendarConfig(),
            providers={"feishu": FakeProvider()},
        )

        events = await service.list_events_async(
            start_date=date(2026, 5, 18),
            end_date=date(2026, 5, 18),
            source="feishu",
        )
        default_events = await service.list_events_async(
            start_date=date(2026, 5, 18),
            end_date=date(2026, 5, 18),
        )
        count = await service.ingest_async(source="feishu")
        status = service.auth_status()

        assert len(events) == 1
        assert len(default_events) == 1
        assert events[0].platform == "feishu"
        assert list(status["providers"]) == ["feishu"]
        assert count >= 1

    asyncio.run(run())


def test_draft_create_requires_confirm_before_provider_write(tmp_path: Path) -> None:
    async def run() -> None:
        provider = FakeProvider()
        service = CalendarService(
            tmp_path,
            calendar_config=CalendarConfig(),
            providers={"feishu": provider},
        )

        draft = await service.draft_create(
            platform="feishu",
            title="Project Sync",
            start=datetime(2026, 5, 18, 10, 0),
            end=datetime(2026, 5, 18, 11, 0),
        )

        assert draft["status"] == "pending"
        assert provider.created == 0

        confirmed = await service.confirm_draft(draft["draft_id"])

        assert confirmed["status"] == "confirmed"
        assert confirmed["result"]["event_id"] == "created-event"
        assert provider.created == 1

    asyncio.run(run())


def test_draft_update_and_discard(tmp_path: Path) -> None:
    async def run() -> None:
        provider = FakeProvider()
        service = CalendarService(
            tmp_path,
            calendar_config=CalendarConfig(),
            providers={"feishu": provider},
        )

        draft = await service.draft_update(
            platform="feishu",
            calendar_id="primary",
            event_id="evt-1",
            fields={"title": "Moved Sync", "start": "2026-05-18T14:00:00"},
        )
        discarded = service.discard_draft(draft["draft_id"])

        assert discarded["status"] == "discarded"
        assert provider.updated == 0
        with pytest.raises(ValueError):
            await service.confirm_draft(draft["draft_id"])

    asyncio.run(run())
