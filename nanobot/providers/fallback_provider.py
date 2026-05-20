"""Fallback wrapper for primary and backup LLM providers."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable

from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse


class FallbackLLMProvider(LLMProvider):
    """Try a primary provider first, then a backup provider on backend errors."""

    def __init__(
        self,
        *,
        primary: LLMProvider,
        fallback_factory: Callable[[], LLMProvider],
        primary_model: str,
        fallback_model: str,
        state_path: Path,
    ) -> None:
        super().__init__()
        self.primary = primary
        self._fallback_factory = fallback_factory
        self._fallback: LLMProvider | None = None
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.state_path = state_path

    def get_default_model(self) -> str:
        return self.primary_model

    async def chat(
        self,
        messages,
        tools=None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        requested_model = model or self.primary_model
        if self._primary_quota_blocked_today():
            logger.info("Primary LLM quota blocked for today; using fallback model {}", self.fallback_model)
            return await self._chat_fallback(messages, tools, max_tokens, temperature, reasoning_effort)

        primary_response = await self.primary.chat(
            messages=messages,
            tools=tools,
            model=requested_model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )
        if not self._is_error_response(primary_response):
            return primary_response

        logger.warning("Primary LLM failed; switching to fallback. reason={} content={}",
                       primary_response.finish_reason, primary_response.content or "")
        if self._is_quota_error(primary_response.content or ""):
            self._mark_primary_quota_blocked_today(primary_response.content or "")
        fallback_response = await self._chat_fallback(messages, tools, max_tokens, temperature, reasoning_effort)
        if self._is_error_response(fallback_response):
            logger.error("Fallback LLM also failed: {}", fallback_response.content or "")
            return fallback_response
        return fallback_response

    async def _chat_fallback(self, messages, tools, max_tokens, temperature, reasoning_effort) -> LLMResponse:
        provider = self._get_fallback()
        return await provider.chat(
            messages=messages,
            tools=tools,
            model=self.fallback_model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )

    def _get_fallback(self) -> LLMProvider:
        if self._fallback is None:
            self._fallback = self._fallback_factory()
        return self._fallback

    @staticmethod
    def _is_error_response(response: LLMResponse) -> bool:
        if response.finish_reason == "error":
            return True
        content = (response.content or "").lower()
        return content.startswith("error:") or "error calling llm" in content

    @staticmethod
    def _is_quota_error(content: str) -> bool:
        lowered = content.lower()
        markers = ("429", "quota", "rate limit", "rate_limit", "insufficient_quota", "exceeded")
        return any(marker in lowered for marker in markers)

    def _primary_quota_blocked_today(self) -> bool:
        state = self._load_state()
        return state.get("quota_blocked_date") == date.today().isoformat()

    def _mark_primary_quota_blocked_today(self, reason: str) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "quota_blocked_date": date.today().isoformat(),
            "primary_model": self.primary_model,
            "fallback_model": self.fallback_model,
            "reason": reason[:500],
        }
        self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}
