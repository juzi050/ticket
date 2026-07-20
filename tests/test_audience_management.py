from __future__ import annotations

import json
import logging
import queue
from decimal import Decimal
from pathlib import Path

import aiosqlite
import pytest
from pydantic import ValidationError

from app.config import ApplicationSettings, MonitorTask, Settings
from app.database import Database
from app.gui.controller import GuiController
from app.gui.ui_events import UiEvent
from app.models import AudienceCreateRequest, LockOrderRequest, LockStatus
from app.platforms.mock import MockPlatform
from app.storage.cache_cleaner import CacheCleaner
from app.storage.task_store import TaskStore


def make_task(**changes: object) -> MonitorTask:
    values: dict[str, object] = {
        "task_id": "audience-task",
        "platform": "mock",
        "event_name": "测试演出",
        "event_url": "https://example.com/event",
        "event_id": "event-1",
        "target_session_id": "mock-session-1",
        "target_listing_id": "mock-listing-audience-task",
        "quantity": 2,
        "max_unit_price": Decimal("1000"),
        "max_total_price": Decimal("2000"),
        "auto_lock": True,
        "platform_audience_ids": ["mock-audience-1", "mock-audience-2"],
        "platform_audience_labels": ["测试甲", "测试乙"],
    }
    values.update(changes)
    return MonitorTask.model_validate(values)


def test_auto_lock_requires_exact_unique_audiences() -> None:
    with pytest.raises(ValidationError, match="必须选择购票人"):
        make_task(platform_audience_ids=[], platform_audience_labels=[])
    with pytest.raises(ValidationError, match="购买数量一致"):
        make_task(platform_audience_ids=["mock-audience-1"])
    with pytest.raises(ValidationError, match="不能重复"):
        make_task(platform_audience_ids=["mock-audience-1", "mock-audience-1"])


def test_notification_only_task_may_have_no_audience() -> None:
    task = make_task(
        auto_lock=False,
        platform_audience_ids=[],
        platform_audience_labels=[],
    )
    assert task.platform_audience_ids == []


async def test_mock_can_list_create_and_invalidate_audience() -> None:
    platform = MockPlatform("mock")
    before = await platform.list_audiences()
    request = AudienceCreateRequest(
        name="内存测试人",
        certificate_type="身份证",
        certificate_number="110101199001010011",
        phone="13800000000",
    )
    option = await platform.create_audience(request)
    assert option.option_id not in {item.option_id for item in before}
    assert "110101199001010011" not in (option.masked_identity or "")
    assert (await platform.validate_audience_ids([option.option_id]))[0]
    platform.invalidate_audience(option.option_id)
    assert not (await platform.validate_audience_ids([option.option_id]))[0]
    request.clear_sensitive()


def test_sensitive_request_cannot_be_serialized_and_can_be_cleared() -> None:
    request = AudienceCreateRequest(
        name="测试人",
        certificate_type="身份证",
        certificate_number="110101199001010011",
        phone="13800000000",
    )
    with pytest.raises(TypeError, match="禁止序列化"):
        request.model_dump()
    with pytest.raises(TypeError, match="禁止序列化"):
        request.model_dump_json()
    request.clear_sensitive()
    assert request.name == ""
    assert request.certificate_number.get_secret_value() == ""
    assert request.phone is None


async def test_controller_clears_create_request_and_does_not_log_secrets(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(
        application=ApplicationSettings(database_path=tmp_path / "controller.db", mock_mode=True),
        tasks=[],
    )
    database = Database(settings.application.database_path)
    await database.initialize()
    controller = GuiController(settings, database, queue.Queue[UiEvent](), mock_mode=True)
    secret_identity = "110101199001010011"
    secret_phone = "13800000000"
    request = AudienceCreateRequest(
        name="内存测试人",
        certificate_type="身份证",
        certificate_number=secret_identity,
        phone=secret_phone,
    )
    with caplog.at_level(logging.INFO):
        option = await controller.create_audience("motianlun", request)
    assert option.option_id
    assert request.certificate_number.get_secret_value() == ""
    assert request.phone is None
    assert secret_identity not in caplog.text
    assert secret_phone not in caplog.text
    await controller.shutdown()


async def test_task_database_only_contains_platform_references(tmp_path: Path) -> None:
    path = tmp_path / "references.db"
    database = Database(path)
    await database.initialize()
    task = make_task()
    await TaskStore(database).save(task)
    raw = path.read_bytes()
    assert b"mock-audience-1" in raw
    assert b"110101199001010011" not in raw
    restored = await TaskStore(database).get(task.task_id)
    assert restored is not None
    assert restored.platform_audience_ids == task.platform_audience_ids
    assert restored.purchase_profile_id == ""


async def test_mock_order_selects_only_exact_option_ids() -> None:
    platform = MockPlatform("mock")
    task = make_task()
    ticket = platform._ticket(task, good=True)
    request = LockOrderRequest(
        task_id=task.task_id,
        ticket=ticket,
        quantity=task.quantity,
        max_unit_price=task.max_unit_price,
        max_total_price=task.max_total_price,
        idempotency_key="audience-lock",
        audience_ids=list(task.platform_audience_ids),
    )
    result = await platform.lock_order(task, request)
    assert result.status is LockStatus.PAYMENT_PENDING
    assert platform.last_selected_audience_ids == task.platform_audience_ids
    valid, _ = await platform.select_order_audiences(None, ["测试甲", "测试乙"], 2)
    assert not valid


async def test_mock_selected_count_mismatch_stops_order() -> None:
    platform = MockPlatform("mock")
    platform.simulate_selected_count_mismatch = True
    task = make_task()
    request = LockOrderRequest(
        task_id=task.task_id,
        ticket=platform._ticket(task, good=True),
        quantity=2,
        max_unit_price=task.max_unit_price,
        max_total_price=task.max_total_price,
        idempotency_key="mismatch",
        audience_ids=list(task.platform_audience_ids),
    )
    result = await platform.lock_order(task, request)
    assert result.status is LockStatus.MANUAL_PROFILE_MISSING
    assert result.stage is not None and result.stage.value == "SELECTING_AUDIENCE"


async def test_legacy_purchase_profile_task_is_disabled_for_reselection(tmp_path: Path) -> None:
    database = Database(tmp_path / "legacy.db")
    await database.initialize()
    legacy = make_task(
        auto_lock=False,
        platform_audience_ids=[],
        platform_audience_labels=[],
        purchase_profile_id="legacy-profile",
    )
    await database.upsert_task(legacy, "pending")
    raw = json.loads(legacy.model_dump_json())
    raw["auto_lock"] = True
    async with aiosqlite.connect(database.path) as connection:
        await connection.execute(
            "UPDATE monitor_tasks SET config_json=?, enabled=1, status='pending' WHERE task_id=?",
            (json.dumps(raw, ensure_ascii=False), legacy.task_id),
        )
        await connection.commit()
    restored = (await database.load_tasks())[0]
    assert not restored.auto_lock
    assert not restored.enabled
    assert restored.purchase_profile_id == ""
    assert await database.get_task_control(restored.task_id) == (
        False,
        "audience_selection_required",
    )


async def test_cache_clear_does_not_delete_mock_remote_audiences(tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "cache.db")
    await database.initialize()
    platform = MockPlatform("mock")
    remote_ids = {item.option_id for item in await platform.list_audiences()}
    await TaskStore(database).save(make_task())
    await CacheCleaner(
        database, tmp_path / "data", log_dir=tmp_path / "logs", private_files=()
    ).clear()
    assert await database.load_tasks() == []
    assert {item.option_id for item in await platform.list_audiences()} == remote_ids
