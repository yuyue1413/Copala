"""Tool for proactive daily schedule planning."""

from __future__ import annotations

from datetime import date
from typing import Any

from copala.agent.tools.base import Tool
from copala.planning.daily import DailyPlanningService


class DailyPlanningTool(Tool):
    """Expose proactive daily planning to the agent."""

    def __init__(self, service: DailyPlanningService):
        self._service = service

    @property
    def name(self) -> str:
        return "daily_plan"

    @property
    def description(self) -> str:
        return (
            "Plan or re-plan a user's day from Feishu calendar events, life events and memory. "
            "Use this for schedule optimization requests; it returns suggestions only and does not mutate calendars."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["plan_day", "reschedule", "free_slots"]},
                "request": {"type": "string"},
                "date": {"type": "string", "description": "Optional YYYY-MM-DD date."},
            },
            "required": ["action"],
        }

    async def execute(self, action: str, request: str = "", date: str | None = None, **kwargs: Any) -> str:
        target_date = self._parse_date(date) if date else None
        if action == "free_slots":
            return await self._service.free_slots(target_date=target_date)
        if action in {"plan_day", "reschedule"}:
            return await self._service.plan_day(request, target_date=target_date, mode=action)
        return f"Unknown daily_plan action: {action}"

    @staticmethod
    def _parse_date(raw: str | None) -> date | None:
        if not raw:
            return None
        return date.fromisoformat(raw)
