"""Tool for travel, mall and short outing planning."""

from __future__ import annotations

from typing import Any

from copala.agent.tools.base import Tool
from copala.planning.outing import OutingPlanningService


class OutingPlanningTool(Tool):
    """Expose context-aware outing planning to the agent."""

    def __init__(self, service: OutingPlanningService):
        self._service = service

    @property
    def name(self) -> str:
        return "outing_plan"

    @property
    def description(self) -> str:
        return (
            "Plan trips, weekend outings, mall visits and temporary outside activities using RAG memory, "
            "Feishu calendar, Amap route/weather and web search. It returns suggestions only."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["plan_trip", "plan_outing", "route_check", "calendar_fit"]},
                "destination": {"type": "string"},
                "time_range": {"type": "string"},
                "origin": {"type": "string"},
                "budget": {"type": "string"},
                "companions": {"type": "string"},
                "preferences": {"type": "string"},
                "constraints": {"type": "string"},
                "city": {"type": "string"},
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        destination: str = "",
        time_range: str = "",
        origin: str = "",
        budget: str = "",
        companions: str = "",
        preferences: str = "",
        constraints: str = "",
        city: str = "",
        **kwargs: Any,
    ) -> str:
        if action == "plan_trip":
            if not destination:
                return "plan_trip requires destination."
            return await self._service.plan_trip(
                destination=destination,
                time_range=time_range,
                origin=origin,
                budget=budget,
                companions=companions,
                preferences=preferences,
                constraints=constraints,
            )
        if action == "plan_outing":
            if not destination:
                return "plan_outing requires destination."
            return await self._service.plan_outing(
                destination=destination,
                time_range=time_range,
                origin=origin,
                budget=budget,
                companions=companions,
                preferences=preferences,
                constraints=constraints,
            )
        if action == "route_check":
            if not destination:
                return "route_check requires destination."
            return await self._service.route_check(destination=destination, origin=origin, city=city)
        if action == "calendar_fit":
            return await self._service.calendar_fit(time_range=time_range)
        return f"Unknown outing_plan action: {action}"
