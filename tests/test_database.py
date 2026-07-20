from pathlib import Path

from app.database import Database
from app.platforms.mock import MockPlatform


async def test_database_writes_task_and_price(sample_task: object, tmp_path: Path) -> None:
    database = Database(tmp_path / "data.db")
    await database.initialize()
    await database.upsert_task(sample_task)  # type: ignore[arg-type]
    platform = MockPlatform("mock")
    ticket = (await platform.query_tickets(sample_task))[0]  # type: ignore[arg-type]
    await database.record_price("test_001", ticket)
    state = await database.get_task_control("test_001")
    history = await database.get_history("test_001")
    assert state == (True, "pending")
    assert len(history["prices"]) == 1
