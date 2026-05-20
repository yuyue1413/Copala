"""Tool for inspecting and reprocessing personal life events."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from copala.agent.tools.base import Tool

if TYPE_CHECKING:
    from copala.life_events.service import LifeEventManager


class LifeEventTool(Tool):
    """Expose life-event automation status to the agent."""

    def __init__(self, manager: "LifeEventManager"):
        self._manager = manager

    @property
    def name(self) -> str:
        return "life_event"

    @property
    def description(self) -> str:
        return (
            "Inspect personal life events extracted from emails, messages and calendars. "
            "Calendar writes are automatically formatted before being sent to Feishu."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "status", "reprocess_email"],
                },
                "limit": {"type": "integer"},
            },
            "required": ["action"],
        }

    async def execute(self, action: str, limit: int = 20, **kwargs: Any) -> str:
        if action == "status":
            return (
                f"Life events enabled={self._manager.enabled}, "
                f"notify={self._manager.config.notify_channel}:{self._manager.config.notify_chat_id or '<missing>'}"
            )
        if action == "list":
            items = self._manager.list_events(limit=limit)
            if not items:
                return "No life events recorded."
            lines = ["Life events:"]
            for item in items:
                lines.append(
                    f"- [{item.get('status')}] {item.get('title')} "
                    f"{item.get('start') or ''} source={item.get('source_ref')}"
                )
            return "\n".join(lines)
        if action == "reprocess_email":
            return await self._manager.patrol_email(limit=limit)
        return f"Unknown life_event action: {action}"
