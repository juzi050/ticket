from app.gui.audit_panel import audit_query_from_values


def test_audit_query_reads_all_supported_filters() -> None:
    query = audit_query_from_values(
        {
            "started_at": "2026-07-20T10:00:00+08:00",
            "ended_at": "2026-07-20T12:00:00+08:00",
            "platform": "piaoniu",
            "task_id": "task-1",
            "order_id": "order-1",
            "level": "INFO",
            "category": "http",
            "keyword": "票品",
        }
    )
    assert query.platform == "piaoniu"
    assert query.task_id == "task-1"
    assert query.started_at is not None
    assert query.keyword == "票品"
