from __future__ import annotations

import asyncio

from copala.agent.tools.web import WebSearchTool


class FakeResponse:
    status_code = 200
    text = "{}"

    def __init__(self, data):
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._data


class FakeAsyncClient:
    last_post = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, json=None, headers=None, timeout=None):
        FakeAsyncClient.last_post = {
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
        }
        return FakeResponse({
            "answer": "徐州两日游可以安排云龙湖、徐州博物馆和户部山。",
            "results": [
                {
                    "title": "徐州旅游攻略",
                    "url": "https://example.com/xuzhou",
                    "content": "云龙湖适合轻松游玩，晚上可以去户部山。",
                    "score": 0.92,
                }
            ],
        })


def test_tavily_search_backend(monkeypatch) -> None:
    async def run() -> None:
        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
        tool = WebSearchTool(
            api_key="tvly-test",
            provider="tavily",
            max_results=3,
            search_depth="advanced",
            include_answer=True,
        )

        result = await tool.execute("徐州 旅游攻略", count=2)

        assert "徐州两日游" in result
        assert "徐州旅游攻略" in result
        assert FakeAsyncClient.last_post["url"] == "https://api.tavily.com/search"
        assert FakeAsyncClient.last_post["json"]["max_results"] == 2
        assert FakeAsyncClient.last_post["json"]["search_depth"] == "advanced"
        assert FakeAsyncClient.last_post["headers"]["Authorization"] == "Bearer tvly-test"

    asyncio.run(run())


def test_auto_provider_detects_tavily_key() -> None:
    tool = WebSearchTool(api_key="tvly-test", provider="auto")

    assert tool.resolved_provider == "tavily"
