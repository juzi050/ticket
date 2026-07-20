from __future__ import annotations

from typing import Any

from app.config import BrowserSettings, PlatformAutomationSettings
from app.platforms.piaoniu import PiaoniuPlatform


class FakeLocator:
    def __init__(self, *, count: int = 1, visible: bool = True) -> None:
        self._count = count
        self._visible = visible
        self.clicks = 0
        self.waits = 0

    @property
    def first(self) -> "FakeLocator":
        return self

    async def count(self) -> int:
        return self._count

    async def is_visible(self) -> bool:
        return self._visible

    async def click(self) -> None:
        self.clicks += 1

    async def wait_for(self, **_: Any) -> None:
        self.waits += 1


class FakePage:
    def __init__(self) -> None:
        self.url = "https://www.piaoniu.com/"
        self.gotos = 0
        self.brought_to_front = 0
        self.user_entry = FakeLocator(count=0, visible=False)
        self.login_entry = FakeLocator()
        self.login_modal = FakeLocator()

    def locator(self, selector: str) -> FakeLocator:
        return {
            ".right-funcs .item-user:visible": self.user_entry,
            ".right-funcs .item-login:visible": self.login_entry,
            ".light-login:visible": self.login_modal,
        }[selector]

    async def goto(self, url: str, **_: Any) -> None:
        self.url = url
        self.gotos += 1

    async def bring_to_front(self) -> None:
        self.brought_to_front += 1


class FakeSession:
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.saved = 0
        self.automation = PlatformAutomationSettings(home_url="https://www.piaoniu.com/")

    async def page(self) -> FakePage:
        return self._page

    async def save_state(self) -> None:
        self.saved += 1


async def test_piaoniu_login_poll_does_not_refresh_open_login_page() -> None:
    platform = PiaoniuPlatform(BrowserSettings())
    page = FakePage()
    session = FakeSession(page)
    platform.session = session  # type: ignore[assignment]

    assert await platform.check_login_status() is False
    assert page.gotos == 0

    page.user_entry = FakeLocator()
    page.login_entry = FakeLocator(count=0, visible=False)
    assert await platform.check_login_status() is True
    assert session.saved == 1
    assert page.gotos == 0


async def test_piaoniu_open_login_uses_header_button_and_waits_for_modal() -> None:
    platform = PiaoniuPlatform(BrowserSettings())
    page = FakePage()
    platform.session = FakeSession(page)  # type: ignore[assignment]

    await platform.open_login_page()

    assert page.gotos == 1
    assert page.brought_to_front == 1
    assert page.login_entry.clicks == 1
    assert page.login_modal.waits == 1
