from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain import MonitorTask, OrderPreview, OrderResult, utc_now
from app.storage.database import MvpDatabase


BLOCKING_ORDER_STATUSES = {
    "creating",
    "payment_pending",
    "success",
    "unknown_after_timeout",
}


def build_idempotency_key(task: MonitorTask) -> str:
    parts = (
        task.ticket.platform,
        task.ticket.event_id,
        task.ticket.session_id,
        task.ticket.listing_id,
        str(task.quantity),
        ",".join(sorted(task.buyer_ids)),
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


@dataclass(slots=True)
class OrderRecord:
    idempotency_key: str
    task_id: str
    platform: str
    order_id: str | None
    status: str
    final_total: Decimal | None
    payment_deadline: datetime | None
    payment_url: str | None
    preview: OrderPreview | None
    result: OrderResult | None
    created_at: datetime
    updated_at: datetime


class OrderRepository:
    def __init__(self, database: MvpDatabase) -> None:
        self.database = database

    async def claim_creating(
        self, task: MonitorTask, preview: OrderPreview
    ) -> tuple[bool, OrderRecord | None]:
        key = build_idempotency_key(task)
        now = utc_now().isoformat()
        async with self.database.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "SELECT * FROM order_records WHERE idempotency_key=?", (key,)
            )
            existing_row = await cursor.fetchone()
            if existing_row and existing_row["status"] in BLOCKING_ORDER_STATUSES:
                await connection.rollback()
                return False, self._from_row(existing_row)
            await connection.execute(
                """
                INSERT INTO order_records (
                    idempotency_key, task_id, platform, status,
                    preview_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'creating', ?, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    task_id=excluded.task_id,
                    platform=excluded.platform,
                    order_id=NULL,
                    status='creating',
                    final_total=NULL,
                    payment_deadline=NULL,
                    payment_url=NULL,
                    preview_json=excluded.preview_json,
                    result_json=NULL,
                    updated_at=excluded.updated_at
                """,
                (
                    key,
                    task.task_id,
                    task.ticket.platform,
                    preview.model_dump_json(),
                    now,
                    now,
                ),
            )
            await connection.commit()
        return True, None

    async def save_result(
        self, idempotency_key: str, result: OrderResult
    ) -> OrderRecord:
        async with self.database.connect() as connection:
            await connection.execute(
                """
                UPDATE order_records SET
                    order_id=?, status=?, final_total=?, payment_deadline=?,
                    payment_url=?, result_json=?, updated_at=?
                WHERE idempotency_key=?
                """,
                (
                    result.order_id,
                    result.status,
                    str(result.final_total) if result.final_total is not None else None,
                    result.payment_deadline.isoformat()
                    if result.payment_deadline
                    else None,
                    result.payment_url,
                    result.model_dump_json(),
                    utc_now().isoformat(),
                    idempotency_key,
                ),
            )
            await connection.commit()
        record = await self.get(idempotency_key)
        if record is None:
            raise ValueError("订单幂等记录不存在")
        return record

    async def mark_unknown_after_timeout(self, idempotency_key: str, message: str) -> None:
        result = OrderResult(
            success=False,
            status="unknown_after_timeout",
            message=message,
        )
        await self.save_result(idempotency_key, result)

    async def get(self, idempotency_key: str) -> OrderRecord | None:
        async with self.database.connect() as connection:
            cursor = await connection.execute(
                "SELECT * FROM order_records WHERE idempotency_key=?",
                (idempotency_key,),
            )
            row = await cursor.fetchone()
        return self._from_row(row) if row else None

    async def find_blocking(self, task: MonitorTask) -> OrderRecord | None:
        record = await self.get(build_idempotency_key(task))
        return record if record and record.status in BLOCKING_ORDER_STATUSES else None

    @staticmethod
    def _from_row(row: object) -> OrderRecord:
        values = dict(row)  # type: ignore[arg-type]
        return OrderRecord(
            idempotency_key=values["idempotency_key"],
            task_id=values["task_id"],
            platform=values["platform"],
            order_id=values["order_id"],
            status=values["status"],
            final_total=Decimal(values["final_total"])
            if values["final_total"] is not None
            else None,
            payment_deadline=datetime.fromisoformat(values["payment_deadline"])
            if values["payment_deadline"]
            else None,
            payment_url=values["payment_url"],
            preview=OrderPreview.model_validate_json(values["preview_json"])
            if values["preview_json"]
            else None,
            result=OrderResult.model_validate_json(values["result_json"])
            if values["result_json"]
            else None,
            created_at=datetime.fromisoformat(values["created_at"]),
            updated_at=datetime.fromisoformat(values["updated_at"]),
        )
