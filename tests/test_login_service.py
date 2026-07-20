import asyncio
import json
from pathlib import Path
from typing import Any

from app.config import (
    BrowserSettings,
    LoginSettings,
    NotificationSettings,
    PlatformAutomationSettings,
)
from app.database import Database
from app.models import LockOrderRequest, LockOrderResult, MatchResult, NotificationMessage, TicketInfo
from app.notifier import Notifier
from app.platforms.base import TicketPlatform
from app.services.login_service import LoginService
from app.services.notification_service import NotificationService
from app.services.session_service import BrowserSessionService, sanitize_storage_state


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


class StateContext:
    def __init__(self) -> None:
        self.cookies: list[dict[str, Any]] = []

    async def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
        self.cookies = cookies


class StatePage:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.entries: list[dict[str, str]] = []
        self.reloads = 0

    async def goto(self, url: str, **_: Any) -> None:
        self.urls.append(url)

    async def evaluate(self, _: str, entries: list[dict[str, str]]) -> None:
        self.entries = entries

    async def reload(self, **_: Any) -> None:
        self.reloads += 1


def test_storage_state_drops_raw_phone_and_identity() -> None:
    state = {
        "cookies": [
            {"name": "session", "value": "safe-token"},
            {"name": "profile", "value": "mobile=13900001234"},
        ],
        "origins": [
            {
                "origin": "https://example.com",
                "localStorage": [
                    {"name": "auth", "value": "safe"},
                    {"name": "audience", "value": "110101199001011234"},
                ],
            }
        ],
    }

    sanitized = sanitize_storage_state(state)

    assert [item["name"] for item in sanitized["cookies"]] == ["session"]
    assert [
        item["name"] for item in sanitized["origins"][0]["localStorage"]
    ] == ["auth"]


async def test_browser_session_restores_storage_state(tmp_path: Path) -> None:
    automation = PlatformAutomationSettings(home_url="https://example.com/")
    service = BrowserSessionService("test", BrowserSettings(), automation, data_dir=tmp_path)
    service.state_file.parent.mkdir(parents=True, exist_ok=True)
    service.state_file.write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "session", "value": "private", "domain": "example.com", "path": "/"},
                    {"name": "profile", "value": "mobile=13900001234", "domain": "example.com", "path": "/"},
                ],
                "origins": [
                    {
                        "origin": "https://example.com",
                        "localStorage": [
                            {"name": "logged-in", "value": "yes"},
                            {"name": "audience", "value": "110101199001011234"},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    context = StateContext()
    page = StatePage()
    service._context = context
    service._page = page

    await service._restore_state()

    assert len(context.cookies) == 1
    assert page.urls == ["https://example.com"]
    assert page.entries == [{"name": "logged-in", "value": "yes"}]
    assert page.reloads == 1
    persisted = service.state_file.read_text(encoding="utf-8")
    assert "13900001234" not in persisted
    assert "110101199001011234" not in persisted
