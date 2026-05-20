from __future__ import annotations

import asyncio
from datetime import date, timedelta

from copala.providers.base import LLMProvider, LLMResponse
from copala.providers.fallback_provider import FallbackLLMProvider


class DummyProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse], default_model: str):
        super().__init__()
        self.responses = responses
        self.default_model = default_model
        self.calls: list[str | None] = []

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7, reasoning_effort=None):
        self.calls.append(model)
        return self.responses.pop(0)

    def get_default_model(self) -> str:
        return self.default_model


def test_fallback_provider_uses_backup_on_quota_and_blocks_primary_for_today(tmp_path) -> None:
    async def run() -> None:
        primary = DummyProvider(
            [LLMResponse("Error: Error code: 429 quota exceeded", finish_reason="error")],
            "primary-model",
        )
        fallback = DummyProvider([LLMResponse("ok"), LLMResponse("ok")], "fallback-model")
        wrapper = FallbackLLMProvider(
            primary=primary,
            fallback_factory=lambda: fallback,
            primary_model="primary-model",
            fallback_model="fallback-model",
            state_path=tmp_path / "state.json",
        )

        first = await wrapper.chat([])
        second = await wrapper.chat([])

        assert first.content == "ok"
        assert second.content == "ok"
        assert primary.calls == ["primary-model"]
        assert fallback.calls == ["fallback-model", "fallback-model"]

    asyncio.run(run())


def test_fallback_provider_retries_primary_next_day(tmp_path) -> None:
    state = tmp_path / "state.json"
    state.write_text(
        '{"quota_blocked_date": "' + (date.today() - timedelta(days=1)).isoformat() + '"}\n',
        encoding="utf-8",
    )

    async def run() -> None:
        primary = DummyProvider([LLMResponse("primary ok")], "primary-model")
        fallback = DummyProvider([LLMResponse("fallback ok")], "fallback-model")
        wrapper = FallbackLLMProvider(
            primary=primary,
            fallback_factory=lambda: fallback,
            primary_model="primary-model",
            fallback_model="fallback-model",
            state_path=state,
        )

        response = await wrapper.chat([])

        assert response.content == "primary ok"
        assert primary.calls == ["primary-model"]
        assert fallback.calls == []

    asyncio.run(run())
