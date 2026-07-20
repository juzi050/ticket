from __future__ import annotations

from pathlib import Path

from app.database import Database
from app.models import LockOrderRequest, LockOrderResult, LockStatus, MatchResult
from app.platforms.mock import MockPlatform
from app.services.order_service import OrderService


class CountMismatchMock(MockPlatform):
    def __init__(self) -> None:
        super().__init__("mock")
        self.logged_in = True
        self.lock_called = False

    async def revalidate_ticket(self, task: object, ticket: object) -> MatchResult:
        current = self._ticket(task, good=True)  # type: ignore[arg-type]
        current.raw["selected_quantity"] = task.quantity - 1  # type: ignore[attr-defined]
        return MatchResult(True, ticket=current)

    async def lock_order(self, task: object, request: LockOrderRequest) -> LockOrderResult:
        self.lock_called = True
        return LockOrderResult(LockStatus.PAYMENT_PENDING, "不应调用")


async def test_retryable_error_can_retry_after_cooldown(
    sample_task: object, tmp_path: Path
) -> None:
    database = Database(tmp_path / "retry.db")
    await database.initialize()
    platform = MockPlatform("mock")
    ticket = platform._ticket(sample_task, good=True)  # type: ignore[arg-type]
    request = LockOrderRequest(
        task_id=sample_task.task_id,  # type: ignore[attr-defined]
        ticket=ticket,
        quantity=sample_task.quantity,  # type: ignore[attr-defined]
        max_unit_price=sample_task.max_unit_price,  # type: ignore[attr-defined]
        max_total_price=sample_task.max_total_price,  # type: ignore[attr-defined]
        idempotency_key="retry-key",
        account_alias="test-account",
    )
    assert await database.claim_lock(request, max_attempts=1, cooldown_seconds=0)
    await database.complete_lock(request.idempotency_key, LockOrderResult(LockStatus.TIMEOUT, "超时"))
    assert await database.claim_lock(request, max_attempts=1, cooldown_seconds=0)


async def test_payment_pending_permanently_blocks_retry(
    sample_task: object, tmp_path: Path
) -> None:
    database = Database(tmp_path / "pending-retry.db")
    await database.initialize()
    platform = MockPlatform("mock")
    ticket = platform._ticket(sample_task, good=True)  # type: ignore[arg-type]
    request = LockOrderRequest(
        task_id=sample_task.task_id,  # type: ignore[attr-defined]
        ticket=ticket,
        quantity=sample_task.quantity,  # type: ignore[attr-defined]
        max_unit_price=sample_task.max_unit_price,  # type: ignore[attr-defined]
        max_total_price=sample_task.max_total_price,  # type: ignore[attr-defined]
        idempotency_key="pending-key",
        account_alias="test-account",
    )
    assert await database.claim_lock(request, 1, 0)
    await database.complete_lock(
        request.idempotency_key,
        LockOrderResult(LockStatus.PAYMENT_PENDING, "待支付", final_total=ticket.payable_total),
    )
    assert not await database.claim_lock(request, 99, 0)


async def test_quantity_mismatch_stops_before_lock(
    sample_task: object, purchase_profile: object, tmp_path: Path
) -> None:
    database = Database(tmp_path / "quantity-mismatch.db")
    await database.initialize()
    platform = CountMismatchMock()
    ticket = platform._ticket(sample_task, good=True)  # type: ignore[arg-type]
    service = OrderService(database, purchase_profiles=[purchase_profile])  # type: ignore[list-item]
    result = await service.lock(sample_task, ticket, platform)  # type: ignore[arg-type]
    assert result.status is LockStatus.QUANTITY_INSUFFICIENT
    assert not platform.lock_called


def test_idempotency_key_contains_account_listing_and_quantity(sample_task: object) -> None:
    platform = MockPlatform("mock")
    ticket = platform._ticket(sample_task, good=True)  # type: ignore[arg-type]
    original = OrderService.idempotency_key(sample_task, ticket, "account-a")  # type: ignore[arg-type]
    other_account = OrderService.idempotency_key(sample_task, ticket, "account-b")  # type: ignore[arg-type]
    replacement = platform._ticket(sample_task, good=True)  # type: ignore[arg-type]
    replacement.listing_id = "other-listing"
    other_listing = OrderService.idempotency_key(sample_task, replacement, "account-a")  # type: ignore[arg-type]
    other_quantity_task = sample_task.model_copy(update={"quantity": 1})  # type: ignore[attr-defined]
    other_quantity = OrderService.idempotency_key(other_quantity_task, ticket, "account-a")
    assert len({original, other_account, other_listing, other_quantity}) == 4
