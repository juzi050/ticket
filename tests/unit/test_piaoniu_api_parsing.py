from decimal import Decimal

from app.platforms.piaoniu_api import (
    _activity_id,
    parse_event,
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
