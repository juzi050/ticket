from decimal import Decimal

from app.domain import BuyerProfile
from app.platforms.motianlun_api import (
    _create_order_body,
    _deadline_from_reserve_time,
    _matching_audience,
    _order_status,
    _price_totals,
    _show_id,
    parse_event,
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
