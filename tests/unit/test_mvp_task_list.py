from decimal import Decimal

from app.domain import MonitorTask, TicketOption
from app.gui.mvp_task_list import task_row


def test_task_row_shows_saved_interval_and_total_limit() -> None:
    task = MonitorTask(
        ticket=TicketOption(
            platform="motianlun",
            event_url="https://m.motianlun.cn/show?showId=1",
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
        buyer_ids=["buyer-1"],
        ideal_price=Decimal("280"),
        query_interval_seconds=12,
    )
    row = task_row(task)
    assert "¥280" in row
    assert "12" in row
    assert "测试演出" in row
