from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from app.config import ConfigurationError, Settings, load_settings
from app.database import Database
from app.logger import setup_logging
from app.models import NotificationMessage
from app.notifier import build_notifier
from app.scheduler import PlatformRegistry, Scheduler
from app.services.login_service import LoginService
from app.services.monitor_service import MonitorService
from app.services.notification_service import NotificationService
from app.services.order_service import OrderService

app = typer.Typer(help="票务价格监控与锁单辅助系统", no_args_is_help=True, add_completion=False)
console = Console()


@dataclass(slots=True)
class Runtime:
    settings: Settings
    database: Database
    notifications: NotificationService
    login: LoginService
    registry: PlatformRegistry
    scheduler: Scheduler

    async def close(self) -> None:
        await self.registry.close()
        await self.notifications.close()


async def _runtime(config: Path, *, mock_mode: bool = False) -> Runtime:
    settings = load_settings(config, allow_example=mock_mode)
    if mock_mode:
        settings.application.mock_mode = True
        settings.notification.provider = "console"
        settings.notification.enabled = True
        settings.login.check_interval_seconds = 0.01
        settings.login.retry_interval_seconds = 1
        settings.monitor.random_delay_min_seconds = 0
        settings.monitor.random_delay_max_seconds = 0
        for task in settings.tasks:
            task.enabled = True
            task.interval_seconds = 0.05
            task.random_delay_min_seconds = 0
            task.random_delay_max_seconds = 0
    setup_logging(settings.application.log_level)
    database = Database(settings.application.database_path)
    await database.initialize()
    notifier = build_notifier(settings.notification, mock_mode=mock_mode)
    notifications = NotificationService(notifier, database, settings.notification)
    login = LoginService(settings.login, notifications)
    registry = PlatformRegistry(settings)
    order = OrderService(database, settings.monitor.lock_cooldown_seconds)
    monitor = MonitorService(database, login, order, notifications, settings.monitor)
    scheduler = Scheduler(settings, database, registry, monitor)
    return Runtime(settings, database, notifications, login, registry, scheduler)


def _load_or_exit(config: Path, *, allow_example: bool = False) -> Settings:
    try:
        return load_settings(config, allow_example=allow_example)
    except ConfigurationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


@app.command()
def run(
    task_id: str | None = typer.Option(None, "--task-id", help="只运行指定任务"),
    config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径"),
) -> None:
    """运行全部启用任务或一个指定任务。"""

    async def execute() -> None:
        runtime = await _runtime(config)
        try:
            await runtime.scheduler.run(task_id)
        finally:
            await runtime.close()

    try:
        asyncio.run(execute())
    except KeyboardInterrupt:
        console.print("\n[yellow]已收到退出信号，资源已释放。[/yellow]")
    except (ConfigurationError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


@app.command("list")
def list_tasks(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径")
) -> None:
    """列出配置中的全部任务。"""
    settings = _load_or_exit(config)
    table = Table("任务编号", "启用", "平台", "演出", "自动锁单", "间隔(秒)")
    for task in settings.tasks:
        table.add_row(
            task.task_id, "是" if task.enabled else "否", task.platform, task.event_name,
            "是" if task.auto_lock else "否", str(task.interval_seconds or settings.monitor.default_interval_seconds),
        )
    console.print(table)


@app.command("validate-config")
def validate_config(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径")
) -> None:
    """校验配置文件。"""
    settings = _load_or_exit(config)
    console.print(f"[green]配置有效，共 {len(settings.tasks)} 个任务。[/green]")


@app.command("test-notification")
def test_notification(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径")
) -> None:
    """发送一条通知测试。"""

    async def execute() -> bool:
        runtime = await _runtime(config)
        try:
            return await runtime.notifications.send(
                NotificationMessage("test", "票务监控通知测试", "如果你看到此消息，通知渠道配置正常。"),
                force=True,
            )
        finally:
            await runtime.close()

    try:
        success = asyncio.run(execute())
        console.print("[green]通知发送成功。[/green]" if success else "[red]通知发送失败，请查看日志。[/red]")
    except ConfigurationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


def _platform_names(value: str) -> list[str]:
    if value == "all":
        return ["piaoniu", "motianlun"]
    if value not in {"piaoniu", "motianlun", "mock"}:
        raise ValueError("platform 必须是 piaoniu、motianlun、mock 或 all")
    return [value]


@app.command()
def login(
    platform: str = typer.Option(..., "--platform", help="piaoniu、motianlun 或 all"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """打开官方登录入口并等待人工登录。"""

    async def execute() -> None:
        runtime = await _runtime(config)
        try:
            for name in _platform_names(platform):
                adapter = runtime.registry.get(name)
                await adapter.initialize()
                success = await runtime.login.ensure_logged_in(adapter, notify=False)
                console.print(f"{adapter.display_name}：[{'green' if success else 'yellow'}]{'已登录' if success else '等待登录'}[/]")
        finally:
            await runtime.close()

    try:
        asyncio.run(execute())
    except (ConfigurationError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


@app.command("login-status")
def login_status(
    platform: str = typer.Option("all", "--platform", help="piaoniu、motianlun 或 all"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """检查一个或全部平台登录状态。"""

    async def execute() -> None:
        runtime = await _runtime(config)
        table = Table("平台", "状态", "说明", "检查时间")
        try:
            for name in _platform_names(platform):
                adapter = runtime.registry.get(name)
                await adapter.initialize()
                status = await runtime.login.status(adapter)
                table.add_row(adapter.display_name, status.state.value, status.message, status.checked_at.isoformat())
            console.print(table)
        finally:
            await runtime.close()

    try:
        asyncio.run(execute())
    except (ConfigurationError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


@app.command()
def history(
    task_id: str = typer.Option(..., "--task-id"),
    limit: int = typer.Option(20, "--limit", min=1, max=200),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """查看历史价格、匹配及锁单记录。"""

    async def execute() -> dict[str, list[dict[str, Any]]]:
        settings = load_settings(config)
        database = Database(settings.application.database_path)
        await database.initialize()
        return await database.get_history(task_id, limit)

    try:
        records = asyncio.run(execute())
    except ConfigurationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    for group, rows in records.items():
        console.rule(f"{group} ({len(rows)})")
        for row in rows:
            console.print(row)


@app.command()
def mock(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="不存在时自动使用示例配置")
) -> None:
    """运行四轮 Mock 完整演示，然后自动退出。"""

    async def execute() -> None:
        runtime = await _runtime(config, mock_mode=True)
        try:
            await runtime.scheduler.run(max_cycles=4)
        finally:
            await runtime.close()

    try:
        asyncio.run(execute())
        console.print("[green]Mock 演示完成，价格、匹配、通知和锁单记录已写入 SQLite。[/green]")
    except (ConfigurationError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


def _change_task_state(task_id: str, enabled: bool, config: Path) -> None:
    async def execute() -> bool:
        settings = load_settings(config)
        database = Database(settings.application.database_path)
        await database.initialize()
        task = next((item for item in settings.tasks if item.task_id == task_id), None)
        if task is None:
            return False
        await database.upsert_task(task)
        return await database.set_task_enabled(task_id, enabled)

    try:
        changed = asyncio.run(execute())
    except ConfigurationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    if not changed:
        console.print(f"[red]任务不存在：{task_id}[/red]")
        raise typer.Exit(2)
    console.print(f"[green]任务 {task_id} 已{'启用' if enabled else '禁用'}。运行中的调度器会自动应用。[/green]")


@app.command()
def enable(
    task_id: str = typer.Option(..., "--task-id"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """动态启用任务。"""
    _change_task_state(task_id, True, config)


@app.command()
def disable(
    task_id: str = typer.Option(..., "--task-id"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """动态禁用任务。"""
    _change_task_state(task_id, False, config)
