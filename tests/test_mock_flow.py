from pathlib import Path

from app.config import (
    ApplicationSettings,
    LoginSettings,
    MonitorSettings,
    NotificationSettings,
    Settings,
)
from app.database import Database
from app.notifier import ConsoleNotifier
from app.scheduler import PlatformRegistry, Scheduler
from app.services.login_service import LoginService
from app.services.monitor_service import MonitorService
from app.services.notification_service import NotificationService
from app.services.order_service import OrderService


async def test_mock_complete_flow(sample_task: object, purchase_profile: object, tmp_path: Path) -> None:
    sample_task.interval_seconds = 0.01  # type: ignore[attr-defined]
    sample_task.random_delay_min_seconds = 0  # type: ignore[attr-defined]
    sample_task.random_delay_max_seconds = 0  # type: ignore[attr-defined]
    settings = Settings(
        application=ApplicationSettings(database_path=tmp_path / "mock.db", mock_mode=True),
        login=LoginSettings(timeout_seconds=1, check_interval_seconds=0.1, retry_interval_seconds=1),
        notification=NotificationSettings(enabled=True, provider="console", retry_interval_seconds=0),
        monitor=MonitorSettings(
            default_interval_seconds=1,
            random_delay_min_seconds=0,
            random_delay_max_seconds=0,
        ),
        tasks=[sample_task],  # type: ignore[list-item]
        purchase_profiles=[purchase_profile],  # type: ignore[list-item]
    )
    database = Database(settings.application.database_path)
    await database.initialize()
    notifications = NotificationService(ConsoleNotifier(), database, settings.notification)
    login = LoginService(settings.login, notifications)
    registry = PlatformRegistry(settings)
    monitor = MonitorService(
        database, login, OrderService(database, purchase_profiles=settings.purchase_profiles),
        notifications, settings.monitor,
    )
    scheduler = Scheduler(settings, database, registry, monitor)
    try:
        await scheduler.run(max_cycles=4)
    finally:
        await registry.close()
    history = await database.get_history("test_001")
    state = await database.get_task_control("test_001")
    assert len(history["prices"]) >= 3
    assert len(history["matches"]) == 1
    assert history["locks"][0]["status"] == "payment_pending"
    stages = {row["stage"] for row in history["stages"]}
    assert {
        "PREFLIGHT",
        "WATCHING",
        "MATCHED",
        "REVALIDATING",
        "SELECTING_QUANTITY",
        "SELECTING_AUDIENCE",
        "SELECTING_CONTACT",
        "VERIFYING_FINAL_PRICE",
        "READY_TO_SUBMIT",
        "SUBMITTING",
        "PAYMENT_PENDING",
    }.issubset(stages)
    assert state == (True, "completed")
