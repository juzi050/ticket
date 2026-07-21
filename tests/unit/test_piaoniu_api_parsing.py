from decimal import Decimal

from app.platforms.piaoniu_api import (
    _activity_id,
    _order_blocks,
    parse_event,
    parse_order_confirmation,
    parse_sessions,
    parse_ticket_groups,
)


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
