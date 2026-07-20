from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.domain import (
    BuyerPlatformBinding,
    BuyerProfile,
    EventInfo,
    MonitorTask,
    OrderPreview,
    OrderResult,
    SessionInfo,
    TicketOption,
)
from app.platform_url import detect_platform, normalize_event_url
from app.platforms.motianlun_crypto import (
    decrypt_profile_value,
    encrypt_profile_value,
)
from app.platforms.http_api import PlatformCapabilityUnavailable, TicketPlatformApi
from app.platforms.http_api import PlatformApiError, PlatformAuthExpiredError


BASE_URL = "https://m.motianlun.cn"
WEB_VERSION = "6.76.1"
FRONT_CONFIG_URL = "https://app.motianlun.cn/prod_configs/property_mtl.json"
CHINA_TIMEZONE = timezone(timedelta(hours=8))
DISCOUNT_ITEM_TYPES = {
    "PREORDER_DISCOUNT",
    "COUPON_DISCOUNT",
    "POINT_DISCOUNT",
    "ORDER_SERVICE_FEE_DISCOUNT",
}


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


def _business_data(payload: dict[str, Any], action: str) -> Any:
    status_code = payload.get("statusCode")
    if status_code not in {0, 200}:
        message = payload.get("comments") or f"摩天轮 {action} 失败"
        if status_code == 1005:
            raise PlatformAuthExpiredError(str(message))
        raise PlatformApiError(str(message))
    return payload.get("data")


def _certificate_type(value: str) -> str:
    normalized = value.strip().upper()
    if normalized in {"身份证", "居民身份证", "IDENTITY_CARD", "ID_CARD"}:
        return "ID_CARD"
    return normalized


def _matching_audience(
    buyer: BuyerProfile, audiences: list[dict[str, Any]]
) -> dict[str, Any] | None:
    certificate_type = _certificate_type(buyer.certificate_type)
    return next(
        (
            audience
            for audience in audiences
            if str(audience.get("name") or "") == buyer.name
            and str(audience.get("idNo") or "") == buyer.certificate_number
            and str(audience.get("idType") or "").upper() == certificate_type
            and audience.get("enable") is not False
            and audience.get("isValid") is not False
        ),
        None,
    )


def _delivery_method(value: Any) -> dict[str, Any]:
    methods = value if isinstance(value, list) else [value]
    available = [item for item in methods if isinstance(item, dict)]
    if not available:
        raise PlatformCapabilityUnavailable("摩天轮预下单没有可用配送方式")
    selected = available[0]
    if str(selected.get("name") or "").upper() in {"EXPRESS", "DELIVER"}:
        raise PlatformCapabilityUnavailable("目标票品要求收货地址，当前 MVP 未配置地址")
    return selected


def _price_totals(items: list[dict[str, Any]]) -> tuple[Decimal, Decimal, Decimal]:
    total = Decimal("0")
    ticket_total = Decimal("0")
    for item in items:
        amount = Decimal(str(item.get("amount") or 0))
        item_type = str(item.get("itemType") or "")
        if item_type == "TICKET_PRICE":
            ticket_total += abs(amount)
        if item_type in DISCOUNT_ITEM_TYPES:
            total -= abs(amount)
        else:
            total += abs(amount)
    return ticket_total, total - ticket_total, total


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

    async def _service_key(self) -> str:
        config = await self._request_json(
            "GET",
            FRONT_CONFIG_URL,
            action="get_front_config",
        )
        service_key = str(config.get("serviceAesKey") or "")
        if not service_key:
            raise PlatformCapabilityUnavailable("摩天轮未返回购票人资料加密密钥")
        return service_key

    async def _audience_list(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        payload = await self._request_json(
            "GET",
            f"{BASE_URL}/buyerapi/buyer/v4/user_audiences",
            action="list_buyers",
            params=_common_params(),
            requires_auth=True,
        )
        try:
            data = _business_data(payload, "查询购票人") or {}
        except PlatformAuthExpiredError:
            if self.sessions:
                await self.sessions.mark_expired(self.platform)
            raise
        return (
            list(data.get("supportedAudiences") or []),
            list(data.get("unsupportedAudiences") or []),
        )

    async def _audience_detail(
        self, remote_id: str, service_key: str
    ) -> dict[str, Any]:
        payload = await self._request_json(
            "POST",
            f"{BASE_URL}/buyerapi/buyer/v2/user_audiences/detail",
            action="get_buyer_detail",
            params=_common_params(),
            json_body={"id": remote_id},
            requires_auth=True,
        )
        data = dict(_business_data(payload, "查询购票人详情") or {})
        try:
            data["name"] = decrypt_profile_value(
                str(data.get("encryptedName") or ""), service_key
            )
            data["idNo"] = decrypt_profile_value(
                str(data.get("encryptedIdNo") or ""), service_key
            )
            data["cellphone"] = decrypt_profile_value(
                str(data.get("encryptedCellphone") or ""), service_key
            )
        except Exception as exc:
            raise PlatformApiError("摩天轮购票人资料解密失败") from exc
        return data

    async def _find_audience(
        self,
        buyer: BuyerProfile,
        audiences: list[dict[str, Any]],
        service_key: str,
    ) -> dict[str, Any] | None:
        for audience in audiences:
            if str(audience.get("name") or "") != buyer.name:
                continue
            detail = await self._audience_detail(str(audience["id"]), service_key)
            detail.update(
                {
                    "enable": audience.get("enable", True),
                    "isValid": audience.get("isValid", True),
                }
            )
            matched = _matching_audience(buyer, [detail])
            if matched:
                return matched
        return None

    async def ensure_remote_buyers(self, buyers: list[BuyerProfile]) -> list[str]:
        service_key = await self._service_key()
        supported, unsupported = await self._audience_list()
        remote_ids: list[str] = []
        for buyer in buyers:
            audience = await self._find_audience(buyer, supported, service_key)
            if audience is None:
                invalid = await self._find_audience(
                    buyer, unsupported, service_key
                )
                if invalid:
                    raise PlatformCapabilityUnavailable(
                        f"购票人 {buyer.name} 在摩天轮中不可用于当前订单"
                    )
                create_payload = await self._request_json(
                    "POST",
                    f"{BASE_URL}/buyerapi/buyer/v1/user_audiences",
                    action="create_buyer",
                    params=_common_params(),
                    json_body={
                        "idType": _certificate_type(buyer.certificate_type),
                        "countryCode": "",
                        "encryptedName": encrypt_profile_value(
                            buyer.name, service_key
                        ),
                        "encryptedIdNo": encrypt_profile_value(
                            buyer.certificate_number, service_key
                        ),
                        "encryptedCellphone": encrypt_profile_value(
                            buyer.phone or "", service_key
                        ),
                    },
                    requires_auth=True,
                )
                _business_data(create_payload, "新增购票人")
                supported, _ = await self._audience_list()
                audience = await self._find_audience(buyer, supported, service_key)
                if audience is None:
                    raise PlatformApiError("摩天轮新增购票人后未能精确读取该资料")
            remote_id = str(audience["id"])
            remote_ids.append(remote_id)
            if self.buyer_bindings:
                await self.buyer_bindings.save(
                    BuyerPlatformBinding(
                        buyer_id=buyer.buyer_id,
                        platform=self.platform,
                        remote_buyer_id=remote_id,
                        remote_payload=audience,
                    )
                )
        return remote_ids

    async def preview_order(
        self, ticket: TicketOption, quantity: int, buyers: list[BuyerProfile]
    ) -> OrderPreview:
        if len(buyers) != quantity:
            raise ValueError("购票人数必须与购票数量一致")
        remote_ids = await self.ensure_remote_buyers(buyers)
        detail_payload = await self._request_json(
            "POST",
            f"{BASE_URL}/showapi/pub/show/v1/find_show_ticket_by_ticket_id",
            action="prepare_order",
            params=_common_params(),
            json_body={"id": ticket.listing_id},
        )
        detail = _business_data(detail_payload, "读取票品下单信息") or {}
        show = detail.get("show") or {}
        session = detail.get("session") or {}
        seat_plan = detail.get("seatPlan") or {}
        current_ticket = detail.get("ticket") or {}
        exact_ids = (
            str(show.get("showId") or "") == ticket.event_id,
            str(session.get("sessionId") or "") == ticket.session_id,
            str(current_ticket.get("ticketId") or "") == ticket.listing_id,
            str(seat_plan.get("seatPlanId") or "") == str(ticket.ticket_group_id),
        )
        if not all(exact_ids):
            raise PlatformApiError("下单前票品稳定标识发生变化，已停止")

        preorder_payload = await self._request_json(
            "POST",
            f"{BASE_URL}/orderapi/v2/preorder",
            action="preview_order",
            params=_common_params(),
            json_body={
                "show": ticket.event_id,
                "session": ticket.session_id,
                "seatPlanId": ticket.ticket_group_id,
                "originalPrice": seat_plan.get("originalPrice"),
                "ticketOID": ticket.listing_id,
                "price": float(
                    Decimal(str(current_ticket.get("price") or 0)) * quantity
                ),
                "qty": quantity,
                "locationCityOID": "3301",
                "adjacentSeat": True,
                "compensatedPrice": 0,
                "user": "000",
            },
            requires_auth=True,
        )
        preorder = _business_data(preorder_payload, "预下单") or {}
        if int(preorder.get("audienceSize") or 0) not in {0, quantity}:
            raise PlatformApiError("平台要求的实名观演人数与购票数量不一致")
        if preorder.get("exceedUnpaidLimit"):
            raise PlatformApiError("摩天轮账号已有过多待支付订单")
        delivery = _delivery_method(preorder.get("supportDeliverMethods"))
        fee_payload = await self._request_json(
            "POST",
            f"{BASE_URL}/orderapi/buyer/v2/order/service_fee",
            action="confirm_final_price",
            params=_common_params(),
            json_body={
                "showId": ticket.event_id,
                "showSessionId": ticket.session_id,
                "seatPlanId": ticket.ticket_group_id,
                "originalPrice": seat_plan.get("originalPrice"),
                "ticketId": ticket.listing_id,
                "ticketPrice": current_ticket.get("price"),
                "qty": quantity,
                "deliverMethod": delivery.get("name"),
            },
            requires_auth=True,
        )
        fee_items = list(_business_data(fee_payload, "确认最终金额") or [])
        ticket_total, fee_total, final_total = _price_totals(fee_items)
        if not fee_items or ticket_total <= 0 or final_total <= 0:
            raise PlatformApiError("无法可靠获得订单最终应付金额")
        return OrderPreview(
            platform=self.platform,
            preview_id=str(preorder.get("token") or "") or None,
            event_id=ticket.event_id,
            session_id=ticket.session_id,
            listing_id=ticket.listing_id,
            quantity=quantity,
            buyer_ids=[buyer.buyer_id for buyer in buyers],
            remote_buyer_ids=remote_ids,
            unit_price=Decimal(str(current_ticket.get("price") or 0)),
            ticket_total=ticket_total,
            fee_total=fee_total,
            final_total=final_total,
            raw_data={
                "detail": detail,
                "preorder": preorder,
                "fee_items": fee_items,
                "delivery": delivery,
            },
        )

    async def create_order(self, preview: OrderPreview) -> OrderResult:
        raise PlatformCapabilityUnavailable("摩天轮创建订单 API 尚未完成登录后验证")

    async def get_order_detail(self, order_id: str) -> OrderResult:
        raise PlatformCapabilityUnavailable("摩天轮订单详情 API 尚未完成登录后验证")

    async def find_recent_order(self, task: MonitorTask) -> OrderResult | None:
        raise PlatformCapabilityUnavailable("摩天轮订单列表 API 尚未完成登录后验证")
