"""Calendar tool for local and enterprise schedules."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.calendar.formatting import calendar_suggestion, format_calendar_events
from nanobot.calendar.service import CalendarService


class CalendarTool(Tool):
    """Read, ingest and safely write calendar data."""

    def __init__(self, service: CalendarService):
        self._service = service

    @property
    def name(self) -> str:
        return "calendar"

    @property
    def description(self) -> str:
        return (
            "Read/search/ingest Feishu private calendar events by default. "
            "For ordinary schedule questions, use action='list' with source='feishu'. "
            "Use action='status' to diagnose calendar authentication without exposing secrets. "
            "For writes, create draft_create/draft_update first, then confirm only after user approval."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list", "search", "ingest", "status",
                        "list_calendars",
                        "draft_create", "draft_update", "confirm", "discard", "freebusy",
                    ],
                },
                "source": {"type": "string", "enum": ["feishu", "local", "all"]},
                "platform": {"type": "string", "enum": ["feishu"]},
                "start_date": {"type": "string", "description": "YYYY-MM-DD for list"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD for list"},
                "query": {"type": "string", "description": "Search query"},
                "days": {"type": "integer", "description": "Lookahead days for search/list"},
                "title": {"type": "string"},
                "start": {"type": "string", "description": "ISO datetime for create/freebusy"},
                "end": {"type": "string", "description": "ISO datetime for create/freebusy"},
                "calendar_id": {"type": "string"},
                "event_id": {"type": "string"},
                "draft_id": {"type": "string"},
                "attendees": {"type": "array", "items": {"type": "object"}},
                "location": {"type": "string"},
                "description": {"type": "string"},
                "fields": {"type": "object", "description": "Fields to update"},
                "user_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        source: str = "feishu",
        platform: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        query: str | None = None,
        days: int | None = None,
        title: str | None = None,
        start: str | None = None,
        end: str | None = None,
        calendar_id: str | None = None,
        event_id: str | None = None,
        draft_id: str | None = None,
        attendees: list[dict[str, Any]] | None = None,
        location: str = "",
        description: str = "",
        fields: dict[str, Any] | None = None,
        user_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            if action == "status":
                return self._format_status(self._service.auth_status())
            if action == "ingest":
                count = await self._service.ingest_async(source=source)
                return f"Ingested {count} calendar memory item(s) from {source}."
            if action == "search":
                events = await self._service.search_async(query or "", days=days, source=source)
                return self._format(events)
            if action == "list":
                start_day = self._parse_date(start_date) if start_date else date.today()
                end_day = self._parse_date(end_date) if end_date else start_day + timedelta(days=days or 7)
                events = await self._service.list_events_async(start_date=start_day, end_date=end_day, source=source)
                return self._format(events)
            if action == "list_calendars":
                calendars = await self._service.list_calendars_async(platform=platform)
                if not calendars:
                    return "No enterprise calendars found."
                lines = ["Enterprise calendars:"]
                for calendar in calendars:
                    primary = " primary" if calendar.is_primary else ""
                    lines.append(f"- [{calendar.platform}] {calendar.id}{primary}: {calendar.name}")
                return "\n".join(lines)
            if action == "draft_create":
                if not title or not start:
                    return "draft_create requires title and start."
                draft = await self._service.draft_create(
                    platform=platform,
                    title=title,
                    start=self._parse_datetime(start),
                    end=self._parse_datetime(end) if end else None,
                    attendees=attendees or [],
                    location=location,
                    description=description,
                    calendar_id=calendar_id,
                )
                return self._format_draft(draft)
            if action == "draft_update":
                if not calendar_id or not event_id:
                    return "draft_update requires calendar_id and event_id."
                draft = await self._service.draft_update(
                    platform=platform,
                    calendar_id=calendar_id,
                    event_id=event_id,
                    fields=fields or {},
                )
                return self._format_draft(draft)
            if action == "confirm":
                if not draft_id:
                    return "confirm requires draft_id."
                draft = await self._service.confirm_draft(draft_id)
                return self._format_draft(draft)
            if action == "discard":
                if not draft_id:
                    return "discard requires draft_id."
                draft = self._service.discard_draft(draft_id)
                return self._format_draft(draft)
            if action == "freebusy":
                if not start or not end:
                    return "freebusy requires start and end."
                slots = await self._service.freebusy(
                    platform=platform,
                    start=self._parse_datetime(start),
                    end=self._parse_datetime(end),
                    user_ids=user_ids,
                )
                if not slots:
                    return "No busy slots found."
                lines = ["Busy slots:"]
                for slot in slots[:50]:
                    user = f" user={slot.user_id}" if slot.user_id else ""
                    lines.append(f"- [{slot.platform}]{user} {slot.start.isoformat()} -> {slot.end.isoformat()}")
                return "\n".join(lines)
        except Exception as exc:
            return f"Calendar {action} failed: {exc}"
        return f"Unknown action: {action}"

    @staticmethod
    def _parse_date(raw: str) -> date:
        return date.fromisoformat(raw)

    @staticmethod
    def _parse_datetime(raw: str) -> datetime:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))

    @staticmethod
    def _format(events) -> str:
        return format_calendar_events(list(events), label="日程查询结果", limit=50)

    @staticmethod
    def _format_status(status: dict[str, Any]) -> str:
        lines = [
            "Calendar auth status:",
            f"- workspace: {status.get('workspace')}",
            f"- enterprise_enabled: {status.get('enterprise_enabled')}",
            f"- default_platform: {status.get('default_platform')}",
        ]
        providers = status.get("providers") or {}
        for name, item in providers.items():
            lines.append(f"- provider={name}")
            for key, value in item.items():
                lines.append(f"  - {key}: {value}")
        return "\n".join(lines)

    @staticmethod
    def _format_draft(draft: dict[str, Any]) -> str:
        payload = draft.get("payload") or {}
        result = draft.get("result") or {}
        lines = [
            f"Calendar draft {draft.get('draft_id')} status={draft.get('status')}",
            f"- platform: {draft.get('platform')}",
            f"- operation: {draft.get('operation')}",
            f"- calendar_id: {draft.get('calendar_id')}",
        ]
        if draft.get("event_id"):
            lines.append(f"- event_id: {draft.get('event_id')}")
        if payload:
            title = str(payload.get("title") or "")
            location = str(payload.get("location") or "未提供")
            time_range = str(payload.get("start") or "未定")
            if payload.get("end"):
                time_range += f" -> {payload.get('end')}"
            lines.append(f"- ⭐ title: {title}")
            lines.append(f"- 🕒 time: {time_range}")
            lines.append(f"- 📍 location: {location}")
            lines.append(f"- 💡 suggestion: {calendar_suggestion(title, location)}")
        if result:
            lines.append(f"- result_event_id: {result.get('event_id')}")
            lines.append(f"- result_title: {result.get('title')}")
        if draft.get("status") == "pending":
            lines.append("Ask the user to confirm before calling calendar(action='confirm').")
        return "\n".join(lines)
