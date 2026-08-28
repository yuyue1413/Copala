"""Calendar service for local files and enterprise calendars."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass
class CalendarEvent:
    """A normalized calendar event from local or enterprise sources."""

    id: str
    title: str
    start: datetime
    end: datetime | None = None
    location: str = ""
    description: str = ""
    source: str = "calendar"
    platform: str | None = None
    calendar_id: str | None = None
    event_id: str | None = None
    organizer_id: str | None = None
    attendees: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_text(self) -> str:
        end = f" -> {self.end.isoformat()}" if self.end else ""
        location = f"\nLocation: {self.location}" if self.location else ""
        desc = f"\n{self.description}" if self.description else ""
        return f"{self.start.isoformat()}{end} | {self.title}{location}{desc}"


class CalendarService:
    """Read, search, write and ingest calendar data."""

    def __init__(
        self,
        workspace: Path,
        *,
        ics_files: list[str] | None = None,
        ics_urls: list[str] | None = None,
        local_json_files: list[str] | None = None,
        lookahead_days: int = 30,
        calendar_config: Any | None = None,
        feishu_config: Any | None = None,
        providers: dict[str, Any] | None = None,
    ) -> None:
        self.workspace = workspace
        self.ics_files = ics_files or []
        self.ics_urls = ics_urls or []
        self.local_json_files = local_json_files or []
        self.lookahead_days = lookahead_days
        self.calendar_config = calendar_config
        self.enterprise_enabled = bool(getattr(calendar_config, "enterprise_enabled", False))
        self.default_platform = str(getattr(calendar_config, "default_platform", "feishu") or "feishu")
        self.write_mode = str(getattr(calendar_config, "write_mode", "draft") or "draft")
        self.providers = providers or self._build_providers(calendar_config, feishu_config)

    def list_events(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        source: str = "local",
    ) -> list[CalendarEvent]:
        """List local events. Use list_events_async for enterprise sources."""
        if source not in ("local", "all"):
            return []
        start_date = start_date or date.today()
        end_date = end_date or (start_date + timedelta(days=self.lookahead_days))
        start_dt = datetime.combine(start_date, time.min)
        end_dt = datetime.combine(end_date, time.max)
        return sorted(
            [e for e in self._load_all() if start_dt <= self._naive(e.start) <= end_dt],
            key=lambda e: e.start,
        )

    async def list_events_async(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        source: str = "feishu",
    ) -> list[CalendarEvent]:
        """List local and/or enterprise events."""
        start_date = start_date or date.today()
        end_date = end_date or (start_date + timedelta(days=self.lookahead_days))
        events: list[CalendarEvent] = []
        if source in ("all", "local"):
            events.extend(self.list_events(start_date=start_date, end_date=end_date))
        if source in ("all", "feishu"):
            events.extend(await self._list_enterprise(source, start_date, end_date))
        return sorted(events, key=lambda e: e.start)

    def search(self, query: str, *, days: int | None = None, source: str = "local") -> list[CalendarEvent]:
        end = date.today() + timedelta(days=days or self.lookahead_days)
        terms = [t for t in re.split(r"\s+", query.lower()) if t]
        events = self.list_events(start_date=date.today() - timedelta(days=7), end_date=end, source=source)
        if not terms:
            return events
        return [
            e for e in events
            if all(t in f"{e.title} {e.location} {e.description}".lower() for t in terms)
        ]

    async def search_async(self, query: str, *, days: int | None = None, source: str = "feishu") -> list[CalendarEvent]:
        end = date.today() + timedelta(days=days or self.lookahead_days)
        terms = [t for t in re.split(r"\s+", query.lower()) if t]
        events = await self.list_events_async(
            start_date=date.today() - timedelta(days=7),
            end_date=end,
            source=source,
        )
        if not terms:
            return events
        return [
            e for e in events
            if all(t in f"{e.title} {e.location} {e.description}".lower() for t in terms)
        ]

    def ingest(self, *, source: str = "local") -> int:
        """Index local calendar events and habits into RAG memory."""
        events = self.list_events(
            start_date=date.today() - timedelta(days=30),
            end_date=date.today() + timedelta(days=self.lookahead_days),
            source=source,
        )
        return self._ingest_events(events)

    async def ingest_async(self, *, source: str = "feishu") -> int:
        """Index local and/or enterprise calendar events into RAG memory."""
        events = await self.list_events_async(
            start_date=date.today() - timedelta(days=30),
            end_date=date.today() + timedelta(days=self.lookahead_days),
            source=source,
        )
        return self._ingest_events(events)

    async def draft_create(
        self,
        *,
        platform: str | None,
        title: str,
        start: datetime,
        end: datetime | None = None,
        attendees: list[dict[str, Any]] | None = None,
        location: str = "",
        description: str = "",
        calendar_id: str | None = None,
    ) -> dict[str, Any]:
        platform = platform or self.default_platform
        provider = self._provider(platform)
        draft = {
            "draft_id": uuid.uuid4().hex[:12],
            "platform": platform,
            "operation": "create",
            "calendar_id": calendar_id or getattr(provider, "default_calendar_id", "primary"),
            "event_id": None,
            "payload": {
                "title": title,
                "start": start.isoformat(),
                "end": end.isoformat() if end else None,
                "attendees": attendees or [],
                "location": location,
                "description": description,
                "fields": {},
            },
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(hours=24)).isoformat(),
            "status": "pending",
        }
        self._save_draft(draft)
        if self.write_mode == "direct":
            return await self.confirm_draft(draft["draft_id"])
        return draft

    async def draft_update(
        self,
        *,
        platform: str | None,
        calendar_id: str,
        event_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        platform = platform or self.default_platform
        self._provider(platform)
        draft = {
            "draft_id": uuid.uuid4().hex[:12],
            "platform": platform,
            "operation": "update",
            "calendar_id": calendar_id,
            "event_id": event_id,
            "payload": self._normalize_payload(fields),
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(hours=24)).isoformat(),
            "status": "pending",
        }
        self._save_draft(draft)
        if self.write_mode == "direct":
            return await self.confirm_draft(draft["draft_id"])
        return draft

    async def confirm_draft(self, draft_id: str) -> dict[str, Any]:
        from copala.calendar.providers.base import CalendarDraft

        drafts = self._load_drafts()
        draft = drafts.get(draft_id)
        if not draft:
            raise ValueError(f"Calendar draft not found: {draft_id}")
        if draft.get("status") != "pending":
            raise ValueError(f"Calendar draft is not pending: {draft.get('status')}")
        expires_at = self._parse_datetime(str(draft.get("expires_at") or ""))
        if expires_at and expires_at < datetime.now():
            draft["status"] = "expired"
            self._write_drafts(drafts)
            raise ValueError(f"Calendar draft expired: {draft_id}")

        provider = self._provider(str(draft["platform"]))
        payload = draft.get("payload") or {}
        calendar_draft = CalendarDraft(
            title=str(payload.get("title") or ""),
            start=self._parse_datetime(str(payload.get("start") or "")) or datetime.now(),
            end=self._parse_datetime(str(payload.get("end") or "")) if payload.get("end") else None,
            location=str(payload.get("location") or ""),
            description=str(payload.get("description") or ""),
            attendees=list(payload.get("attendees") or []),
            fields=dict(payload.get("fields") or {}),
        )
        if draft.get("operation") == "create":
            event = await provider.create_event(str(draft["calendar_id"]), calendar_draft)
        elif draft.get("operation") == "update":
            event = await provider.update_event(str(draft["calendar_id"]), str(draft["event_id"]), calendar_draft)
        else:
            raise ValueError(f"Unknown calendar draft operation: {draft.get('operation')}")

        draft["status"] = "confirmed"
        draft["confirmed_at"] = datetime.now().isoformat()
        draft["result"] = {
            "id": event.id,
            "event_id": event.event_id,
            "calendar_id": event.calendar_id,
            "platform": event.platform,
            "title": event.title,
            "start": event.start.isoformat(),
        }
        drafts[draft_id] = draft
        self._write_drafts(drafts)
        self._ingest_events([event])
        return draft

    def discard_draft(self, draft_id: str) -> dict[str, Any]:
        drafts = self._load_drafts()
        draft = drafts.get(draft_id)
        if not draft:
            raise ValueError(f"Calendar draft not found: {draft_id}")
        if draft.get("status") != "pending":
            raise ValueError(f"Calendar draft is not pending: {draft.get('status')}")
        draft["status"] = "discarded"
        draft["discarded_at"] = datetime.now().isoformat()
        drafts[draft_id] = draft
        self._write_drafts(drafts)
        return draft

    async def freebusy(
        self,
        *,
        platform: str | None,
        start: datetime,
        end: datetime,
        user_ids: list[str] | None = None,
    ):
        provider = self._provider(platform or self.default_platform)
        return await provider.get_freebusy(start, end, user_ids)

    async def list_calendars_async(self, *, platform: str | None = None):
        """List calendars visible to an enterprise provider."""
        if platform:
            return await self._provider(platform).list_calendars()
        calendars = []
        for provider in self.providers.values():
            calendars.extend(await provider.list_calendars())
        return calendars

    def auth_status(self) -> dict[str, Any]:
        """Return non-secret calendar auth diagnostics for troubleshooting."""
        status: dict[str, Any] = {
            "workspace": str(self.workspace),
            "enterprise_enabled": self.enterprise_enabled,
            "default_platform": self.default_platform,
            "providers": {},
        }
        for name, provider in self.providers.items():
            item: dict[str, Any] = {
                "configured": True,
                "default_calendar_id": getattr(provider, "default_calendar_id", ""),
            }
            if name == "feishu":
                token_path = getattr(provider, "token_store_path", None)
                item.update({
                    "auth_mode": getattr(provider, "auth_mode", ""),
                    "has_app_id": bool(getattr(provider, "app_id", "")),
                    "has_app_secret": bool(getattr(provider, "app_secret", "")),
                    "token_file": str(token_path) if token_path else "",
                    "token_file_exists": bool(token_path and token_path.exists()),
                })
            status["providers"][name] = item
        return status

    def _ingest_events(self, events: list[CalendarEvent]) -> int:
        from copala.agent.memory_rag.schema import MemoryItem
        from copala.agent.memory_rag.store import MemoryRAGStore

        store = MemoryRAGStore(self.workspace)
        items: list[MemoryItem] = []
        for event in events:
            platform = event.platform or "local"
            source_ref = (
                f"{platform}:{event.calendar_id}:{event.event_id}"
                if event.calendar_id or event.event_id else event.source
            )
            text = f"日程事件：{event.to_text()}"
            items.append(
                MemoryItem(
                    id=f"calendar:{platform}:{event.id}",
                    type="calendar_event",
                    text=text,
                    summary=f"{event.start.isoformat()} {event.title}",
                    source="calendar",
                    source_ref=source_ref,
                    tags=["calendar", "schedule", platform] + (["enterprise"] if platform != "local" else []),
                    confidence=1.0,
                    priority=5,
                    metadata={
                        "platform": event.platform,
                        "calendar_id": event.calendar_id,
                        "event_id": event.event_id,
                        "start": event.start.isoformat(),
                        "end": event.end.isoformat() if event.end else None,
                        "location": event.location,
                        "attendees": event.attendees,
                    },
                )
            )
        items.extend(self._habit_items(events))
        return store.upsert_many(items)

    def _habit_items(self, events: list[CalendarEvent]):
        from copala.agent.memory_rag.schema import MemoryItem

        workday_morning = sum(
            1 for e in events
            if e.start.weekday() < 5 and 9 <= e.start.hour < 12 and self._looks_like_meeting(e)
        )
        items = []
        if workday_morning >= 2:
            text = "用户工作日上午经常安排会议，做日程建议时应避开已占用会议时段。"
            items.append(
                MemoryItem(
                    id="calendar_habit:workday_morning_meetings",
                    type="calendar_habit",
                    text=text,
                    summary=text,
                    source="calendar",
                    source_ref="calendar:auto_habit",
                    tags=["calendar", "habit"],
                    confidence=0.72,
                    priority=7,
                )
            )
        return items

    @staticmethod
    def _looks_like_meeting(event: CalendarEvent) -> bool:
        text = f"{event.title} {event.description}".lower()
        return any(word in text for word in ("会议", "meeting", "sync", "standup", "review", "call"))

    async def _list_enterprise(self, source: str, start_date: date, end_date: date) -> list[CalendarEvent]:
        if not self.enterprise_enabled:
            return []
        start = datetime.combine(start_date, time.min)
        end = datetime.combine(end_date, time.max)
        events: list[CalendarEvent] = []
        provider_names = list(self.providers) if source == "all" else [source]
        for name in provider_names:
            provider = self.providers.get(name)
            if not provider:
                continue
            calendar_id = getattr(provider, "default_calendar_id", "primary")
            events.extend(await provider.list_events(calendar_id, start, end))
        return events

    def _build_providers(self, calendar_config: Any | None, feishu_config: Any | None) -> dict[str, Any]:
        if not calendar_config or not getattr(calendar_config, "enterprise_enabled", False):
            return {}
        feishu_config = self._fill_missing_feishu_config(feishu_config)
        providers_cfg = getattr(calendar_config, "providers", None)
        providers: dict[str, Any] = {}
        feishu_provider_cfg = getattr(providers_cfg, "feishu", None)
        if getattr(feishu_provider_cfg, "enabled", False):
            from copala.calendar.providers.feishu import FeishuCalendarProvider

            providers["feishu"] = FeishuCalendarProvider(
                app_id=getattr(feishu_config, "app_id", ""),
                app_secret=getattr(feishu_config, "app_secret", ""),
                default_calendar_id=getattr(feishu_provider_cfg, "default_calendar_id", "primary"),
                api_base=getattr(feishu_provider_cfg, "calendar_api_base", "") or "https://open.feishu.cn",
                auth_mode=getattr(feishu_provider_cfg, "auth_mode", "tenant"),
                redirect_uri=getattr(feishu_provider_cfg, "redirect_uri", "http://localhost:18790/feishu/oauth/callback"),
                oauth_scopes=getattr(feishu_provider_cfg, "oauth_scopes", []),
                token_store_path=self._resolve(getattr(feishu_provider_cfg, "token_file", "calendar/feishu_user_token.json")),
            )
        return providers

    @staticmethod
    def _fill_missing_feishu_config(feishu_config: Any | None) -> Any | None:
        """Fallback to the active config file if Feishu credentials were not passed in."""
        need_feishu = not getattr(feishu_config, "app_id", "") or not getattr(feishu_config, "app_secret", "")
        if not need_feishu:
            return feishu_config
        try:
            from copala.config.loader import load_config

            cfg = load_config()
        except Exception:
            return feishu_config
        return getattr(getattr(cfg, "channels", None), "feishu", feishu_config)

    def _provider(self, platform: str):
        if not self.enterprise_enabled:
            raise ValueError("Enterprise calendar is disabled. Set calendar.enterpriseEnabled=true.")
        provider = self.providers.get(platform)
        if not provider:
            raise ValueError(f"Enterprise calendar provider is not configured: {platform}")
        return provider

    def feishu_authorization_url(self, *, state: str = "copala") -> str:
        provider = self._provider("feishu")
        if not hasattr(provider, "authorization_url"):
            raise ValueError("Configured Feishu provider does not support OAuth authorization URLs.")
        return provider.authorization_url(state=state)

    async def feishu_exchange_code(self, code: str) -> dict[str, Any]:
        provider = self._provider("feishu")
        if not hasattr(provider, "exchange_authorization_code"):
            raise ValueError("Configured Feishu provider does not support OAuth code exchange.")
        return await provider.exchange_authorization_code(code)

    @property
    def _drafts_path(self) -> Path:
        return self.workspace / "calendar" / "drafts.json"

    def _load_drafts(self) -> dict[str, dict[str, Any]]:
        path = self._drafts_path
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if isinstance(data, dict) and isinstance(data.get("drafts"), dict):
            return data["drafts"]
        return data if isinstance(data, dict) else {}

    def _write_drafts(self, drafts: dict[str, dict[str, Any]]) -> None:
        path = self._drafts_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"drafts": drafts}, ensure_ascii=False, indent=2), encoding="utf-8")

    def _save_draft(self, draft: dict[str, Any]) -> None:
        drafts = self._load_drafts()
        drafts[str(draft["draft_id"])] = draft
        self._write_drafts(drafts)

    def _normalize_payload(self, fields: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "title": str(fields.get("title") or fields.get("summary") or ""),
            "start": str(fields.get("start") or fields.get("start_time") or ""),
            "end": str(fields.get("end") or fields.get("end_time") or "") or None,
            "attendees": list(fields.get("attendees") or []),
            "location": str(fields.get("location") or ""),
            "description": str(fields.get("description") or ""),
            "fields": {},
        }
        known = {"title", "summary", "start", "start_time", "end", "end_time", "attendees", "location", "description"}
        payload["fields"] = {k: v for k, v in fields.items() if k not in known}
        return payload

    def _load_all(self) -> list[CalendarEvent]:
        events: list[CalendarEvent] = []
        for raw in self.ics_files:
            path = self._resolve(raw)
            if path.exists():
                events.extend(self._parse_ics(path.read_text(encoding="utf-8", errors="replace"), str(path)))
        for url in self.ics_urls:
            try:
                with urllib.request.urlopen(url, timeout=10) as response:
                    text = response.read().decode("utf-8", errors="replace")
                events.extend(self._parse_ics(text, url))
            except Exception:
                continue
        for raw in self.local_json_files:
            path = self._resolve(raw)
            if path.exists():
                events.extend(self._parse_json(path.read_text(encoding="utf-8", errors="replace"), str(path)))
        return events

    def _resolve(self, raw: str) -> Path:
        path = Path(raw).expanduser()
        return path if path.is_absolute() else self.workspace / path

    def _parse_json(self, text: str, source: str) -> list[CalendarEvent]:
        data = json.loads(text)
        rows = data if isinstance(data, list) else data.get("events", []) if isinstance(data, dict) else []
        events = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            start = self._parse_datetime(str(row.get("start") or row.get("start_time") or ""))
            if not start:
                continue
            end = self._parse_datetime(str(row.get("end") or row.get("end_time") or "")) if row.get("end") or row.get("end_time") else None
            title = str(row.get("title") or row.get("summary") or "Untitled event")
            event_id = str(row.get("id") or hashlib.sha1(f"{source}|{title}|{start}".encode()).hexdigest())
            events.append(
                CalendarEvent(
                    id=event_id,
                    title=title,
                    start=start,
                    end=end,
                    location=str(row.get("location") or ""),
                    description=str(row.get("description") or ""),
                    source=source,
                    platform="local",
                    metadata=row,
                    raw=row,
                )
            )
        return events

    def _parse_ics(self, text: str, source: str) -> list[CalendarEvent]:
        unfolded = self._unfold_ics(text)
        events = []
        current: dict[str, str] | None = None
        for line in unfolded:
            if line == "BEGIN:VEVENT":
                current = {}
                continue
            if line == "END:VEVENT" and current is not None:
                event = self._event_from_ics(current, source)
                if event:
                    events.append(event)
                current = None
                continue
            if current is not None and ":" in line:
                key, value = line.split(":", 1)
                key = key.split(";", 1)[0].upper()
                current[key] = self._ics_unescape(value)
        return events

    @staticmethod
    def _unfold_ics(text: str) -> list[str]:
        lines: list[str] = []
        for raw in text.splitlines():
            if raw.startswith((" ", "\t")) and lines:
                lines[-1] += raw[1:]
            else:
                lines.append(raw.strip())
        return lines

    def _event_from_ics(self, data: dict[str, str], source: str) -> CalendarEvent | None:
        start = self._parse_datetime(data.get("DTSTART", ""))
        if not start:
            return None
        end = self._parse_datetime(data.get("DTEND", "")) if data.get("DTEND") else None
        title = data.get("SUMMARY") or "Untitled event"
        uid = data.get("UID") or hashlib.sha1(f"{source}|{title}|{start}".encode()).hexdigest()
        return CalendarEvent(
            id=uid,
            title=title,
            start=start,
            end=end,
            location=data.get("LOCATION", ""),
            description=data.get("DESCRIPTION", ""),
            source=source,
            platform="local",
            metadata=data,
            raw=data,
        )

    @staticmethod
    def _parse_datetime(raw: str) -> datetime | None:
        raw = raw.strip()
        if not raw:
            return None
        try:
            if re.fullmatch(r"\d{8}", raw):
                return datetime.strptime(raw, "%Y%m%d")
            if raw.endswith("Z") and re.fullmatch(r"\d{8}T\d{6}Z", raw):
                return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            if raw.endswith("Z"):
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if re.fullmatch(r"\d{8}T\d{6}", raw):
                return datetime.strptime(raw, "%Y%m%dT%H%M%S")
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    @staticmethod
    def _ics_unescape(value: str) -> str:
        return (
            value.replace("\\n", "\n")
            .replace("\\,", ",")
            .replace("\\;", ";")
            .replace("\\\\", "\\")
        )

    @staticmethod
    def _naive(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone().replace(tzinfo=None)
