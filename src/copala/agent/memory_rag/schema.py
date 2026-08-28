"""Schemas for RAG-backed long-term memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def now_iso() -> str:
    """Return a stable local ISO timestamp."""
    return datetime.now().replace(microsecond=0).isoformat()


@dataclass
class MemoryItem:
    """A structured long-term memory item."""

    id: str
    type: str
    text: str
    summary: str = ""
    source: str = "manual"
    source_ref: str | None = None
    tags: list[str] = field(default_factory=list)
    confidence: float = 1.0
    priority: int = 0
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievedMemory:
    """A memory item with retrieval metadata."""

    item: MemoryItem
    score: float
    retrievers: list[str] = field(default_factory=list)
