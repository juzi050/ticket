from decimal import Decimal

import pytest

from app.models import TicketInfo
from app.services.ticket_matcher import TicketMatcher, parse_price


def make_ticket(**changes: object) -> TicketInfo:
    values = {
        "platform": "mock",
        "event_id": "event-1",
        "event_name": "测试演唱会",
        "session_id": "session-1",
        "session_name": "2026-08-01 19:30",
        "ticket_level": "VIP 1280",
        "area": "内场A区",
        "row": "第5排",
        "seat": "8号",
        "adjacent": True,
        "unit_price": Decimal("1100"),
        "total_price": Decimal("2200"),
        "available_quantity": 2,
        "detail_url": "https://example.com/event",
    }
    values.update(changes)
    return TicketInfo(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("¥1,280 起", Decimal("1280")),
        ("￥999.50", Decimal("999.50")),
        ("原价 1280，折后 899", Decimal("899")),
        (100, Decimal("100")),
    ],
)
def test_parse_price(raw: object, expected: Decimal) -> None:
    assert parse_price(raw) == expected  # type: ignore[arg-type]


def test_full_match(sample_task: object) -> None:
    result = TicketMatcher().match(sample_task, make_ticket())  # type: ignore[arg-type]
    assert result.matched


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"unit_price": Decimal("1200.01")}, "单价超过上限"),
        ({"total_price": Decimal("2400.01")}, "实际应付总价超过上限"),
        ({"area": "遮挡区"}, "命中排除关键词"),
        ({"row": "第11排"}, "排数高于范围"),
        ({"available_quantity": 1}, "可购数量不足"),
        ({"adjacent": False}, "不满足连座要求"),
        ({"ticket_level": "普通票"}, "票档不匹配"),
    ],
)
def test_mismatch_reasons(sample_task: object, changes: dict[str, object], reason: str) -> None:
    result = TicketMatcher().match(sample_task, make_ticket(**changes))  # type: ignore[arg-type]
    assert not result.matched
    assert reason in result.reasons


def test_final_price_includes_fees(sample_task: object) -> None:
    ticket = make_ticket(
        total_price=Decimal("2300"), service_fee=Decimal("101"), final_total=None
    )
    result = TicketMatcher().match(sample_task, ticket)  # type: ignore[arg-type]
    assert "实际应付总价超过上限" in result.reasons


def test_missing_area_does_not_match_configured_area(sample_task: object) -> None:
    result = TicketMatcher().match(sample_task, make_ticket(area=None))  # type: ignore[arg-type]
    assert "区域不匹配" in result.reasons
