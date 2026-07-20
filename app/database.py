from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import aiosqlite

from app.config import MonitorTask
from app.models import LockOrderRequest, LockOrderResult, MatchResult, TicketInfo


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(value: Any) -> str:
    if isinstance(value, (Decimal, datetime)):
        return str(value)
    raise TypeError(f"无法序列化 {type(value)!r}")


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS monitor_tasks (
                    task_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    sessions_json TEXT NOT NULL,
                    max_unit_price TEXT NOT NULL,
                    max_total_price TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_run_at TEXT,
                    consecutive_errors INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    session_name TEXT NOT NULL,
                    ticket_level TEXT NOT NULL,
                    area TEXT,
                    unit_price TEXT NOT NULL,
                    total_price TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    queried_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_price_task_time ON price_history(task_id, queried_at DESC);
                CREATE TABLE IF NOT EXISTS match_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    ticket_json TEXT NOT NULL,
                    conditions_json TEXT NOT NULL,
                    matched_at TEXT NOT NULL,
                    lock_triggered INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lock_records (
                    idempotency_key TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    event_id TEXT,
                    session_id TEXT,
                    area TEXT,
                    price TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    order_id TEXT,
                    error_reason TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_lock_task ON lock_records(task_id);
                CREATE TABLE IF NOT EXISTS notification_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notification_type TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    content_summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    retry_count INTEGER NOT NULL,
                    sent_at TEXT NOT NULL,
                    error_reason TEXT
                );
                """
            )
            await db.commit()

    async def upsert_task(self, task: MonitorTask, status: str = "pending") -> None:
        now = utc_now().isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO monitor_tasks(
                    task_id, platform, event_name, sessions_json, max_unit_price, max_total_price,
                    enabled, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    platform=excluded.platform, event_name=excluded.event_name,
                    sessions_json=excluded.sessions_json, max_unit_price=excluded.max_unit_price,
                    max_total_price=excluded.max_total_price, enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    task.task_id, task.platform, task.event_name, json.dumps(task.target_sessions, ensure_ascii=False),
                    str(task.max_unit_price), str(task.max_total_price), int(task.enabled), status, now, now,
                ),
            )
            await db.commit()

    async def update_task_state(
        self, task_id: str, status: str, *, consecutive_errors: int = 0, last_run: bool = False
    ) -> None:
        now = utc_now().isoformat()
        last_run_at = now if last_run else None
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE monitor_tasks SET status=?, consecutive_errors=?, updated_at=?,
                    last_run_at=COALESCE(?, last_run_at) WHERE task_id=?
                """,
                (status, consecutive_errors, now, last_run_at, task_id),
            )
            await db.commit()

    async def set_task_enabled(self, task_id: str, enabled: bool) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "UPDATE monitor_tasks SET enabled=?, status=?, updated_at=? WHERE task_id=?",
                (int(enabled), "pending" if enabled else "disabled", utc_now().isoformat(), task_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def is_task_enabled(self, task_id: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT enabled FROM monitor_tasks WHERE task_id=?", (task_id,))
            row = await cursor.fetchone()
            return bool(row and row[0])

    async def get_task_control(self, task_id: str) -> tuple[bool, str] | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT enabled, status FROM monitor_tasks WHERE task_id=?", (task_id,)
            )
            row = await cursor.fetchone()
            return (bool(row[0]), str(row[1])) if row else None

    async def list_task_states(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM monitor_tasks ORDER BY task_id")
            return [dict(row) for row in await cursor.fetchall()]

    async def record_price(self, task_id: str, ticket: TicketInfo) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO price_history(
                    platform, task_id, event_name, session_name, ticket_level, area,
                    unit_price, total_price, quantity, queried_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket.platform, task_id, ticket.event_name, ticket.session_name, ticket.ticket_level,
                    ticket.area, str(ticket.unit_price), str(ticket.payable_total), ticket.available_quantity,
                    utc_now().isoformat(),
                ),
            )
            await db.commit()

    async def record_match(self, task: MonitorTask, result: MatchResult, lock_triggered: bool) -> None:
        if result.ticket is None:
            return
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO match_records(task_id, ticket_json, conditions_json, matched_at, lock_triggered)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    json.dumps(asdict(result.ticket), ensure_ascii=False, default=_json_default),
                    task.model_dump_json(), utc_now().isoformat(), int(lock_triggered),
                ),
            )
            await db.commit()

    async def claim_lock(
        self, request: LockOrderRequest, max_attempts: int, cooldown_seconds: int = 0
    ) -> bool:
        current_time = utc_now()
        now = current_time.isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT status, attempt_count, updated_at FROM lock_records WHERE idempotency_key=?",
                (request.idempotency_key,),
            )
            existing = await cursor.fetchone()
            if existing:
                status, attempts, updated_at = existing
                if status in {"success", "in_progress"} or attempts >= max_attempts:
                    await db.rollback()
                    return False
                last_update = datetime.fromisoformat(updated_at)
                if (current_time - last_update).total_seconds() < cooldown_seconds:
                    await db.rollback()
                    return False
                await db.execute(
                    """
                    UPDATE lock_records SET status='in_progress', attempt_count=attempt_count+1,
                        error_reason=NULL, updated_at=? WHERE idempotency_key=?
                    """,
                    (now, request.idempotency_key),
                )
            else:
                ticket = request.ticket
                await db.execute(
                    """
                    INSERT INTO lock_records(
                        idempotency_key, task_id, platform, event_id, session_id, area, price,
                        quantity, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'in_progress', ?, ?)
                    """,
                    (
                        request.idempotency_key, request.task_id, ticket.platform, ticket.event_id,
                        ticket.session_id, ticket.area, str(ticket.payable_total), request.quantity, now, now,
                    ),
                )
            await db.commit()
            return True

    async def complete_lock(self, key: str, result: LockOrderResult) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE lock_records SET status=?, order_id=?, price=COALESCE(?, price),
                    error_reason=?, updated_at=? WHERE idempotency_key=?
                """,
                (
                    result.status.value, result.order_id,
                    str(result.final_total) if result.final_total is not None else None,
                    None if result.success else result.message, utc_now().isoformat(), key,
                ),
            )
            await db.commit()

    async def record_notification(
        self, notification_type: str, provider: str, summary: str, success: bool,
        retry_count: int = 0, error: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO notification_records(
                    notification_type, provider, content_summary, status, retry_count, sent_at, error_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notification_type, provider, summary[:500], "success" if success else "failed",
                    retry_count, utc_now().isoformat(), error,
                ),
            )
            await db.commit()

    async def get_history(self, task_id: str, limit: int = 50) -> dict[str, list[dict[str, Any]]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            prices_cursor = await db.execute(
                "SELECT * FROM price_history WHERE task_id=? ORDER BY queried_at DESC LIMIT ?",
                (task_id, limit),
            )
            matches_cursor = await db.execute(
                "SELECT * FROM match_records WHERE task_id=? ORDER BY matched_at DESC LIMIT ?",
                (task_id, limit),
            )
            locks_cursor = await db.execute(
                "SELECT * FROM lock_records WHERE task_id=? ORDER BY updated_at DESC LIMIT ?",
                (task_id, limit),
            )
            return {
                "prices": [dict(row) for row in await prices_cursor.fetchall()],
                "matches": [dict(row) for row in await matches_cursor.fetchall()],
                "locks": [dict(row) for row in await locks_cursor.fetchall()],
            }
