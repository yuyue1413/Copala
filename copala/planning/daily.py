"""Daily schedule planning over calendar events, life events and memory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from copala.calendar.service import CalendarEvent


@dataclass
class PlanningTask:
    title: str
    duration_minutes: int
    location: str = ""
    priority: int = 5
    source: str = "request"
    suggestion: str = ""


@dataclass
class PlannedItem:
    title: str
    start: datetime
    end: datetime
    location: str = ""
    fixed: bool = False
    suggestion: str = ""
    source: str = "calendar"


class DailyPlanningService:
    """Create a practical day plan without mutating the user's calendar."""

    TASK_RULES: tuple[tuple[str, tuple[str, ...], int, int, str, str], ...] = (
        ("取快递", ("取快递", "拿快递", "快递"), 20, 6, "小区驿站", "尽量放在出门顺路或两段日程之间，避免专门折返。"),
        ("买药", ("买药", "药店", "买点药"), 30, 8, "药店", "建议顺路完成，若身体不舒服就把它放在较早时间。"),
        ("回复邮件", ("回复邮件", "回邮件", "处理邮件", "邮件回复"), 30, 7, "线上", "建议放在安静空档，先处理最影响后续安排的邮件。"),
        ("购物", ("购物", "买东西", "采购"), 60, 5, "待确认", "建议列好清单，避免临时增加太多耗时。"),
        ("取餐/吃饭", ("吃饭", "取餐", "买饭"), 45, 6, "待确认", "建议和下一个地点顺路安排，预留排队时间。"),
    )

    def __init__(
        self,
        *,
        config: Any,
        calendar_service: Any,
        life_events: Any | None = None,
        memory_store: Any | None = None,
    ) -> None:
        self.config = config
        self.calendar = calendar_service
        self.life_events = life_events
        self.memory = memory_store

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "enabled", True))

    async def plan_day(
        self,
        request: str = "",
        *,
        target_date: date | None = None,
        mode: str = "plan_day",
    ) -> str:
        if not self.enabled:
            return "主动规划功能未启用。"
        day = target_date or self._target_date_from_text(request)
        fixed_events = await self.calendar.list_events_async(
            start_date=day,
            end_date=day,
            source="feishu",
        )
        tasks = self._collect_tasks(request, day)
        memories = self._retrieve_memories(request, day)
        buffer_minutes = self._buffer_minutes(memories)
        planned, delayed, risks = self._schedule(day, fixed_events, tasks, buffer_minutes)
        return self._render(day, planned, delayed, risks, memories, mode=mode)

    async def free_slots(self, *, target_date: date | None = None) -> str:
        day = target_date or date.today()
        events = await self.calendar.list_events_async(start_date=day, end_date=day, source="feishu")
        slots = self._free_slots(day, events, self._base_buffer_minutes())
        if not slots:
            return f"{day.isoformat()} 暂时没有可用空档。"
        lines = [f"🧩 {day.isoformat()} 可用空档"]
        for start, end in slots:
            minutes = int((end - start).total_seconds() // 60)
            lines.append(f"- {start.strftime('%H:%M')}-{end.strftime('%H:%M')}，约 {minutes} 分钟")
        return "\n".join(lines)

    async def replan_with_risks(
        self,
        risks: list[Any],
        request: str = "",
        *,
        target_date: date | None = None,
    ) -> str:
        """Render a day plan with explicit risk context, without changing the calendar."""
        base = await self.plan_day(request, target_date=target_date, mode="reschedule")
        if not risks:
            return base
        lines = [base, "", "风险调整建议："]
        for risk in risks:
            if isinstance(risk, dict):
                title = str(risk.get("title") or "风险事件")
                suggestion = str(risk.get("suggestion") or "建议预留缓冲并准备备选方案。")
            else:
                title = str(getattr(risk, "title", "风险事件"))
                suggestion = str(getattr(risk, "suggestion", "建议预留缓冲并准备备选方案。"))
            lines.append(f"- {title}：{suggestion}")
        lines.append("说明：以上为重规划建议，不会自动修改飞书日历。")
        return "\n".join(lines)

    def _collect_tasks(self, request: str, day: date) -> list[PlanningTask]:
        tasks = self._tasks_from_request(request)
        tasks.extend(self._tasks_from_life_events(day))
        return self._dedupe_tasks(tasks)

    def _tasks_from_request(self, request: str) -> list[PlanningTask]:
        tasks: list[PlanningTask] = []
        for title, keywords, duration, priority, location, suggestion in self.TASK_RULES:
            if any(word in request for word in keywords):
                tasks.append(PlanningTask(title, duration, location, priority, "request", suggestion))
        return tasks

    def _tasks_from_life_events(self, day: date) -> list[PlanningTask]:
        if not self.life_events:
            return []
        rows = self.life_events.list_events(limit=80)
        tasks: list[PlanningTask] = []
        for row in rows:
            status = str(row.get("status") or "")
            if status not in {"todo", "missing_time", "low_confidence"}:
                continue
            start = self._parse_dt(row.get("start") or "")
            if start and start.date() != day:
                continue
            title = str(row.get("title") or row.get("raw_summary") or "待办事项")
            tasks.append(
                PlanningTask(
                    title=self._clean_title(title),
                    duration_minutes=int(row.get("duration_minutes") or self._duration_for_type(row.get("type"))),
                    location=str(row.get("location") or "待确认"),
                    priority=int(row.get("priority") or self._priority_for_type(row.get("type"))),
                    source="life_event",
                    suggestion="来自生活事件库，建议结合当天空档处理。",
                )
            )
        return tasks

    def _retrieve_memories(self, request: str, day: date) -> list[Any]:
        if not self.memory:
            return []
        try:
            query = f"日程规划 时间偏好 通勤 任务安排 {day.isoformat()} {request}"
            return list(self.memory.retrieve(query, top_k=4))
        except Exception:
            return []

    def _schedule(
        self,
        day: date,
        fixed_events: list[CalendarEvent],
        tasks: list[PlanningTask],
        buffer_minutes: int,
    ) -> tuple[list[PlannedItem], list[PlanningTask], list[str]]:
        planned = [self._fixed_item(event) for event in fixed_events]
        movable = sorted(tasks, key=lambda item: (-item.priority, item.duration_minutes, item.title))
        delayed: list[PlanningTask] = []
        risks: list[str] = []
        slots = self._free_slots(day, fixed_events, buffer_minutes)
        for task in movable:
            placed = False
            for index, (slot_start, slot_end) in enumerate(list(slots)):
                duration = timedelta(minutes=task.duration_minutes)
                if slot_start + duration <= slot_end:
                    item = PlannedItem(
                        title=task.title,
                        start=slot_start,
                        end=slot_start + duration,
                        location=task.location,
                        fixed=False,
                        suggestion=task.suggestion or "建议预留缓冲，完成后再进入下一项安排。",
                        source=task.source,
                    )
                    planned.append(item)
                    new_start = item.end + timedelta(minutes=buffer_minutes)
                    slots[index] = (new_start, slot_end)
                    placed = True
                    break
            if not placed:
                delayed.append(task)
        if delayed:
            risks.append("今天空档不足，部分任务建议延期或压缩处理。")
        if buffer_minutes >= 25:
            risks.append("已根据偏好增加缓冲时间，整体节奏会更稳一些。")
        planned.sort(key=lambda item: item.start)
        return planned, delayed, risks

    def _free_slots(
        self,
        day: date,
        fixed_events: list[CalendarEvent],
        buffer_minutes: int,
    ) -> list[tuple[datetime, datetime]]:
        day_start = datetime.combine(day, time(hour=int(getattr(self.config, "day_start_hour", 8))))
        day_end = datetime.combine(day, time(hour=int(getattr(self.config, "day_end_hour", 22))))
        if day == date.today():
            now = datetime.now().replace(second=0, microsecond=0)
            day_start = max(day_start, now + timedelta(minutes=self._base_buffer_minutes()))
        slots: list[tuple[datetime, datetime]] = []
        cursor = day_start
        buffer = timedelta(minutes=buffer_minutes)
        for event in sorted(fixed_events, key=lambda item: self._naive(item.start)):
            start = self._naive(event.start)
            end = self._naive(event.end or event.start + timedelta(minutes=30))
            gap_end = start - buffer
            if cursor < gap_end:
                slots.append((cursor, gap_end))
            cursor = max(cursor, end + buffer)
        if cursor < day_end:
            slots.append((cursor, day_end))
        return slots

    def _render(
        self,
        day: date,
        planned: list[PlannedItem],
        delayed: list[PlanningTask],
        risks: list[str],
        memories: list[Any],
        *,
        mode: str,
    ) -> str:
        heading = "🔁 今日重新规划" if mode == "reschedule" else "🗓️ 今日主动规划"
        if day != date.today():
            heading = f"🗓️ {day.isoformat()} 主动规划"
        lines = [heading, ""]
        if not planned:
            lines.append("今天没有可规划的日程或待办，可以安排一段完整的深度工作时间。")
        for item in planned:
            marker = "📌" if item.fixed else "✅"
            lines.extend([
                f"{item.start.strftime('%H:%M')}-{item.end.strftime('%H:%M')}  {marker} {item.title}",
                f"📍 地点：{item.location or '未提供'}",
                f"💡 建议：{item.suggestion or self._suggestion_for_title(item.title)}",
                "",
            ])
        if delayed:
            lines.append("⚠️ 建议延期")
            for task in delayed:
                lines.append(f"- {task.title}：预计 {task.duration_minutes} 分钟，今天空档不足。")
            lines.append("")
        if risks:
            lines.append("⚠️ 风险提醒")
            lines.extend(f"- {risk}" for risk in risks)
            lines.append("")
        if memories:
            lines.append("🧠 个性化依据")
            for memory in memories[:3]:
                item = getattr(memory, "item", memory)
                text = getattr(item, "text", "")
                if text:
                    lines.append(f"- {text[:80]}")
        lines.append("说明：这是规划建议，不会自动修改飞书日历；需要写入或改期时请明确告诉我。")
        return "\n".join(lines).strip()

    def _buffer_minutes(self, memories: list[Any]) -> int:
        base = self._base_buffer_minutes()
        text = " ".join(getattr(getattr(memory, "item", memory), "text", "") for memory in memories)
        if any(word in text for word in ("不喜欢太赶", "轻松", "缓冲", "留白")):
            return max(base, base + 10)
        return base

    def _base_buffer_minutes(self) -> int:
        return int(getattr(self.config, "default_buffer_minutes", 15))

    @staticmethod
    def _fixed_item(event: CalendarEvent) -> PlannedItem:
        end = event.end or event.start + timedelta(minutes=30)
        return PlannedItem(
            title=event.title,
            start=DailyPlanningService._naive(event.start),
            end=DailyPlanningService._naive(end),
            location=event.location,
            fixed=True,
            suggestion=DailyPlanningService._suggestion_for_title(event.title),
            source=event.source,
        )

    @staticmethod
    def _target_date_from_text(text: str) -> date:
        today = date.today()
        if "后天" in text:
            return today + timedelta(days=2)
        if "明天" in text:
            return today + timedelta(days=1)
        match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
        if match:
            year, month, day = map(int, match.groups())
            return date(year, month, day)
        return today

    @staticmethod
    def _duration_for_type(event_type: Any) -> int:
        return {
            "delivery": 20,
            "bill": 20,
            "shopping": 60,
            "generic_todo": 30,
        }.get(str(event_type or ""), 30)

    @staticmethod
    def _priority_for_type(event_type: Any) -> int:
        return {
            "bill": 9,
            "delivery": 6,
            "shopping": 5,
            "generic_todo": 5,
        }.get(str(event_type or ""), 5)

    @staticmethod
    def _dedupe_tasks(tasks: list[PlanningTask]) -> list[PlanningTask]:
        seen: set[str] = set()
        result: list[PlanningTask] = []
        for task in tasks:
            key = task.title
            if key in seen:
                continue
            seen.add(key)
            result.append(task)
        return result

    @staticmethod
    def _clean_title(title: str) -> str:
        return re.sub(r"^[^\w\u4e00-\u9fff]*", "", title).strip() or "待办事项"

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

    @staticmethod
    def _suggestion_for_title(title: str) -> str:
        text = title.lower()
        if any(word in text for word in ("会议", "同步", "周会", "meeting")):
            return "提前 10 分钟准备议题、资料和会议链接。"
        if any(word in text for word in ("健身", "运动", "乒乓球")):
            return "预留换装和路上时间，结束后注意补水。"
        if any(word in text for word in ("聚餐", "吃饭", "餐厅")):
            return "提前确认路线、人数和是否需要订位。"
        return "确认时间地点，并给前后安排留出一点缓冲。"
