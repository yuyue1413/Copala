"""Friendly calendar formatting helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def event_time_range(event: Any) -> str:
    start = _fmt_dt(getattr(event, "start", None))
    end = _fmt_time(getattr(event, "end", None)) if getattr(event, "end", None) else "未定"
    return f"{start}-{end}"


def format_calendar_events(events: list[Any], *, label: str = "日程", limit: int = 20) -> str:
    if not events:
        return f"{label}没有找到日程。"
    sorted_events = sorted(events, key=lambda item: item.start)
    lines = [f"🗓️ {label}"]
    for index, event in enumerate(sorted_events[:limit], 1):
        title = getattr(event, "title", "") or "未命名日程"
        location = getattr(event, "location", "") or "未提供"
        suggestion = calendar_suggestion(title, location)
        lines.extend([
            "",
            f"{index}. ⭐ {title}",
            f"   🕒 时间：{event_time_range(event)}",
            f"   📍 地点：{location}",
            f"   💡 建议：{suggestion}",
        ])
    if len(sorted_events) > limit:
        lines.append(f"\n还有 {len(sorted_events) - limit} 条日程未展示。")
    return "\n".join(lines)


def calendar_suggestion(title: str, location: str = "") -> str:
    text = f"{title} {location}".lower()
    if any(word in text for word in ("面试", "interview")):
        return "提前 15 分钟进入会议，准备简历、项目亮点和常见问题。"
    if any(word in text for word in ("会议", "同步", "周会", "meeting", "call", "zoom")):
        return "提前 10 分钟准备议题、资料和会议链接。"
    if any(word in text for word in ("体检", "医院", "检查")):
        return "提前确认是否空腹，带好身份证、预约码和医保卡。"
    if any(word in text for word in ("航班", "机票", "起飞", "flight")):
        return "提前查看天气、交通、值机和行李限制。"
    if any(word in text for word in ("酒店", "入住", "hotel")):
        return "提前确认入住时间、证件、押金和路线。"
    if any(word in text for word in ("聚餐", "吃饭", "餐厅")):
        return "提前确认人数、路线和是否需要订位。"
    return "提前确认时间地点，并预留一点缓冲时间。"


def _fmt_dt(value: datetime | None) -> str:
    if not value:
        return "未定"
    return value.strftime("%Y-%m-%d %H:%M")


def _fmt_time(value: datetime | None) -> str:
    if not value:
        return "未定"
    return value.strftime("%H:%M")
