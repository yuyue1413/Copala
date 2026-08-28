"""Context-aware smart reminders with Amap weather and route risk checks."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from copala.bus.events import OutboundMessage
from copala.context_awareness.amap import AmapClient, AmapError, AmapRouteUnavailable

if TYPE_CHECKING:
    from copala.agent.memory_rag.store import MemoryRAGStore
    from copala.calendar.service import CalendarService
    from copala.config.schema import SmartRemindersConfig
    from copala.life_events.service import LifeEventManager
    from copala.planning.daily import DailyPlanningService


@dataclass
class RiskItem:
    risk_type: str
    title: str
    severity: str
    confidence: float
    basis: str
    suggestion: str
    source_ref: str
    event_time: datetime | None = None

    @property
    def key(self) -> str:
        raw = "|".join([self.risk_type, self.source_ref, self.title, self.event_time.isoformat() if self.event_time else ""])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


class SmartReminderStore:
    def __init__(self, workspace: Path) -> None:
        self.path = workspace / "smart_reminders" / "risks.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def _connect(self):
        """Yield a transactional connection and always close it."""
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notified_risks (
                    key TEXT PRIMARY KEY,
                    risk_type TEXT,
                    title TEXT,
                    notified_at TEXT
                )
                """
            )

    def was_notified(self, risk: RiskItem) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM notified_risks WHERE key=?", (risk.key,)).fetchone() is not None

    def mark_notified(self, risk: RiskItem) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO notified_risks (key, risk_type, title, notified_at) VALUES (?, ?, ?, ?)",
                (risk.key, risk.risk_type, risk.title, datetime.now().isoformat()),
            )


class SmartReminderService:
    """Generate context-aware reminders and risk warnings."""

    WEATHER_RISK_WORDS = ("雨", "雪", "雷", "大风", "暴", "高温", "冰雹")

    def __init__(
        self,
        *,
        workspace: Path,
        config: "SmartRemindersConfig",
        calendar_service: "CalendarService",
        life_events: "LifeEventManager | None" = None,
        daily_planning: "DailyPlanningService | None" = None,
        memory_store: "MemoryRAGStore | None" = None,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
    ) -> None:
        self.workspace = workspace
        self.config = config
        self.calendar = calendar_service
        self.life_events = life_events
        self.daily_planning = daily_planning
        self.memory = memory_store
        self.send_callback = send_callback
        amap_cfg = getattr(config, "amap", None)
        self.amap = AmapClient(
            api_key=getattr(amap_cfg, "api_key", ""),
            api_base=getattr(amap_cfg, "api_base", "https://restapi.amap.com"),
            timeout_seconds=getattr(amap_cfg, "timeout_seconds", 8),
        )
        self._store: SmartReminderStore | None = None

    @property
    def store(self) -> SmartReminderStore:
        if self._store is None:
            self._store = SmartReminderStore(self.workspace)
        return self._store

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "enabled", True))

    def status(self) -> str:
        amap_cfg = getattr(self.config, "amap", None)
        return "\n".join([
            "情境感知智能提醒状态：",
            f"- enabled: {self.enabled}",
            f"- amap.enabled: {bool(getattr(amap_cfg, 'enabled', False))}",
            f"- amap.apiKey: {'已配置' if getattr(amap_cfg, 'api_key', '') else '未配置'}",
            f"- defaultCity: {getattr(amap_cfg, 'default_city', '') or '未配置'}",
            f"- routeMode: {getattr(amap_cfg, 'route_mode', 'driving')}",
        ])

    async def check_risks(self, *, days: int = 2) -> list[RiskItem]:
        if not self.enabled:
            return []
        risks: list[RiskItem] = []
        risks.extend(await self._weather_risks(days))
        risks.extend(await self._route_risks(days))
        risks.extend(self._bill_risks(days))
        threshold = float(getattr(self.config, "min_risk_confidence", 0.75))
        return [risk for risk in risks if risk.confidence >= threshold]

    async def patrol(self) -> str:
        risks = await self.check_risks(days=max(1, int(getattr(self.config, "lookahead_hours", 48)) // 24))
        fresh = [risk for risk in risks if not self.store.was_notified(risk)]
        if not fresh:
            return "暂无新的情境风险。"
        message = self.format_risks(fresh)
        await self._notify(message)
        for risk in fresh:
            self.store.mark_notified(risk)
        return message

    async def route_risk(self, origin: str, destination: str, city: str = "") -> str:
        try:
            route = await self._route_between(origin, destination, city=city)
            minutes = int(route["duration_seconds"] // 60)
            distance = int(route["distance_meters"] // 1000)
            return f"路线评估：{origin} -> {destination}\n预计耗时：{minutes} 分钟\n距离：约 {distance} 公里\n方式：{route['mode']}"
        except Exception as exc:
            logger.warning("Smart reminder route risk failed: {}", exc)
            return f"无法确认路线风险：{exc}"

    async def plan_reminders(self, event: dict[str, Any]) -> str:
        event_type = str(event.get("type") or "")
        title = str(event.get("title") or "事件")
        start = self._parse_dt(str(event.get("start") or ""))
        if event_type == "bill" and start:
            remind_at = start - timedelta(days=3)
            return f"建议在 {remind_at.strftime('%Y-%m-%d %H:%M')} 提醒：{title}，避免临近截止时间才处理。"
        if event_type in {"flight", "hotel"} and start:
            return f"建议提前 1 天和提前 3 小时提醒：{title}，同时检查天气、交通和证件。"
        if start:
            return f"建议提前 1 小时提醒：{title}，并预留交通缓冲。"
        return f"事件 {title} 缺少明确时间，建议先补充时间后再设置智能提醒。"

    def format_risks(self, risks: list[RiskItem]) -> str:
        if not risks:
            return "未来一段时间暂未发现明显天气、交通或任务风险。"
        lines = ["⚠️ 情境感知风险预警"]
        for risk in risks:
            time_text = risk.event_time.strftime("%Y-%m-%d %H:%M") if risk.event_time else "未提供"
            lines.extend([
                "",
                f"- {risk.title}",
                f"  类型：{risk.risk_type} / {risk.severity}",
                f"  时间：{time_text}",
                f"  依据：{risk.basis}",
                f"  建议：{risk.suggestion}",
            ])
        return "\n".join(lines)

    async def _weather_risks(self, days: int) -> list[RiskItem]:
        amap_cfg = getattr(self.config, "amap", None)
        if not getattr(amap_cfg, "enabled", False):
            return []
        risks: list[RiskItem] = []
        for row in self._life_event_rows(days):
            if not row.get("weather_sensitive") and not row.get("outdoor"):
                continue
            when = self._parse_dt(row.get("start") or "")
            if not when:
                continue
            location = str(row.get("location") or "")
            try:
                city_or_adcode = await self._city_or_adcode(location)
                weather = await self.amap.weather(city_or_adcode, extensions="base")
            except Exception as exc:
                logger.info("Weather risk skipped for {}: {}", row.get("title"), exc)
                continue
            text = str(weather.get("weather") or "")
            if any(word in text for word in self._weather_keywords()):
                risks.append(RiskItem(
                    risk_type="weather",
                    title=str(row.get("title") or "室外活动"),
                    severity="medium",
                    confidence=0.86,
                    basis=f"高德天气：{weather.get('city')} {text} {weather.get('temperature')}℃",
                    suggestion="建议改到室内场馆、调整时间，或准备备用活动。",
                    source_ref=str(row.get("source_ref") or row.get("id") or ""),
                    event_time=when,
                ))
        return risks

    async def _route_risks(self, days: int) -> list[RiskItem]:
        amap_cfg = getattr(self.config, "amap", None)
        if not getattr(amap_cfg, "enabled", False):
            return []
        end = date.today() + timedelta(days=days)
        events = await self.calendar.list_events_async(start_date=date.today(), end_date=end, source="feishu")
        events = sorted((event for event in events if event.location and event.start), key=lambda event: self._naive(event.start))
        risks: list[RiskItem] = []
        buffer_minutes = int(getattr(amap_cfg, "default_travel_buffer_minutes", 15))
        for prev, curr in zip(events, events[1:]):
            if not self._is_physical_location(prev.location) or not self._is_physical_location(curr.location):
                continue
            prev_end = self._naive(prev.end or prev.start + timedelta(minutes=30))
            curr_start = self._naive(curr.start)
            gap_minutes = int((curr_start - prev_end).total_seconds() // 60)
            if gap_minutes <= 0:
                continue
            try:
                route = await self._route_between(prev.location, curr.location)
            except AmapRouteUnavailable as exc:
                logger.debug("Route risk skipped: {}", exc)
                continue
            except Exception as exc:
                logger.info("Route risk skipped: {}", exc)
                continue
            need = int(route["duration_seconds"] // 60) + buffer_minutes
            if gap_minutes < need:
                risks.append(RiskItem(
                    risk_type="traffic",
                    title=f"{prev.title} -> {curr.title}",
                    severity="high" if gap_minutes < need - 15 else "medium",
                    confidence=0.84,
                    basis=f"高德路线预计 {need - buffer_minutes} 分钟，缓冲 {buffer_minutes} 分钟，实际空档 {gap_minutes} 分钟",
                    suggestion="建议提前出发、压缩前一项安排，或重新规划当天日程。",
                    source_ref=f"{prev.event_id or prev.id}:{curr.event_id or curr.id}",
                    event_time=curr.start,
                ))
        return risks

    def _bill_risks(self, days: int) -> list[RiskItem]:
        risks: list[RiskItem] = []
        for row in self._life_event_rows(days):
            if row.get("type") != "bill":
                continue
            start = self._parse_dt(row.get("start") or row.get("deadline") or "")
            if not start:
                continue
            hours = (start - datetime.now()).total_seconds() / 3600
            if 0 <= hours <= 72:
                risks.append(RiskItem(
                    risk_type="deadline",
                    title=str(row.get("title") or "账单提醒"),
                    severity="high" if hours <= 24 else "medium",
                    confidence=0.9,
                    basis=f"账单/缴费截止时间接近：{start.strftime('%Y-%m-%d %H:%M')}",
                    suggestion="建议提前确认余额并完成支付，避免逾期。",
                    source_ref=str(row.get("source_ref") or row.get("id") or ""),
                    event_time=start,
                ))
        return risks

    async def _route_between(self, origin_text: str, destination_text: str, *, city: str = "") -> dict[str, Any]:
        amap_cfg = getattr(self.config, "amap", None)
        default_city = city or getattr(amap_cfg, "default_city", "")
        if not self._is_physical_location(origin_text) or not self._is_physical_location(destination_text):
            raise AmapRouteUnavailable("非线下地点不计算路线")
        origin = await self.amap.geocode(origin_text, default_city)
        destination = await self.amap.geocode(destination_text, default_city)
        if origin.get("location") == destination.get("location"):
            raise AmapRouteUnavailable("起终点坐标相同")
        mode = str(getattr(amap_cfg, "route_mode", "driving") or "driving")
        if mode == "transit":
            return await self.amap.transit(origin["location"], destination["location"], origin.get("city") or default_city)
        return await self.amap.driving(origin["location"], destination["location"])

    @staticmethod
    def _is_physical_location(location: str | None) -> bool:
        text = (location or "").strip().lower()
        if not text:
            return False
        virtual_markers = (
            "未提供",
            "线上",
            "飞书会议",
            "腾讯会议",
            "zoom",
            "meeting",
            "http://",
            "https://",
            "待确认",
        )
        return not any(marker in text for marker in virtual_markers)

    async def _city_or_adcode(self, location: str) -> str:
        amap_cfg = getattr(self.config, "amap", None)
        default_city = getattr(amap_cfg, "default_city", "")
        if location and location != "未提供":
            try:
                geo = await self.amap.geocode(location, default_city)
                return str(geo.get("adcode") or geo.get("city") or default_city)
            except AmapError:
                pass
        return default_city

    def _life_event_rows(self, days: int) -> list[dict[str, Any]]:
        if not self.life_events:
            return []
        rows = self.life_events.list_events(limit=100)
        end = datetime.now() + timedelta(days=days)
        result = []
        for row in rows:
            start = self._parse_dt(row.get("start") or row.get("deadline") or "")
            if not start or start <= end:
                result.append(row)
        return result

    def _weather_keywords(self) -> tuple[str, ...]:
        amap_cfg = getattr(self.config, "amap", None)
        keywords = getattr(amap_cfg, "outdoor_weather_keywords", None)
        return tuple(keywords or self.WEATHER_RISK_WORDS)

    async def _notify(self, content: str) -> None:
        if not self.send_callback:
            return
        channel = getattr(self.config, "notify_channel", "") or ""
        chat_id = getattr(self.config, "notify_chat_id", "") or ""
        if not channel or not chat_id:
            logger.info("Smart reminder notice skipped; notify target missing: {}", content)
            return
        await self.send_callback(OutboundMessage(channel=channel, chat_id=chat_id, content=content))

    @staticmethod
    def _parse_dt(raw: str) -> datetime | None:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    @staticmethod
    def _naive(value: datetime) -> datetime:
        if value.tzinfo is not None:
            return value.astimezone().replace(tzinfo=None)
        return value
