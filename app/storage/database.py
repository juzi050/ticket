from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS buyers (
    buyer_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    certificate_type TEXT NOT NULL,
    certificate_number TEXT NOT NULL,
    phone TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS buyer_platform_bindings (
    buyer_id TEXT NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('piaoniu', 'motianlun')),
    remote_buyer_id TEXT NOT NULL,
    remote_payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (buyer_id, platform),
    FOREIGN KEY (buyer_id) REFERENCES buyers(buyer_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS monitor_tasks (
    task_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL CHECK (platform IN ('piaoniu', 'motianlun')),
    ticket_json TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 1),
    buyer_ids_json TEXT NOT NULL,
    ideal_price TEXT NOT NULL,
    query_interval_seconds REAL NOT NULL CHECK (
        query_interval_seconds >= 1 AND query_interval_seconds <= 86400
    ),
    enabled INTEGER NOT NULL,
    status TEXT NOT NULL,
    last_unit_price TEXT,
    last_estimated_total TEXT,
    last_final_total TEXT,
    last_checked_at TEXT,
    next_check_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_records (
    idempotency_key TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('piaoniu', 'motianlun')),
    order_id TEXT,
    status TEXT NOT NULL,
    final_total TEXT,
    payment_deadline TEXT,
    payment_url TEXT,
    preview_json TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES monitor_tasks(task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    level TEXT NOT NULL,
    category TEXT NOT NULL,
    action TEXT NOT NULL,
    platform TEXT,
    task_id TEXT,
    buyer_id TEXT,
    order_id TEXT,
    message TEXT NOT NULL,
    request_url TEXT,
    request_method TEXT,
    request_headers_json TEXT,
    request_body_json TEXT,
    response_status INTEGER,
    response_headers_json TEXT,
    response_body_json TEXT,
    context_json TEXT,
    exception_type TEXT,
    exception_message TEXT,
    exception_stack TEXT
);

CREATE TABLE IF NOT EXISTS platform_sessions (
    platform TEXT PRIMARY KEY CHECK (platform IN ('piaoniu', 'motianlun')),
    status TEXT NOT NULL,
    auth_session_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_enabled_next
ON monitor_tasks(enabled, next_check_at);
CREATE INDEX IF NOT EXISTS idx_orders_task_status
ON order_records(task_id, status);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp
ON audit_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_filters
ON audit_logs(platform, task_id, order_id, level, category);
"""


class MvpDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute("PRAGMA foreign_keys=ON")
            await connection.execute("PRAGMA journal_mode=WAL")
            await connection.executescript(SCHEMA)
            await connection.commit()

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            await connection.close()
