from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse


def test_llm_quota_error_is_not_sent_to_user(tmp_path: Path) -> None:
    async def run() -> None:
        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")
        loop.provider.chat = MagicMock(return_value=None)

        async def _chat(*_args, **_kwargs):
            return LLMResponse(
                content=(
                    "Error: Error code: 429 - {'error': {'message': "
                    "\"You have exceeded today's quota for model deepseek-ai/DeepSeek-R1-0528\"}}"
                ),
                finish_reason="error",
            )

        loop.provider.chat = _chat
        out = await loop._process_message(InboundMessage(channel="qq", sender_id="u", chat_id="c", content="hi"))

        assert out is not None
        assert "429" not in out.content
        assert "deepseek" not in out.content.lower()
        assert "额度不足" in out.content

    asyncio.run(run())


def test_backend_error_sanitizer_catches_error_like_content() -> None:
    raw = "Error: Client error '500 Server Error' for url https://example.test with bearer token"
    safe = AgentLoop._sanitize_user_facing_content(raw)

    assert "bearer" not in safe.lower()
    assert "example.test" not in safe
    assert "后台日志" in safe


def test_unverified_calendar_write_claim_is_blocked_without_tools() -> None:
    assert AgentLoop._looks_like_unverified_calendar_write_claim("已写入飞书日历，明天见。", [])
    assert not AgentLoop._looks_like_unverified_calendar_write_claim("已写入飞书日历，明天见。", ["life_event"])
    assert not AgentLoop._looks_like_unverified_calendar_write_claim("我可以帮你查看日程。", [])
