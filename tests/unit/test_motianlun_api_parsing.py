from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.domain import BuyerProfile, TicketOption
from app.domain import MonitorTask, OrderResult
from app.platforms.motianlun_api import (
    MotianlunApi,
    _create_order_body,
    _deadline_from_reserve_time,
    _matching_audience,
    _order_status,
    _price_totals,
    _show_id,
    parse_event,
    parse_exact_ticket,
    parse_sessions,
    parse_tickets,
)


def test_parse_motianlun_event() -> None:
    url = "https://m.motianlun.cn/pages/show-detail/show-detail?showId=show-1"
    event = parse_event(
        url,
        {
            "result": {
                "data": {
                    "showOID": "show-1",
                    "showName": "测试演出",
                    "cityOID": "3301",
                }
            }
        },
    )
    assert _show_id(url) == "show-1"
    assert event.event_name == "测试演出"
    assert event.event_url.endswith("showId=show-1")

    sessions = parse_sessions(
        event.event_id,
        {
            "data": [
                {
                    "sessionId": "session-1",
                    "sessionName": "2026.07.25 周六 19:12",
                    "sessionShowTime": "2026-07-25 19:12:00",
                }
            ]
        },
    )
    assert sessions[0].session_id == "session-1"
    assert sessions[0].start_time is not None

    tickets = parse_tickets(
        event=event,
        session=sessions[0],
        payload={
            "data": {
                "sessionTicketList": [
                    {
                        "ticketId": "ticket-1",
                        "seatPlanId": "plan-1",
                        "ticketTitle": "480票面 看台",
                        "price": 278,
                        "leftStocks": 2,
                        "sectorName": "看台",
                        "zoneName": "A区",
                        "ticketNoteTags": [{"noteName": "随机座位"}],
                    }
                ]
            }
        },
    )
    assert tickets[0].listing_id == "ticket-1"
    assert tickets[0].unit_price == Decimal("278")
    assert tickets[0].available_quantity == 2


def exact_ticket() -> TicketOption:
    return TicketOption(
        platform="motianlun",
        event_url="https://m.motianlun.cn/pages/show-detail/show-detail?showId=show-1",
        event_id="show-1",
        event_name="测试演出",
        session_id="session-1",
        session_name="测试场次",
        listing_id="ticket-1",
        ticket_group_id="plan-1",
        ticket_name="980票面 看台",
        unit_price=Decimal("1056"),
        available_quantity=4,
    )


def test_parse_exact_ticket_uses_requested_quantity_when_detail_omits_stock() -> None:
    current = parse_exact_ticket(
        reference=exact_ticket(),
        quantity=2,
        payload={
            "statusCode": 200,
            "data": {
                "show": {"showId": "show-1"},
                "session": {"sessionId": "session-1"},
                "seatPlan": {"seatPlanId": "plan-1"},
                "ticket": {
                    "ticketId": "ticket-1",
                    "ticketTitle": "980票面 看台",
                    "price": 999,
                },
            },
        },
    )

    assert current is not None
    assert current.listing_id == "ticket-1"
    assert current.unit_price == Decimal("999")
    assert current.available_quantity == 2


def test_parse_exact_ticket_rejects_changed_identity() -> None:
    current = parse_exact_ticket(
        reference=exact_ticket(),
        quantity=1,
        payload={
            "statusCode": 200,
            "data": {
                "show": {"showId": "show-1"},
                "session": {"sessionId": "session-1"},
                "seatPlan": {"seatPlanId": "plan-1"},
                "ticket": {"ticketId": "another-ticket", "price": 999},
            },
        },
    )

    assert current is None


@pytest.mark.asyncio
async def test_get_exact_ticket_uses_ticket_detail_instead_of_random_listing() -> None:
    api = MotianlunApi(httpx.AsyncClient(), Mock())
    api._request_json = AsyncMock(
        return_value={
            "statusCode": 200,
            "data": {
                "show": {"showId": "show-1"},
                "session": {"sessionId": "session-1"},
                "seatPlan": {"seatPlanId": "plan-1"},
                "ticket": {"ticketId": "ticket-1", "price": 1056},
            },
        }
    )

    try:
        current = await api.get_exact_ticket(exact_ticket(), 1)
    finally:
        await api.close()

    assert current is not None
    request = api._request_json.await_args
    assert request.kwargs["action"] == "get_exact_ticket"
    assert request.kwargs["json_body"] == {"id": "ticket-1"}


@pytest.mark.asyncio
async def test_create_order_reads_nested_result_data() -> None:
    preview = order_preview()
    pending = OrderResult(
        success=True,
        status="payment_pending",
        order_id="order-1",
        final_total=Decimal("1108.8"),
        message="待支付",
    )
    api = MotianlunApi(httpx.AsyncClient(), Mock())
    api._request_json = AsyncMock(
        return_value={
            "statusCode": 200,
            "data": None,
            "result": {"data": {"orderOID": "order-1"}},
        }
    )
    api.get_order_detail = AsyncMock(return_value=pending)

    try:
        result = await api.create_order(preview)
    finally:
        await api.close()

    assert result.order_id == "order-1"
    api.get_order_detail.assert_awaited_once_with("order-1")


@pytest.mark.asyncio
async def test_order_detail_reads_nested_reserve_time() -> None:
    api = MotianlunApi(httpx.AsyncClient(), Mock())
    api._request_json = AsyncMock(
        side_effect=[
            {
                "statusCode": 200,
                "data": {
                    "orderOID": "order-1",
                    "orderStatus": {"name": "Unpaid"},
                    "unPaidTransactionIds": ["transaction-1"],
                    "total": 1108.8,
                },
            },
            {
                "statusCode": 200,
                "data": None,
                "result": {"time": 300000},
            },
        ]
    )

    try:
        result = await api.get_order_detail("order-1")
    finally:
        await api.close()

    assert result.status == "payment_pending"
    assert result.payment_deadline is not None


@pytest.mark.asyncio
async def test_recent_order_matches_stable_fields_without_listing_id() -> None:
    current = exact_ticket().model_copy(
        update={"seat_description": "随机座位 / 票品提供 测试商家"}
    )
    task = MonitorTask(
        ticket=current,
        quantity=1,
        buyer_ids=["buyer-1"],
        ideal_price=Decimal("3000"),
    )
    pending = OrderResult(
        success=True,
        status="payment_pending",
        order_id="order-1",
        message="待支付",
        raw_data={
            "showOID": "show-1",
            "showSessionOID": "session-1",
            "seatPlanOID": "plan-1",
            "price": 1056.0,
            "items": [{"qty": 1, "ticket": {"sellerName": "测试商家"}}],
        },
    )
    api = MotianlunApi(httpx.AsyncClient(), Mock())
    api._request_json = AsyncMock(
        return_value={
            "statusCode": 200,
            "data": [{"orderOID": "order-1"}],
        }
    )
    api.get_order_detail = AsyncMock(return_value=pending)

    try:
        result = await api.find_recent_order(task)
    finally:
        await api.close()

    assert result == pending


def order_preview():
    from app.domain import OrderPreview

    return OrderPreview(
        platform="motianlun",
        preview_id="preview-token",
        event_id="show-1",
        session_id="session-1",
        listing_id="ticket-1",
        quantity=1,
        buyer_ids=["buyer-1"],
        remote_buyer_ids=["audience-1"],
        unit_price=Decimal("1056"),
        ticket_total=Decimal("1056"),
        fee_total=Decimal("52.8"),
        final_total=Decimal("1108.8"),
        raw_data={
            "detail": {
                "seatPlan": {"seatPlanId": "plan-1"},
                "ticket": {"ticketId": "ticket-1"},
            },
            "preorder": {
                "agreement": {"orderAgreementOID": "agreement-1"},
                "ticketChecksumToken": "checksum-1",
                "transactionId": "transaction-1",
                "memberLevel": {"name": "NORMAL"},
                "audienceSize": 1,
            },
            "fee_items": [
                {"itemType": "TICKET_PRICE", "amount": 1056},
                {"itemType": "SERVICE_FEE", "amount": 52.8},
            ],
            "delivery": {"code": 5, "name": "E_TICKET"},
        },
    )


def test_matches_remote_audience_by_exact_identity() -> None:
    buyer = BuyerProfile(
        name="测试用户",
        certificate_type="身份证",
        certificate_number="000000000000000000",
        phone="13000000000",
    )
    audience = {
        "id": "audience-1",
        "name": "测试用户",
        "idType": "ID_CARD",
        "idNo": "000000000000000000",
        "enable": True,
        "isValid": True,
    }

    assert _matching_audience(buyer, [audience]) == audience
    assert _matching_audience(
        buyer, [{**audience, "idNo": "000000000000000001"}]
    ) is None


def test_calculates_final_total_from_official_fee_items() -> None:
    ticket_total, fee_total, final_total = _price_totals(
        [
            {"itemType": "TICKET_PRICE", "amount": 1056},
            {"itemType": "SERVICE_FEE", "amount": 52.8},
            {"itemType": "COUPON_DISCOUNT", "amount": 10},
        ]
    )

    assert ticket_total == Decimal("1056")
    assert fee_total == Decimal("42.8")
    assert final_total == Decimal("1098.8")


def test_builds_verified_create_order_body() -> None:
    from app.domain import OrderPreview

    preview = OrderPreview(
        platform="motianlun",
        preview_id="preview-token",
        event_id="show-1",
        session_id="session-1",
        listing_id="ticket-1",
        quantity=1,
        buyer_ids=["buyer-1"],
        remote_buyer_ids=["audience-1"],
        unit_price=Decimal("1056"),
        ticket_total=Decimal("1056"),
        fee_total=Decimal("52.8"),
        final_total=Decimal("1108.8"),
        raw_data={
            "detail": {
                "seatPlan": {"seatPlanId": "plan-1"},
                "ticket": {"ticketId": "ticket-1"},
            },
            "preorder": {
                "agreement": {"orderAgreementOID": "agreement-1"},
                "ticketChecksumToken": "checksum-1",
                "transactionId": "transaction-1",
                "memberLevel": {"name": "NORMAL"},
                "audienceSize": 1,
            },
            "fee_items": [
                {"itemType": "TICKET_PRICE", "amount": 1056},
                {"itemType": "SERVICE_FEE", "amount": 52.8},
            ],
            "delivery": {"code": 5, "name": "E_TICKET"},
        },
    )

    body = _create_order_body(preview)

    assert body["ticketOID"] == "ticket-1"
    assert body["total"] == 1108.8
    assert body["audienceIdList"] == ["audience-1"]
    assert body["priceItemsV2"][1]["amount"] == 52.8


def test_maps_pending_status_and_countdown_deadline() -> None:
    from app.domain import utc_now

    assert _order_status({"name": "Unpaid"}) == "payment_pending"
    deadline = _deadline_from_reserve_time(300)
    assert deadline is not None
    assert 295 <= (deadline - utc_now()).total_seconds() <= 300
    millisecond_deadline = _deadline_from_reserve_time(300000)
    assert millisecond_deadline is not None
    assert 295 <= (millisecond_deadline - utc_now()).total_seconds() <= 300
