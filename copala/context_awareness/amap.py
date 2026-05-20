"""Amap Web Service API client for geocoding, weather and route planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class AmapError(RuntimeError):
    """Raised when Amap returns an unusable response."""


class AmapRouteUnavailable(AmapError):
    """Raised when Amap cannot calculate a route for the given points."""


@dataclass
class AmapClient:
    api_key: str
    api_base: str = "https://restapi.amap.com"
    timeout_seconds: int = 8

    def __post_init__(self) -> None:
        self.api_base = self.api_base.rstrip("/")
        self._cache: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}

    async def geocode(self, address: str, city: str = "") -> dict[str, Any]:
        data = await self._get("/v3/geocode/geo", {"address": address, "city": city})
        geocodes = data.get("geocodes") or []
        if not geocodes:
            raise AmapError(f"高德未找到地址：{address}")
        item = geocodes[0]
        location = str(item.get("location") or "")
        if not location:
            raise AmapError(f"高德地址缺少坐标：{address}")
        return {
            "address": address,
            "location": location,
            "formatted_address": item.get("formatted_address") or address,
            "city": item.get("city") or city,
            "adcode": item.get("adcode") or "",
            "raw": item,
        }

    async def weather(self, city_or_adcode: str, extensions: str = "base") -> dict[str, Any]:
        data = await self._get("/v3/weather/weatherInfo", {"city": city_or_adcode, "extensions": extensions})
        lives = data.get("lives") or []
        forecasts = data.get("forecasts") or []
        if lives:
            item = lives[0]
            return {
                "city": item.get("city") or city_or_adcode,
                "adcode": item.get("adcode") or city_or_adcode,
                "weather": item.get("weather") or "",
                "temperature": item.get("temperature") or "",
                "winddirection": item.get("winddirection") or "",
                "windpower": item.get("windpower") or "",
                "reporttime": item.get("reporttime") or "",
                "raw": item,
            }
        if forecasts:
            item = forecasts[0]
            casts = item.get("casts") or []
            first = casts[0] if casts else {}
            return {
                "city": item.get("city") or city_or_adcode,
                "adcode": item.get("adcode") or city_or_adcode,
                "weather": first.get("dayweather") or first.get("nightweather") or "",
                "temperature": first.get("daytemp") or first.get("nighttemp") or "",
                "winddirection": first.get("daywind") or "",
                "windpower": first.get("daypower") or "",
                "reporttime": item.get("reporttime") or "",
                "raw": item,
            }
        raise AmapError(f"高德未返回天气：{city_or_adcode}")

    async def driving(self, origin: str, destination: str, strategy: int = 10) -> dict[str, Any]:
        data = await self._get(
            "/v5/direction/driving",
            {"origin": origin, "destination": destination, "strategy": str(strategy)},
        )
        route = data.get("route") or {}
        paths = route.get("paths") or []
        if not paths:
            raise AmapRouteUnavailable("高德未返回驾车路线")
        path = paths[0]
        duration = int(float(path.get("duration") or 0))
        distance = int(float(path.get("distance") or 0))
        return {"mode": "driving", "duration_seconds": duration, "distance_meters": distance, "raw": path}

    async def transit(self, origin: str, destination: str, city: str, cityd: str | None = None) -> dict[str, Any]:
        params = {"origin": origin, "destination": destination, "city": city}
        if cityd:
            params["cityd"] = cityd
        data = await self._get("/v3/direction/transit/integrated", params)
        route = data.get("route") or {}
        transits = route.get("transits") or []
        if not transits:
            raise AmapRouteUnavailable("高德未返回公交路线")
        item = transits[0]
        duration = int(float(item.get("duration") or 0))
        distance = int(float(item.get("distance") or 0))
        return {"mode": "transit", "duration_seconds": duration, "distance_meters": distance, "raw": item}

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise AmapError("缺少高德 Web 服务 API Key")
        clean = {key: str(value) for key, value in params.items() if value is not None and str(value) != ""}
        cache_key = (path, tuple(sorted(clean.items())))
        if cache_key in self._cache:
            return self._cache[cache_key]

        clean["key"] = self.api_key
        async with httpx.AsyncClient(timeout=float(self.timeout_seconds)) as client:
            response = await client.get(f"{self.api_base}{path}", params=clean)
            response.raise_for_status()
        data = response.json()
        if str(data.get("status", "1")) != "1":
            info = data.get("info") or "unknown"
            infocode = str(data.get("infocode") or "")
            message = f"高德 API 返回失败：{info}"
            if infocode:
                message += f" ({infocode})"
            if info == "ENGINE_RESPONSE_DATA_ERROR":
                raise AmapRouteUnavailable(message)
            raise AmapError(message)

        self._cache[cache_key] = data
        return data
