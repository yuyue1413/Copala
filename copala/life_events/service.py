"""Personal life-event manager for email/message/calendar automation."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from copala.bus.events import OutboundMessage
from copala.calendar.providers.base import CalendarDraft
from copala.calendar.service import CalendarEvent
from copala.cron.types import CronSchedule

if TYPE_CHECKING:
    from copala.agent.memory_rag.store import MemoryRAGStore
    from copala.calendar.service import CalendarService
    from copala.config.schema import LifeEventsConfig, MailboxConfig
    from copala.cron.service import CronService


CALENDAR_TYPES = {"meeting", "interview", "medical", "meal", "flight", "hotel", "personal_activity"}
TODO_ONLY_TYPES = {"bill", "delivery", "shopping", "generic_todo"}
CHANGE_WORDS = ("改到", "改为", "延期", "推迟", "提前", "地点变更", "时间变更", "取消", "变更")
TYPE_LABELS = {
    "meeting": ("📌", "会议"),
    "interview": ("🌟", "面试"),
    "medical": ("🩺", "体检"),
    "meal": ("🍽️", "聚餐"),
    "flight": ("✈️", "航班"),
    "hotel": ("🏨", "酒店"),
    "bill": ("💳", "账单"),
    "delivery": ("📦", "快递"),
    "shopping": ("🛍️", "购物"),
    "personal_activity": ("🏓", "活动"),
    "generic_todo": ("✅", "待办"),
}


@dataclass
class LifeEvent:
    """Structured event extracted from a user signal."""

    event_type: str
    title: str
    summary: str
    source: str
    source_ref: str
    confidence: float
    start: datetime | None = None
    end: datetime | None = None
    location: str = ""
    contact: str = ""
    amount: str = ""
    raw_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    duration_minutes: int = 30
    fixed_time: bool = False
    priority: int = 5
    movable: bool = True
    deadline: datetime | None = None
    risk_tags: list[str] = field(default_factory=list)
    weather_sensitive: bool = False
    outdoor: bool = False
    reminder_policy: dict[str, Any] = field(default_factory=dict)
    last_risk_checked_at: str = ""


@dataclass
class ProcessResult:
    """Result of processing one input text."""

    events: list[LifeEvent] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    created: int = 0
    updated: int = 0
    skipped: int = 0


class LifeEventExtractor:
    """Deterministic extractor for demo-stable life events."""

    TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("interview", ("面试", "邀约", "笔试", "offer", "interview")),
        ("medical", ("体检", "医院", "门诊", "预约", "检查")),
        ("flight", ("航班", "登机", "值机", "机票", "改签", "起飞", "flight")),
        ("hotel", ("酒店", "入住", "退房", "预订", "hotel")),
        ("bill", ("信用卡", "账单", "还款", "扣款", "缴费", "支付", "金额")),
        ("delivery", ("快递", "包裹", "派送", "送达", "取件")),
        ("meal", ("聚餐", "吃饭", "晚饭", "午饭", "餐厅", "饭店")),
        ("meeting", ("会议", "同步会", "周会", "腾讯会议", "飞书会议", "zoom", "meeting", "call")),
    )
    LOCATION_RE = re.compile(r"(?:地点|地址|会议链接|会议地址|位置)[:：]\s*([^\n，。；;]+)")
    AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?\s*(?:元|人民币|RMB|¥|￥))", re.I)
    CONTACT_RE = re.compile(r"(?:联系人|面试官|HR|发件人|from|From)[:：]\s*([^\n，。；;<>]+)", re.I)
    ISO_RE = re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?(?:\s*(上午|下午|晚上|中午|早上)?\s*[T ]?\s*(\d{1,2})[:：点](\d{1,2})?)?")
    MD_TIME_RE = re.compile(r"(\d{1,2})月(\d{1,2})日(?:\s*(上午|下午|晚上|中午|早上)?\s*(\d{1,2})[:：点](\d{1,2})?)?")
    REL_TIME_RE = re.compile(r"(今天|明天|后天|今晚|明晚)(?:\s*(上午|下午|晚上|中午|早上)?\s*(\d{1,2})[:：点](\d{1,2})?)?")

    def extract(self, text: str, *, source: str, source_ref: str) -> list[LifeEvent]:
        start = self._parse_time(text)
        event_type = self._classify(text, start)
        if not event_type:
            return []
        confidence = self._confidence(event_type, start, text)
        title = self._title(event_type, text)
        location = self._location(text)
        contact = self._contact(text)
        amount = self._amount(text)
        end = self._default_end(event_type, start)
        return [
            LifeEvent(
                event_type=event_type,
                title=title,
                summary=self._summary(text),
                source=source,
                source_ref=source_ref,
                confidence=confidence,
                start=start,
                end=end,
                location=location,
                contact=contact,
                amount=amount,
                raw_text=text,
                metadata={"is_change": any(word in text for word in CHANGE_WORDS)},
                duration_minutes=self._duration_for_type(event_type),
                fixed_time=bool(start and event_type in CALENDAR_TYPES),
                priority=self._priority_for_type(event_type),
                movable=event_type in TODO_ONLY_TYPES,
                deadline=start if event_type == "bill" else None,
                risk_tags=self._risk_tags(event_type, text),
                weather_sensitive=self._weather_sensitive(event_type, text),
                outdoor=self._outdoor(event_type, text),
                reminder_policy=self._reminder_policy(event_type),
            )
        ]

    def _classify(self, text: str, start: datetime | None = None) -> str | None:
        lowered = text.lower()
        for event_type, words in self.TYPE_KEYWORDS:
            if any(word in text or word in lowered for word in words):
                return event_type
        if start and self._looks_like_personal_activity(text):
            return "personal_activity"
        if any(word in text for word in ("待办", "提醒我", "记得")):
            return "generic_todo"
        return None

    @staticmethod
    def _looks_like_personal_activity(text: str) -> bool:
        activity_markers = (
            "我要去", "我想去", "我准备去", "我计划去", "要去", "准备去", "计划去",
            "我要", "我想", "我准备", "我计划", "需要去",
        )
        activity_words = (
            "打", "看", "去", "参加", "练", "运动", "健身", "跑步", "游泳",
            "乒乓球", "羽毛球", "篮球", "足球", "网球", "电影", "演出", "活动",
        )
        return any(marker in text for marker in activity_markers) and any(word in text for word in activity_words)

    def _parse_time(self, text: str) -> datetime | None:
        match = self.ISO_RE.search(text)
        if match:
            year, month, day, period, hour, minute = match.groups()
            return self._build_dt(int(year), int(month), int(day), hour, minute, period or text)
        match = self.MD_TIME_RE.search(text)
        if match:
            month, day, period, hour, minute = match.groups()
            year = date.today().year
            return self._build_dt(year, int(month), int(day), hour, minute, period or text)
        match = self.REL_TIME_RE.search(text)
        if match:
            rel, period, hour, minute = match.groups()
            base = date.today()
            if rel in ("明天", "明晚"):
                base += timedelta(days=1)
            elif rel == "后天":
                base += timedelta(days=2)
            period_text = period or ("晚上" if rel in ("今晚", "明晚") else text)
            return self._build_dt(base.year, base.month, base.day, hour, minute, period_text)
        return None

    @staticmethod
    def _build_dt(year: int, month: int, day: int, hour: str | None, minute: str | None, period_text: str) -> datetime:
        h = int(hour) if hour else 9
        m = int(minute) if minute else 0
        if hour and ("下午" in period_text or "晚上" in period_text) and h < 12:
            h += 12
        if hour and "中午" in period_text and h < 11:
            h += 12
        return datetime(year, month, day, h, m)

    @staticmethod
    def _default_end(event_type: str, start: datetime | None) -> datetime | None:
        if not start:
            return None
        minutes = {
            "meeting": 60,
            "interview": 60,
            "medical": 120,
            "meal": 120,
            "hotel": 60,
            "flight": 60,
            "personal_activity": 120,
        }.get(event_type, 30)
        return start + timedelta(minutes=minutes)

    @staticmethod
    def _duration_for_type(event_type: str) -> int:
        return {
            "meeting": 60,
            "interview": 60,
            "medical": 120,
            "meal": 120,
            "flight": 60,
            "hotel": 60,
            "personal_activity": 120,
            "delivery": 20,
            "bill": 20,
            "shopping": 60,
            "generic_todo": 30,
        }.get(event_type, 30)

    @staticmethod
    def _priority_for_type(event_type: str) -> int:
        return {
            "interview": 10,
            "flight": 10,
            "medical": 9,
            "bill": 9,
            "meeting": 8,
            "hotel": 8,
            "meal": 6,
            "delivery": 6,
            "shopping": 5,
            "personal_activity": 5,
            "generic_todo": 5,
        }.get(event_type, 5)

    @staticmethod
    def _risk_tags(event_type: str, text: str) -> list[str]:
        tags: list[str] = []
        lowered = text.lower()
        if event_type in {"flight", "hotel"}:
            tags.extend(["travel", "weather", "traffic"])
        if event_type == "bill":
            tags.append("deadline")
        if event_type in {"meeting", "interview"}:
            tags.append("preparation")
        if any(word in text for word in ("室外", "公园", "球场", "跑步", "羽毛球", "乒乓球")) or any(
            word in lowered for word in ("outdoor", "park", "badminton", "pingpong", "running")
        ):
            tags.extend(["weather", "outdoor"])
        return sorted(set(tags))

    @staticmethod
    def _weather_sensitive(event_type: str, text: str) -> bool:
        lowered = text.lower()
        return (
            event_type in {"flight", "hotel", "personal_activity"}
            or any(word in text for word in ("室外", "公园", "球场", "跑步", "羽毛球", "乒乓球"))
            or any(word in lowered for word in ("outdoor", "park", "badminton", "pingpong", "running"))
        )

    @staticmethod
    def _outdoor(event_type: str, text: str) -> bool:
        lowered = text.lower()
        return any(word in text for word in ("室外", "公园", "球场", "跑步", "羽毛球", "乒乓球")) or any(
            word in lowered for word in ("outdoor", "park", "badminton", "pingpong", "running")
        )

    @staticmethod
    def _reminder_policy(event_type: str) -> dict[str, Any]:
        if event_type == "bill":
            return {"advance_days": [3, 1], "reason": "deadline"}
        if event_type in {"flight", "hotel"}:
            return {"advance_hours": [24, 3], "check": ["weather", "traffic", "documents"]}
        if event_type in {"meeting", "interview"}:
            return {"advance_minutes": [60, 10], "check": ["preparation", "traffic"]}
        return {"advance_minutes": [60], "check": ["traffic"]}

    def _title(self, event_type: str, text: str) -> str:
        icon, label = TYPE_LABELS.get(event_type, ("📝", "事件"))
        subject = ""
        for line in text.splitlines():
            cleaned = line.strip(" -\t")
            if cleaned.startswith(("主题:", "Subject:")):
                subject = cleaned.split(":", 1)[-1].strip()
                break
        core = subject or self._clean_title_core(text)
        core = re.sub(r"^(re|fw|回复|转发)[:：]\s*", "", core, flags=re.I)
        return f"{icon}【{label}】{core}"

    @classmethod
    def _clean_title_core(cls, text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        first = re.split(r"[，。；;\n]", text, maxsplit=1)[0].strip()
        first = cls.ISO_RE.sub("", first)
        first = cls.MD_TIME_RE.sub("", first)
        first = cls.REL_TIME_RE.sub("", first)
        first = re.sub(r"(地点|地址|会议链接|会议地址|位置|联系人|面试官|HR)[:：].*$", "", first, flags=re.I)
        first = re.sub(r"^(我)?(要|想|准备|计划|需要)?去", "", first)
        first = re.sub(r"^我(要|想|准备|计划)", "", first)
        first = first.strip(" ，。；;:-\t")
        return first[:28] or cls._summary(text, max_len=28)

    @classmethod
    def _location(cls, text: str) -> str:
        match = cls.LOCATION_RE.search(text)
        if match:
            return match.group(1).strip()
        link = re.search(r"https?://\S+", text)
        if link and any(word in text for word in ("会议", "面试", "腾讯会议", "飞书会议", "zoom")):
            return link.group(0).rstrip("。；;,")
        for marker in ("腾讯会议", "飞书会议", "zoom", "Zoom"):
            if marker in text:
                return marker
        return "未提供"

    @classmethod
    def _contact(cls, text: str) -> str:
        match = cls.CONTACT_RE.search(text)
        if match:
            return match.group(1).strip()
        email = re.search(r"[\w.+-]+@[\w.-]+\.\w+", text)
        return email.group(0) if email else ""

    @classmethod
    def _amount(cls, text: str) -> str:
        match = cls.AMOUNT_RE.search(text)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _summary(text: str, max_len: int = 160) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip()
        return cleaned[:max_len]

    @staticmethod
    def _confidence(event_type: str, start: datetime | None, text: str) -> float:
        score = 0.74
        if event_type in CALENDAR_TYPES:
            score += 0.08
        if start:
            score += 0.12
        if any(word in text for word in ("确认", "邀请", "预约", "通知", "会议", "面试")):
            score += 0.04
        return min(score, 0.96)


class LifeEventStore:
    """SQLite-backed life-event state and dedupe store."""

    SELECT_COLUMNS = (
        "id, dedupe_key, type, title, source, source_ref, start, end, "
        "location, contact, amount, confidence, calendar_id, event_id, "
        "status, raw_summary, duration_minutes, fixed_time, priority, movable, "
        "deadline, risk_tags, weather_sensitive, outdoor, reminder_policy, last_risk_checked_at, "
        "created_at, updated_at"
    )

    def __init__(self, workspace: Path):
        self.path = workspace / "life_events" / "life_events.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        return sqlite3.connect(self.path)

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS life_events (
                    id TEXT PRIMARY KEY,
                    dedupe_key TEXT UNIQUE,
                    type TEXT,
                    title TEXT,
                    source TEXT,
                    source_ref TEXT,
                    start TEXT,
                    end TEXT,
                    location TEXT,
                    contact TEXT,
                    amount TEXT,
                    confidence REAL,
                    calendar_id TEXT,
                    event_id TEXT,
                    status TEXT,
                    raw_summary TEXT,
                    duration_minutes INTEGER DEFAULT 30,
                    fixed_time INTEGER DEFAULT 0,
                    priority INTEGER DEFAULT 5,
                    movable INTEGER DEFAULT 1,
                    deadline TEXT,
                    risk_tags TEXT,
                    weather_sensitive INTEGER DEFAULT 0,
                    outdoor INTEGER DEFAULT 0,
                    reminder_policy TEXT,
                    last_risk_checked_at TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    id TEXT PRIMARY KEY,
                    event_id TEXT,
                    remind_at TEXT,
                    cron_job_id TEXT,
                    status TEXT,
                    created_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS calendar_reminders (
                    key TEXT PRIMARY KEY,
                    reminded_at TEXT
                )
                """
            )
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        desired = {
            "duration_minutes": "INTEGER DEFAULT 30",
            "fixed_time": "INTEGER DEFAULT 0",
            "priority": "INTEGER DEFAULT 5",
            "movable": "INTEGER DEFAULT 1",
            "deadline": "TEXT",
            "risk_tags": "TEXT",
            "weather_sensitive": "INTEGER DEFAULT 0",
            "outdoor": "INTEGER DEFAULT 0",
            "reminder_policy": "TEXT",
            "last_risk_checked_at": "TEXT",
        }
        with self._connect() as conn:
            existing = {row[1] for row in conn.execute("PRAGMA table_info(life_events)").fetchall()}
            for name, ddl in desired.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE life_events ADD COLUMN {name} {ddl}")

    @staticmethod
    def dedupe_key(event: LifeEvent) -> str:
        raw = "|".join([
            event.source,
            event.source_ref,
            event.event_type,
            event.title,
            event.start.isoformat() if event.start else "",
        ])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def get_by_dedupe(self, key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self.SELECT_COLUMNS} FROM life_events WHERE dedupe_key=?",
                (key,),
            ).fetchone()
            return self._row(row) if row else None

    def find_recent_match(self, event: LifeEvent) -> dict[str, Any] | None:
        needle = re.sub(r"【[^】]+】", "", event.title).strip()
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {self.SELECT_COLUMNS} FROM life_events
                WHERE type=? AND event_id IS NOT NULL
                ORDER BY created_at DESC LIMIT 20
                """,
                (event.event_type,),
            ).fetchall()
        for row in rows:
            item = self._row(row)
            if needle and needle[:8] in item.get("title", ""):
                return item
            if event.contact and event.contact == item.get("contact"):
                return item
        return None

    def upsert_event(
        self,
        event: LifeEvent,
        *,
        status: str,
        calendar_id: str = "",
        event_id: str = "",
    ) -> str:
        event_id_local = hashlib.sha1(f"{event.source_ref}|{event.title}|{event.start}".encode("utf-8")).hexdigest()[:16]
        key = self.dedupe_key(event)
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO life_events (
                    id, dedupe_key, type, title, source, source_ref, start, end, location,
                    contact, amount, confidence, calendar_id, event_id, status, raw_summary,
                    duration_minutes, fixed_time, priority, movable, deadline, risk_tags,
                    weather_sensitive, outdoor, reminder_policy, last_risk_checked_at,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dedupe_key) DO UPDATE SET
                    calendar_id=excluded.calendar_id,
                    event_id=excluded.event_id,
                    status=excluded.status,
                    duration_minutes=excluded.duration_minutes,
                    fixed_time=excluded.fixed_time,
                    priority=excluded.priority,
                    movable=excluded.movable,
                    deadline=excluded.deadline,
                    risk_tags=excluded.risk_tags,
                    weather_sensitive=excluded.weather_sensitive,
                    outdoor=excluded.outdoor,
                    reminder_policy=excluded.reminder_policy,
                    last_risk_checked_at=excluded.last_risk_checked_at,
                    updated_at=excluded.updated_at
                """,
                (
                    event_id_local,
                    key,
                    event.event_type,
                    event.title,
                    event.source,
                    event.source_ref,
                    event.start.isoformat() if event.start else "",
                    event.end.isoformat() if event.end else "",
                    event.location,
                    event.contact,
                    event.amount,
                    event.confidence,
                    calendar_id,
                    event_id,
                    status,
                    event.summary,
                    event.duration_minutes,
                    1 if event.fixed_time else 0,
                    event.priority,
                    1 if event.movable else 0,
                    event.deadline.isoformat() if event.deadline else "",
                    json.dumps(event.risk_tags, ensure_ascii=False),
                    1 if event.weather_sensitive else 0,
                    1 if event.outdoor else 0,
                    json.dumps(event.reminder_policy, ensure_ascii=False),
                    event.last_risk_checked_at,
                    now,
                    now,
                ),
            )
        return event_id_local

    def add_reminder(self, event_id: str, remind_at: datetime, cron_job_id: str) -> None:
        rid = hashlib.sha1(f"{event_id}|{remind_at.isoformat()}".encode("utf-8")).hexdigest()[:16]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO reminders (id, event_id, remind_at, cron_job_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (rid, event_id, remind_at.isoformat(), cron_job_id, "scheduled", datetime.now().isoformat()),
            )

    def was_calendar_reminded(self, key: str) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM calendar_reminders WHERE key=?", (key,)).fetchone() is not None

    def mark_calendar_reminded(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO calendar_reminders (key, reminded_at) VALUES (?, ?)",
                (key, datetime.now().isoformat()),
            )

    def list_events(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self.SELECT_COLUMNS} FROM life_events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row) -> dict[str, Any]:
        columns = (
            "id", "dedupe_key", "type", "title", "source", "source_ref", "start", "end",
            "location", "contact", "amount", "confidence", "calendar_id", "event_id",
            "status", "raw_summary", "duration_minutes", "fixed_time", "priority", "movable",
            "deadline", "risk_tags", "weather_sensitive", "outdoor", "reminder_policy", "last_risk_checked_at",
            "created_at", "updated_at",
        )
        item = dict(zip(columns, row))
        item["fixed_time"] = bool(item.get("fixed_time"))
        item["movable"] = bool(item.get("movable"))
        item["weather_sensitive"] = bool(item.get("weather_sensitive"))
        item["outdoor"] = bool(item.get("outdoor"))
        for key, default in (("risk_tags", []), ("reminder_policy", {})):
            raw = item.get(key)
            if isinstance(raw, str) and raw:
                try:
                    item[key] = json.loads(raw)
                except json.JSONDecodeError:
                    item[key] = default
            elif raw is None:
                item[key] = default
        return item


class LifeEventManager:
    """Coordinates life-event extraction, calendar writes, reminders and QQ notices."""

    def __init__(
        self,
        *,
        workspace: Path,
        config: "LifeEventsConfig",
        calendar_service: "CalendarService",
        memory_store: "MemoryRAGStore | None" = None,
        cron_service: "CronService | None" = None,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        mailbox_config: "MailboxConfig | None" = None,
    ):
        self.workspace = workspace
        self.config = config
        self.calendar = calendar_service
        self.memory = memory_store
        self.cron = cron_service
        self.send_callback = send_callback
        self.mailbox_config = mailbox_config
        self.extractor = LifeEventExtractor()
        self.store = LifeEventStore(workspace)

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "enabled", False))

    async def process_text(
        self,
        text: str,
        *,
        source: str,
        source_ref: str,
        notify_channel: str | None = None,
        notify_chat_id: str | None = None,
        send_notifications: bool = True,
    ) -> ProcessResult:
        if not self.enabled or not text.strip():
            return ProcessResult()
        result = ProcessResult()
        for event in self.extractor.extract(text, source=source, source_ref=source_ref):
            result.events.append(event)
            processed = await self._process_event(
                event,
                notify_channel=notify_channel,
                notify_chat_id=notify_chat_id,
                send_notifications=send_notifications,
            )
            result.created += processed.created
            result.updated += processed.updated
            result.skipped += processed.skipped
            result.messages.extend(processed.messages)
        return result

    async def _process_event(
        self,
        event: LifeEvent,
        *,
        notify_channel: str | None = None,
        notify_chat_id: str | None = None,
        send_notifications: bool = True,
    ) -> ProcessResult:
        result = ProcessResult(events=[event])
        key = self.store.dedupe_key(event)
        if self.store.get_by_dedupe(key):
            result.skipped += 1
            return result
        if event.event_type in CALENDAR_TYPES:
            if not event.start:
                self.store.upsert_event(event, status="missing_time")
                message = self._missing_time_message(event)
                if send_notifications:
                    await self._notify(message, channel=notify_channel, chat_id=notify_chat_id)
                result.messages.append(message)
                result.skipped += 1
                return result
            if event.confidence < getattr(self.config, "min_calendar_confidence", 0.82):
                self.store.upsert_event(event, status="low_confidence")
                result.skipped += 1
                return result
            await self._write_calendar_event(
                event,
                result,
                notify_channel=notify_channel,
                notify_chat_id=notify_chat_id,
                send_notifications=send_notifications,
            )
        else:
            event_id = self.store.upsert_event(event, status="todo")
            result.created += 1
            await self._create_reminders(
                event,
                event_id,
                notify_channel=notify_channel,
                notify_chat_id=notify_chat_id,
            )
            self._remember_event(event, status="todo")
        return result

    async def _write_calendar_event(
        self,
        event: LifeEvent,
        result: ProcessResult,
        *,
        notify_channel: str | None = None,
        notify_chat_id: str | None = None,
        send_notifications: bool = True,
    ) -> None:
        try:
            provider = self.calendar._provider("feishu")
            calendar_id = getattr(provider, "default_calendar_id", "primary")
            try:
                conflict = await self._has_conflict(event)
            except Exception as exc:
                logger.warning("Life event conflict check skipped: {}", exc)
                conflict = False
            existing = self.store.find_recent_match(event) if event.metadata.get("is_change") else None
            draft = self._calendar_draft(event)
            if existing and existing.get("event_id"):
                calendar_event = await provider.update_event(calendar_id, existing["event_id"], draft)
                status = "calendar_updated"
                result.updated += 1
                message = self._calendar_notice("已修改飞书日历：", event, calendar_event, conflict=conflict)
            else:
                calendar_event = await provider.create_event(calendar_id, draft)
                status = "calendar_created"
                result.created += 1
                message = self._calendar_notice("已写入飞书日历：", event, calendar_event, conflict=conflict)
                if event.metadata.get("is_change"):
                    message += "\n提示：未匹配到旧日程，已创建新日程。"
            local_id = self.store.upsert_event(
                event,
                status=status,
                calendar_id=calendar_event.calendar_id or calendar_id,
                event_id=calendar_event.event_id or calendar_event.id,
            )
            await self._create_reminders(
                event,
                local_id,
                notify_channel=notify_channel,
                notify_chat_id=notify_chat_id,
            )
            self._remember_event(event, status=status, calendar_event=calendar_event)
            if send_notifications:
                await self._notify(message, channel=notify_channel, chat_id=notify_chat_id)
            result.messages.append(message)
        except Exception as exc:
            logger.exception("Life event calendar write failed")
            self.store.upsert_event(event, status="calendar_failed")
            message = f"生活事件写入飞书日历失败：{exc}\n事件：{event.title}\n来源：{event.source_ref}"
            if send_notifications:
                await self._notify(message, channel=notify_channel, chat_id=notify_chat_id)
            result.messages.append(message)

    def _calendar_draft(self, event: LifeEvent) -> CalendarDraft:
        return CalendarDraft(
            title=event.title,
            start=event.start or datetime.now(),
            end=event.end,
            location=event.location or "未提供",
            description=self._description(event),
            attendees=[],
        )

    def _description(self, event: LifeEvent) -> str:
        icon, label = TYPE_LABELS.get(event.event_type, ("📝", event.event_type))
        lines = [
            f"{icon} 生活事件管家自动整理",
            f"⭐ 重点：{event.title}",
            f"🕒 时间：{self._time_range(event.start, event.end)}",
            f"📍 地点：{event.location or '未提供'}",
            f"👤 联系人：{event.contact or '未提供'}",
            f"💰 金额：{event.amount or '未提供'}",
            f"🏷️ 类型：{label}",
            f"📨 来源：{event.source}",
            f"🧾 原始摘要：{event.summary}",
            f"💡 建议：{self._suggestion(event)}",
            "🤖 自动处理：由生活事件管家识别、格式化并写入飞书日历",
        ]
        if event.location == "未提供":
            lines.append("📍 地点说明：来源未提供明确地点。")
        return "\n".join(lines)

    async def _has_conflict(self, event: LifeEvent) -> bool:
        if not event.start:
            return False
        start_day = event.start.date()
        events = await self.calendar.list_events_async(start_date=start_day, end_date=start_day, source="feishu")
        event_start = self._naive(event.start)
        event_end = self._naive(event.end or event.start + timedelta(minutes=30))
        for existing in events:
            existing_start = self._naive(existing.start)
            existing_end = self._naive(existing.end or existing.start + timedelta(minutes=30))
            if event_start < existing_end and event_end > existing_start:
                return True
        return False

    async def _create_reminders(
        self,
        event: LifeEvent,
        local_event_id: str,
        *,
        notify_channel: str | None = None,
        notify_chat_id: str | None = None,
    ) -> None:
        if not getattr(self.config, "auto_create_reminder", True) or not self.cron or not event.start:
            return
        target_channel = notify_channel or getattr(self.config, "notify_channel", "qq")
        target_chat_id = notify_chat_id or getattr(self.config, "notify_chat_id", "")
        for remind_at in self._reminder_times(event):
            if remind_at <= datetime.now():
                continue
            job = self.cron.add_job(
                name=f"LifeEvent {event.event_type}",
                schedule=CronSchedule(kind="at", at_ms=int(remind_at.timestamp() * 1000)),
                message=f"提醒：{event.title}\n时间：{self._time_range(event.start, event.end)}\n地点：{event.location or '未提供'}",
                deliver=True,
                channel=target_channel,
                to=target_chat_id,
                delete_after_run=True,
            )
            self.store.add_reminder(local_event_id, remind_at, job.id)

    def _reminder_times(self, event: LifeEvent) -> list[datetime]:
        if event.event_type in {"flight", "hotel"}:
            return [event.start - timedelta(days=1), event.start - timedelta(hours=3)]
        if event.event_type == "bill":
            return [event.start - timedelta(days=3)]
        return [event.start - timedelta(days=1), event.start - timedelta(hours=1)]

    async def patrol_calendar(self) -> str:
        if not self.enabled:
            return "Life events disabled."
        now = datetime.now()
        end = now + timedelta(hours=getattr(self.config, "calendar_lookahead_hours", 24))
        events = await self.calendar.list_events_async(start_date=now.date(), end_date=end.date(), source="feishu")
        upcoming = [
            event for event in events
            if now <= self._naive(event.start) <= end
        ]
        messages: list[str] = []
        day_key = f"daily:{now.date().isoformat()}"
        if upcoming and not self.store.was_calendar_reminded(day_key):
            lines = ["今日日程摘要："]
            for event in upcoming[:8]:
                lines.append(f"- {self._time_range(event.start, event.end)} {event.title}")
            message = "\n".join(lines)
            await self._notify(message)
            self.store.mark_calendar_reminded(day_key)
            messages.append(message)
        for event in upcoming:
            reminder_key = f"event:{event.platform}:{event.calendar_id}:{event.event_id}:{event.start.isoformat()}"
            if self.store.was_calendar_reminded(reminder_key):
                continue
            starts_in = self._naive(event.start) - now
            if timedelta(minutes=0) <= starts_in <= timedelta(hours=1):
                message = f"日程即将开始：\n{event.title}\n时间：{self._time_range(event.start, event.end)}\n地点：{event.location or '未提供'}"
                await self._notify(message)
                self.store.mark_calendar_reminded(reminder_key)
                messages.append(message)
        return "\n\n".join(messages) if messages else "No calendar reminders due."

    async def patrol_email(self, *, limit: int | None = None) -> str:
        if not self.enabled or not self.mailbox_config or not getattr(self.mailbox_config, "enabled", False):
            return "Life event email patrol disabled."
        from copala.utils.email_reader import EmailReader

        reader = EmailReader(
            host=self.mailbox_config.imap_host,
            port=self.mailbox_config.imap_port,
            username=self.mailbox_config.username,
            password=self.mailbox_config.password,
            timeout=self.mailbox_config.timeout,
            body_max_chars=self.mailbox_config.body_max_chars,
        )
        emails = reader.fetch_emails(
            folder="INBOX",
            limit=limit or getattr(self.mailbox_config, "fetch_limit", 20),
            unread_only=True,
        )
        totals = ProcessResult()
        for email in emails:
            processed = await self.process_text(email.to_text(), source="email", source_ref=f"email:{email.uid}")
            totals.created += processed.created
            totals.updated += processed.updated
            totals.skipped += processed.skipped
            totals.messages.extend(processed.messages)
        return f"Life event email patrol: created={totals.created}, updated={totals.updated}, skipped={totals.skipped}"

    def list_events(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.store.list_events(limit=limit)

    async def _notify(self, content: str, *, channel: str | None = None, chat_id: str | None = None) -> None:
        channel = channel or getattr(self.config, "notify_channel", "qq") or "qq"
        chat_id = chat_id or getattr(self.config, "notify_chat_id", "")
        if not self.send_callback or not chat_id:
            logger.info("Life event notice skipped; notify target missing: {}", content)
            return
        await self.send_callback(OutboundMessage(channel=channel, chat_id=chat_id, content=content))

    def _remember_event(
        self,
        event: LifeEvent,
        *,
        status: str,
        calendar_event: CalendarEvent | None = None,
    ) -> None:
        if not self.memory:
            return
        try:
            from copala.agent.memory_rag.schema import MemoryItem, now_iso

            ref = event.source_ref
            if calendar_event:
                ref = f"feishu:{calendar_event.calendar_id}:{calendar_event.event_id}"
            item = MemoryItem(
                id=f"life_event:{hashlib.sha1((event.source_ref + event.title).encode('utf-8')).hexdigest()[:16]}",
                type="life_event",
                text=f"{event.title}，时间：{self._time_range(event.start, event.end) if event.start else '未提供'}，地点：{event.location or '未提供'}",
                summary=event.summary,
                source=event.source,
                source_ref=ref,
                tags=["life_event", event.event_type, status],
                confidence=event.confidence,
                priority=7 if event.event_type in CALENDAR_TYPES else 5,
                created_at=now_iso(),
                updated_at=now_iso(),
                metadata={
                    "event_type": event.event_type,
                    "status": status,
                    "calendar_event_id": calendar_event.event_id if calendar_event else "",
                    "calendar_id": calendar_event.calendar_id if calendar_event else "",
                },
            )
            self.memory.upsert_many([item])
        except Exception:
            logger.debug("Life event memory upsert skipped", exc_info=True)

    def _calendar_notice(
        self,
        heading: str,
        event: LifeEvent,
        calendar_event: CalendarEvent,
        *,
        conflict: bool,
    ) -> str:
        icon, _ = TYPE_LABELS.get(event.event_type, ("📝", event.event_type))
        lines = [
            f"{icon} {heading}",
            f"⭐ {calendar_event.title or event.title}",
            f"🕒 时间：{self._time_range(calendar_event.start, calendar_event.end)}",
            f"📍 地点：{calendar_event.location or event.location or '未提供'}",
            f"👤 联系人：{event.contact or '未提供'}",
            f"📨 来源：{event.source_ref}",
            f"💡 建议：{self._suggestion(event)}",
        ]
        if conflict:
            lines.append("⚠️ 注意：该时间段与已有日程冲突。")
        return "\n".join(lines)

    @staticmethod
    def _missing_time_message(event: LifeEvent) -> str:
        return "\n".join([
            "发现疑似日程，但缺少开始时间，暂未写入飞书日历。",
            f"来源：{event.source_ref}",
            f"摘要：{event.summary}",
        ])

    @staticmethod
    def _time_range(start: datetime | None, end: datetime | None) -> str:
        if not start:
            return "未提供"
        start_text = start.strftime("%Y-%m-%d %H:%M")
        end_text = end.strftime("%H:%M") if end else "未定"
        return f"{start_text}-{end_text}"

    @staticmethod
    def _naive(value: datetime) -> datetime:
        if value.tzinfo is not None:
            return value.astimezone().replace(tzinfo=None)
        return value

    @staticmethod
    def _suggestion(event: LifeEvent) -> str:
        return {
            "meeting": "建议提前 10 分钟打开会议链接，准备好议题和资料。",
            "interview": "建议提前 15 分钟进入会议，准备简历、项目亮点和常见问题。",
            "medical": "建议提前确认是否空腹，带好身份证、预约码和医保卡。",
            "meal": "建议提前确认人数、路线和是否需要订位。",
            "flight": "建议提前查看天气、交通、值机和行李限制。",
            "hotel": "建议提前确认入住时间、证件、押金和路线。",
            "personal_activity": "建议提前确认路线和装备，预留一点出发缓冲时间。",
            "bill": "建议提前确认余额，避免逾期。",
            "delivery": "建议留意取件码和派送电话。",
        }.get(event.event_type, "建议提前确认关键信息，并预留一点缓冲时间。")
