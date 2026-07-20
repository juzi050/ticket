from app.domain import AuthSession, utc_now
from app.storage.database import MvpDatabase
from app.storage.session_repository import PlatformSessionRepository


async def test_platform_session_round_trip_and_status(tmp_path) -> None:
    database = MvpDatabase(tmp_path / "ticket.db")
    await database.initialize()
    repository = PlatformSessionRepository(database)
    session = AuthSession(
        platform="piaoniu",
        cookies={"session": "fake-local-secret"},
        headers={"User-Agent": "test"},
        csrf_token="fake-csrf",
        device_id="fake-device",
        created_at=utc_now(),
    )

    await repository.save(session)
    restored = await repository.get("piaoniu")
    assert restored == session
    assert await repository.status("piaoniu") == "logged_in"

    await repository.mark_expired("piaoniu")
    assert await repository.status("piaoniu") == "auth_expired"
    await repository.clear("piaoniu")
    assert await repository.status("piaoniu") == "logged_out"
    assert await repository.get("piaoniu") is None
