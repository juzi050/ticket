from app.storage.audit_repository import (
    REDACTED,
    AuditEntry,
    AuditQuery,
    AuditRepository,
    scrub_secrets,
)
from app.storage.database import MvpDatabase


def test_scrub_secrets_recursively() -> None:
    source = {
        "Authorization": "Bearer secret",
        "nested": {"csrf_token": "secret", "visible": "ok"},
        "items": [{"send-key": "secret"}],
    }
    scrubbed = scrub_secrets(source)
    assert scrubbed["Authorization"] == REDACTED
    assert scrubbed["nested"] == {"csrf_token": REDACTED, "visible": "ok"}
    assert scrubbed["items"] == [{"send-key": REDACTED}]


async def test_append_and_query_audit_logs(tmp_path) -> None:
    database = MvpDatabase(tmp_path / "ticket.db")
    await database.initialize()
    repository = AuditRepository(database)
    first_id = await repository.append(
        AuditEntry(
            level="INFO",
            category="http",
            action="fetch_event",
            message="读取演出",
            platform="motianlun",
            task_id="task-1",
            request_url="https://example.com/api/event",
            request_headers={"Cookie": "session=secret", "Accept": "json"},
            response_status=200,
            response_body={"event": "演出一"},
        )
    )
    await repository.append(
        AuditEntry(
            level="ERROR",
            category="order",
            action="preview_order",
            message="预览失败",
            platform="piaoniu",
        )
    )

    rows = await repository.query(
        AuditQuery(platform="motianlun", keyword="演出", limit=10)
    )
    assert len(rows) == 1
    assert rows[0].id == first_id
    assert rows[0].request_headers == {"Cookie": REDACTED, "Accept": "json"}
