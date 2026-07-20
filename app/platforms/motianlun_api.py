from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.domain import (
    BuyerProfile,
    EventInfo,
    MonitorTask,
    OrderPreview,
    OrderResult,
    SessionInfo,
    TicketOption,
)
from app.platform_url import detect_platform, normalize_event_url
from app.platforms.http_api import PlatformCapabilityUnavailable, TicketPlatformApi


BASE_URL = "https://m.motianlun.cn"
WEB_VERSION = "6.76.1"
CHINA_TIMEZONE = timezone(timedelta(hours=8))


def _show_id(event_url: str) -> str:
    if detect_platform(event_url) != "motianlun":
        raise ValueError("不是摩天轮官方演出网址")
    values = parse_qs(urlsplit(event_url).query).get("showId", [])
    if not values or not values[0]:
        raise ValueError("摩天轮演出网址中缺少 showId")
    return values[0]


def _common_params() -> dict[str, str]:
    return {
        "src": "m_web",
        "time": str(int(time.time() * 1000)),
        "ver": WEB_VERSION,
    }


def parse_event(event_url: str, payload: dict[str, Any]) -> EventInfo:
    data = payload["result"]["data"]
    return EventInfo(
        platform="motianlun",
        event_url=normalize_event_url(event_url),
        event_id=str(data["showOID"]),
        event_name=str(data["showName"]),
        raw_data=data,
    )


def _parse_start_time(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=CHINA_TIMEZONE) if parsed.tzinfo is None else parsed


def parse_sessions(event_id: str, payload: dict[str, Any]) -> list[SessionInfo]:
    return [
        SessionInfo(
            platform="motianlun",
            event_id=event_id,
            session_id=str(item["sessionId"]),
            session_name=str(item["sessionName"]),
            start_time=_parse_start_time(item.get("sessionShowTime")),
            raw_data=item,
        )
        for item in payload.get("data", [])
    ]


def parse_tickets(
    *,
    event: EventInfo,
    session: SessionInfo,
    payload: dict[str, Any],
) -> list[TicketOption]:
    result: list[TicketOption] = []
    for item in payload.get("data", {}).get("sessionTicketList", []):
        notes = [
            str(note.get("noteName"))
            for note in item.get("ticketNoteTags") or []
            if note.get("noteName")
        ]
        area_parts = [item.get("sectorName"), item.get("zoneName")]
        area = " ".join(str(part) for part in area_parts if part)
        listing_id = str(item["ticketId"])
        seat_plan_id = item.get("seatPlanId")
        result.append(
            TicketOption(
                platform="motianlun",
                event_url=event.event_url,
                event_id=event.event_id,
                event_name=event.event_name,
                session_id=session.session_id,
                session_name=session.session_name,
                listing_id=listing_id,
                ticket_group_id=str(seat_plan_id) if seat_plan_id else None,
                sku_id=listing_id,
                ticket_name=str(item.get("ticketTitle") or "摩天轮票品"),
                area=area or None,
                seat_description=" / ".join(notes) or None,
                unit_price=Decimal(str(item["price"])),
                available_quantity=int(item.get("leftStocks") or 0),
                raw_data=item,
            )
        )
    return result


class MotianlunApi(TicketPlatformApi):
    platform = "motianlun"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._event_cache: dict[str, EventInfo] = {}
        self._session_cache: dict[tuple[str, str], SessionInfo] = {}

    async def check_auth(self) -> bool:
        payload = await self._request_json(
            "GET",
            f"{BASE_URL}/userapi/user/000/info",
            action="check_auth",
            params=_common_params(),
            requires_auth=True,
        )
        authenticated = payload.get("statusCode") == 200
        if not authenticated and payload.get("statusCode") == 1005 and self.sessions:
            await self.sessions.mark_expired(self.platform)
        return authenticated

    async def get_event(self, event_url: str) -> EventInfo:
        show_id = _show_id(event_url)
        payload = await self._request_json(
            "GET",
            f"{BASE_URL}/showapi/pub/show/{show_id}",
            action="get_event",
            params={**_common_params(), "locationCityOID": "3301", "utmNo": ""},
        )
        event = parse_event(event_url, payload)
        self._event_cache[event.event_id] = event
        return event

    async def list_sessions(self, event_id: str) -> list[SessionInfo]:
        event = self._event_cache.get(event_id)
        city_id = str(event.raw_data.get("cityOID") if event else "3301")
        payload = await self._request_json(
            "GET",
            f"{BASE_URL}/showapi/pub/v3/show/{event_id}/sessionone",
            action="list_sessions",
            params={
                **_common_params(),
                "locationCityOID": city_id,
                "orderDecision": "RANDOM",
            },
        )
        sessions = parse_sessions(event_id, payload)
        for session in sessions:
            self._session_cache[(event_id, session.session_id)] = session
        return sessions

    async def list_tickets(
        self, event_id: str, session_id: str, quantity: int
    ) -> list[TicketOption]:
        event = self._event_cache.get(event_id)
        session = self._session_cache.get((event_id, session_id))
        if event is None:
            raise PlatformCapabilityUnavailable("请先通过演出网址解析摩天轮演出")
        if session is None:
            await self.list_sessions(event_id)
            session = self._session_cache.get((event_id, session_id))
        if session is None:
            return []

        city_id = str(event.raw_data.get("cityOID") or "3301")
        offset = 0
        tickets: list[TicketOption] = []
        while True:
            common = _common_params()
            payload = await self._request_json(
                "POST",
                f"{BASE_URL}/showapi/pub/show_session/v2/find_tickets",
                action="list_tickets",
                params=common,
                json_body={
                    **common,
                    "offset": offset,
                    "length": 20,
                    "ticketNumber": quantity,
                    "showSessionId": session_id,
                    "locationCityOID": city_id,
                    "ticketSortType": "TICKET_PRICE_ASC",
                    "zoneIdList": [],
                    "seatPlanId": "",
                },
            )
            tickets.extend(parse_tickets(event=event, session=session, payload=payload))
            page = payload.get("data") or {}
            total = int(page.get("total") or 0)
            next_offset = int(page.get("lastOffset") or total)
            if next_offset <= offset or next_offset >= total:
                break
            offset = next_offset
        return tickets

    async def get_exact_ticket(
        self, ticket: TicketOption, quantity: int
    ) -> TicketOption | None:
        current = await self.list_tickets(ticket.event_id, ticket.session_id, quantity)
        return next(
            (item for item in current if item.listing_id == ticket.listing_id), None
        )

    async def ensure_remote_buyers(self, buyers: list[BuyerProfile]) -> list[str]:
        raise PlatformCapabilityUnavailable("摩天轮购票人 API 尚未完成登录后验证")

    async def preview_order(
        self, ticket: TicketOption, quantity: int, buyers: list[BuyerProfile]
    ) -> OrderPreview:
        raise PlatformCapabilityUnavailable("摩天轮订单预览 API 尚未完成登录后验证")

    async def create_order(self, preview: OrderPreview) -> OrderResult:
        raise PlatformCapabilityUnavailable("摩天轮创建订单 API 尚未完成登录后验证")

    async def get_order_detail(self, order_id: str) -> OrderResult:
        raise PlatformCapabilityUnavailable("摩天轮订单详情 API 尚未完成登录后验证")

    async def find_recent_order(self, task: MonitorTask) -> OrderResult | None:
        raise PlatformCapabilityUnavailable("摩天轮订单列表 API 尚未完成登录后验证")
