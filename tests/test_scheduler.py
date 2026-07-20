from pathlib import Path

from app.config import (
    ApplicationSettings,
    LoginSettings,
    MonitorSettings,
    NotificationSettings,
    Settings,
)
from app.database import Database
from app.models import NotificationMessage
from app.notifier import Notifier
from app.platforms.mock import MockPlatform
from app.scheduler import PlatformRegistry, Scheduler
from app.services.login_service import LoginService
from app.services.monitor_service import MonitorService
from app.services.notification_service import NotificationService
from app.services.order_service import OrderService


class SilentNotifier(Notifier):
    provider = "silent"

    async def send(self, message: NotificationMessage) -> bool:
        return True


class OnceFailingMock(MockPlatform):
    def __init__(self) -> None:
        super().__init__("mock")
        self.failed = False

    async def query_tickets(self, task: object):  # type: ignore[no-untyped-def]
        if getattr(task, "task_id") == "bad" and not self.failed:
            self.failed = True
            raise RuntimeError("模拟单任务查询异常")
        return await super().query_tickets(task)  # type: ignore[arg-type]


async def test_multi_task_and_error_isolation(sample_task: object, tmp_path: Path) -> None:
    good = sample_task.model_copy(update={"task_id": "good"})  # type: ignore[attr-defined]
    bad = sample_task.model_copy(update={"task_id": "bad"})  # type: ignore[attr-defined]
    for task in (good, bad):
        task.interval_seconds = 0.01
        task.random_delay_min_seconds = 0
        task.random_delay_max_seconds = 0
    settings = Settings(
        application=ApplicationSettings(database_path=tmp_path / "scheduler.db", mock_mode=True),
        login=LoginSettings(timeout_seconds=1, check_interval_seconds=0.1, retry_interval_seconds=1),
        notification=NotificationSettings(enabled=True, provider="console", retry_interval_seconds=0),
        monitor=MonitorSettings(random_delay_min_seconds=0, random_delay_max_seconds=0),
        tasks=[good, bad],
    )
    database = Database(settings.application.database_path)
    await database.initialize()
    notifications = NotificationService(SilentNotifier(), database, settings.notification)
    login = LoginService(settings.login, notifications)
    registry = PlatformRegistry(settings)
    registry._platforms["mock"] = OnceFailingMock()
    monitor = MonitorService(database, login, OrderService(database, 0), notifications, settings.monitor)
    scheduler = Scheduler(settings, database, registry, monitor)
    try:
        await scheduler.run(max_cycles=4)
    finally:
        await registry.close()
        await notifications.close()
    assert (await database.get_task_control("good")) == (True, "completed")
    assert (await database.get_task_control("bad")) == (True, "completed")
