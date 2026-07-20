from decimal import Decimal
from unittest.mock import AsyncMock, Mock

from app.domain import MonitorTask, TicketOption
from app.platforms.http_api import PlatformCapabilityUnavailable
from app.services.price_monitor_service import PriceMonitorService, evaluate_price
from app.storage.audit_repository import AuditQuery, AuditRepository
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
            unit_price=Decimal("300"),
            available_quantity=1,
        ),
        quantity=1,
        buyer_ids=["buyer-1"],
        ideal_price=Decimal("280"),
    )


def test_evaluate_price_uses_whole_order_total() -> None:
    task = make_task()
    ticket = task.ticket.model_copy(
        update={"unit_price": Decimal("270"), "known_fee": Decimal("9")}
    )
    matched = evaluate_price(task, ticket)
    assert matched.matched
    assert matched.estimated_total == Decimal("279")

    expensive = evaluate_price(
        task, ticket.model_copy(update={"known_fee": Decimal("11")})
    )
    assert not expensive.matched
    assert expensive.estimated_total == Decimal("281")


def test_evaluate_price_never_substitutes_missing_or_short_ticket() -> None:
    task = make_task()
    assert evaluate_price(task, None).status == "ticket_unavailable"
    short = task.ticket.model_copy(update={"available_quantity": 0})
    assert evaluate_price(task, short).status == "quantity_insufficient"


async def test_order_capability_failure_pauses_task_with_explicit_status(
    tmp_path,
) -> None:
    database = MvpDatabase(tmp_path / "ticket.db")
    await database.initialize()
    tasks = TaskRepository(database)
    audit = AuditRepository(database)
    task = make_task().model_copy(update={"ideal_price": Decimal("400")})
    saved = await tasks.save(task)
    api = Mock(get_exact_ticket=AsyncMock(return_value=saved.ticket))
    callback = AsyncMock(
        side_effect=PlatformCapabilityUnavailable("票牛预下单接口不可用")
    )

    service = PriceMonitorService(
        {"piaoniu": api}, tasks, audit, matched_callback=callback
    )
    await service.check_once(saved)

    current = await tasks.get(saved.task_id)
    assert current is not None
    assert current.enabled is False
    assert current.status == "order_unavailable"
    assert current.last_error == "票牛预下单接口不可用"
    entries = await audit.query(AuditQuery(task_id=saved.task_id))
    assert entries[0].action == "order_unavailable"
