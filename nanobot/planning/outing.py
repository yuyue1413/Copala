"""Context-aware outing and travel planning service."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from loguru import logger

from nanobot.agent.memory_rag.schema import MemoryItem
from nanobot.agent.tools.web import WebSearchTool
from nanobot.calendar.service import CalendarEvent
from nanobot.context_awareness.amap import AmapClient, AmapError, AmapRouteUnavailable


@dataclass
class OutingWindow:
    start: date
    end: date
    label: str
    days: int


class OutingPlanningService:
    """Orchestrate memory, calendar, Amap and web search for outing plans."""

    def __init__(
        self,
        *,
        config: Any,
        calendar_service: Any,
        memory_store: Any | None = None,
        amap_config: Any | None = None,
        web_api_key: str | None = None,
        web_search_config: Any | None = None,
        web_proxy: str | None = None,
    ) -> None:
        self.config = config
        self.calendar = calendar_service
        self.memory = memory_store
        self.amap_config = amap_config
        self.amap = AmapClient(
            api_key=getattr(amap_config, "api_key", ""),
            api_base=getattr(amap_config, "api_base", "https://restapi.amap.com"),
            timeout_seconds=getattr(amap_config, "timeout_seconds", 8),
        )
        self.web = WebSearchTool(
            api_key=(getattr(web_search_config, "api_key", "") or web_api_key),
            max_results=int(getattr(web_search_config, "max_results", getattr(config, "default_search_count", 5))),
            proxy=web_proxy,
            provider=str(getattr(web_search_config, "provider", "auto")),
            api_base=getattr(web_search_config, "api_base", "") or None,
            search_depth=str(getattr(web_search_config, "search_depth", "basic")),
            include_answer=bool(getattr(web_search_config, "include_answer", True)),
        )

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "enabled", True))

    async def plan_trip(
        self,
        *,
        destination: str,
        time_range: str = "",
        origin: str = "",
        budget: str = "",
        companions: str = "",
        preferences: str = "",
        constraints: str = "",
    ) -> str:
        return await self._plan(
            kind="trip",
            destination=destination,
            time_range=time_range,
            origin=origin,
            budget=budget,
            companions=companions,
            preferences=preferences,
            constraints=constraints,
        )

    async def plan_outing(
        self,
        *,
        destination: str,
        time_range: str = "",
        origin: str = "",
        budget: str = "",
        companions: str = "",
        preferences: str = "",
        constraints: str = "",
    ) -> str:
        return await self._plan(
            kind="outing",
            destination=destination,
            time_range=time_range,
            origin=origin,
            budget=budget,
            companions=companions,
            preferences=preferences,
            constraints=constraints,
        )

    async def plan_from_request(self, request: str) -> str:
        destination = self._destination_from_text(request)
        kind = "trip" if self._looks_like_trip(request) else "outing"
        if kind == "trip":
            return await self.plan_trip(destination=destination, time_range=request, preferences=request, constraints=request)
        return await self.plan_outing(destination=destination, time_range=request, preferences=request, constraints=request)

    async def route_check(self, destination: str, origin: str = "", city: str = "") -> str:
        route = await self._route(origin, destination, city=city)
        if route.get("error"):
            return f"路线评估：{route['error']}"
        return (
            f"路线评估：{route['origin']} -> {route['destination']}\n"
            f"- 方式：{route['mode']}\n"
            f"- 预计耗时：{route['minutes']} 分钟\n"
            f"- 距离：约 {route['distance_km']} 公里"
        )

    async def calendar_fit(self, time_range: str = "") -> str:
        window = self._window(time_range)
        events = await self._calendar_events(window)
        return self._render_calendar_fit(window, events)

    async def _plan(
        self,
        *,
        kind: str,
        destination: str,
        time_range: str,
        origin: str,
        budget: str,
        companions: str,
        preferences: str,
        constraints: str,
    ) -> str:
        if not self.enabled:
            return "出行规划功能未启用。"
        destination = destination.strip() or "待确认目的地"
        window = self._window(time_range)
        origin = origin.strip() or str(getattr(self.amap_config, "default_origin", "") or "")
        memories = self._memories(destination, time_range, budget, preferences, constraints)
        events = await self._calendar_events(window)
        route = await self._route(origin, destination)
        weather = await self._weather(destination)
        web_brief = await self._web_brief(destination, kind)
        result = self._render_plan(
            kind=kind,
            destination=destination,
            window=window,
            origin=origin,
            budget=budget,
            companions=companions,
            preferences=preferences,
            constraints=constraints,
            memories=memories,
            events=events,
            route=route,
            weather=weather,
            web_brief=web_brief,
        )
        self._save_plan(kind, destination, result)
        return result

    def _memories(self, destination: str, time_range: str, budget: str, preferences: str, constraints: str) -> list[Any]:
        if not self.memory:
            return []
        try:
            query = f"旅行 出行 商场 预算 偏好 行程 {destination} {time_range} {budget} {preferences} {constraints}"
            return list(self.memory.retrieve(query, top_k=6))
        except Exception:
            logger.debug("Outing memory retrieval skipped", exc_info=True)
            return []

    async def _calendar_events(self, window: OutingWindow) -> list[CalendarEvent]:
        try:
            return await self.calendar.list_events_async(start_date=window.start, end_date=window.end, source="feishu")
        except Exception:
            logger.debug("Outing calendar lookup skipped", exc_info=True)
            return []

    async def _route(self, origin: str, destination: str, *, city: str = "") -> dict[str, Any]:
        if not origin:
            return {"error": "未配置出发地，无法计算路线耗时；可在 smartReminders.amap.defaultOrigin 填写常用出发地。"}
        if not destination or destination == "待确认目的地":
            return {"error": "目的地不明确，无法计算路线。"}
        try:
            default_city = city or str(getattr(self.amap_config, "default_city", "") or "")
            origin_geo = await self.amap.geocode(origin, default_city)
            dest_geo = await self.amap.geocode(destination, default_city)
            mode = str(getattr(self.amap_config, "route_mode", "driving") or "driving")
            if mode == "transit":
                data = await self.amap.transit(origin_geo["location"], dest_geo["location"], origin_geo.get("city") or default_city)
            else:
                data = await self.amap.driving(origin_geo["location"], dest_geo["location"])
            return {
                "origin": origin,
                "destination": destination,
                "mode": data.get("mode", mode),
                "minutes": int(data.get("duration_seconds", 0) // 60),
                "distance_km": round(int(data.get("distance_meters", 0)) / 1000, 1),
            }
        except AmapRouteUnavailable as exc:
            return {"error": f"高德暂时无法计算该路线：{exc}"}
        except AmapError as exc:
            return {"error": f"高德路线查询失败：{exc}"}
        except Exception as exc:
            logger.debug("Outing route lookup failed", exc_info=True)
            return {"error": f"路线查询失败：{exc}"}

    async def _weather(self, destination: str) -> dict[str, Any]:
        try:
            default_city = str(getattr(self.amap_config, "default_city", "") or "")
            if destination and destination != "待确认目的地":
                geo = await self.amap.geocode(destination, default_city)
                city_or_adcode = str(geo.get("adcode") or geo.get("city") or default_city)
            else:
                city_or_adcode = default_city
            return await self.amap.weather(city_or_adcode, extensions="base")
        except AmapError as exc:
            return {"error": f"天气查询失败：{exc}"}
        except Exception as exc:
            logger.debug("Outing weather lookup failed", exc_info=True)
            return {"error": f"天气查询失败：{exc}"}

    async def _web_brief(self, destination: str, kind: str) -> str:
        count = int(getattr(self.config, "default_search_count", 5))
        sites = list(getattr(self.config, "guide_sites", []) or [])
        if kind == "trip":
            topic = "旅游攻略 景点 餐饮 住宿 避坑 营业时间"
        else:
            topic = "商场 营业时间 餐饮 停车 交通 攻略"
        query = f"{destination} {topic}"
        if sites:
            query += " " + " OR ".join(f"site:{site}" for site in sites[:4])
        result = await self.web.execute(query=query, count=count)
        if result.startswith("Error:") or result.startswith("Proxy error:"):
            return "外部攻略搜索暂不可用；需要配置 Brave Search API Key 后补充实时攻略、营业时间和避坑信息。"
        return result[:1800]

    def _render_plan(
        self,
        *,
        kind: str,
        destination: str,
        window: OutingWindow,
        origin: str,
        budget: str,
        companions: str,
        preferences: str,
        constraints: str,
        memories: list[Any],
        events: list[CalendarEvent],
        route: dict[str, Any],
        weather: dict[str, Any],
        web_brief: str,
    ) -> str:
        title = "多日旅行智能规划" if kind == "trip" and window.days > 1 else "临时外出智能规划"
        lines = [
            f"{title}：{destination}",
            "",
            "基础信息：",
            f"- 时间：{window.label}（{window.start.isoformat()} 至 {window.end.isoformat()}）",
            f"- 出发地：{origin or '未配置'}",
            f"- 预算：{budget or '未指定'}",
            f"- 同行：{companions or '未指定'}",
            f"- 偏好/约束：{preferences or constraints or '未指定'}",
            "",
            "日程校验：",
        ]
        lines.extend(self._calendar_lines(events))
        lines.extend(["", "路线与天气："])
        if route.get("error"):
            lines.append(f"- 路线：{route['error']}")
        else:
            lines.append(f"- 路线：{route['mode']} 约 {route['minutes']} 分钟，距离约 {route['distance_km']} 公里")
        if weather.get("error"):
            lines.append(f"- 天气：{weather['error']}")
        else:
            lines.append(f"- 天气：{weather.get('city', destination)} {weather.get('weather', '未知')} {weather.get('temperature', '')}℃")
        lines.extend(["", "个性化依据："])
        lines.extend(self._memory_lines(memories))
        lines.extend(["", "外部信息检索：", web_brief or "- 未检索到外部攻略。"])
        lines.extend(["", "建议行程："])
        lines.extend(self._itinerary_lines(kind, destination, window, route, weather, budget))
        lines.extend(["", "预算估算："])
        lines.extend(self._budget_lines(kind, budget, window.days))
        lines.extend(["", "风险与备用方案："])
        lines.extend(self._risk_lines(weather, route, events))
        lines.extend([
            "",
            "提醒建议：",
            "- 出发前 1 天检查天气、证件/门票/预约和交通。",
            "- 出发前 2 小时检查路线耗时和日程冲突。",
            "- 如涉及住宿/车票，建议单独设置订票或入住提醒。",
            "",
            "是否需要我把出发时间、行程、订票提醒或天气提醒写入飞书日历？确认后我会走真实日历工具。",
        ])
        return "\n".join(lines)

    @staticmethod
    def _calendar_lines(events: list[CalendarEvent]) -> list[str]:
        if not events:
            return ["- 目标时间段未发现飞书日程冲突。"]
        lines = ["- 目标时间段已有日程，需要避开："]
        for event in events[:8]:
            end = event.end.strftime("%H:%M") if event.end else "未定"
            lines.append(f"- {event.start.strftime('%Y-%m-%d %H:%M')}-{end} {event.title}，地点：{event.location or '未提供'}")
        return lines

    @staticmethod
    def _memory_lines(memories: list[Any]) -> list[str]:
        if not memories:
            return ["- 暂无匹配的历史偏好，建议先按轻量、留缓冲的默认策略规划。"]
        lines = []
        for memory in memories[:4]:
            item = getattr(memory, "item", memory)
            text = getattr(item, "text", "")
            if text:
                lines.append(f"- {text[:120]}")
        return lines or ["- 暂无可展示的历史偏好。"]

    @staticmethod
    def _itinerary_lines(kind: str, destination: str, window: OutingWindow, route: dict[str, Any], weather: dict[str, Any], budget: str) -> list[str]:
        if kind == "trip" and window.days > 1:
            lines = []
            for index in range(window.days):
                day = window.start + timedelta(days=index)
                if index == 0:
                    lines.append(f"- Day {index + 1}（{day.isoformat()}）：抵达 {destination}，安排核心景点 + 轻量晚餐，避免第一天过满。")
                elif index == window.days - 1:
                    lines.append(f"- Day {index + 1}（{day.isoformat()}）：上午补一个低强度景点或商圈，下午返程并预留交通缓冲。")
                else:
                    lines.append(f"- Day {index + 1}（{day.isoformat()}）：上午景点，下午餐饮/城市漫步，晚上自由活动。")
            return lines
        depart = "建议结合日历空档选择出发时间"
        if not route.get("error"):
            depart = f"建议至少提前 {max(30, int(route.get('minutes', 30)) + 15)} 分钟出发"
        return [
            f"- 出发：{depart}。",
            f"- 到达：优先完成 {destination} 的核心目标，再安排餐饮/购物/休息。",
            "- 返程：避开晚高峰，给返程和后续日程保留缓冲。",
        ]

    @staticmethod
    def _budget_lines(kind: str, budget: str, days: int) -> list[str]:
        if budget:
            return [f"- 用户预算：{budget}。建议把交通、餐饮、门票/购物分开记录，避免现场超支。"]
        if kind == "trip":
            return [f"- 未指定预算。可先按交通 + 住宿 + 餐饮 + 门票估算，{days} 天行程建议预留 15%-20% 机动金。"]
        return ["- 未指定预算。商场/临时外出建议先设定餐饮和购物上限，避免临时消费过多。"]

    @staticmethod
    def _risk_lines(weather: dict[str, Any], route: dict[str, Any], events: list[CalendarEvent]) -> list[str]:
        lines: list[str] = []
        weather_text = str(weather.get("weather") or "")
        if any(word in weather_text for word in ("雨", "雪", "雷", "大风", "高温", "rain", "storm")):
            lines.append("- 天气存在不确定性，建议准备室内备选、雨具或调整户外段。")
        if route.get("error"):
            lines.append("- 路线耗时未确认，建议出发前再次查询。")
        if events:
            lines.append("- 目标时间段已有日程，建议避开冲突或缩短行程。")
        if not lines:
            lines.append("- 暂未发现明显天气、路线或日程风险。")
        return lines

    def _save_plan(self, kind: str, destination: str, text: str) -> None:
        if not self.memory or not getattr(self.config, "save_plan_to_memory", True):
            return
        try:
            item = MemoryItem(
                id=hashlib.sha1(f"outing|{kind}|{destination}|{text[:200]}".encode("utf-8")).hexdigest(),
                type="plan_artifact",
                text=text[:4000],
                summary=text[:160],
                source="conversation",
                tags=["outing", kind, destination],
                confidence=0.82,
                priority=5,
                metadata={"destination": destination, "kind": kind},
            )
            self.memory.upsert(item)
        except Exception:
            logger.debug("Outing plan memory save skipped", exc_info=True)

    @staticmethod
    def _render_calendar_fit(window: OutingWindow, events: list[CalendarEvent]) -> str:
        lines = [f"日程适配：{window.label}（{window.start.isoformat()} 至 {window.end.isoformat()}）"]
        lines.extend(OutingPlanningService._calendar_lines(events))
        return "\n".join(lines)

    @staticmethod
    def _window(text: str) -> OutingWindow:
        today = date.today()
        if any(word in text for word in ("后天", "day after tomorrow")):
            day = today + timedelta(days=2)
            return OutingWindow(day, day, "后天", 1)
        if any(word in text for word in ("明天", "tomorrow")):
            day = today + timedelta(days=1)
            return OutingWindow(day, day, "明天", 1)
        if any(word in text for word in ("周末", "weekend")):
            saturday = today + timedelta(days=(5 - today.weekday()) % 7)
            sunday = saturday + timedelta(days=1)
            return OutingWindow(saturday, sunday, "本周末", 2)
        if any(word in text for word in ("两天", "2天", "二日", "两日")):
            return OutingWindow(today, today + timedelta(days=1), "两天", 2)
        return OutingWindow(today, today, "今天", 1)

    @staticmethod
    def _looks_like_trip(text: str) -> bool:
        return any(word in text for word in ("旅行", "旅游", "出游", "周末", "两天", "三天", "玩两天", "城市"))

    @staticmethod
    def _destination_from_text(text: str) -> str:
        lowered = text.lower()
        if " to " in lowered:
            tail = text[lowered.index(" to ") + 4:]
            for sep in (" for ", " with ", ",", ".", "，", "。"):
                if sep in tail:
                    tail = tail.split(sep, 1)[0]
            cleaned = tail.strip()
            if cleaned:
                return cleaned[:24]
        markers = ("去", "前往", "到")
        for marker in markers:
            if marker in text:
                tail = text.split(marker, 1)[1]
                for sep in ("玩", "旅行", "旅游", "出游", "逛", "，", ",", "。", " "):
                    if sep in tail:
                        tail = tail.split(sep, 1)[0]
                cleaned = tail.strip()
                if cleaned:
                    return cleaned[:24]
        return "待确认目的地"
