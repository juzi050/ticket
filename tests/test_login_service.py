import asyncio
from pathlib import Path
from typing import Any

from app.config import LoginSettings, NotificationSettings
from app.database import Database
from app.models import LockOrderRequest, LockOrderResult, MatchResult, NotificationMessage, TicketInfo
from app.notifier import Notifier
from app.platforms.base import TicketPlatform
from app.services.login_service import LoginService
from app.services.notification_service import NotificationService


class SilentNotifier(Notifier):
    provider = "silent"

    async def send(self, message: NotificationMessage) -> bool:
        return True


class LoginPlatform(TicketPlatform):
    name = "mock"
    display_name = "测试平台"

    def __init__(self) -> None:
        self.logged_in = False
        self.open_count = 0

    async def initialize(self) -> None:
        return None

    async def check_login_status(self) -> bool:
        await asyncio.sleep(0)
        return self.logged_in

    async def open_login_page(self) -> None:
        self.open_count += 1
        await asyncio.sleep(0.01)
        self.logged_in = True

    async def search_event(self, task: Any) -> Any:
        return None

    async def query_tickets(self, task: Any) -> list[TicketInfo]:
        return []

    async def match_ticket(self, task: Any, tickets: Any) -> MatchResult:
        return MatchResult(False)

    async def lock_order(self, task: Any, request: LockOrderRequest) -> LockOrderResult:
        raise NotImplementedError

    async def close(self) -> None:
        return None


async def test_same_platform_only_opens_one_login_window(tmp_path: Path) -> None:
    database = Database(tmp_path / "login.db")
    await database.initialize()
    notifications = NotificationService(
        SilentNotifier(), database,
        NotificationSettings(enabled=True, provider="console", retry_interval_seconds=0),
    )
    service = LoginService(
        LoginSettings(timeout_seconds=1, check_interval_seconds=0.1, retry_interval_seconds=1),
        notifications,
    )
    platform = LoginPlatform()
    results = await asyncio.gather(*(service.ensure_logged_in(platform, notify=False) for _ in range(5)))
    assert all(results)
    assert platform.open_count == 1
