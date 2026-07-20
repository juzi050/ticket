from __future__ import annotations

import re
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

class PiaoniuApi(TicketPlatformApi):
    platform = "piaoniu"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._event_cache: dict[str, EventInfo] = {}

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
        raise PlatformCapabilityUnavailable("票牛场次 API 尚未实现")

    async def list_tickets(
        self, event_id: str, session_id: str, quantity: int
    ) -> list[TicketOption]:
        raise PlatformCapabilityUnavailable("票牛票品 API 尚未实现")

    async def get_exact_ticket(
        self, ticket: TicketOption, quantity: int
    ) -> TicketOption | None:
        raise PlatformCapabilityUnavailable("票牛票品 API 尚未实现")

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
