from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from app.domain import MonitorTask, TicketOption, utc_now
from app.storage.database import MvpDatabase


class TaskRepository:
    def __init__(self, database: MvpDatabase) -> None:
        self.database = database

    async def save(self, task: MonitorTask) -> MonitorTask:
        saved = task.model_copy(update={"updated_at": utc_now()})
        async with self.database.connect() as connection:
            await connection.execute(
                """
                INSERT INTO monitor_tasks (
                    task_id, platform, ticket_json, quantity, buyer_ids_json,
                    ideal_price, query_interval_seconds, enabled, status,
                    last_unit_price, last_estimated_total, last_final_total,
                    last_checked_at, next_check_at, last_error,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    platform=excluded.platform,
                    ticket_json=excluded.ticket_json,
                    quantity=excluded.quantity,
                    buyer_ids_json=excluded.buyer_ids_json,
                    ideal_price=excluded.ideal_price,
                    query_interval_seconds=excluded.query_interval_seconds,
                    enabled=excluded.enabled,
                    status=excluded.status,
                    last_unit_price=excluded.last_unit_price,
                    last_estimated_total=excluded.last_estimated_total,
                    last_final_total=excluded.last_final_total,
                    last_checked_at=excluded.last_checked_at,
                    next_check_at=excluded.next_check_at,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at
                """,
                self._to_values(saved),
            )
            await connection.commit()
        return saved

    async def get(self, task_id: str) -> MonitorTask | None:
        async with self.database.connect() as connection:
            cursor = await connection.execute(
                "SELECT * FROM monitor_tasks WHERE task_id=?", (task_id,)
            )
            row = await cursor.fetchone()
        return self._from_row(row) if row else None

    async def list(self) -> list[MonitorTask]:
        async with self.database.connect() as connection:
            cursor = await connection.execute(
                "SELECT * FROM monitor_tasks ORDER BY created_at, task_id"
            )
            rows = await cursor.fetchall()
        return [self._from_row(row) for row in rows]

    async def delete(self, task_id: str) -> None:
        async with self.database.connect() as connection:
            await connection.execute("DELETE FROM monitor_tasks WHERE task_id=?", (task_id,))
            await connection.commit()

    async def update_interval(self, task_id: str, seconds: float) -> None:
        if not 1 <= seconds <= 86400:
            raise ValueError("查询间隔必须在 1 到 86400 秒之间")
        async with self.database.connect() as connection:
            await connection.execute(
                """
                UPDATE monitor_tasks
                SET query_interval_seconds=?, updated_at=?
                WHERE task_id=?
                """,
                (seconds, utc_now().isoformat(), task_id),
            )
            await connection.commit()

    async def set_enabled(self, task_id: str, enabled: bool, status: str) -> None:
        async with self.database.connect() as connection:
            await connection.execute(
                """
                UPDATE monitor_tasks
                SET enabled=?, status=?, updated_at=?
                WHERE task_id=?
                """,
                (int(enabled), status, utc_now().isoformat(), task_id),
            )
            await connection.commit()

    async def update_runtime(
        self,
        task_id: str,
        *,
        status: str,
        last_unit_price: Decimal | None = None,
        last_estimated_total: Decimal | None = None,
        last_final_total: Decimal | None = None,
        last_checked_at: datetime | None = None,
        next_check_at: datetime | None = None,
        last_error: str | None = None,
    ) -> None:
        async with self.database.connect() as connection:
            await connection.execute(
                """
                UPDATE monitor_tasks SET
                    status=?, last_unit_price=?, last_estimated_total=?,
                    last_final_total=?, last_checked_at=?, next_check_at=?,
                    last_error=?, updated_at=?
                WHERE task_id=?
                """,
                (
                    status,
                    self._decimal(last_unit_price),
                    self._decimal(last_estimated_total),
                    self._decimal(last_final_total),
                    last_checked_at.isoformat() if last_checked_at else None,
                    next_check_at.isoformat() if next_check_at else None,
                    last_error,
                    utc_now().isoformat(),
                    task_id,
                ),
            )
            await connection.commit()

    @staticmethod
    def _decimal(value: Decimal | None) -> str | None:
        return str(value) if value is not None else None

    @classmethod
    def _to_values(cls, task: MonitorTask) -> tuple[object, ...]:
        return (
            task.task_id,
            task.ticket.platform,
            task.ticket.model_dump_json(),
            task.quantity,
            json.dumps(task.buyer_ids, ensure_ascii=False),
            str(task.ideal_price),
            task.query_interval_seconds,
            int(task.enabled),
            task.status,
            cls._decimal(task.last_unit_price),
            cls._decimal(task.last_estimated_total),
            cls._decimal(task.last_final_total),
            task.last_checked_at.isoformat() if task.last_checked_at else None,
            task.next_check_at.isoformat() if task.next_check_at else None,
            task.last_error,
            task.created_at.isoformat(),
            task.updated_at.isoformat(),
        )

    @staticmethod
    def _from_row(row: object) -> MonitorTask:
        values = dict(row)  # type: ignore[arg-type]
        values["ticket"] = TicketOption.model_validate_json(values.pop("ticket_json"))
        values["buyer_ids"] = json.loads(values.pop("buyer_ids_json"))
        values["enabled"] = bool(values["enabled"])
        values.pop("platform")
        return MonitorTask.model_validate(values)
