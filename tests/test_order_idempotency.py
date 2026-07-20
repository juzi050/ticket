from pathlib import Path

from app.database import Database
from app.models import LockStatus
from app.platforms.mock import MockPlatform
from app.services.order_service import OrderService


async def test_order_idempotency(sample_task: object, purchase_profile: object, tmp_path: Path) -> None:
    database = Database(tmp_path / "order.db")
    await database.initialize()
    service = OrderService(database, purchase_profiles=[purchase_profile])  # type: ignore[list-item]
    platform = MockPlatform("mock")
    platform.logged_in = True
    for _ in range(3):
        tickets = await platform.query_tickets(sample_task)  # type: ignore[arg-type]
    ticket = tickets[0]
    first = await service.lock(sample_task, ticket, platform)  # type: ignore[arg-type]
    second = await service.lock(sample_task, ticket, platform)  # type: ignore[arg-type]
    assert first.status is LockStatus.PAYMENT_PENDING
    assert second.status is LockStatus.ORDER_EXISTS
    history = await database.get_history("test_001")
    assert len(history["locks"]) == 1
