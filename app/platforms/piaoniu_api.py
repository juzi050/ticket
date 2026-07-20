from __future__ import annotations

import asyncio
import html
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

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
from app.platforms.http_api import (
    PlatformApiError,
    PlatformAuthExpiredError,
    PlatformCapabilityUnavailable,
    TicketPlatformApi,
)
from app.storage.audit_repository import AuditEntry


BASE_URL = "https://www.piaoniu.com"
CHINA_TIMEZONE = timezone(timedelta(hours=8))
CERTIFICATE_TYPE_IDS = {
    "身份证": 1,
    "护照": 2,
    "港澳居民来往内地通行证": 3,
    "台湾居民来往大陆通行证": 4,
    "港澳台居民居住证": 5,
    "外国人永久居留身份证": 6,
}


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


def _tag_attribute(tag: str, name: str) -> str | None:
    match = re.search(rf'\b{re.escape(name)}=["\']([^"\']*)["\']', tag)
    return html.unescape(match.group(1)) if match else None


def _tag_with_class(page: str, class_name: str) -> str | None:
    for tag in re.findall(r"<[^>]+>", page):
        classes = _tag_attribute(tag, "class")
        if classes and class_name in classes.split():
            return tag
    return None


def _money_after_label(page: str, label: str) -> Decimal:
    match = re.search(
        rf"{re.escape(label)}.*?¥\s*([0-9]+(?:\.[0-9]+)?)", page, re.DOTALL
    )
    if not match:
        raise PlatformApiError(f"票牛确认订单页缺少{label}")
    return Decimal(match.group(1))


def parse_order_confirmation(page: str, ticket_total: Decimal) -> dict[str, Any]:
    delivery_tags = [
        tag
        for tag in re.findall(r"<[^>]+>", page)
        if _tag_attribute(tag, "data-type")
        and any(
            name.startswith("delivery-type-")
            for name in (_tag_attribute(tag, "class") or "").split()
        )
    ]
    selected_delivery = next(
        (
            tag
            for tag in delivery_tags
            if "selected" in (_tag_attribute(tag, "class") or "").split()
        ),
        delivery_tags[0] if delivery_tags else None,
    )
    if selected_delivery is None:
        raise PlatformApiError("票牛确认订单页缺少配送方式")
    receive_type = int(_tag_attribute(selected_delivery, "data-type") or 0)
    service_tag = _tag_with_class(page, "service-fee") or ""
    split_tag = _tag_with_class(page, "split-order-fee") or ""
    service_fee = Decimal(_tag_attribute(service_tag, "data-fee") or "0")
    split_order_fee = Decimal(_tag_attribute(split_tag, "data-fee") or "0")
    try:
        final_total = _money_after_label(page, "应付金额")
    except PlatformApiError:
        # 官网 HTML 初始金额为空，由页面脚本按票款和费用计算后填入。
        final_total = ticket_total + service_fee + split_order_fee
    if final_total < ticket_total:
        raise PlatformApiError("票牛确认订单页金额异常")
    return {
        "receive_type": receive_type,
        "service_fee": service_fee,
        "split_order_fee": split_order_fee,
        "final_total": final_total,
        "fee_total": final_total - ticket_total,
    }


def _order_blocks(page: str) -> list[dict[str, Any]]:
    pattern = re.compile(
        r'<(?P<tag>[a-zA-Z0-9]+)(?P<attrs>[^>]*class=["\'][^"\']*\border\b'
        r'[^"\']*["\'][^>]*)>(?P<body>.*?)</(?P=tag)>',
        re.DOTALL,
    )
    result: list[dict[str, Any]] = []
    for match in pattern.finditer(page):
        attrs = match.group("attrs")
        order_id = _tag_attribute(attrs, "data-id")
        if not order_id:
            continue
        products = _tag_attribute(attrs, "data-products") or ""
        left_time = int(float(_tag_attribute(attrs, "data-left-time") or 0))
        body = html.unescape(match.group("body"))
        amounts = re.findall(r"¥\s*([0-9]+(?:\.[0-9]+)?)", body)
        result.append(
            {
                "order_id": order_id,
                "left_time": left_time,
                "products": products,
                "text": re.sub(r"<[^>]+>", " ", body),
                "final_total": Decimal(amounts[-1]) if amounts else None,
            }
        )
    return result


def _decimal_number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


class PiaoniuApi(TicketPlatformApi):
    platform = "piaoniu"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._event_cache: dict[str, EventInfo] = {}
        self._session_cache: dict[tuple[str, str], SessionInfo] = {}
        self._pending_previews: dict[str, dict[str, Any]] = {}

    async def check_auth(self) -> bool:
        try:
            payload = await self._request_json(
                "GET",
                f"{BASE_URL}/api/v1/user",
                action="check_auth",
                requires_auth=True,
            )
        except PlatformAuthExpiredError:
            return False
        return isinstance(payload, dict) and bool(payload)

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
        for buyer in buyers:
            if buyer.certificate_type != "身份证":
                raise PlatformCapabilityUnavailable(
                    f"票牛暂不支持证件类型：{buyer.certificate_type}"
                )
            if not buyer.phone:
                raise PlatformApiError("票牛电子票订单要求购票人手机号")
        return [buyer.buyer_id for buyer in buyers]

    async def preview_order(
        self, ticket: TicketOption, quantity: int, buyers: list[BuyerProfile]
    ) -> OrderPreview:
        remote_buyer_ids = await self.ensure_remote_buyers(buyers)
        details = [{"ticketGroupId": int(ticket.listing_id), "count": quantity}]
        page = await self._request_html(
            "GET",
            f"{BASE_URL}/order/confirm",
            action="preview_order",
            params={
                "ticketGroupDetails": json.dumps(
                    details, ensure_ascii=False, separators=(",", ":")
                )
            },
        )
        ticket_total = ticket.unit_price * quantity
        values = parse_order_confirmation(page, ticket_total)
        if values["receive_type"] != 4:
            raise PlatformCapabilityUnavailable("票牛当前票品不是电子票，无法自动填写配送信息")

        preview_id = uuid4().hex
        payload = {
            "ticketGroupDetails": details,
            "receiveType": 4,
            "totalAmount": _decimal_number(values["final_total"]),
            "paymentAmount": _decimal_number(values["final_total"]),
            "couponIds": [],
            "campaignIds": [],
            "postageContext": {
                "postageDesc": "",
                "postageOrigin": 0,
                "postage": 0,
            },
            "addition": [],
            "supplement": "",
            "receiverMobile": str(buyers[0].phone),
            "receiverName": buyers[0].name,
            "orderIdCards": [
                {
                    "name": buyer.name,
                    "idCard": buyer.certificate_number,
                    "idCardType": CERTIFICATE_TYPE_IDS[buyer.certificate_type],
                }
                for buyer in buyers
            ],
        }
        self._pending_previews[preview_id] = payload
        return OrderPreview(
            platform="piaoniu",
            preview_id=preview_id,
            event_id=ticket.event_id,
            session_id=ticket.session_id,
            listing_id=ticket.listing_id,
            quantity=quantity,
            buyer_ids=[buyer.buyer_id for buyer in buyers],
            remote_buyer_ids=remote_buyer_ids,
            unit_price=ticket.unit_price,
            ticket_total=ticket_total,
            fee_total=values["fee_total"],
            final_total=values["final_total"],
            raw_data={
                "receive_type": values["receive_type"],
                "service_fee": str(values["service_fee"]),
                "split_order_fee": str(values["split_order_fee"]),
            },
        )

    async def create_order(self, preview: OrderPreview) -> OrderResult:
        payload = self._pending_previews.pop(preview.preview_id or "", None)
        if payload is None:
            raise PlatformApiError("票牛订单预览已失效，请重新预下单")
        result = await self._request_json(
            "POST",
            f"{BASE_URL}/api/v1/order.json",
            action="create_order",
            json_body=payload,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": (
                    f"{BASE_URL}/order/confirm?ticketGroupDetails="
                    + json.dumps(payload["ticketGroupDetails"], separators=(",", ":"))
                ),
            },
            redact_request_body=True,
            requires_auth=True,
        )
        order_id = str(result.get("orderId") or "") if isinstance(result, dict) else ""
        if not order_id:
            raise PlatformApiError("票牛创建订单成功响应缺少订单号")
        return OrderResult(
            success=True,
            status="payment_pending",
            order_id=order_id,
            final_total=preview.final_total,
            payment_url=f"{BASE_URL}/order/{order_id}/pay",
            message="票牛真实待支付订单创建成功",
            raw_data={"orderId": order_id},
        )

    async def get_order_detail(self, order_id: str) -> OrderResult:
        page = await self._request_html(
            "GET", f"{BASE_URL}/user/order", action="get_order_detail"
        )
        row = next(
            (item for item in _order_blocks(page) if item["order_id"] == order_id),
            None,
        )
        if row is None:
            return OrderResult(
                success=False,
                status="not_found",
                order_id=order_id,
                message="票牛订单列表中未找到该订单",
            )
        return self._order_result(row)

    async def find_recent_order(self, task: MonitorTask) -> OrderResult | None:
        page = await self._request_html(
            "GET", f"{BASE_URL}/user/order", action="find_recent_order"
        )
        for row in _order_blocks(page):
            matches_ticket = task.ticket.listing_id in row["products"]
            matches_event = task.ticket.event_name in row["text"]
            if (matches_ticket or matches_event) and row["left_time"] > 0:
                return self._order_result(row)
        return None

    def _order_result(self, row: dict[str, Any]) -> OrderResult:
        pending = row["left_time"] > 0
        deadline = (
            datetime.now(timezone.utc)
            + timedelta(milliseconds=row["left_time"])
            if pending
            else None
        )
        order_id = str(row["order_id"])
        return OrderResult(
            success=pending,
            status="payment_pending" if pending else "closed",
            order_id=order_id,
            final_total=row["final_total"],
            payment_deadline=deadline,
            payment_url=f"{BASE_URL}/order/{order_id}/pay" if pending else None,
            message="票牛订单待支付" if pending else "票牛订单已关闭",
            raw_data={"left_time": row["left_time"]},
        )

    async def _request_html(
        self,
        method: str,
        url: str,
        *,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        try:
            response = await self.client.request(method, url, params=params)
            await self.audit.append(
                AuditEntry(
                    level="INFO" if response.is_success else "ERROR",
                    category="http",
                    action=action,
                    platform=self.platform,
                    message=f"{method} {action} -> HTTP {response.status_code}",
                    request_url=str(response.request.url),
                    request_method=method,
                    response_status=response.status_code,
                    response_body={"content_length": len(response.content)},
                )
            )
            if response.status_code in {401, 403} or "/login" in response.url.path:
                if self.sessions:
                    await self.sessions.mark_expired(self.platform)
                raise PlatformAuthExpiredError("登录状态已失效，请重新登录")
            response.raise_for_status()
            return response.text
        except (PlatformApiError, PlatformAuthExpiredError):
            raise
        except (httpx.HTTPError, OSError) as exc:
            await self.audit.append(
                AuditEntry(
                    level="ERROR",
                    category="http",
                    action=action,
                    platform=self.platform,
                    message="票牛页面请求失败",
                    request_url=url,
                    request_method=method,
                    exception_type=type(exc).__name__,
                    exception_message=str(exc),
                )
            )
            raise PlatformApiError(f"票牛 {action} 请求失败：{exc}") from exc
