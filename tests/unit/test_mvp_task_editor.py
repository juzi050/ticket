from decimal import Decimal

from app.domain import BuyerProfile, TicketOption
from app.gui.mvp_task_editor import build_monitor_task, ticket_choice_label


def test_task_editor_builds_generated_task_without_name_or_manual_id() -> None:
    buyer = BuyerProfile(
        name="测试用户",
        certificate_type="身份证",
        certificate_number="110101199001011234",
    )
    ticket = TicketOption(
        platform="piaoniu",
        event_url="https://www.piaoniu.com/activity/1",
        event_id="1",
        event_name="测试演出",
        session_id="2",
        session_name="晚场",
        listing_id="3",
        ticket_name="480票面 看台",
        unit_price=Decimal("278"),
        available_quantity=1,
    )
    task = build_monitor_task(
        ticket=ticket,
        quantity=1,
        buyer_ids=[buyer.buyer_id],
        ideal_price="280",
        query_interval_seconds="12",
    )
    assert task.task_id.startswith("task_")
    assert task.ideal_price == Decimal("280")
    assert task.query_interval_seconds == 12
    assert "3" in ticket_choice_label(ticket)
