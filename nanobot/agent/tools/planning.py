"""Personalized travel and shopping planning tools."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any

from nanobot.agent.memory_rag.schema import MemoryItem
from nanobot.agent.memory_rag.store import MemoryRAGStore
from nanobot.agent.tools.base import Tool
from nanobot.calendar.service import CalendarService


class TravelPlanTool(Tool):
    """Create a personalized travel planning brief."""

    def __init__(self, store: MemoryRAGStore, calendar: CalendarService | None = None):
        self._store = store
        self._calendar = calendar

    @property
    def name(self) -> str:
        return "travel_plan"

    @property
    def description(self) -> str:
        return "Create a personalized travel plan using long-term memory and local calendar availability."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "destination": {"type": "string"},
                "dates": {"type": "string"},
                "budget": {"type": "string"},
                "companions": {"type": "string"},
                "constraints": {"type": "string"},
            },
            "required": ["destination"],
        }

    async def execute(
        self,
        destination: str,
        dates: str = "",
        budget: str = "",
        companions: str = "",
        constraints: str = "",
        **kwargs: Any,
    ) -> str:
        query = f"旅行 出行 航班 酒店 行程 {destination} {dates} {budget} {constraints}"
        memories = self._store.retrieve(query, top_k=6)
        events = self._calendar.list_events(
            start_date=date.today(),
            end_date=date.today() + timedelta(days=30),
        )[:8] if self._calendar else []
        result = self._render_travel(destination, dates, budget, companions, constraints, memories, events)
        self._save_plan("travel", result, ["travel", destination])
        return result

    @staticmethod
    def _render_travel(destination, dates, budget, companions, constraints, memories, events) -> str:
        lines = [
            f"Personalized travel plan: {destination}",
            "",
            "Inputs:",
            f"- Dates: {dates or 'flexible / not specified'}",
            f"- Budget: {budget or 'not specified'}",
            f"- Companions: {companions or 'not specified'}",
            f"- Constraints: {constraints or 'none'}",
            "",
            "Personal memory used:",
        ]
        lines.extend(
            f"- [{m.item.type}, {m.item.source}, score={m.score:.3f}] {m.item.text}" for m in memories
        )
        if not memories:
            lines.append("- No matching personal memory yet; ask follow-up questions before booking.")
        lines.extend(["", "Calendar conflicts to consider:"])
        lines.extend(f"- {e.start.isoformat()}: {e.title}" for e in events)
        if not events:
            lines.append("- No upcoming local calendar events found.")
        lines.extend(
            [
                "",
                "Suggested plan:",
                "- Keep the itinerary light and leave recovery buffers between major stops.",
                "- Use web_search/web_fetch for current transport, hotel, opening-hour, and weather facts before finalizing.",
                "- Prefer choices that match the retrieved personal constraints above.",
            ]
        )
        return "\n".join(lines)

    def _save_plan(self, kind: str, text: str, tags: list[str]) -> None:
        item = MemoryItem(
            id=hashlib.sha1(f"plan|{kind}|{text[:200]}".encode("utf-8")).hexdigest(),
            type="plan_artifact",
            text=text[:4000],
            summary=text[:160],
            source="conversation",
            tags=tags,
            confidence=0.8,
            priority=4,
        )
        self._store.upsert(item)


class ShoppingPlanTool(Tool):
    """Create a personalized shopping decision brief."""

    def __init__(self, store: MemoryRAGStore):
        self._store = store

    @property
    def name(self) -> str:
        return "shopping_plan"

    @property
    def description(self) -> str:
        return "Create a personalized shopping plan using long-term memory and web-search-ready criteria."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "budget": {"type": "string"},
                "priorities": {"type": "string"},
                "avoid": {"type": "string"},
            },
            "required": ["category"],
        }

    async def execute(
        self,
        category: str,
        budget: str = "",
        priorities: str = "",
        avoid: str = "",
        **kwargs: Any,
    ) -> str:
        query = f"购物 商品 品牌 预算 性价比 售后 {category} {budget} {priorities} {avoid}"
        memories = self._store.retrieve(query, top_k=6)
        lines = [
            f"Personalized shopping plan: {category}",
            "",
            "Inputs:",
            f"- Budget: {budget or 'not specified'}",
            f"- Priorities: {priorities or 'not specified'}",
            f"- Avoid: {avoid or 'none'}",
            "",
            "Personal memory used:",
        ]
        lines.extend(f"- [{m.item.type}, {m.item.source}, score={m.score:.3f}] {m.item.text}" for m in memories)
        if not memories:
            lines.append("- No matching shopping memory yet; ask for brand, budget, and must-have constraints.")
        lines.extend(
            [
                "",
                "Decision framework:",
                "- Compare 3-5 candidates by price, must-have features, warranty, return policy, and known user preferences.",
                "- Use web_search/web_fetch for current prices and reviews before recommending a final pick.",
                "- Save any clear preference or avoid-list item back to memory after the user confirms it.",
            ]
        )
        result = "\n".join(lines)
        self._store.upsert(
            MemoryItem(
                id=hashlib.sha1(f"plan|shopping|{result[:200]}".encode("utf-8")).hexdigest(),
                type="plan_artifact",
                text=result[:4000],
                summary=result[:160],
                source="conversation",
                tags=["shopping", category],
                confidence=0.8,
                priority=4,
            )
        )
        return result
