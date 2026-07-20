from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any

from app.config import BrowserSettings, PlatformAutomationSettings
from app.exceptions import PlatformError


_RAW_PERSONAL_DATA_PATTERNS = (
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
)


def _contains_raw_personal_data(value: object) -> bool:
    text = str(value or "")
    return any(pattern.search(text) for pattern in _RAW_PERSONAL_DATA_PATTERNS)


def sanitize_storage_state(state: dict[str, Any]) -> dict[str, Any]:
    """移除含完整手机号或身份证号的 Cookie/LocalStorage 项。"""

    sanitized = dict(state)
    sanitized["cookies"] = [
        dict(cookie)
        for cookie in state.get("cookies", [])
        if not _contains_raw_personal_data(cookie.get("value"))
    ]
    origins: list[dict[str, Any]] = []
    for origin_state in state.get("origins", []):
        clean_origin = dict(origin_state)
        clean_origin["localStorage"] = [
            dict(item)
            for item in origin_state.get("localStorage", [])
            if not _contains_raw_personal_data(item.get("value"))
        ]
        origins.append(clean_origin)
    sanitized["origins"] = origins
    return sanitized


class BrowserSessionService:
    """每个平台复用一个持久化浏览器上下文。"""

    def __init__(
        self,
        platform: str,
        browser_settings: BrowserSettings,
        automation: PlatformAutomationSettings,
        data_dir: str | Path = "data",
    ) -> None:
        self.platform = platform
        self.settings = browser_settings
        self.automation = automation
        self.data_dir = Path(data_dir)
        self.profile_dir = self.data_dir / "browser_profiles" / platform
        self.state_file = self.data_dir / "browser_states" / f"{platform}_state.json"
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None
        self.logger = logging.getLogger(f"app.browser.{platform}")

    async def initialize(self) -> None:
        if self._context is not None:
            return
        try:
            from playwright.async_api import async_playwright

            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self._playwright = await async_playwright().start()
            launch_options: dict[str, Any] = {"headless": self.settings.headless}
            if self.settings.executable_path:
                launch_options["executable_path"] = self.settings.executable_path
            elif self.settings.channel:
                launch_options["channel"] = self.settings.channel
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir.resolve()), **launch_options
            )
            self._context.set_default_timeout(self.settings.page_timeout_seconds * 1000)
            self._context.set_default_navigation_timeout(self.settings.page_timeout_seconds * 1000)
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            await self._restore_state()
        except Exception as exc:
            await self.close()
            raise PlatformError(f"{self.platform} 浏览器启动失败：{exc}") from exc

    async def page(self) -> Any:
        try:
            await self.initialize()
            if self._page is None or self._page.is_closed():
                self._page = await self._context.new_page()
            return self._page
        except Exception:
            self.logger.warning("浏览器上下文不可用，正在重新创建", exc_info=True)
            await self.close()
            await self.initialize()
            return self._page

    async def check_login_status(self) -> bool:
        page = await self.page()
        target = self.automation.auth_check_url or self.automation.home_url
        try:
            await page.goto(target, wait_until="domcontentloaded")
            for selector in self.automation.unauthenticated_selectors:
                locator = page.locator(selector).first
                if await locator.count() and await locator.is_visible():
                    return False
            for selector in self.automation.authenticated_selectors:
                locator = page.locator(selector).first
                if await locator.count() and await locator.is_visible():
                    await self.save_state()
                    return True
            self.logger.warning("未配置或未命中可靠的登录态选择器，保守判定为未登录")
            return False
        except Exception as exc:
            self.logger.warning("登录状态检查失败：%s", exc)
            return False

    async def open_login_page(self) -> None:
        page = await self.page()
        await page.bring_to_front()
        await page.goto(self.automation.login_url or self.automation.home_url, wait_until="domcontentloaded")
        if self.automation.login_url or not self.automation.login_trigger_text:
            return
        trigger = page.get_by_text(self.automation.login_trigger_text, exact=True).first
        if await trigger.count() and await trigger.is_visible():
            await trigger.click()

    async def save_state(self) -> None:
        if self._context is not None:
            state = await self._context.storage_state()
            sanitized = sanitize_storage_state(state)
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(
                json.dumps(sanitized, ensure_ascii=False), encoding="utf-8"
            )

    async def _restore_state(self) -> None:
        """将 Storage State 恢复到持久化上下文，兼容仅靠资料目录无法恢复的站点。"""
        if self._context is None or self._page is None or not self.state_file.exists():
            return
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            sanitized = sanitize_storage_state(state)
            if sanitized != state:
                self.state_file.write_text(
                    json.dumps(sanitized, ensure_ascii=False), encoding="utf-8"
                )
                state = sanitized
            cookies = state.get("cookies", [])
            if cookies:
                await self._context.add_cookies(cookies)
            for origin_state in state.get("origins", []):
                origin = origin_state.get("origin")
                entries = origin_state.get("localStorage", [])
                if not origin or not entries:
                    continue
                await self._page.goto(origin, wait_until="domcontentloaded")
                await self._page.evaluate(
                    "entries => entries.forEach(item => localStorage.setItem(item.name, item.value))",
                    entries,
                )
                await self._page.reload(wait_until="domcontentloaded")
        except Exception as exc:
            self.logger.warning("已保存的登录状态恢复失败，将使用浏览器资料目录继续：%s", exc)

    async def close(self) -> None:
        if self._context is not None:
            try:
                await self._context.close()
            finally:
                self._context = None
                self._page = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
