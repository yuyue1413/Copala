"""Feishu/Lark Calendar v4 provider."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from copala.calendar.providers.base import (
    BusySlot,
    CalendarAccount,
    CalendarDraft,
    EnterpriseCalendarError,
)
from copala.calendar.service import CalendarEvent


class FeishuCalendarProvider:
    """Async provider for Feishu/Lark Calendar OpenAPI."""

    name = "feishu"

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        default_calendar_id: str = "primary",
        api_base: str = "https://open.feishu.cn",
        auth_mode: str = "tenant",
        redirect_uri: str = "http://localhost:18790/feishu/oauth/callback",
        oauth_scopes: list[str] | None = None,
        token_store_path: Path | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.default_calendar_id = default_calendar_id or "primary"
        self.api_base = api_base.rstrip("/")
        self.auth_mode = auth_mode
        self.redirect_uri = redirect_uri
        self.oauth_scopes = oauth_scopes or []
        self.token_store_path = token_store_path
        self._http = http or httpx.AsyncClient(timeout=20)
        self._owns_http = http is None
        self._tenant_token_value: str | None = None
        self._tenant_token_expiry = 0.0
        self._app_token_value: str | None = None
        self._app_token_expiry = 0.0
        self._primary_calendar_id: str | None = None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    def authorization_url(self, *, state: str = "copala") -> str:
        params = {
            "app_id": self.app_id,
            "redirect_uri": self.redirect_uri,
            "state": state,
        }
        if self.oauth_scopes:
            params["scope"] = " ".join(self.oauth_scopes)
        return f"{self.api_base}/open-apis/authen/v1/index?{urlencode(params)}"

    async def exchange_authorization_code(self, code: str) -> dict[str, Any]:
        app_token = await self._app_token()
        resp = await self._http.post(
            f"{self.api_base}/open-apis/authen/v1/access_token",
            headers={"Authorization": f"Bearer {app_token}"},
            json={"grant_type": "authorization_code", "code": code},
        )
        data = self._unwrap_auth_response(resp, "Feishu user access token")
        token = self._normalize_user_token(data)
        self._write_user_token(token)
        return token

    async def list_calendars(self) -> list[CalendarAccount]:
        data = await self._request("GET", "/open-apis/calendar/v4/calendars")
        calendars = data.get("calendar_list") or data.get("calendars") or data.get("items") or []
        return [
            CalendarAccount(
                id=str(row.get("calendar_id") or row.get("id") or ""),
                name=str(row.get("summary") or row.get("name") or row.get("description") or "Feishu Calendar"),
                platform=self.name,
                is_primary=bool(row.get("is_primary") or row.get("primary")),
                raw=row,
            )
            for row in calendars
            if isinstance(row, dict)
        ]

    async def list_events(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
    ) -> list[CalendarEvent]:
        cal_id = await self._resolve_calendar_id(calendar_id or self.default_calendar_id)
        params = {
            "start_time": str(int(start.timestamp())),
            "end_time": str(int(end.timestamp())),
        }
        data = await self._request(
            "GET",
            f"/open-apis/calendar/v4/calendars/{quote(cal_id, safe='')}/events",
            params=params,
        )
        rows = data.get("items") or data.get("event_list") or data.get("events") or []
        return [self._event_from_feishu(row, cal_id) for row in rows if isinstance(row, dict)]

    async def create_event(self, calendar_id: str, draft: CalendarDraft) -> CalendarEvent:
        cal_id = await self._resolve_calendar_id(calendar_id or self.default_calendar_id)
        data = await self._request(
            "POST",
            f"/open-apis/calendar/v4/calendars/{quote(cal_id, safe='')}/events",
            json=self._payload_from_draft(draft),
        )
        event = data.get("event") if isinstance(data.get("event"), dict) else data
        return self._event_from_feishu(event, cal_id)

    async def update_event(
        self,
        calendar_id: str,
        event_id: str,
        patch: CalendarDraft,
    ) -> CalendarEvent:
        cal_id = await self._resolve_calendar_id(calendar_id or self.default_calendar_id)
        data = await self._request(
            "PATCH",
            f"/open-apis/calendar/v4/calendars/{quote(cal_id, safe='')}/events/{quote(event_id, safe='')}",
            json=self._payload_from_draft(patch, partial=True),
        )
        event = data.get("event") if isinstance(data.get("event"), dict) else data
        event.setdefault("event_id", event_id)
        return self._event_from_feishu(event, cal_id)

    async def get_freebusy(
        self,
        start: datetime,
        end: datetime,
        user_ids: list[str] | None = None,
    ) -> list[BusySlot]:
        payload: dict[str, Any] = {
            "time_min": str(int(start.timestamp())),
            "time_max": str(int(end.timestamp())),
        }
        if user_ids:
            payload["user_id_list"] = user_ids
        data = await self._request("POST", "/open-apis/calendar/v4/freebusy/list", json=payload)
        rows = data.get("freebusy_list") or data.get("items") or []
        slots: list[BusySlot] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            busy_rows = row.get("busy_time_list") or row.get("busy") or [row]
            for busy in busy_rows:
                if not isinstance(busy, dict):
                    continue
                slot_start = self._parse_time_obj(busy.get("start_time") or busy.get("start"))
                slot_end = self._parse_time_obj(busy.get("end_time") or busy.get("end"))
                if slot_start and slot_end:
                    slots.append(BusySlot(slot_start, slot_end, self.name, str(row.get("user_id") or ""), busy))
        return slots

    async def _resolve_calendar_id(self, calendar_id: str) -> str:
        """Resolve Feishu's `primary` alias to the real calendar_id."""
        if calendar_id and calendar_id != "primary":
            return calendar_id
        if self._primary_calendar_id:
            return self._primary_calendar_id

        data = await self._request("POST", "/open-apis/calendar/v4/calendars/primary")
        candidate = self._extract_calendar_id(data)
        if not candidate:
            calendars = await self.list_calendars()
            primary = next((c for c in calendars if c.is_primary), calendars[0] if calendars else None)
            candidate = primary.id if primary else ""
        if not candidate:
            raise EnterpriseCalendarError(
                "Feishu primary calendar could not be resolved. "
                "Check calendar read permission and set calendar.providers.feishu.defaultCalendarId to a real calendar_id."
            )
        self._primary_calendar_id = candidate
        return candidate

    async def _tenant_token(self) -> str:
        if self._tenant_token_value and time.time() < self._tenant_token_expiry:
            return self._tenant_token_value
        if not self.app_id or not self.app_secret:
            raise EnterpriseCalendarError("Feishu calendar is missing app_id/app_secret.")
        resp = await self._http.post(
            f"{self.api_base}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        data = self._decode_response(resp)
        token = data.get("tenant_access_token")
        if not token:
            raise EnterpriseCalendarError(f"Feishu token response missing tenant_access_token: {data}")
        self._tenant_token_value = str(token)
        self._tenant_token_expiry = time.time() + int(data.get("expire", 7200)) - 60
        return self._tenant_token_value

    async def _app_token(self) -> str:
        if self._app_token_value and time.time() < self._app_token_expiry:
            return self._app_token_value
        if not self.app_id or not self.app_secret:
            raise EnterpriseCalendarError("Feishu calendar is missing app_id/app_secret.")
        resp = await self._http.post(
            f"{self.api_base}/open-apis/auth/v3/app_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        data = self._decode_response(resp)
        token = data.get("app_access_token")
        if not token:
            raise EnterpriseCalendarError(f"Feishu app token response missing app_access_token: {data}")
        self._app_token_value = str(token)
        self._app_token_expiry = time.time() + int(data.get("expire", 7200)) - 60
        return self._app_token_value

    async def _user_token(self) -> str:
        token_data = self._load_user_token()
        access_token = token_data.get("access_token")
        expires_at = float(token_data.get("expires_at") or 0)
        if access_token and time.time() < expires_at - 60:
            return str(access_token)
        refresh_token = token_data.get("refresh_token")
        if refresh_token:
            app_token = await self._app_token()
            resp = await self._http.post(
                f"{self.api_base}/open-apis/authen/v1/refresh_access_token",
                headers={"Authorization": f"Bearer {app_token}"},
                json={"grant_type": "refresh_token", "refresh_token": refresh_token},
            )
            data = self._unwrap_auth_response(resp, "Feishu refresh user access token")
            refreshed = self._normalize_user_token(data, fallback_refresh_token=str(refresh_token))
            self._write_user_token(refreshed)
            return str(refreshed["access_token"])
        raise EnterpriseCalendarError(
            "Feishu user calendar is not authorized. Run `copala calendar feishu-auth-login` "
            "to finish browser authorization, or use `feishu-auth-url` plus `feishu-auth-code CODE` manually."
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        token = await self._user_token() if self.auth_mode == "user" else await self._tenant_token()
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {token}"
        resp = await self._http.request(method, f"{self.api_base}{path}", headers=headers, **kwargs)
        data = self._decode_response(resp)
        code = data.get("code", 0)
        if code not in (0, "0", None):
            if str(code) == "190007":
                raise EnterpriseCalendarError(
                    "Feishu calendar API error 190007: app bot_id not found. "
                    "Enable the Bot capability in Feishu Open Platform: "
                    "Developer Console -> App Features -> Bot, then publish/release the app if required."
                )
            raise EnterpriseCalendarError(f"Feishu calendar API error {code}: {data.get('msg') or data}")
        nested = data.get("data")
        return nested if isinstance(nested, dict) else data

    @staticmethod
    def _decode_response(resp: httpx.Response) -> dict[str, Any]:
        try:
            data = resp.json()
        except Exception as exc:
            raise EnterpriseCalendarError(f"Feishu calendar HTTP error: {exc}") from exc
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = data.get("code")
            if str(code) == "190007" or "app bot_id not found" in str(data.get("msg", "")):
                raise EnterpriseCalendarError(
                    "Feishu calendar HTTP 404 / error 190007: app bot_id not found. "
                    "Enable the Bot capability in Feishu Open Platform: "
                    "Developer Console -> App Features -> Bot, then publish/release the app if required."
                ) from exc
            raise EnterpriseCalendarError(
                f"Feishu calendar HTTP {resp.status_code}: {data.get('msg') or data}"
            ) from exc
        if not isinstance(data, dict):
            raise EnterpriseCalendarError(f"Feishu calendar returned non-object JSON: {data!r}")
        return data

    def _load_user_token(self) -> dict[str, Any]:
        if not self.token_store_path or not self.token_store_path.exists():
            return {}
        try:
            import json

            data = json.loads(self.token_store_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_user_token(self, token: dict[str, Any]) -> None:
        if not self.token_store_path:
            raise EnterpriseCalendarError("Feishu user token store path is not configured.")
        import json

        self.token_store_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_store_path.write_text(
            json.dumps(token, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _normalize_user_token(
        self,
        data: dict[str, Any],
        *,
        fallback_refresh_token: str = "",
    ) -> dict[str, Any]:
        access_token = data.get("access_token") or data.get("user_access_token")
        if not access_token:
            raise EnterpriseCalendarError(f"Feishu user token response missing access_token: {data}")
        expires_in = int(data.get("expires_in") or data.get("expire") or 7200)
        return {
            "access_token": str(access_token),
            "refresh_token": str(data.get("refresh_token") or fallback_refresh_token or ""),
            "expires_at": time.time() + expires_in,
            "created_at": datetime.now().isoformat(),
            "raw": {k: v for k, v in data.items() if "token" not in k.lower()},
        }

    def _unwrap_auth_response(self, resp: httpx.Response, label: str) -> dict[str, Any]:
        data = self._decode_response(resp)
        code = data.get("code", 0)
        if code not in (0, "0", None):
            raise EnterpriseCalendarError(f"{label} API error {code}: {data.get('msg') or data}")
        nested = data.get("data")
        return nested if isinstance(nested, dict) else data

    @staticmethod
    def _extract_calendar_id(data: dict[str, Any]) -> str:
        for key in ("calendar_id", "id"):
            if data.get(key):
                return str(data[key])
        calendar = data.get("calendar")
        if isinstance(calendar, dict):
            for key in ("calendar_id", "id"):
                if calendar.get(key):
                    return str(calendar[key])
        calendars = data.get("calendar_list") or data.get("calendars") or data.get("items")
        if isinstance(calendars, list):
            for row in calendars:
                if isinstance(row, dict):
                    value = FeishuCalendarProvider._extract_calendar_id(row)
                    if value:
                        return value
        return ""

    def _event_from_feishu(self, row: dict[str, Any], calendar_id: str) -> CalendarEvent:
        start = self._parse_time_obj(row.get("start_time") or row.get("start")) or datetime.now()
        end = self._parse_time_obj(row.get("end_time") or row.get("end"))
        event_id = str(row.get("event_id") or row.get("id") or "")
        title = str(row.get("summary") or row.get("title") or "Untitled event")
        return CalendarEvent(
            id=f"{self.name}:{calendar_id}:{event_id or title}:{start.isoformat()}",
            title=title,
            start=start,
            end=end,
            location=str(row.get("location", {}).get("name") if isinstance(row.get("location"), dict) else row.get("location") or ""),
            description=str(row.get("description") or ""),
            source=self.name,
            platform=self.name,
            calendar_id=calendar_id,
            event_id=event_id or None,
            organizer_id=str(row.get("organizer") or row.get("organizer_id") or ""),
            attendees=list(row.get("attendees") or row.get("attendee_list") or []),
            raw=row,
            metadata=row,
        )

    @staticmethod
    def _parse_time_obj(value: Any) -> datetime | None:
        if isinstance(value, dict):
            value = value.get("timestamp") or value.get("date") or value.get("date_time")
        if value is None or value == "":
            return None
        try:
            if isinstance(value, (int, float)) or str(value).isdigit():
                return datetime.fromtimestamp(int(value), tz=timezone.utc).astimezone()
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _payload_from_draft(draft: CalendarDraft, *, partial: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if draft.title or not partial:
            payload["summary"] = draft.title
        if draft.description:
            payload["description"] = draft.description
        if draft.start:
            payload["start_time"] = {"timestamp": str(int(draft.start.timestamp()))}
        if draft.end:
            payload["end_time"] = {"timestamp": str(int(draft.end.timestamp()))}
        if draft.location:
            payload["location"] = {"name": draft.location}
        if draft.attendees:
            payload["attendees"] = draft.attendees
        payload.update(draft.fields)
        return payload
