from datetime import timedelta

from app.domain import utc_now
from app.headless import HeadlessApplication
from app.settings import AppSettings
from app.storage.audit_repository import AuditEntry, AuditQuery, AuditRepository
from app.storage.database import MvpDatabase


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


async def test_headless_removes_audit_logs_older_than_24_hours(tmp_path) -> None:
    settings = AppSettings(database_path=tmp_path / "ticket.db")
    database = MvpDatabase(settings.database_path)
    await database.initialize()
    audit = AuditRepository(database)
    await audit.append(
        AuditEntry(
            level="INFO",
            category="monitor",
            action="expired",
            message="过期日志",
            timestamp=utc_now() - timedelta(hours=25),
        )
    )
    application = HeadlessApplication(settings)

    await application.start()
    assert await application.audit.query(AuditQuery(keyword="过期日志")) == []
    cleanup = await application.audit.query(AuditQuery(category="maintenance"))
    assert cleanup[0].action == "audit_retention_cleanup"
    assert cleanup[0].context == {"deleted_count": 1, "retention_hours": 24}
    await application.close()
