import aiosqlite

from app.storage.database import MvpDatabase


async def test_mvp_database_creates_only_required_business_tables(tmp_path) -> None:
    path = tmp_path / "ticket.db"
    database = MvpDatabase(path)

    await database.initialize()

    async with aiosqlite.connect(path) as connection:
        cursor = await connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = {row[0] for row in await cursor.fetchall()}
    assert tables == {
        "buyers",
        "buyer_platform_bindings",
        "monitor_tasks",
        "order_records",
        "audit_logs",
        "platform_sessions",
        "app_metadata",
    }


async def test_query_interval_is_enforced_by_sqlite(tmp_path) -> None:
    database = MvpDatabase(tmp_path / "ticket.db")
    await database.initialize()

    async with database.connect() as connection:
        try:
            await connection.execute(
                """
                INSERT INTO monitor_tasks (
                    task_id, platform, ticket_json, quantity, buyer_ids_json,
                    ideal_price, query_interval_seconds, enabled, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "task-1",
                    "motianlun",
                    "{}",
                    1,
                    "[]",
                    "300",
                    0,
                    1,
                    "monitoring",
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                ),
            )
        except aiosqlite.IntegrityError:
            pass
        else:
            raise AssertionError("SQLite 必须拒绝小于 1 秒的查询间隔")
