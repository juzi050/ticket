from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain import BuyerProfile, MonitorTask, OrderPreview, TicketOption


def ticket() -> TicketOption:
    return TicketOption(
        platform="motianlun",
        event_url="https://m.motianlun.cn/pages/show-detail/show-detail?showId=test",
        event_id="event-1",
        event_name="测试演出",
        session_id="session-1",
        session_name="2026-08-01 19:30",
        listing_id="listing-1",
        ticket_name="看台票",
        unit_price=Decimal("280"),
        available_quantity=2,
        known_fee=Decimal("20"),
    )


def test_ids_are_generated_and_estimated_total_is_order_total() -> None:
    buyer = BuyerProfile(
        name="测试用户",
        certificate_type="身份证",
        certificate_number="110101199001011234",
    )
    option = ticket()
    task = MonitorTask(
        ticket=option,
        quantity=1,
        buyer_ids=[buyer.buyer_id],
        ideal_price=Decimal("300"),
    )

    assert buyer.buyer_id.startswith("buyer_")
    assert task.task_id.startswith("task_")
    assert option.estimated_total(1) == Decimal("300")


@pytest.mark.parametrize("interval", [0, 86401])
def test_query_interval_has_fixed_bounds(interval: float) -> None:
    with pytest.raises(ValidationError):
        MonitorTask(
            ticket=ticket(),
            quantity=1,
            buyer_ids=["buyer-1"],
            ideal_price=Decimal("300"),
            query_interval_seconds=interval,
        )


def test_task_requires_exact_unique_buyers() -> None:
    with pytest.raises(ValidationError, match="购票人数必须与购票数量一致"):
        MonitorTask(
            ticket=ticket(),
            quantity=2,
            buyer_ids=["buyer-1"],
            ideal_price=Decimal("600"),
        )

    with pytest.raises(ValidationError, match="不能重复选择同一购票人"):
        MonitorTask(
            ticket=ticket(),
            quantity=2,
            buyer_ids=["buyer-1", "buyer-1"],
            ideal_price=Decimal("600"),
        )


def test_order_preview_requires_exact_remote_buyers() -> None:
    with pytest.raises(ValidationError, match="远程购票人数"):
        OrderPreview(
            platform="motianlun",
            event_id="event-1",
            session_id="session-1",
            listing_id="listing-1",
            quantity=1,
            buyer_ids=["buyer-1"],
            remote_buyer_ids=[],
            unit_price=Decimal("280"),
            ticket_total=Decimal("280"),
            fee_total=Decimal("20"),
            final_total=Decimal("300"),
        )
