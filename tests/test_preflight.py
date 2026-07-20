from __future__ import annotations

from pathlib import Path

from app.config import (
    ApplicationSettings,
    LoginSettings,
    MonitorSettings,
    PurchaseProfile,
    Settings,
)
from app.database import Database
from app.models import LockOrderRequest, LockOrderResult, LockStatus, NotificationMessage
from app.notifier import Notifier
from app.platforms.mock import MockPlatform
from app.scheduler import PlatformRegistry, Scheduler
from app.services.login_service import LoginService
from app.services.monitor_service import MonitorService
from app.services.notification_service import NotificationService
from app.services.order_service import OrderService
from app.services.preflight_service import PreflightService


class SilentNotifier(Notifier):
    provider = "silent"

    async def send(self, message: NotificationMessage) -> bool:
        return True


def strict_task(sample_task: object):
    return sample_task.model_copy(  # type: ignore[attr-defined]
        update={
            "target_session_id": "mock-session-1",
            "target_listing_id": "mock-listing-test_001",
            "target_ticket_group_id": "mock-group-test_001",
        }
    )


async def run_preflight(task: object, profile: PurchaseProfile, path: Path):
    seed = task.model_copy(  # type: ignore[attr-defined]
        update={
            "platform_audience_ids": [
                f"mock-audience-{index + 1}" for index in range(task.quantity)  # type: ignore[attr-defined]
            ]
        }
    )
    settings = Settings(
        application=ApplicationSettings(database_path=path),
        purchase_profiles=[profile],
        tasks=[seed],
    )
    settings.tasks = [task]  # type: ignore[list-item]
    database = Database(path)
    await database.initialize()
    notifications = NotificationService(SilentNotifier(), database, settings.notification)
    platform = MockPlatform("mock")
    platform.logged_in = True
    result = await PreflightService(settings, database, notifications).run(task, platform)  # type: ignore[arg-type]
    return result, database, platform


async def test_audience_count_mismatch_fails_preflight(
    sample_task: object, purchase_profile: PurchaseProfile, tmp_path: Path
) -> None:
    task = strict_task(sample_task)
    task.platform_audience_ids = task.platform_audience_ids[:1]
    result, _, _ = await run_preflight(task, purchase_profile, tmp_path / "audience.db")
    checks = {check.name: check.passed for check in result.checks}
    assert not checks["购票人数等于购买数量"]
    assert not result.passed


async def test_deleted_platform_audience_fails_preflight(
    sample_task: object, purchase_profile: PurchaseProfile, tmp_path: Path
) -> None:
    task = strict_task(sample_task).model_copy(
        update={"platform_audience_ids": ["mock-audience-1", "deleted-audience"]}
    )
    result, _, _ = await run_preflight(task, purchase_profile, tmp_path / "deleted.db")
    checks = {check.name: check.passed for check in result.checks}
    assert not checks["平台购票人引用仍然有效"]


async def test_pending_order_blocks_preflight(
    sample_task: object, purchase_profile: PurchaseProfile, tmp_path: Path
) -> None:
    task = strict_task(sample_task)
    result, database, platform = await run_preflight(task, purchase_profile, tmp_path / "pending.db")
    assert result.ticket is not None
    request = LockOrderRequest(
        task_id=task.task_id,
        ticket=result.ticket,
        quantity=task.quantity,
        max_unit_price=task.max_unit_price,
        max_total_price=task.max_total_price,
        idempotency_key="pending-key",
        account_alias="mock:default",
    )
    assert await database.claim_lock(request, 1, 0)
    await database.complete_lock(
        request.idempotency_key,
        LockOrderResult(LockStatus.PAYMENT_PENDING, "待支付", final_total=result.ticket.payable_total),
    )
    blocked = await PreflightService(
        Settings(
            application=ApplicationSettings(database_path=database.path),
            purchase_profiles=[purchase_profile],
            tasks=[task],
        ),
        database,
        NotificationService(SilentNotifier(), database, Settings(tasks=[task]).notification),
    ).run(task, platform)
    checks = {check.name: check.passed for check in blocked.checks}
    assert not checks["没有相同待支付订单"]


async def test_scheduler_does_not_start_auto_lock_when_preflight_fails(
    sample_task: object, purchase_profile: PurchaseProfile, tmp_path: Path
) -> None:
    task = strict_task(sample_task)
    task = task.model_copy(
        update={"platform_audience_ids": ["mock-audience-1", "deleted-audience"]}
    )
    settings = Settings(
        application=ApplicationSettings(database_path=tmp_path / "blocked.db", mock_mode=True),
        login=LoginSettings(timeout_seconds=1, check_interval_seconds=0.1),
        monitor=MonitorSettings(random_delay_min_seconds=0, random_delay_max_seconds=0),
        purchase_profiles=[purchase_profile],
        tasks=[task],
    )
    database = Database(settings.application.database_path)
    await database.initialize()
    notifications = NotificationService(SilentNotifier(), database, settings.notification)
    login = LoginService(settings.login, notifications)
    registry = PlatformRegistry(settings)
    order = OrderService(database, 0, settings.purchase_profiles)
    monitor = MonitorService(database, login, order, notifications, settings.monitor)
    preflight = PreflightService(settings, database, notifications)
    scheduler = Scheduler(settings, database, registry, monitor, preflight)
    try:
        await scheduler.run(max_cycles=1)
    finally:
        await registry.close()
        await notifications.close()
    assert await database.get_task_control(task.task_id) == (True, "preflight_failed")
    history = await database.get_history(task.task_id)
    assert not history["prices"]
