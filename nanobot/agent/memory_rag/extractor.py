"""Heuristic memory extraction for demo-stable personalization."""

from __future__ import annotations

import hashlib
import re

from nanobot.agent.memory_rag.schema import MemoryItem, now_iso


class MemoryExtractor:
    """Extract lightweight structured memories without an extra LLM call."""

    _TRAVEL = ("旅行", "出行", "航班", "酒店", "高铁", "靠窗", "红眼", "行程", "景点", "机票")
    _SHOPPING = ("购物", "购买", "商品", "预算", "品牌", "性价比", "售后", "避雷", "推荐")
    _EMAIL = ("邮件", "发件人", "subject", "from:", "email received", "deadline", "todo")
    _CALENDAR = ("会议", "日程", "calendar", "event", "安排", "提醒", "早会")
    _PREFERENCE = ("喜欢", "偏好", "不喜欢", "希望", "习惯", "倾向", "prefer", "avoid")

    def extract(self, text: str, *, source: str, source_ref: str | None = None) -> list[MemoryItem]:
        """Return candidate memories from text."""
        memories: list[MemoryItem] = []
        for sentence in self._sentences(text):
            lowered = sentence.lower()
            mem_type = self._classify(sentence, lowered)
            if not mem_type:
                continue
            memories.append(
                MemoryItem(
                    id=self._id(mem_type, sentence, source, source_ref),
                    type=mem_type,
                    text=sentence,
                    summary=sentence[:160],
                    source=source,
                    source_ref=source_ref,
                    tags=self._tags(sentence, lowered),
                    confidence=0.75 if mem_type.endswith("_preference") else 0.68,
                    priority=self._priority(mem_type),
                    created_at=now_iso(),
                    updated_at=now_iso(),
                    metadata={},
                )
            )
        return memories

    @staticmethod
    def _sentences(text: str) -> list[str]:
        parts = re.split(r"(?<=[。！？!?])\s+|\n+", text)
        return [p.strip(" -\t\r") for p in parts if 8 <= len(p.strip()) <= 500]

    def _classify(self, sentence: str, lowered: str) -> str | None:
        if self._has(sentence, self._TRAVEL):
            return "travel_preference" if self._has(sentence, self._PREFERENCE) else "plan_artifact"
        if self._has(sentence, self._SHOPPING):
            return "shopping_preference" if self._has(sentence, self._PREFERENCE) else "plan_artifact"
        if self._has(sentence, self._EMAIL) or lowered.startswith(("from:", "subject:")):
            if "deadline" in lowered or "截止" in sentence or "之前" in sentence:
                return "email_deadline"
            return "email_fact"
        if self._has(sentence, self._CALENDAR):
            return "calendar_habit" if self._has(sentence, self._PREFERENCE) else "reminder_rule"
        if self._has(sentence, self._PREFERENCE):
            return "preference"
        return None

    @staticmethod
    def _has(sentence: str, words: tuple[str, ...]) -> bool:
        lowered = sentence.lower()
        return any(w in sentence or w in lowered for w in words)

    def _tags(self, sentence: str, lowered: str) -> list[str]:
        tags = []
        for tag, words in {
            "travel": self._TRAVEL,
            "shopping": self._SHOPPING,
            "email": self._EMAIL,
            "calendar": self._CALENDAR,
            "preference": self._PREFERENCE,
        }.items():
            if any(w in sentence or w in lowered for w in words):
                tags.append(tag)
        return tags

    @staticmethod
    def _priority(mem_type: str) -> int:
        return {
            "travel_preference": 8,
            "shopping_preference": 8,
            "preference": 7,
            "calendar_habit": 7,
            "email_deadline": 6,
            "reminder_rule": 6,
            "email_fact": 4,
            "plan_artifact": 3,
        }.get(mem_type, 0)

    @staticmethod
    def _id(mem_type: str, sentence: str, source: str, source_ref: str | None) -> str:
        raw = f"{mem_type}|{source}|{source_ref or ''}|{sentence}".encode("utf-8")
        return hashlib.sha1(raw).hexdigest()
