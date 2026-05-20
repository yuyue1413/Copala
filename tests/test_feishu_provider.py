from __future__ import annotations

import asyncio
from datetime import datetime

import httpx

from nanobot.calendar.providers.feishu import FeishuCalendarProvider


def test_feishu_primary_calendar_is_resolved_before_listing_events() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "token", "expire": 7200})
        if request.url.path.endswith("/calendar/v4/calendars/primary"):
            return httpx.Response(200, json={"code": 0, "data": {"calendar": {"calendar_id": "cal_real_1"}}})
        if request.url.path.endswith("/calendar/v4/calendars/cal_real_1/events"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "event_id": "evt_1",
                                "summary": "Feishu Sync",
                                "start_time": {"timestamp": "1779033600"},
                                "end_time": {"timestamp": "1779037200"},
                            }
                        ]
                    },
                },
            )
        return httpx.Response(404, json={"code": 404, "msg": "not found"})

    async def run() -> None:
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://open.feishu.cn")
        provider = FeishuCalendarProvider(
            app_id="app",
            app_secret="secret",
            http=http,
        )
        events = await provider.list_events("primary", datetime(2026, 5, 18), datetime(2026, 5, 25))
        await http.aclose()

        assert len(events) == 1
        assert events[0].calendar_id == "cal_real_1"
        assert any(request.url.path.endswith("/calendar/v4/calendars/primary") for request in requests)
        assert any(request.url.path.endswith("/calendar/v4/calendars/cal_real_1/events") for request in requests)

    asyncio.run(run())
