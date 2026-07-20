from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from app.config import LoginSettings
from app.models import LoginState, LoginStatus, NotificationMessage
from app.platforms.base import TicketPlatform
from app.services.notification_service import NotificationService


class LoginService:
    def __init__(
        self,
        settings: LoginSettings,
        notifications: NotificationService,
        status_callback: Callable[[str, bool], Awaitable[None]] | None = None,
    ) -> None:
        self.settings = settings
        self.notifications = notifications
        self.status_callback = status_callback
        self._locks: dict[str, asyncio.Lock] = {}
        self.logger = logging.getLogger("app.login")

    def _lock_for(self, platform: str) -> asyncio.Lock:
        return self._locks.setdefault(platform, asyncio.Lock())

    async def status(self, platform: TicketPlatform) -> LoginStatus:
        logged_in = await platform.check_login_status()
        return LoginStatus(
            platform=platform.name,
            state=LoginState.LOGGED_IN if logged_in else LoginState.LOGGED_OUT,
            checked_at=datetime.now(timezone.utc),
            message="登录有效" if logged_in else "未登录或无法可靠确认登录状态",
        )

    async def ensure_logged_in(self, platform: TicketPlatform, *, notify: bool = True) -> bool:
        if await platform.check_login_status():
            return True
        if self.status_callback is not None:
            await self.status_callback(platform.name, False)
        async with self._lock_for(platform.name):
            async with platform.priority_operation():
                if await platform.check_login_status():
                    if self.status_callback is not None:
                        await self.status_callback(platform.name, True)
                    return True
                if notify:
                    self.notifications.dispatch(
                        NotificationMessage(
                            "login_required",
                            "登录状态失效",
                            f"平台：{platform.display_name}\n状态：未登录或登录状态已失效\n处理：准备打开官方登录入口，请在运行设备上人工完成验证",
                        )
                    )
                if not self.settings.auto_open_login_page:
                    return False
                print(
                    f"\n检测到{platform.display_name}账号尚未登录或登录状态已经失效。\n\n"
                    f"程序将打开{platform.display_name}官方登录入口。\n"
                    "请在浏览器中手动完成登录、短信验证、扫码或验证码操作。\n"
                    "程序会自动检测登录结果，请不要直接关闭浏览器窗口。\n"
                )
                await platform.open_login_page()
                loop = asyncio.get_running_loop()
                deadline = loop.time() + self.settings.timeout_seconds
                while loop.time() < deadline:
                    if await platform.check_login_status():
                        print(
                            f"\n{platform.display_name}账号登录成功。\n登录状态已经保存。\n"
                            f"正在恢复{platform.display_name}平台相关的监控任务。\n"
                        )
                        await platform.on_login_success()
                        if self.status_callback is not None:
                            await self.status_callback(platform.name, True)
                        return True
                    await asyncio.sleep(self.settings.check_interval_seconds)
                self.notifications.dispatch(
                    NotificationMessage(
                        "login_timeout",
                        "登录等待超时",
                        f"平台：{platform.display_name}\n状态：等待人工登录超时\n任务将保持等待，并在后续周期重新检查",
                    )
                )
                return False
