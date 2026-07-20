import asyncio
from decimal import Decimal

from app.domain import MonitorTask, TicketOption
from app.monitor_scheduler import MonitorScheduler
from app.storage.audit_repository import AuditRepository
from app.storage.database import MvpDatabase
from app.storage.task_repository import TaskRepository


def make_task() -> MonitorTask:
    return MonitorTask(
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
        buyer_ids=["buyer-1"],
        ideal_price=Decimal("280"),
        query_interval_seconds=30,
    )


async def test_immediate_checks_never_overlap(tmp_path) -> None:
    database = MvpDatabase(tmp_path / "ticket.db")
    await database.initialize()
    tasks = TaskRepository(database)
    saved = await tasks.save(make_task())
    active = 0
    maximum = 0

    async def check(_task: MonitorTask) -> None:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.02)
        active -= 1

    scheduler = MonitorScheduler(tasks, AuditRepository(database), check)
    await asyncio.gather(
        scheduler.immediate_check(saved.task_id),
        scheduler.immediate_check(saved.task_id),
    )
    assert maximum == 1


async def test_pause_interrupts_worker_and_clears_next_check(tmp_path) -> None:
    database = MvpDatabase(tmp_path / "ticket.db")
    await database.initialize()
    tasks = TaskRepository(database)
    saved = await tasks.save(make_task())
    checked = asyncio.Event()

    async def check(_task: MonitorTask) -> None:
        checked.set()

    scheduler = MonitorScheduler(tasks, AuditRepository(database), check)
    await scheduler.resume(saved.task_id)
    await asyncio.wait_for(checked.wait(), timeout=1)
    await scheduler.pause(saved.task_id)
    current = await tasks.get(saved.task_id)
    assert current is not None and current.next_check_at is None
    await scheduler.stop()
