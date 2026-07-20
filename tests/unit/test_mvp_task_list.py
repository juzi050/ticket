from decimal import Decimal

from app.domain import MonitorTask, TicketOption
from app.gui.mvp_task_list import sync_task_rows, task_row


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


class FakeTree:
    def __init__(self) -> None:
        self.rows = {"selected-task": ("旧状态",), "removed-task": ("旧任务",)}
        self.selected_id = "selected-task"

    def get_children(self):
        return tuple(self.rows)

    def item(self, item_id: str, *, values):
        self.rows[item_id] = values

    def insert(self, _parent, _position, *, iid: str, values):
        self.rows[iid] = values

    def delete(self, *item_ids: str):
        for item_id in item_ids:
            self.rows.pop(item_id, None)
            if self.selected_id == item_id:
                self.selected_id = ""


def test_task_refresh_updates_rows_without_losing_selection() -> None:
    tree = FakeTree()
    current = MonitorTask(
        task_id="selected-task",
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
        status="monitoring",
    )

    sync_task_rows(tree, [current])

    assert tree.selected_id == "selected-task"
    assert tree.rows["selected-task"] == task_row(current)
    assert "removed-task" not in tree.rows
