from __future__ import annotations

import asyncio
import logging

from app.config import NotificationSettings
from app.database import Database
from app.models import NotificationMessage
from app.notifier import Notifier


class NotificationService:
    def __init__(
        self, notifier: Notifier, database: Database, settings: NotificationSettings
    ) -> None:
        self.notifier = notifier
        self.database = database
        self.settings = settings
        self.logger = logging.getLogger("app.notification")
        self._pending: set[asyncio.Task[bool]] = set()

    def dispatch(self, message: NotificationMessage) -> asyncio.Task[bool]:
        """后台发送通知，避免阻塞查询和锁单调度。"""
        task = asyncio.create_task(self.send(message), name=f"notification-{message.message_type}")
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return task

    async def send(self, message: NotificationMessage, *, force: bool = False) -> bool:
        if not self.settings.enabled and not force:
            return False
        error: str | None = None
        for attempt in range(1, self.settings.max_retries + 1):
            try:
                success = await self.notifier.send(message)
                await self.database.record_notification(
                    message.message_type, self.notifier.provider, message.content, success, attempt - 1
                )
                return success
            except Exception as exc:
                error = str(exc)
                self.logger.warning("通知第 %s 次发送失败：%s", attempt, exc)
                if attempt < self.settings.max_retries:
                    await asyncio.sleep(self.settings.retry_interval_seconds * (2 ** (attempt - 1)))
        await self.database.record_notification(
            message.message_type, self.notifier.provider, message.content, False,
            self.settings.max_retries, error,
        )
        return False

    async def close(self) -> None:
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
        await self.notifier.close()
