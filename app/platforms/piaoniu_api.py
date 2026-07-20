from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

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


BASE_URL = "https://www.piaoniu.com"
CHINA_TIMEZONE = timezone(timedelta(hours=8))


def _activity_id(event_url: str) -> str:
    if detect_platform(event_url) != "piaoniu":
        raise ValueError("不是票牛官方演出网址")
    path = urlsplit(event_url).path.rstrip("/")
    match = re.search(r"/(?:activity|activities)/(\d+)(?:\.html)?$", path)
    if not match:
        raise ValueError("票牛演出网址中缺少 activity ID")
    return match.group(1)


def parse_event(event_url: str, payload: dict[str, Any]) -> EventInfo:
    event_id = str(payload.get("id") or _activity_id(event_url))
    return EventInfo(
        platform="piaoniu",
        event_url=normalize_event_url(event_url),
        event_id=event_id,
        event_name=str(payload["name"]),
        raw_data=payload,
    )


def _parse_start_time(milliseconds: Any) -> datetime | None:
    if milliseconds in (None, ""):
        return None
    return datetime.fromtimestamp(float(milliseconds) / 1000, tz=CHINA_TIMEZONE)


def parse_sessions(event_id: str, payload: dict[str, Any]) -> list[SessionInfo]:
    return [
        SessionInfo(
            platform="piaoniu",
            event_id=event_id,
            session_id=str(item["id"]),
            session_name=str(item["specification"]),
            start_time=_parse_start_time(item.get("start")),
            raw_data=item,
        )
        for item in payload.get("events", [])
    ]


def parse_ticket_groups(
    *,
    event_url: str,
    event_id: str,
    event_name: str,
    session_id: str,
    session_name: str,
    quantity: int,
    category: dict[str, Any],
    payload: dict[str, Any],
) -> list[TicketOption]:
    quantity_group = payload.get("ticketGroups", {}).get(str(quantity), {})
    groups = quantity_group.get("ticketGroups", [])
    result: list[TicketOption] = []
    for item in groups:
        listing_id = str(item["id"])
        addition = item.get("addition") or {}
        seller_id = item.get("providerId") or item.get("shopId")
        result.append(
            TicketOption(
                platform="piaoniu",
                event_url=event_url,
                event_id=event_id,
                event_name=event_name,
                session_id=session_id,
                session_name=session_name,
                listing_id=listing_id,
                ticket_group_id=listing_id,
                seller_id=str(seller_id) if seller_id else None,
                ticket_name=str(category["specification"]),
                area=str(item.get("areaName") or category["specification"]),
                seat_description=str(item.get("areaName") or "") or None,
                unit_price=Decimal(str(item["salePrice"])),
                available_quantity=int(addition.get("numMax") or quantity),
                raw_data={"category": category, "listing": item},
            )
        )
    return result

class PiaoniuApi(TicketPlatformApi):
    platform = "piaoniu"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._event_cache: dict[str, EventInfo] = {}
        self._session_cache: dict[tuple[str, str], SessionInfo] = {}

    async def check_auth(self) -> bool:
        return False

    async def get_event(self, event_url: str) -> EventInfo:
        activity_id = _activity_id(event_url)
        payload = await self._request_json(
            "GET",
            f"{BASE_URL}/api/v1/activities/{activity_id}.json",
            action="get_event",
        )
        event = parse_event(event_url, payload)
        self._event_cache[event.event_id] = event
        return event

    async def list_sessions(self, event_id: str) -> list[SessionInfo]:
        payload = await self._request_json(
            "GET",
            f"{BASE_URL}/api/v1/activities/{event_id}.json",
            action="list_sessions",
        )
        sessions = parse_sessions(event_id, payload)
        for session in sessions:
            self._session_cache[(event_id, session.session_id)] = session
        if event_id not in self._event_cache:
            self._event_cache[event_id] = parse_event(
                f"{BASE_URL}/activity/{event_id}", payload
            )
        return sessions

    async def list_tickets(
        self, event_id: str, session_id: str, quantity: int
    ) -> list[TicketOption]:
        event = self._event_cache.get(event_id)
        session = self._session_cache.get((event_id, session_id))
        if event is None or session is None:
            await self.list_sessions(event_id)
            event = self._event_cache[event_id]
            session = self._session_cache.get((event_id, session_id))
        if session is None:
            return []
        categories = await self._request_json(
            "GET",
            f"{BASE_URL}/api/v1/ticketCategories.json",
            action="list_ticket_categories",
            params={"b2c": "true", "eventId": session_id},
        )

        async def fetch(category: dict[str, Any]) -> list[TicketOption]:
            payload = await self._request_json(
                "GET",
                f"{BASE_URL}/api/v4/tickets.json",
                action="list_tickets",
                params={
                    "b2c": "true",
                    "eventId": session_id,
                    "ticketCategoryId": category["id"],
                },
            )
            return parse_ticket_groups(
                event_url=event.event_url,
                event_id=event_id,
                event_name=event.event_name,
                session_id=session_id,
                session_name=session.session_name,
                quantity=quantity,
                category=category,
                payload=payload,
            )

        nested = await asyncio.gather(*(fetch(category) for category in categories))
        return [ticket for group in nested for ticket in group]

    async def get_exact_ticket(
        self, ticket: TicketOption, quantity: int
    ) -> TicketOption | None:
        current = await self.list_tickets(ticket.event_id, ticket.session_id, quantity)
        return next(
            (item for item in current if item.listing_id == ticket.listing_id), None
        )

    async def ensure_remote_buyers(self, buyers: list[BuyerProfile]) -> list[str]:
        raise PlatformCapabilityUnavailable("票牛购票人 API 尚未完成登录后验证")

    async def preview_order(
        self, ticket: TicketOption, quantity: int, buyers: list[BuyerProfile]
    ) -> OrderPreview:
        raise PlatformCapabilityUnavailable("票牛订单预览 API 尚未完成登录后验证")

    async def create_order(self, preview: OrderPreview) -> OrderResult:
        raise PlatformCapabilityUnavailable("票牛创建订单 API 尚未完成登录后验证")

    async def get_order_detail(self, order_id: str) -> OrderResult:
        raise PlatformCapabilityUnavailable("票牛订单详情 API 尚未完成登录后验证")

    async def find_recent_order(self, task: MonitorTask) -> OrderResult | None:
        raise PlatformCapabilityUnavailable("票牛订单列表 API 尚未完成登录后验证")
