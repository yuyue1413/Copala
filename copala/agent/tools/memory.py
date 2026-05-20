"""Tool for RAG-backed personal memory."""

from __future__ import annotations

import hashlib
from typing import Any

from copala.agent.memory_rag.schema import MemoryItem
from copala.agent.memory_rag.store import MemoryRAGStore
from copala.agent.tools.base import Tool


class MemoryTool(Tool):
    """Search and maintain the personal RAG memory store."""

    def __init__(self, store: MemoryRAGStore):
        self._store = store

    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return "Search, save, forget, list, and reindex the user's long-term personal memory."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "save", "forget", "list", "reindex"],
                },
                "query": {"type": "string", "description": "Search query"},
                "text": {"type": "string", "description": "Memory text to save"},
                "type": {"type": "string", "description": "Memory type"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "item_id": {"type": "string", "description": "Memory item id for forget"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        query: str | None = None,
        text: str | None = None,
        type: str | None = None,
        tags: list[str] | None = None,
        item_id: str | None = None,
        limit: int = 10,
        **kwargs: Any,
    ) -> str:
        if action == "reindex":
            count = self._store.reindex_workspace()
            return f"Reindexed {count} memory item(s)."
        if action == "search":
            results = self._store.retrieve(query or "", top_k=limit)
            if not results:
                return "No relevant memory found."
            lines = ["Memory search results:"]
            for result in results:
                item = result.item
                lines.append(
                    f"- {item.id} [{item.type}, score={result.score:.3f}, source={item.source}] {item.text}"
                )
            return "\n".join(lines)
        if action == "list":
            items = self._store.list_items(mem_type=type, limit=limit)
            if not items:
                return "No memory items found."
            return "\n".join(
                f"- {item.id} [{item.type}, source={item.source}] {item.text}" for item in items
            )
        if action == "forget":
            if not item_id:
                return "Error: item_id is required for forget"
            return f"Forgot memory {item_id}." if self._store.forget(item_id) else f"Memory {item_id} not found."
        if action == "save":
            if not text:
                return "Error: text is required for save"
            mem_type = type or "preference"
            item = MemoryItem(
                id=hashlib.sha1(f"tool|{mem_type}|{text}".encode("utf-8")).hexdigest(),
                type=mem_type,
                text=text,
                summary=text[:160],
                source="conversation",
                tags=tags or [],
                confidence=0.9,
                priority=8 if mem_type.endswith("preference") or mem_type == "preference" else 5,
            )
            self._store.upsert(item)
            return f"Saved memory {item.id}."
        return f"Unknown action: {action}"
