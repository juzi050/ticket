from __future__ import annotations

import asyncio
import logging

from app.config import MonitorTask, Settings
from app.database import Database
from app.platforms.base import TicketPlatform
from app.platforms.mock import MockPlatform
from app.platforms.motianlun import MotianlunPlatform
from app.platforms.piaoniu import PiaoniuPlatform
from app.services.monitor_service import MonitorService


class PlatformRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._platforms: dict[str, TicketPlatform] = {}

    def get(self, name: str) -> TicketPlatform:
        if name in self._platforms:
            return self._platforms[name]
        if self.settings.application.mock_mode or name == "mock":
            platform: TicketPlatform = MockPlatform(name)
        elif name == "piaoniu":
            platform = PiaoniuPlatform(self.settings.browser, self.settings.platforms.get(name))
        elif name == "motianlun":
            platform = MotianlunPlatform(self.settings.browser, self.settings.platforms.get(name))
        else:
            raise ValueError(f"不支持的平台：{name}")
        self._platforms[name] = platform
        return platform

    async def close(self) -> None:
        await asyncio.gather(*(platform.close() for platform in self._platforms.values()), return_exceptions=True)


class Scheduler:
    def __init__(
        self, settings: Settings, database: Database, registry: PlatformRegistry,
        monitor: MonitorService,
    ) -> None:
        self.settings = settings
        self.database = database
        self.registry = registry
        self.monitor = monitor
        self.logger = logging.getLogger("app.scheduler")

    async def run(self, task_id: str | None = None, *, max_cycles: int | None = None) -> None:
        selected = [task for task in self.settings.tasks if task_id is None or task.task_id == task_id]
        if task_id is not None and not selected:
            raise ValueError(f"任务不存在：{task_id}")
        for task in selected:
            await self.database.upsert_task(task, "pending" if task.enabled else "disabled")
            if max_cycles is not None:
                await self.database.set_task_enabled(task.task_id, True)

        initialized: dict[str, bool] = {}
        for platform_name in {task.platform for task in selected}:
            platform = self.registry.get(platform_name)
            try:
                await platform.initialize()
                initialized[platform_name] = True
            except Exception as exc:
                initialized[platform_name] = False
                self.logger.exception("平台 %s 初始化失败，不影响其他平台：%s", platform_name, exc)

        running: dict[str, asyncio.Task[None]] = {}
        finished: set[str] = set()
        try:
            while True:
                for task in selected:
                    if task.task_id in finished or not initialized.get(task.platform, False):
                        continue
                    control = await self.database.get_task_control(task.task_id)
                    if control and control[1] in {"completed", "adapter_unavailable"}:
                        finished.add(task.task_id)
                        continue
                    enabled = bool(control and control[0])
                    current = running.get(task.task_id)
                    if enabled and current is None:
                        running[task.task_id] = asyncio.create_task(
                            self.monitor.run_task(task, self.registry.get(task.platform), max_cycles=max_cycles),
                            name=f"monitor-{task.task_id}",
                        )
                    elif not enabled and current is not None:
                        current.cancel()

                for current_id, future in list(running.items()):
                    if not future.done():
                        continue
                    try:
                        await future
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        self.logger.exception("任务 %s 意外退出：%s", current_id, exc)
                    control = await self.database.get_task_control(current_id)
                    if control and control[1] in {"completed", "demo_finished", "adapter_unavailable"}:
                        finished.add(current_id)
                    running.pop(current_id, None)

                runnable_ids = {
                    task.task_id for task in selected
                    if initialized.get(task.platform, False)
                    and (await self.database.get_task_control(task.task_id) or (False, ""))[0]
                }
                if runnable_ids and runnable_ids.issubset(finished):
                    return
                if max_cycles is not None and not running and all(
                    task.task_id in finished or not initialized.get(task.platform, False) for task in selected
                ):
                    return
                if not running and not runnable_ids:
                    self.logger.info("没有启用的任务")
                    return
                await asyncio.sleep(0.25)
        finally:
            for future in running.values():
                future.cancel()
            await asyncio.gather(*running.values(), return_exceptions=True)
