from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from datetime import timedelta

from app.domain import MonitorTask, utc_now
from app.storage.audit_repository import AuditEntry, AuditRepository
from app.storage.task_repository import TaskRepository


CheckCallback = Callable[[MonitorTask], Awaitable[None]]


class MonitorScheduler:
    def __init__(
        self,
        task_repository: TaskRepository,
        audit_repository: AuditRepository,
        check_callback: CheckCallback,
    ) -> None:
        self.tasks_repository = task_repository
        self.audit = audit_repository
        self.check_callback = check_callback
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._wakeups: dict[str, asyncio.Event] = {}
        self._check_locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        for task in await self.tasks_repository.list():
            if task.enabled:
                await self.resume(task.task_id)

    async def resume(self, task_id: str) -> None:
        existing = self._workers.get(task_id)
        if existing and not existing.done():
            self._wakeups.setdefault(task_id, asyncio.Event()).set()
            return
        self._wakeups[task_id] = asyncio.Event()
        self._workers[task_id] = asyncio.create_task(
            self._run_task(task_id), name=f"monitor:{task_id}"
        )

    async def pause(self, task_id: str) -> None:
        wakeup = self._wakeups.get(task_id)
        if wakeup:
            wakeup.set()
        worker = self._workers.get(task_id)
        if worker and not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        await self.tasks_repository.set_next_check_at(task_id, None)

    async def immediate_check(self, task_id: str) -> None:
        task = await self.tasks_repository.get(task_id)
        if task is None:
            raise ValueError("监控任务不存在")
        await self._check_once_locked(task)

    async def stop(self) -> None:
        workers = [worker for worker in self._workers.values() if not worker.done()]
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._workers.clear()
        self._wakeups.clear()

    async def _run_task(self, task_id: str) -> None:
        wakeup = self._wakeups[task_id]
        error_count = 0
        while True:
            task = await self.tasks_repository.get(task_id)
            if task is None or not task.enabled:
                return
            try:
                await self._check_once_locked(task)
                error_count = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error_count += 1
                await self.audit.append(
                    AuditEntry(
                        level="ERROR",
                        category="monitor",
                        action="scheduled_check_failed",
                        platform=task.ticket.platform,
                        task_id=task_id,
                        message="定时价格查询失败",
                        exception_type=type(exc).__name__,
                        exception_message=str(exc),
                    )
                )

            current = await self.tasks_repository.get(task_id)
            if current is None or not current.enabled:
                return
            interval = current.query_interval_seconds
            jitter = random.uniform(0, min(2, interval * 0.1))
            backoff = min(60, interval * (2 ** min(error_count, 4))) if error_count else interval
            delay = backoff + jitter
            await self.tasks_repository.set_next_check_at(
                task_id, utc_now() + timedelta(seconds=delay)
            )
            wakeup.clear()
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def _check_once_locked(self, task: MonitorTask) -> None:
        lock = self._check_locks.setdefault(task.task_id, asyncio.Lock())
        async with lock:
            current = await self.tasks_repository.get(task.task_id)
            if current is None:
                return
            await self.check_callback(current)
