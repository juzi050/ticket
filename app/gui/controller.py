from __future__ import annotations

import asyncio
import logging
import queue
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.config import MonitorTask, Settings, load_settings
from app.database import Database
from app.gui.ui_events import UiEvent
from app.models import (
    AudienceCreateRequest,
    MatchResult,
    NotificationMessage,
    PlatformAudienceOption,
    TicketInfo,
)
from app.notifier import ConsoleNotifier, build_notifier
from app.scheduler import PlatformRegistry
from app.services.login_service import LoginService
from app.services.monitor_service import MonitorService
from app.services.notification_service import NotificationService
from app.services.order_service import OrderService
from app.services.preflight_service import PreflightService
from app.storage.cache_cleaner import CacheCleaner
from app.storage.task_store import TaskStore


class GuiController:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        events: queue.Queue[UiEvent],
        *,
        mock_mode: bool,
    ) -> None:
        self.settings = settings
        self.database = database
        self.events = events
        self.mock_mode = mock_mode
        self.task_store = TaskStore(database)
        self.running: dict[str, asyncio.Task[None]] = {}
        # 购票人列表只保存在当前进程内存，不写入数据库或配置文件。
        self.audience_cache: dict[str, list[PlatformAudienceOption]] = {}
        self.platform_status = {"piaoniu": "未检查", "motianlun": "未检查"}
        self.logger = logging.getLogger("app.gui.controller")
        self._build_services()

    def _build_services(self) -> None:
        notifier = build_notifier(self.settings.notification, mock_mode=self.mock_mode)
        self.notifications = NotificationService(notifier, self.database, self.settings.notification)
        self.login = LoginService(
            self.settings.login, self.notifications, self._on_login_state_changed
        )
        self.registry = PlatformRegistry(self.settings)
        self.order = OrderService(
            self.database,
            self.settings.monitor.lock_cooldown_seconds,
            self.settings.purchase_profiles,
            self.settings.strict_lock.stage_timeout_seconds,
            self.settings.strict_lock.max_price_slippage,
        )
        self.monitor = MonitorService(
            self.database, self.login, self.order, self.notifications, self.settings.monitor
        )
        self.preflight = PreflightService(self.settings, self.database, self.notifications)

    async def _on_login_state_changed(self, platform_name: str, logged_in: bool) -> None:
        status = "已登录" if logged_in else "登录已失效"
        self.platform_status[platform_name] = status
        await self.database.save_platform_session(platform_name, status)
        if not logged_in:
            for task in await self.task_store.list():
                if task.platform == platform_name and task.enabled:
                    await self.database.update_task_snapshot(
                        task.task_id, status="正在检查登录", error="登录状态失效"
                    )
        self.emit("platform_status", platform=platform_name, status=status)
        self.emit("refresh")

    @classmethod
    async def create(
        cls,
        config: Path,
        events: queue.Queue[UiEvent],
        *,
        mock_mode: bool = False,
    ) -> "GuiController":
        config_exists = config.exists()
        settings = load_settings(config, allow_example=True)
        if not config_exists:
            settings.purchase_profiles_file = (
                config.parent / "purchase_profiles.yaml"
            ).resolve()
        if mock_mode:
            settings.application.mock_mode = True
            settings.application.database_path = Path("data/ticket_monitor_gui_mock.db")
            settings.notification.enabled = True
            settings.notification.provider = "console"
            settings.login.check_interval_seconds = 0.05
            settings.login.retry_interval_seconds = 1
            settings.monitor.random_delay_min_seconds = 0
            settings.monitor.random_delay_max_seconds = 0
            for profile in settings.purchase_profiles:
                profile.account_alias = f"{profile.account_alias}-gui-{uuid4().hex[:6]}"
        elif not config_exists:
            settings.tasks = []
            settings.purchase_profiles = []

        database = Database(settings.application.database_path)
        await database.initialize()
        stored = await database.load_tasks()
        if stored:
            settings.tasks = stored
        elif (config_exists or mock_mode) and await database.get_metadata("tasks_initialized") != "1":
            for task in settings.tasks:
                if mock_mode:
                    task.enabled = True
                    task.interval_seconds = 1
                await database.upsert_task(task, "pending" if task.enabled else "paused")
            await database.set_metadata("tasks_initialized", "1")
        else:
            settings.tasks = []
        return cls(settings, database, events, mock_mode=mock_mode)

    def emit(self, event_type: str, message: str = "", **payload: object) -> None:
        self.events.put(UiEvent(event_type, message, dict(payload)))

    async def startup(self) -> None:
        for name in ("piaoniu", "motianlun"):
            platform = self.registry.get(name)
            try:
                await platform.initialize()
                logged_in = await platform.check_login_status()
                self.platform_status[name] = "已登录" if logged_in else "登录已失效"
            except Exception as exc:
                self.platform_status[name] = "异常"
                self.logger.exception("%s 启动恢复失败：%s", name, exc)
            await self.database.save_platform_session(name, self.platform_status[name])
            self.emit("platform_status", platform=name, status=self.platform_status[name])

        enabled = [task for task in await self.task_store.list() if task.enabled]
        for task in enabled:
            if self.platform_status.get(task.platform) != "已登录":
                await self.login_platform(task.platform)
            if self.platform_status.get(task.platform) == "已登录":
                await self.start_task(task.task_id)
        self.emit("refresh")

    async def login_platform(self, platform_name: str) -> bool:
        platform = self.registry.get(platform_name)
        self.platform_status[platform_name] = "正在登录"
        self.emit("platform_status", platform=platform_name, status="正在登录")
        await platform.initialize()
        success = await self.login.ensure_logged_in(platform, notify=False)
        self.platform_status[platform_name] = "已登录" if success else "登录已失效"
        await self.database.save_platform_session(
            platform_name, self.platform_status[platform_name]
        )
        self.emit(
            "platform_status", platform=platform_name, status=self.platform_status[platform_name]
        )
        if success:
            for task in await self.task_store.list():
                if task.platform == platform_name and task.enabled and task.task_id not in self.running:
                    await self.start_task(task.task_id)
        return success

    async def open_platform_home(self, platform_name: str) -> None:
        platform = self.registry.get(platform_name)
        await platform.initialize()
        session = getattr(platform, "session", None)
        if session is None:
            return
        async with platform.normal_operation():
            page = await session.page()
            await page.bring_to_front()
            await page.goto(session.automation.home_url, wait_until="domcontentloaded")

    async def list_audiences(self, platform_name: str) -> list[PlatformAudienceOption]:
        platform = self.registry.get(platform_name)
        await platform.initialize()
        if not await platform.check_login_status():
            if not await self.login_platform(platform_name):
                raise ValueError(f"{platform.display_name}尚未登录")
        options = await platform.list_audiences()
        self.audience_cache[platform_name] = list(options)
        self.logger.info(
            "刷新平台购票人 platform=%s count=%s labels=%s",
            platform_name,
            len(options),
            [option.display_name for option in options],
        )
        return list(options)

    async def open_audience_management(self, platform_name: str) -> None:
        platform = self.registry.get(platform_name)
        await platform.initialize()
        if not await platform.check_login_status():
            if not await self.login_platform(platform_name):
                raise ValueError(f"{platform.display_name}尚未登录")
        await platform.open_audience_management()

    async def create_audience(
        self, platform_name: str, request: AudienceCreateRequest
    ) -> PlatformAudienceOption:
        platform = self.registry.get(platform_name)
        try:
            await platform.initialize()
            if not await platform.check_login_status():
                if not await self.login_platform(platform_name):
                    raise ValueError(f"{platform.display_name}尚未登录")
            option = await platform.create_audience(request)
            self.audience_cache[platform_name] = await platform.list_audiences()
            self.logger.info(
                "平台购票人新增成功 platform=%s option_id=%s display_name=%s",
                platform_name,
                option.option_id,
                option.display_name,
            )
            return option
        finally:
            # 不记录请求内容；成功、失败或取消都清除内存中的敏感值。
            request.clear_sensitive()

    async def list_tasks(self) -> list[dict[str, object]]:
        tasks = {task.task_id: task for task in await self.task_store.list()}
        states = {row["task_id"]: row for row in await self.database.list_task_states()}
        rows: list[dict[str, object]] = []
        for task_id, task in tasks.items():
            state = states.get(task_id, {})
            future = self.running.get(task_id)
            rows.append(
                {
                    "task": task,
                    **state,
                    "is_running": bool(future and not future.done()),
                }
            )
        return rows

    async def save_task(
        self, task: MonitorTask, *, original_task_id: str | None = None
    ) -> None:
        existing = await self.task_store.get(task.task_id)
        if existing is not None and original_task_id != task.task_id:
            raise ValueError(f"任务 ID 已存在：{task.task_id}")
        if original_task_id and original_task_id != task.task_id:
            raise ValueError("编辑任务时不能修改 task_id")
        if task.platform_audience_ids:
            platform = self.registry.get(task.platform)
            await platform.initialize()
            valid, message = await platform.validate_audience_ids(
                task.platform_audience_ids
            )
            if not valid:
                raise ValueError(message)
        await self._cancel_running(task.task_id)
        await self.task_store.save(task)
        self.settings.tasks = [item for item in self.settings.tasks if item.task_id != task.task_id]
        self.settings.tasks.append(task)
        self.logger.info(
            "保存任务 task_id=%s task_name=%s platform=%s event_url=%s event=%s session=%s "
            "session_id=%s event_date=%s event_time=%s ticket=%s ticket_id=%s area=%s listing_id=%s quantity=%s "
            "adjacent=%s max_unit=%s max_total=%s interval=%s",
            task.task_id,
            task.task_name,
            task.platform,
            task.event_url,
            task.event_name,
            task.target_sessions,
            task.target_session_id,
            task.event_date,
            task.event_time,
            task.target_ticket_levels,
            task.target_ticket_level_id or task.target_ticket_group_id,
            task.target_areas,
            task.target_listing_id,
            task.quantity,
            task.adjacent_seats_required,
            task.max_unit_price,
            task.max_total_price,
            task.interval_seconds,
        )
        self.logger.info(
            "任务指定购票人 task_id=%s labels=%s",
            task.task_id,
            task.platform_audience_labels,
        )
        if task.enabled:
            await self.start_task(task.task_id)
        self.emit("refresh")

    async def duplicate_task(self, task_id: str) -> MonitorTask:
        task = await self.task_store.duplicate(task_id)
        self.settings.tasks.append(task)
        self.emit("refresh")
        return task

    async def delete_task(self, task_id: str) -> None:
        await self.stop_task(task_id, status="已停止")
        await self.task_store.delete(task_id)
        self.settings.tasks = [task for task in self.settings.tasks if task.task_id != task_id]
        self.emit("refresh")

    async def start_task(self, task_id: str) -> None:
        current = self.running.get(task_id)
        if current and not current.done():
            return
        task = await self.task_store.get(task_id)
        if task is None:
            raise ValueError(f"任务不存在：{task_id}")
        task.enabled = True
        await self.task_store.save(task)
        platform = self.registry.get(task.platform)
        await platform.initialize()
        if not await platform.check_login_status():
            await self.database.update_task_snapshot(task_id, status="正在检查登录")
            self.emit("manual", f"{task.task_name} 的平台登录已失效", task_id=task_id)
            await self.login_platform(task.platform)
            if self.platform_status.get(task.platform) != "已登录":
                return
            if task_id in self.running:
                return
        if task.auto_lock:
            result = await self.preflight.run(task, platform)
            if not result.passed:
                message = "；".join(
                    f"{check.name}: {check.message}" for check in result.checks if not check.passed
                )
                await self.database.update_task_snapshot(
                    task_id, status="需要人工处理", error=message
                )
                self.emit("manual", message, task_id=task_id)
                return

        async def run_monitor() -> None:
            try:
                await self.monitor.run_task(task, platform)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.exception("GUI 任务 %s 异常退出：%s", task_id, exc)
            finally:
                self.running.pop(task_id, None)
                self.emit("refresh")

        self.running[task_id] = asyncio.create_task(run_monitor(), name=f"gui-monitor-{task_id}")
        self.emit("refresh")

    async def pause_task(self, task_id: str) -> None:
        await self.stop_task(task_id, status="已暂停")

    async def stop_task(self, task_id: str, *, status: str = "已停止") -> None:
        await self._cancel_running(task_id)
        task = await self.task_store.get(task_id)
        if task:
            task.enabled = False
            await self.task_store.save(task)
            await self.database.update_task_snapshot(task_id, status=status)
        self.emit("refresh")

    async def _cancel_running(self, task_id: str) -> None:
        future = self.running.pop(task_id, None)
        if future:
            future.cancel()
            await asyncio.gather(future, return_exceptions=True)

    async def stop_all(self) -> None:
        for task_id in list(self.running):
            await self.stop_task(task_id)

    async def query_now(self, task_id: str) -> MatchResult:
        task = await self.task_store.get(task_id)
        if task is None:
            raise ValueError(f"任务不存在：{task_id}")
        platform = self.registry.get(task.platform)
        await platform.initialize()
        await self.database.update_task_snapshot(task_id, status="正在查询")
        tickets = list(await platform.query_tickets(task))
        for ticket in tickets:
            await self.database.record_price(task_id, ticket)
        result = await platform.match_ticket(task, tickets)
        ticket = result.ticket or (tickets[0] if tickets else None)
        await self.database.update_task_snapshot(
            task_id,
            status="发现目标票" if result.matched else "未发现目标票",
            query_increment=True,
            last_price=ticket.unit_price if ticket else None,
            available_quantity=ticket.available_quantity if ticket else None,
            ticket_level=ticket.ticket_level if ticket else None,
            area=ticket.area if ticket else None,
            matched=result.matched,
            mismatch_reason=None if result.matched else "、".join(result.reasons),
        )
        self.emit("refresh")
        return result

    async def discover(
        self, platform_name: str, event_url: str, quantity: int
    ) -> list[TicketInfo]:
        platform = self.registry.get(platform_name)
        await platform.initialize()
        task = MonitorTask(
            task_id="gui_discover",
            task_name="演出识别",
            enabled=False,
            platform=platform_name,
            event_name="待识别演出",
            event_url=event_url,
            quantity=quantity,
            max_unit_price=Decimal("99999999"),
            max_total_price=Decimal("99999999"),
        )
        tickets = list(await platform.preflight_tickets(task))
        for ticket in tickets:
            self.logger.info(
                "识别票品 platform=%s event_url=%s event=%s event_id=%s session=%s session_id=%s "
                "ticket=%s ticket_group_id=%s listing_id=%s area=%s price=%s quantity=%s adjacent=%s",
                ticket.platform,
                event_url,
                ticket.event_name,
                ticket.event_id,
                ticket.session_name,
                ticket.session_id,
                ticket.ticket_level,
                ticket.ticket_group_id,
                ticket.listing_id,
                ticket.area,
                ticket.unit_price,
                ticket.available_quantity,
                ticket.adjacent,
            )
        return tickets

    async def clear_cache(self) -> None:
        await self.stop_all()
        await self.registry.close()
        await self.notifications.close()
        # Windows 下先关闭日志文件句柄；FileHandler 下一次写入时会自动重开新文件。
        for handler in logging.getLogger().handlers:
            if isinstance(handler, logging.FileHandler):
                handler.flush()
                handler.close()
        profile_file = self.settings.purchase_profiles_file
        if ".example." in profile_file.name:
            profile_file = Path("purchase_profiles.yaml").resolve()
        await CacheCleaner(
            self.database,
            private_files=(Path(".env"), profile_file),
        ).clear()
        self.settings.tasks = []
        self.settings.purchase_profiles = []
        self.audience_cache.clear()
        self.settings.notification.enabled = False
        self.settings.notification.provider = "console"
        self.running.clear()
        self.platform_status = {"piaoniu": "未登录", "motianlun": "未登录"}
        self._build_services()
        self.emit("cleared", "缓存已清理")

    async def shutdown(self) -> None:
        futures = list(self.running.values())
        self.running.clear()
        for future in futures:
            future.cancel()
        if futures:
            await asyncio.gather(*futures, return_exceptions=True)
        await self.registry.close()
        await self.notifications.close()
