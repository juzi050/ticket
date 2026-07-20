from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.auth.session_bridge import AuthSessionBridge
from app.domain import AuthSession, PlatformName
from app.storage.audit_repository import AuditEntry, AuditRepository
from app.storage.session_repository import PlatformSessionRepository


PLATFORM_HOME_URLS: dict[PlatformName, str] = {
    "piaoniu": "https://www.piaoniu.com/",
    "motianlun": "https://m.motianlun.cn/",
}


@dataclass(slots=True)
class LoginOptions:
    browser_channel: str = "msedge"
    timeout_seconds: float = 600
    check_interval_seconds: float = 2
    headless: bool = False


class PlaywrightLoginService:
    """仅在用户主动登录时打开浏览器，并在 API 验证成功后立即关闭。"""

    def __init__(
        self,
        session_repository: PlatformSessionRepository,
        audit_repository: AuditRepository,
        data_dir: str | Path = "data",
        options: LoginOptions | None = None,
    ) -> None:
        self.sessions = session_repository
        self.audit = audit_repository
        self.data_dir = Path(data_dir)
        self.options = options or LoginOptions()
        self.bridge = AuthSessionBridge()
        self._locks: dict[PlatformName, asyncio.Lock] = {}

    async def login(
        self,
        platform: PlatformName,
        verify_session: Callable[[AuthSession], Awaitable[bool]],
        *,
        landing_url: str | None = None,
    ) -> AuthSession:
        lock = self._locks.setdefault(platform, asyncio.Lock())
        async with lock:
            await self.audit.append(
                AuditEntry(
                    level="INFO",
                    category="auth",
                    action="login_started",
                    platform=platform,
                    message="用户主动打开官方登录窗口",
                )
            )
            return await self._run_browser_login(
                platform, verify_session, landing_url=landing_url
            )

    async def _run_browser_login(
        self,
        platform: PlatformName,
        verify_session: Callable[[AuthSession], Awaitable[bool]],
        *,
        landing_url: str | None,
    ) -> AuthSession:
        from playwright.async_api import async_playwright

        profile_dir = self.data_dir / "browser_profiles" / platform
        profile_dir.mkdir(parents=True, exist_ok=True)
        playwright = await async_playwright().start()
        context: Any = None
        try:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir.resolve()),
                channel=self.options.browser_channel,
                headless=self.options.headless,
                locale="zh-CN",
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(
                landing_url or PLATFORM_HOME_URLS[platform],
                wait_until="domcontentloaded",
            )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.options.timeout_seconds
            last_error: Exception | None = None
            while loop.time() < deadline:
                if page.is_closed():
                    raise RuntimeError("登录窗口已被关闭")
                try:
                    session = await self.bridge.capture_from_browser(platform, context)
                    if await verify_session(session):
                        await self.sessions.save(session)
                        await self.audit.append(
                            AuditEntry(
                                level="INFO",
                                category="auth",
                                action="login_succeeded",
                                platform=platform,
                                message="官方 API 已确认登录有效，会话已保存到本地",
                            )
                        )
                        return session
                except Exception as exc:
                    last_error = exc
                await asyncio.sleep(self.options.check_interval_seconds)
            message = "等待人工登录超时"
            if last_error:
                message = f"{message}：{type(last_error).__name__}"
            raise TimeoutError(message)
        except Exception as exc:
            await self.audit.append(
                AuditEntry(
                    level="ERROR",
                    category="auth",
                    action="login_failed",
                    platform=platform,
                    message="官方登录未完成",
                    exception_type=type(exc).__name__,
                    exception_message=str(exc),
                )
            )
            raise
        finally:
            if context is not None:
                await context.close()
            await playwright.stop()

    async def clear_session(self, platform: PlatformName) -> None:
        await self.sessions.clear(platform)
        await self.audit.append(
            AuditEntry(
                level="INFO",
                category="auth",
                action="session_cleared",
                platform=platform,
                message="本地 HTTP 登录会话已清除",
            )
        )
