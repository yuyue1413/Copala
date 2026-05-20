import asyncio

import pytest

from copala.context_awareness.amap import AmapClient, AmapError, AmapRouteUnavailable


def test_amap_geocode_success() -> None:
    client = AmapClient(api_key="key")

    async def fake_get(path, params):
        assert path == "/v3/geocode/geo"
        return {"geocodes": [{"location": "121.1,31.2", "formatted_address": "People Park", "city": "Shanghai", "adcode": "310000"}]}

    client._get = fake_get

    result = asyncio.run(client.geocode("People Park", "Shanghai"))

    assert result["location"] == "121.1,31.2"
    assert result["adcode"] == "310000"


def test_amap_geocode_empty_result() -> None:
    client = AmapClient(api_key="key")

    async def fake_get(_path, _params):
        return {"geocodes": []}

    client._get = fake_get

    with pytest.raises(AmapError):
        asyncio.run(client.geocode("missing"))


def test_amap_weather_rain() -> None:
    client = AmapClient(api_key="key")

    async def fake_get(path, params):
        assert path == "/v3/weather/weatherInfo"
        return {"lives": [{"city": "Shanghai", "weather": "rain", "temperature": "20", "adcode": "310000"}]}

    client._get = fake_get

    weather = asyncio.run(client.weather("310000"))

    assert weather["weather"] == "rain"


def test_amap_driving_duration() -> None:
    client = AmapClient(api_key="key")

    async def fake_get(path, params):
        assert path == "/v5/direction/driving"
        return {"route": {"paths": [{"duration": "1800", "distance": "9000"}]}}

    client._get = fake_get

    route = asyncio.run(client.driving("121.1,31.2", "121.2,31.3"))

    assert route["duration_seconds"] == 1800
    assert route["distance_meters"] == 9000


def test_amap_requires_api_key() -> None:
    client = AmapClient(api_key="")

    with pytest.raises(AmapError):
        asyncio.run(client.geocode("People Park"))


def test_amap_engine_response_error_is_route_unavailable() -> None:
    client = AmapClient(api_key="key")

    async def fake_get(_url, params=None):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"status": "0", "info": "ENGINE_RESPONSE_DATA_ERROR", "infocode": "20800"}

        return Response()

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params=None):
            return await fake_get(url, params=params)

    import copala.context_awareness.amap as amap_module

    original = amap_module.httpx.AsyncClient
    amap_module.httpx.AsyncClient = FakeAsyncClient
    try:
        with pytest.raises(AmapRouteUnavailable):
            asyncio.run(client.driving("121.1,31.2", "121.2,31.3"))
    finally:
        amap_module.httpx.AsyncClient = original
