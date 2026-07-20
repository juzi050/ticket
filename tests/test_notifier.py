from pathlib import Path

from app.config import NotificationSettings
from app.database import Database
from app.models import NotificationMessage
from app.notifier import Notifier
from app.services.notification_service import NotificationService


class FlakyNotifier(Notifier):
    provider = "test"

    def __init__(self) -> None:
        self.calls = 0

    async def send(self, message: NotificationMessage) -> bool:
        self.calls += 1
        if self.calls < 3:
            raise RuntimeError("temporary")
        return True


async def test_notification_retries_and_records(tmp_path: Path) -> None:
    database = Database(tmp_path / "notify.db")
    await database.initialize()
    notifier = FlakyNotifier()
    service = NotificationService(
        notifier,
        database,
        NotificationSettings(enabled=True, provider="console", max_retries=3, retry_interval_seconds=0),
    )
    assert await service.send(NotificationMessage("test", "title", "content"))
    assert notifier.calls == 3
