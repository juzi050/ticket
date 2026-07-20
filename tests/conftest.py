from decimal import Decimal

import pytest

from app.config import MonitorTask


@pytest.fixture
def sample_task() -> MonitorTask:
    return MonitorTask(
        task_id="test_001",
        enabled=True,
        platform="mock",
        event_name="测试演唱会",
        event_url="https://example.com/event",
        event_id="event-1",
        target_sessions=["2026-08-01 19:30"],
        target_ticket_levels=["1280", "VIP"],
        target_areas=["内场A区", "内场B区"],
        excluded_keywords=["遮挡", "站票"],
        row_min=1,
        row_max=10,
        quantity=2,
        adjacent_seats_required=True,
        max_unit_price=Decimal("1200"),
        max_total_price=Decimal("2400"),
        interval_seconds=1,
        auto_lock=True,
        max_lock_attempts=1,
    )
