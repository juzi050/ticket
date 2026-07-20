import json
from decimal import Decimal

import httpx

from app.domain import BuyerProfile, TicketOption
from app.platforms.piaoniu_api import (
    PiaoniuApi,
    _activity_id,
    _order_blocks,
    parse_event,
    parse_order_confirmation,
    parse_sessions,
    parse_ticket_groups,
)
from app.storage.audit_repository import AuditRepository
from app.storage.database import MvpDatabase


def test_parse_piaoniu_event() -> None:
    url = "https://www.piaoniu.com/activity/779707"
    payload = {
        "id": 779707,
        "name": "测试演出",
        "events": [
            {"id": 14944160, "specification": "2026.07.25 周六 19:12", "start": 1784977920000}
        ],
    }
    assert _activity_id(url) == "779707"
    event = parse_event(url, payload)
    assert event.event_name == "测试演出"
    sessions = parse_sessions(event.event_id, payload)
    assert sessions[0].session_id == "14944160"
    assert sessions[0].start_time is not None


def test_parse_exact_quantity_ticket_groups() -> None:
    tickets = parse_ticket_groups(
        event_url="https://www.piaoniu.com/activity/1",
        event_id="1",
        event_name="测试演出",
        session_id="2",
        session_name="晚场",
        quantity=1,
        category={"id": 3, "specification": "480票面 看台"},
        payload={
            "ticketGroups": {
                "1": {
                    "ticketGroups": [
                        {
                            "id": 4,
                            "salePrice": 278,
                            "areaName": "区域随机",
                            "providerId": 5,
                            "addition": {"numMax": 6},
                        }
                    ]
                },
                "2": {"ticketGroups": []},
            }
        },
    )
    assert len(tickets) == 1
    assert tickets[0].listing_id == "4"
    assert tickets[0].unit_price == Decimal("278")
    assert tickets[0].available_quantity == 6


def test_parse_order_confirmation_and_pending_order() -> None:
    confirmation = """
    <div data-type="4" class="item delivery-type-4 selected">电子票</div>
    <div data-fee="43" class="service-fee row"></div>
    <div class="deal row"><div class="label">应付金额：</div>
    <div class="price">¥921.00</div></div>
    """
    values = parse_order_confirmation(confirmation, Decimal("878"))
    assert values["receive_type"] == 4
    assert values["fee_total"] == Decimal("43")
    assert values["final_total"] == Decimal("921.00")

    orders = _order_blocks(
        """
        <tr class="order" data-id="123" data-left-time="600000"
            data-products="[{&quot;ticketGroupId&quot;:38563675}]">
          <td>测试演出</td><td class="total">¥921.00</td>
        </tr>
        """
    )
    assert orders[0]["order_id"] == "123"
    assert orders[0]["left_time"] == 600000
    assert orders[0]["final_total"] == Decimal("921.00")


async def test_preview_and_create_real_piaoniu_request_shape(tmp_path) -> None:
    database = MvpDatabase(tmp_path / "ticket.db")
    await database.initialize()
    audit = AuditRepository(database)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/order/confirm":
            assert "38563675" in request.url.params["ticketGroupDetails"]
            return httpx.Response(
                200,
                text="""
                <div data-type="4" class="item delivery-type-4">电子票</div>
                <div data-fee="43" class="service-fee row"></div>
                <div>应付金额：<span>¥921.00</span></div>
                """,
            )
        assert request.url.path == "/api/v1/order.json"
        body = json.loads(request.content)
        assert body["ticketGroupDetails"] == [
            {"ticketGroupId": 38563675, "count": 1}
        ]
        assert body["paymentAmount"] == 921
        assert body["orderIdCards"][0]["idCardType"] == 1
        return httpx.Response(200, json={"orderId": 987654321})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://www.piaoniu.com"
    )
    api = PiaoniuApi(client, audit)
    ticket = TicketOption(
        platform="piaoniu",
        event_url="https://www.piaoniu.com/activity/779707",
        event_id="779707",
        event_name="测试演出",
        session_id="14944160",
        session_name="测试场次",
        listing_id="38563675",
        ticket_name="480票面 看台",
        unit_price=Decimal("878"),
        available_quantity=1,
    )
    buyer = BuyerProfile(
        name="测试用户",
        certificate_type="身份证",
        certificate_number="110101199001010000",
        phone="13800000000",
    )

    preview = await api.preview_order(ticket, 1, [buyer])
    result = await api.create_order(preview)

    assert preview.final_total == Decimal("921.00")
    assert result.status == "payment_pending"
    assert result.payment_url == "https://www.piaoniu.com/order/987654321/pay"
    logs = await audit.query()
    create_log = next(log for log in logs if log.action == "create_order")
    assert create_log.request_body == "[REDACTED]"
    await api.close()
