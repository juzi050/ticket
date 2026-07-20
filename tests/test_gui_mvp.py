from __future__ import annotations

import asyncio
import logging
import queue
import tkinter as tk
from pathlib import Path

import pytest
from app.config import ApplicationSettings, MonitorSettings, Settings
from app.database import Database
from app.gui.application import TicketMonitorApplication
from app.gui.async_runner import AsyncRunner
from app.gui.controller import GuiController
from app.gui.ui_events import UiEvent
from app.logger import ContextAndSensitiveFilter
from app.platforms.mock import MockPlatform
from app.storage.cache_cleaner import CacheCleaner
from app.storage.task_store import TaskStore


def test_gui_can_initialize_without_display() -> None:
    app = TicketMonitorApplication(None, None, None, build_ui=False)
    assert app.root is None
    assert app.rows == []


def test_real_tk_widgets_can_be_constructed(tmp_path: Path) -> None:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"当前环境没有桌面显示：{exc}")
    root.withdraw()
    runner = AsyncRunner()
    settings = Settings(
        application=ApplicationSettings(database_path=tmp_path / "tk.db", mock_mode=True),
        tasks=[],
    )
    database = Database(settings.application.database_path)
    runner.submit(database.initialize()).result(timeout=5)
    controller = GuiController(settings, database, queue.Queue[UiEvent](), mock_mode=True)
    app = TicketMonitorApplication(root, controller, runner)
    root.update_idletasks()
    assert root.title() == "票务值守台 · Ticket Monitor"
    app.close()


def test_each_platform_registry_reuses_one_account_session(
    sample_task: object, purchase_profile: object, tmp_path: Path
) -> None:
    settings = Settings(
        application=ApplicationSettings(database_path=tmp_path / "single-session.db", mock_mode=True),
        purchase_profiles=[purchase_profile],  # type: ignore[list-item]
        tasks=[sample_task],  # type: ignore[list-item]
    )
    database = Database(settings.application.database_path)
    controller = GuiController(settings, database, queue.Queue(), mock_mode=True)
    assert controller.registry.get("piaoniu") is controller.registry.get("piaoniu")
    assert controller.registry.get("motianlun") is controller.registry.get("motianlun")
    assert controller.registry.get("piaoniu") is not controller.registry.get("motianlun")


async def test_multiple_tasks_are_saved_without_overwriting(
    sample_task: object, tmp_path: Path
) -> None:
    database = Database(tmp_path / "tasks.db")
    await database.initialize()
    store = TaskStore(database)
    first = sample_task.model_copy(update={"task_id": "piaoniu_a", "task_name": "票牛任务 A"})  # type: ignore[attr-defined]
    second = sample_task.model_copy(update={"task_id": "piaoniu_b", "task_name": "票牛任务 B"})  # type: ignore[attr-defined]
    third = sample_task.model_copy(  # type: ignore[attr-defined]
        update={"task_id": "motianlun_a", "task_name": "摩天轮任务 A", "platform": "motianlun"}
    )
    for task in (first, second, third):
        await store.save(task)
    restored = await store.list()
    assert {task.task_id for task in restored} == {"piaoniu_a", "piaoniu_b", "motianlun_a"}
    assert (await store.get("piaoniu_a")).task_name == "票牛任务 A"  # type: ignore[union-attr]


async def test_task_copy_keeps_original_and_disables_auto_lock(
    sample_task: object, tmp_path: Path
) -> None:
    database = Database(tmp_path / "copy.db")
    await database.initialize()
    store = TaskStore(database)
    await store.save(sample_task)  # type: ignore[arg-type]
    copied = await store.duplicate(sample_task.task_id)  # type: ignore[attr-defined]
    tasks = await store.list()
    assert len(tasks) == 2
    assert copied.task_id != sample_task.task_id  # type: ignore[attr-defined]
    assert not copied.enabled
    assert not copied.auto_lock


async def test_priority_lock_blocks_new_queries_until_lock_flow_finishes() -> None:
    platform = MockPlatform("mock")
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    order: list[str] = []

    async def first_query() -> None:
        async with platform.normal_operation():
            order.append("query-1")
            first_entered.set()
            await release_first.wait()

    async def lock_flow() -> None:
        async with platform.priority_operation():
            order.append("lock")
            await asyncio.sleep(0.01)

    async def second_query() -> None:
        async with platform.normal_operation():
            order.append("query-2")

    first = asyncio.create_task(first_query())
    await first_entered.wait()
    priority = asyncio.create_task(lock_flow())
    await asyncio.sleep(0)
    second = asyncio.create_task(second_query())
    release_first.set()
    await asyncio.gather(first, priority, second)
    assert order == ["query-1", "lock", "query-2"]


async def test_gui_controller_runs_multiple_tasks_and_shutdown_preserves_enabled(
    sample_task: object, purchase_profile: object, tmp_path: Path
) -> None:
    first = sample_task.model_copy(  # type: ignore[attr-defined]
        update={
            "task_id": "gui_a",
            "task_name": "GUI A",
            "auto_lock": False,
            "interval_seconds": 1,
            "random_delay_min_seconds": 0,
            "random_delay_max_seconds": 0,
        }
    )
    second = first.model_copy(update={"task_id": "gui_b", "task_name": "GUI B"})
    settings = Settings(
        application=ApplicationSettings(database_path=tmp_path / "gui.db", mock_mode=True),
        monitor=MonitorSettings(random_delay_min_seconds=0, random_delay_max_seconds=0),
        purchase_profiles=[purchase_profile],  # type: ignore[list-item]
        tasks=[first, second],
    )
    database = Database(settings.application.database_path)
    await database.initialize()
    controller = GuiController(settings, database, queue.Queue[UiEvent](), mock_mode=True)
    await controller.task_store.save(first)
    await controller.task_store.save(second)
    await controller.start_task(first.task_id)
    await controller.start_task(second.task_id)
    await asyncio.sleep(0.08)
    assert set(controller.running) == {"gui_a", "gui_b"}
    assert controller.registry.get("mock") is controller.registry.get("mock")
    await controller.shutdown()
    assert (await controller.task_store.get("gui_a")).enabled  # type: ignore[union-attr]
    assert (await controller.task_store.get("gui_b")).enabled  # type: ignore[union-attr]


async def test_cache_clear_removes_tasks_login_files_cache_and_private_config(
    sample_task: object, tmp_path: Path
) -> None:
    data_dir = tmp_path / "data"
    database = Database(data_dir / "ticket_monitor.db")
    await database.initialize()
    await TaskStore(database).save(sample_task)  # type: ignore[arg-type]
    platform = MockPlatform("mock")
    await database.save_ticket_cache("test_001", platform._ticket(sample_task, good=True))  # type: ignore[arg-type]
    for relative in (
        Path("browser_states") / "piaoniu_state.json",
        Path("browser_profiles") / "piaoniu" / "profile.dat",
        Path("cache") / "piaoniu_ticket_cache.json",
    ):
        target = data_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("private", encoding="utf-8")
    env_file = tmp_path / ".env"
    profile_file = tmp_path / "purchase_profiles.yaml"
    env_file.write_text("TOKEN=secret", encoding="utf-8")
    profile_file.write_text("profiles: []", encoding="utf-8")
    await CacheCleaner(
        database, data_dir, private_files=(env_file, profile_file)
    ).clear()
    assert await database.load_tasks() == []
    assert await database.list_ticket_cache() == []
    assert not env_file.exists()
    assert not profile_file.exists()
    for name in ("browser_states", "browser_profiles", "cache"):
        assert (data_dir / name).is_dir()
        assert list((data_dir / name).iterdir()) == []


def test_cancel_cache_clear_does_not_confirm() -> None:
    called = False

    def cancel(_title: str, _message: str) -> bool:
        nonlocal called
        called = True
        return False

    assert not TicketMonitorApplication.confirm_cache_clear(cancel)
    assert called


def test_business_log_is_complete_but_credentials_are_filtered() -> None:
    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        "任务 piaoniu_001 演出链接=https://www.piaoniu.com/activity/1 场次=晚场 "
        "票档=1280 区域=内场A区 数量=2 价格=1200 password=abc cookie=xyz "
        "token=secret Authorization=Bearer-credential card_number=6222020000000000",
        (),
        None,
    )
    ContextAndSensitiveFilter().filter(record)
    message = record.getMessage()
    for value in ("piaoniu_001", "https://www.piaoniu.com/activity/1", "晚场", "1280", "内场A区", "数量=2", "价格=1200"):
        assert value in message
    for secret in ("abc", "xyz", "secret", "Bearer-credential", "6222020000000000"):
        assert secret not in message


def test_async_runner_stops_without_background_thread() -> None:
    runner = AsyncRunner()

    async def value() -> int:
        await asyncio.sleep(0)
        return 7

    assert runner.submit(value()).result(timeout=3) == 7
    runner.stop()
    assert not runner._thread.is_alive()
