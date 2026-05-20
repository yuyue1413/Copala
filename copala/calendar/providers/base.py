"""Shared enterprise calendar provider contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from copala.calendar.service import CalendarEvent


class EnterpriseCalendarError(RuntimeError):
    """Raised when an enterprise calendar provider cannot complete an operation."""


@dataclass
class CalendarAccount:
    """A calendar account/list entry returned by an enterprise platform."""

    id: str
    name: str
    platform: str
    is_primary: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class BusySlot:
    """A normalized busy slot."""

    start: datetime
    end: datetime
    platform: str
    user_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CalendarDraft:
    """Normalized payload used to create or update calendar events."""

    title: str
    start: datetime
    end: datetime | None = None
    location: str = ""
    description: str = ""
    attendees: list[dict[str, Any]] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)


class EnterpriseCalendarProvider(Protocol):
    """Minimal async interface implemented by enterprise calendar providers."""

    name: str
    default_calendar_id: str

    async def list_calendars(self) -> list[CalendarAccount]:
        ...

    async def list_events(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
    ) -> list[CalendarEvent]:
        ...

    async def create_event(self, calendar_id: str, draft: CalendarDraft) -> CalendarEvent:
        ...

    async def update_event(
        self,
        calendar_id: str,
        event_id: str,
        patch: CalendarDraft,
    ) -> CalendarEvent:
        ...

    async def get_freebusy(
        self,
        start: datetime,
        end: datetime,
        user_ids: list[str] | None = None,
    ) -> list[BusySlot]:
        ...
