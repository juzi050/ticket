from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import typer
import yaml
from rich.console import Console
from rich.table import Table

from app.config import ConfigurationError, MonitorTask, Settings, load_settings
from app.database import Database
from app.logger import setup_logging
from app.models import NotificationMessage
from app.notifier import build_notifier
from app.scheduler import PlatformRegistry, Scheduler
from app.services.login_service import LoginService
from app.services.monitor_service import MonitorService
from app.services.notification_service import NotificationService
from app.services.order_service import OrderService
from app.services.preflight_service import PreflightService

app = typer.Typer(help="票务价格监控与锁单辅助系统", no_args_is_help=True, add_completion=False)
console = Console()


async def _discover_tickets(
    runtime: "Runtime", platform_name: str, event_url: str, quantity: int = 1
):
    platform = runtime.registry.get(platform_name)
    await platform.initialize()
    task = MonitorTask(
        task_id="discover",
        enabled=False,
        platform=platform_name,
        event_name="待发现演出",
        event_url=event_url,
        target_sessions=[],
        target_ticket_levels=[],
        quantity=quantity,
        max_unit_price=Decimal("99999999"),
        max_total_price=Decimal("99999999"),
    )
    return list(await platform.preflight_tickets(task))


@dataclass(slots=True)
class Runtime:
    settings: Settings
    database: Database
    notifications: NotificationService
    login: LoginService
    registry: PlatformRegistry
    scheduler: Scheduler
    preflight: PreflightService

    async def close(self) -> None:
        await self.registry.close()
        await self.notifications.close()


async def _runtime(
    config: Path, *, mock_mode: bool = False, allow_example: bool = False
) -> Runtime:
    settings = load_settings(config, allow_example=mock_mode or allow_example)
    if mock_mode:
        settings.application.mock_mode = True
        settings.notification.provider = "console"
        settings.notification.enabled = True
        settings.login.check_interval_seconds = 0.01
        settings.login.retry_interval_seconds = 1
        settings.monitor.random_delay_min_seconds = 0
        settings.monitor.random_delay_max_seconds = 0
        run_alias = uuid4().hex[:8]
        for profile in settings.purchase_profiles:
            profile.account_alias = f"{profile.account_alias}-{run_alias}"
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
    order = OrderService(
        database,
        settings.monitor.lock_cooldown_seconds,
        settings.purchase_profiles,
        settings.strict_lock.stage_timeout_seconds,
        settings.strict_lock.max_price_slippage,
    )
    monitor = MonitorService(database, login, order, notifications, settings.monitor)
    preflight = PreflightService(settings, database, notifications)
    scheduler = Scheduler(settings, database, registry, monitor, preflight)
    return Runtime(settings, database, notifications, login, registry, scheduler, preflight)


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


@app.command()
def preflight(
    task_id: str = typer.Option(..., "--task-id", help="要预检的任务编号"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """执行自动锁单启动前的全部确定性校验。"""

    async def execute() -> bool:
        runtime = await _runtime(config)
        try:
            task = next((item for item in runtime.settings.tasks if item.task_id == task_id), None)
            if task is None:
                raise ValueError(f"任务不存在：{task_id}")
            platform = runtime.registry.get(task.platform)
            await platform.initialize()
            result = await runtime.preflight.run(task, platform)
            table = Table("结果", "检查项", "说明")
            for check in result.checks:
                table.add_row("通过" if check.passed else "失败", check.name, check.message)
            console.print(table)
            return result.passed
        finally:
            await runtime.close()

    try:
        passed = asyncio.run(execute())
    except (ConfigurationError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    if not passed:
        console.print("[red]预检未通过，auto_lock 任务不会启动。[/red]")
        raise typer.Exit(2)
    console.print("[green]预检全部通过。[/green]")


@app.command()
def discover(
    platform: str = typer.Option(..., "--platform", help="piaoniu 或 motianlun"),
    url: str = typer.Option(..., "--url", help="官方演出详情页 URL"),
    quantity: int = typer.Option(1, "--quantity", min=1, help="只展示可精确选择该数量的票品"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """只读发现演出、场次、票档与当前稳定票品 ID。"""
    if platform not in {"piaoniu", "motianlun", "mock"}:
        raise typer.BadParameter("platform 必须是 piaoniu、motianlun 或 mock")

    async def execute():
        runtime = await _runtime(config, allow_example=True)
        try:
            return await _discover_tickets(runtime, platform, url, quantity)
        finally:
            await runtime.close()

    try:
        tickets = asyncio.run(execute())
    except (ConfigurationError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    table = Table(
        "演出ID", "场次 / ID", "票档", "listing_id", "ticket_group_id",
        "区域/座位", "seller_id", "单价",
    )
    for ticket in tickets:
        table.add_row(
            ticket.event_id,
            f"{ticket.session_name}\n{ticket.session_id}",
            ticket.ticket_level,
            ticket.listing_id,
            ticket.ticket_group_id or "-",
            " / ".join(filter(None, [ticket.area, ticket.seat])) or "-",
            ticket.seller_id or "-",
            str(ticket.unit_price),
        )
    console.print(table)
    if not tickets:
        console.print("[yellow]没有发现可稳定定位的当前票品。[/yellow]")


@app.command("create-task")
def create_task(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    """交互发现并选择真实 ID，向现有配置追加一个任务。"""
    if not config.exists():
        console.print("[red]配置文件不存在，请先复制 config.example.yaml 为 config.yaml。[/red]")
        raise typer.Exit(2)
    platform = typer.prompt("平台（piaoniu/motianlun）")
    if platform not in {"piaoniu", "motianlun"}:
        raise typer.BadParameter("平台必须是 piaoniu 或 motianlun")
    event_url = typer.prompt("官方演出详情页 URL")
    quantity = typer.prompt("购买数量", type=int)
    if quantity < 1:
        raise typer.BadParameter("购买数量必须大于 0")

    async def execute():
        runtime = await _runtime(config)
        try:
            return runtime.settings, await _discover_tickets(
                runtime, platform, event_url, quantity
            )
        finally:
            await runtime.close()

    try:
        settings, tickets = asyncio.run(execute())
    except (ConfigurationError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    if not tickets:
        console.print("[red]没有发现可用票品，未修改配置。[/red]")
        raise typer.Exit(2)

    sessions: list[tuple[str, str]] = []
    for ticket in tickets:
        value = (ticket.session_name, ticket.session_id)
        if value not in sessions:
            sessions.append(value)
    for index, value in enumerate(sessions, 1):
        console.print(f"{index}. {value[0]} ({value[1]})")
    session_index = typer.prompt("选择场次序号", type=int)
    if session_index < 1 or session_index > len(sessions):
        raise typer.BadParameter("场次序号无效")
    chosen_session = sessions[session_index - 1]
    listings = [ticket for ticket in tickets if ticket.session_id == chosen_session[1]]
    for index, ticket in enumerate(listings, 1):
        console.print(
            f"{index}. {ticket.ticket_level} / {ticket.area or '-'} / {ticket.unit_price} / "
            f"{ticket.ticket_group_id or ticket.listing_id}"
        )
    listing_index = typer.prompt("选择票品序号", type=int)
    if listing_index < 1 or listing_index > len(listings):
        raise typer.BadParameter("票品序号无效")
    chosen = listings[listing_index - 1]
    profile_ids = [profile.profile_id for profile in settings.purchase_profiles]
    if not profile_ids:
        console.print("[red]私有购票档案为空，未修改配置。[/red]")
        raise typer.Exit(2)
    for index, profile_id in enumerate(profile_ids, 1):
        console.print(f"{index}. {profile_id}")
    profile_index = typer.prompt("选择购票档案序号", type=int)
    if profile_index < 1 or profile_index > len(profile_ids):
        raise typer.BadParameter("购票档案序号无效")

    raw = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    tasks = raw.setdefault("tasks", [])
    task_id = typer.prompt("任务编号")
    tasks.append(
        {
            "task_id": task_id,
            "enabled": True,
            "platform": platform,
            "event_name": chosen.event_name,
            "event_url": event_url,
            "event_id": chosen.event_id,
            "target_session_id": chosen.session_id,
            "target_listing_id": chosen.listing_id,
            "target_ticket_group_id": chosen.ticket_group_id,
            "target_sessions": [chosen.session_name],
            "target_ticket_levels": [chosen.ticket_level],
            "target_areas": [chosen.area] if chosen.area else [],
            "quantity": quantity,
            "max_unit_price": str(chosen.unit_price),
            "max_total_price": str(chosen.unit_price * quantity),
            "auto_lock": False,
            "purchase_profile_id": profile_ids[profile_index - 1],
        }
    )
    config.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    console.print(f"[green]已追加任务 {task_id}。请核对价格上限并通过 preflight 后再启用 auto_lock。[/green]")


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
