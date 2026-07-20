from __future__ import annotations

from uuid import uuid4

from app.config import MonitorTask
from app.database import Database


class TaskStore:
    """GUI 和运行时使用的任务唯一数据源。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def list(self) -> list[MonitorTask]:
        return await self.database.load_tasks()

    async def get(self, task_id: str) -> MonitorTask | None:
        return await self.database.get_task(task_id)

    async def save(self, task: MonitorTask) -> None:
        await self.database.upsert_task(task, "pending" if task.enabled else "paused")

    async def duplicate(self, task_id: str) -> MonitorTask:
        source = await self.get(task_id)
        if source is None:
            raise ValueError(f"任务不存在：{task_id}")
        copy = source.model_copy(
            update={
                "task_id": f"{source.platform}_{uuid4().hex[:8]}",
                "task_name": f"{source.task_name} - 副本",
                "enabled": False,
                "auto_lock": False,
            }
        )
        await self.save(copy)
        return copy

    async def delete(self, task_id: str) -> bool:
        return await self.database.delete_task(task_id)

    async def set_enabled(self, task_id: str, enabled: bool) -> bool:
        task = await self.get(task_id)
        if task is None:
            return False
        task.enabled = enabled
        await self.database.upsert_task(task, "pending" if enabled else "paused")
        return True
