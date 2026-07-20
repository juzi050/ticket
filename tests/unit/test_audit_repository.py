import csv
import json

from app.storage.audit_repository import (
    REDACTED,
    AuditEntry,
    AuditQuery,
    AuditRepository,
    scrub_secrets,
    scrub_url,
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


def test_scrub_url_redacts_auth_query_values() -> None:
    url = "https://example.com/api?accessToken=secret&id=event-1&refresh_token=hidden"
    scrubbed = scrub_url(url)
    assert "secret" not in scrubbed
    assert "hidden" not in scrubbed
    assert "id=event-1" in scrubbed


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


async def test_export_audit_logs_as_json_and_csv(tmp_path) -> None:
    database = MvpDatabase(tmp_path / "ticket.db")
    await database.initialize()
    repository = AuditRepository(database)
    await repository.append(
        AuditEntry(
            level="INFO",
            category="monitor",
            action="check_price",
            message="价格查询完成",
            platform="motianlun",
            request_headers={"Authorization": "secret"},
        )
    )

    json_path = await repository.export_json(tmp_path / "export" / "audit.json")
    csv_path = await repository.export_csv(tmp_path / "export" / "audit.csv")
    exported = json.loads(json_path.read_text(encoding="utf-8"))
    assert exported[0]["request_headers"]["Authorization"] == REDACTED
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["message"] == "价格查询完成"
