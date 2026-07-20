from datetime import timedelta
from decimal import Decimal

from app.domain import MonitorTask, OrderPreview, OrderResult, TicketOption, utc_now
from app.storage.database import MvpDatabase
from app.storage.order_repository import (
    OrderRepository,
    build_idempotency_key,
)
from app.storage.task_repository import TaskRepository


def task() -> MonitorTask:
    ticket = TicketOption(
        platform="motianlun",
        event_url="https://m.motianlun.cn/show?showId=1",
        event_id="event-1",
        event_name="测试演出",
        session_id="session-1",
        session_name="晚场",
        listing_id="listing-1",
        ticket_name="看台票",
        unit_price=Decimal("280"),
        available_quantity=2,
    )
    return MonitorTask(
        ticket=ticket,
        quantity=2,
        buyer_ids=["buyer-2", "buyer-1"],
        ideal_price=Decimal("600"),
    )


def preview() -> OrderPreview:
    return OrderPreview(
        platform="motianlun",
        preview_id="preview-1",
        event_id="event-1",
        session_id="session-1",
        listing_id="listing-1",
        quantity=2,
        buyer_ids=["buyer-2", "buyer-1"],
        remote_buyer_ids=["remote-2", "remote-1"],
        unit_price=Decimal("280"),
        ticket_total=Decimal("560"),
        fee_total=Decimal("20"),
        final_total=Decimal("580"),
    )


def test_idempotency_key_is_stable_for_buyer_order() -> None:
    first = task()
    second = first.model_copy(update={"buyer_ids": ["buyer-1", "buyer-2"]})
    assert build_idempotency_key(first) == build_idempotency_key(second)


async def test_creating_and_payment_pending_block_duplicate_order(tmp_path) -> None:
    database = MvpDatabase(tmp_path / "ticket.db")
    await database.initialize()
    current = await TaskRepository(database).save(task())
    repository = OrderRepository(database)

    claimed, _ = await repository.claim_creating(current, preview())
    duplicated, existing = await repository.claim_creating(current, preview())

    assert claimed
    assert not duplicated
    assert existing is not None and existing.status == "creating"

    deadline = utc_now() + timedelta(minutes=10)
    result = OrderResult(
        success=True,
        status="payment_pending",
        order_id="order-1",
        final_total=Decimal("580"),
        payment_deadline=deadline,
        payment_url="https://example.com/pay/order-1",
        message="待支付",
    )
    saved = await repository.save_result(build_idempotency_key(current), result)
    assert saved.status == "payment_pending"
    assert saved.final_total == Decimal("580")
    assert await repository.find_blocking(current) is not None


async def test_unknown_after_timeout_blocks_blind_retry(tmp_path) -> None:
    database = MvpDatabase(tmp_path / "ticket.db")
    await database.initialize()
    current = await TaskRepository(database).save(task())
    repository = OrderRepository(database)
    await repository.claim_creating(current, preview())
    key = build_idempotency_key(current)

    await repository.mark_unknown_after_timeout(key, "订单状态无法确认")
    duplicated, existing = await repository.claim_creating(current, preview())

    assert not duplicated
    assert existing is not None
    assert existing.status == "unknown_after_timeout"
