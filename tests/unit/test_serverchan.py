from datetime import timedelta
from decimal import Decimal

from app.domain import MonitorTask, OrderResult, TicketOption, utc_now
from app.notifications.serverchan import build_success_message


def test_serverchan_message_contains_payment_fields_but_no_buyer_secrets() -> None:
    task = MonitorTask(
        ticket=TicketOption(
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
        ),
        quantity=1,
        buyer_ids=["buyer-secret"],
        ideal_price=Decimal("300"),
    )
    result = OrderResult(
        success=True,
        status="payment_pending",
        order_id="order-1",
        final_total=Decimal("290"),
        payment_deadline=utc_now() + timedelta(minutes=10),
        payment_url="https://example.com/pay/order-1",
        message="待支付",
    )
    message = build_success_message(task, result)
    assert "¥290" in message
    assert "order-1" in message
    assert result.payment_url in message
    assert "buyer-secret" not in message
