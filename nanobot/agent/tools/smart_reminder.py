"""Tool for context-aware smart reminders and risk checks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.smart_reminders.service import SmartReminderService


class SmartReminderTool(Tool):
    """Expose smart reminder risk checks to the agent."""

    def __init__(self, service: "SmartReminderService"):
        self._service = service

    @property
    def name(self) -> str:
        return "smart_reminder"

    @property
    def description(self) -> str:
        return (
            "Check weather, traffic, calendar and deadline risks using Amap, calendar, life events and memory. "
            "Use this before claiming weather/route risk."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["check_risks", "patrol", "status", "route_risk"]},
                "days": {"type": "integer", "minimum": 1, "maximum": 7},
                "origin": {"type": "string"},
                "destination": {"type": "string"},
                "city": {"type": "string"},
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        days: int = 2,
        origin: str = "",
        destination: str = "",
        city: str = "",
        **kwargs: Any,
    ) -> str:
        if action == "status":
            return self._service.status()
        if action == "check_risks":
            risks = await self._service.check_risks(days=days)
            return self._service.format_risks(risks)
        if action == "patrol":
            return await self._service.patrol()
        if action == "route_risk":
            if not origin or not destination:
                return "route_risk requires origin and destination."
            return await self._service.route_risk(origin, destination, city=city)
        return f"Unknown smart_reminder action: {action}"
