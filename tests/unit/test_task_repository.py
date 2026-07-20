from datetime import timedelta
from decimal import Decimal

import pytest

from app.domain import MonitorTask, TicketOption, utc_now
from app.storage.database import MvpDatabase
from app.storage.task_repository import TaskRepository


def task(interval: float = 10) -> MonitorTask:
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
        available_quantity=1,
    )
    return MonitorTask(
        ticket=ticket,
        quantity=1,
        buyer_ids=["buyer-1"],
        ideal_price=Decimal("300"),
        query_interval_seconds=interval,
    )


async def test_task_and_independent_interval_survive_repository_restart(tmp_path) -> None:
    database = MvpDatabase(tmp_path / "ticket.db")
    await database.initialize()
    repository = TaskRepository(database)
    first = await repository.save(task(5))
    second = await repository.save(task(30))

    restored = TaskRepository(MvpDatabase(database.path))
    assert (await restored.get(first.task_id)).query_interval_seconds == 5  # type: ignore[union-attr]
    assert (await restored.get(second.task_id)).query_interval_seconds == 30  # type: ignore[union-attr]

    await restored.update_interval(first.task_id, 12.5)
    assert (await restored.get(first.task_id)).query_interval_seconds == 12.5  # type: ignore[union-attr]
    assert (await restored.get(second.task_id)).query_interval_seconds == 30  # type: ignore[union-attr]


async def test_runtime_fields_and_pause_are_persisted(tmp_path) -> None:
    database = MvpDatabase(tmp_path / "ticket.db")
    await database.initialize()
    repository = TaskRepository(database)
    saved = await repository.save(task())
    checked_at = utc_now()
    next_at = checked_at + timedelta(seconds=10)

    await repository.update_runtime(
        saved.task_id,
        status="price_too_high",
        last_unit_price=Decimal("320"),
        last_estimated_total=Decimal("320"),
        last_checked_at=checked_at,
        next_check_at=next_at,
        last_error="价格超过理想总价",
    )
    await repository.set_enabled(saved.task_id, False, "paused")

    restored = await repository.get(saved.task_id)
    assert restored is not None
    assert not restored.enabled
    assert restored.status == "paused"
    assert restored.last_unit_price == Decimal("320")
    assert restored.next_check_at == next_at


async def test_repository_rejects_invalid_interval(tmp_path) -> None:
    database = MvpDatabase(tmp_path / "ticket.db")
    await database.initialize()
    repository = TaskRepository(database)
    saved = await repository.save(task())

    with pytest.raises(ValueError, match="查询间隔"):
        await repository.update_interval(saved.task_id, 0)
