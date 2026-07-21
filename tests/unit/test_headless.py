from app.headless import HeadlessApplication
from app.settings import AppSettings
from app.storage.audit_repository import AuditQuery


async def test_headless_application_starts_and_stops(tmp_path) -> None:
    application = HeadlessApplication(
        AppSettings(database_path=tmp_path / "ticket.db")
    )

    await application.start()
    assert application.scheduler is not None
    assert application._started is True

    await application.close()
    assert application._started is False

    entries = await application.audit.query(AuditQuery(category="application"))
    assert [entry.action for entry in entries] == [
        "headless_stopped",
        "headless_started",
    ]
