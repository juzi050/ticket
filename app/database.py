from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import aiosqlite

from app.config import MonitorTask
from app.models import LockOrderRequest, LockOrderResult, LockStage, MatchResult, TicketInfo


_PERMANENT_LOCK_STATUSES = {"success", "payment_pending", "order_exists"}


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
                CREATE TABLE IF NOT EXISTS lock_stage_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_lock_stage_key
                    ON lock_stage_records(idempotency_key, created_at);
                CREATE TABLE IF NOT EXISTS ticket_cache (
                    task_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    listing_id TEXT NOT NULL,
                    ticket_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, listing_id)
                );
                CREATE TABLE IF NOT EXISTS app_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS platform_sessions (
                    platform TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    checked_at TEXT NOT NULL
                );
                """
            )
            await self._ensure_columns(
                db,
                "monitor_tasks",
                {
                    "task_name": "TEXT NOT NULL DEFAULT ''",
                    "config_json": "TEXT",
                    "query_count": "INTEGER NOT NULL DEFAULT 0",
                    "last_price": "TEXT",
                    "min_price": "TEXT",
                    "available_quantity": "INTEGER",
                    "current_ticket_level": "TEXT",
                    "current_area": "TEXT",
                    "is_matched": "INTEGER",
                    "last_mismatch": "TEXT",
                    "last_error": "TEXT",
                    "last_lock_result": "TEXT",
                },
            )
            await self._ensure_columns(
                db,
                "lock_records",
                {
                    "account_alias": "TEXT NOT NULL DEFAULT ''",
                    "listing_id": "TEXT NOT NULL DEFAULT ''",
                    "failure_kind": "TEXT",
                },
            )
            await db.commit()

    @staticmethod
    async def _ensure_columns(
        db: aiosqlite.Connection, table: str, columns: dict[str, str]
    ) -> None:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        existing = {str(row[1]) for row in await cursor.fetchall()}
        for name, definition in columns.items():
            if name not in existing:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    async def upsert_task(self, task: MonitorTask, status: str = "pending") -> None:
        now = utc_now().isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO monitor_tasks(
                    task_id, platform, event_name, sessions_json, max_unit_price, max_total_price,
                    enabled, status, task_name, config_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    platform=excluded.platform, event_name=excluded.event_name,
                    sessions_json=excluded.sessions_json, max_unit_price=excluded.max_unit_price,
                    max_total_price=excluded.max_total_price, enabled=excluded.enabled,
                    task_name=excluded.task_name, config_json=excluded.config_json,
                    updated_at=excluded.updated_at
                """,
                (
                    task.task_id, task.platform, task.event_name, json.dumps(task.target_sessions, ensure_ascii=False),
                    str(task.max_unit_price), str(task.max_total_price), int(task.enabled), status,
                    task.task_name, task.model_dump_json(), now, now,
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

    async def load_tasks(self) -> list[MonitorTask]:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT task_id, config_json, enabled FROM monitor_tasks "
                "WHERE config_json IS NOT NULL ORDER BY created_at"
            )
            result: list[MonitorTask] = []
            migrated = False
            for task_id, config_json, enabled in await cursor.fetchall():
                raw = json.loads(config_json)
                row_migrated = False
                # 兼容早期 Mock 加速版本曾写入的亚秒间隔；真实任务仍由模型限制为至少 1 秒。
                interval = raw.get("interval_seconds")
                if interval is not None and float(interval) < 1:
                    raw["interval_seconds"] = 1
                    migrated = True
                    row_migrated = True
                task = MonitorTask.model_validate(raw)
                task.enabled = bool(enabled)
                result.append(task)
                if row_migrated:
                    await db.execute(
                        "UPDATE monitor_tasks SET config_json=? WHERE task_id=?",
                        (task.model_dump_json(), task_id),
                    )
            if migrated:
                await db.commit()
            return result

    async def get_metadata(self, key: str) -> str | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT value FROM app_metadata WHERE key=?", (key,))
            row = await cursor.fetchone()
            return str(row[0]) if row else None

    async def set_metadata(self, key: str, value: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO app_metadata(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, utc_now().isoformat()),
            )
            await db.commit()

    async def save_platform_session(self, platform: str, status: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO platform_sessions(platform, status, checked_at) VALUES (?, ?, ?)
                ON CONFLICT(platform) DO UPDATE SET
                    status=excluded.status, checked_at=excluded.checked_at
                """,
                (platform, status, utc_now().isoformat()),
            )
            await db.commit()

    async def list_platform_sessions(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM platform_sessions ORDER BY platform")
            return [dict(row) for row in await cursor.fetchall()]

    async def get_task(self, task_id: str) -> MonitorTask | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT config_json, enabled FROM monitor_tasks WHERE task_id=?", (task_id,)
            )
            row = await cursor.fetchone()
            if not row or not row[0]:
                return None
            raw = json.loads(row[0])
            interval = raw.get("interval_seconds")
            if interval is not None and float(interval) < 1:
                raw["interval_seconds"] = 1
            task = MonitorTask.model_validate(raw)
            task.enabled = bool(row[1])
            if interval is not None and float(interval) < 1:
                await db.execute(
                    "UPDATE monitor_tasks SET config_json=? WHERE task_id=?",
                    (task.model_dump_json(), task_id),
                )
                await db.commit()
            return task

    async def delete_task(self, task_id: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            for table in ("price_history", "match_records", "lock_records", "lock_stage_records", "ticket_cache"):
                await db.execute(f"DELETE FROM {table} WHERE task_id=?", (task_id,))
            cursor = await db.execute("DELETE FROM monitor_tasks WHERE task_id=?", (task_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def update_task_snapshot(
        self,
        task_id: str,
        *,
        status: str | None = None,
        query_increment: bool = False,
        last_price: Decimal | None = None,
        available_quantity: int | None = None,
        ticket_level: str | None = None,
        area: str | None = None,
        matched: bool | None = None,
        mismatch_reason: str | None = None,
        error: str | None = None,
        lock_result: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE monitor_tasks SET
                    status=COALESCE(?, status),
                    query_count=query_count + ?,
                    last_price=COALESCE(?, last_price),
                    min_price=CASE
                        WHEN ? IS NULL THEN min_price
                        WHEN min_price IS NULL OR CAST(? AS REAL) < CAST(min_price AS REAL) THEN ?
                        ELSE min_price END,
                    available_quantity=COALESCE(?, available_quantity),
                    current_ticket_level=COALESCE(?, current_ticket_level),
                    current_area=COALESCE(?, current_area),
                    is_matched=COALESCE(?, is_matched),
                    last_mismatch=?, last_error=?, last_lock_result=COALESCE(?, last_lock_result),
                    updated_at=?, last_run_at=CASE WHEN ? THEN ? ELSE last_run_at END
                WHERE task_id=?
                """,
                (
                    status,
                    int(query_increment),
                    str(last_price) if last_price is not None else None,
                    str(last_price) if last_price is not None else None,
                    str(last_price) if last_price is not None else None,
                    str(last_price) if last_price is not None else None,
                    available_quantity,
                    ticket_level,
                    area,
                    int(matched) if matched is not None else None,
                    mismatch_reason,
                    error,
                    lock_result,
                    utc_now().isoformat(),
                    int(query_increment),
                    utc_now().isoformat(),
                    task_id,
                ),
            )
            await db.commit()

    async def save_ticket_cache(self, task_id: str, ticket: TicketInfo) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO ticket_cache(task_id, platform, listing_id, ticket_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(task_id, listing_id) DO UPDATE SET
                    ticket_json=excluded.ticket_json, updated_at=excluded.updated_at
                """,
                (
                    task_id,
                    ticket.platform,
                    ticket.listing_id or "unknown",
                    json.dumps(asdict(ticket), ensure_ascii=False, default=_json_default),
                    utc_now().isoformat(),
                ),
            )
            await db.commit()

    async def list_ticket_cache(self, task_id: str | None = None) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            if task_id:
                cursor = await db.execute(
                    "SELECT * FROM ticket_cache WHERE task_id=? ORDER BY updated_at DESC", (task_id,)
                )
            else:
                cursor = await db.execute("SELECT * FROM ticket_cache ORDER BY updated_at DESC")
            return [dict(row) for row in await cursor.fetchall()]

    async def clear_all_data(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            for table in (
                "notification_records",
                "lock_stage_records",
                "lock_records",
                "match_records",
                "price_history",
                "ticket_cache",
                "monitor_tasks",
                "platform_sessions",
            ):
                await db.execute(f"DELETE FROM {table}")
            await db.commit()

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
        await self.save_ticket_cache(task_id, ticket)

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
        # 保留参数以兼容现有调用；attempt_count 只用于审计，不再永久封禁临时失败。
        _ = max_attempts
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
                status, _attempts, updated_at = existing
                if status in _PERMANENT_LOCK_STATUSES:
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
                        quantity, status, account_alias, listing_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'in_progress', ?, ?, ?, ?)
                    """,
                    (
                        request.idempotency_key, request.task_id, ticket.platform, ticket.event_id,
                        ticket.session_id, ticket.area, str(ticket.payable_total), request.quantity,
                        request.account_alias, ticket.listing_id, now, now,
                    ),
                )
            await db.commit()
            return True

    async def complete_lock(self, key: str, result: LockOrderResult) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE lock_records SET status=?, order_id=?, price=COALESCE(?, price),
                    error_reason=?, failure_kind=?, updated_at=? WHERE idempotency_key=?
                """,
                (
                    result.status.value, result.order_id,
                    str(result.final_total) if result.final_total is not None else None,
                    None if result.success else result.message,
                    result.failure_kind.value if result.failure_kind else None,
                    utc_now().isoformat(), key,
                ),
            )
            await db.commit()

    async def get_lock_record(self, key: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM lock_records WHERE idempotency_key=?", (key,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def has_pending_order(
        self,
        *,
        account_alias: str,
        platform: str,
        event_id: str,
        session_id: str,
        listing_id: str,
        quantity: int,
    ) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """
                SELECT 1 FROM lock_records
                WHERE account_alias=? AND platform=? AND event_id=? AND session_id=?
                    AND listing_id=? AND quantity=?
                    AND status IN ('success', 'payment_pending', 'order_exists')
                LIMIT 1
                """,
                (account_alias, platform, event_id, session_id, listing_id, quantity),
            )
            return await cursor.fetchone() is not None

    async def record_lock_stage(
        self, key: str, task_id: str, stage: LockStage, message: str = ""
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO lock_stage_records(idempotency_key, task_id, stage, message, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (key, task_id, stage.value, message, utc_now().isoformat()),
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
            stages_cursor = await db.execute(
                "SELECT * FROM lock_stage_records WHERE task_id=? ORDER BY created_at DESC LIMIT ?",
                (task_id, limit),
            )
            return {
                "prices": [dict(row) for row in await prices_cursor.fetchall()],
                "matches": [dict(row) for row in await matches_cursor.fetchall()],
                "locks": [dict(row) for row in await locks_cursor.fetchall()],
                "stages": [dict(row) for row in await stages_cursor.fetchall()],
            }
