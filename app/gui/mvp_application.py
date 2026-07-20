from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import httpx

from app.auth import AuthSessionBridge, LoginOptions, PlaywrightLoginService
from app.domain import AuthSession, MonitorTask, PlatformName
from app.gui.async_runner import AsyncRunner
from app.gui.audit_panel import AuditPanel
from app.gui.buyer_panel import BuyerManagerFrame
from app.gui.login_panel import LoginPanel
from app.gui.mvp_task_editor import MvpTaskEditor
from app.gui.mvp_task_list import MvpTaskList
from app.monitor_scheduler import MonitorScheduler
from app.notifications import ServerChanNotifier
from app.platforms.http_api import TicketPlatformApi
from app.platforms.motianlun_api import MotianlunApi
from app.platforms.piaoniu_api import PiaoniuApi
from app.services.price_monitor_service import PriceMonitorService
from app.settings import AppSettings
from app.storage.audit_repository import AuditEntry, AuditRepository
from app.storage.buyer_repository import BuyerRepository
from app.storage.database import MvpDatabase
from app.storage.order_repository import OrderRepository
from app.storage.session_repository import PlatformSessionRepository
from app.storage.task_repository import TaskRepository


API_TYPES = {"piaoniu": PiaoniuApi, "motianlun": MotianlunApi}


class MvpApplication:
    def __init__(self, root: tk.Tk, settings: AppSettings | None = None) -> None:
        self.root = root
        self.settings = settings or AppSettings.load()
        self.runner = AsyncRunner()
        self.database = MvpDatabase(self.settings.database_path)
        self.runner.submit(self.database.initialize()).result(timeout=15)
        self.audit = AuditRepository(self.database)
        self.buyers = BuyerRepository(self.database)
        self.tasks = TaskRepository(self.database)
        self.orders = OrderRepository(self.database)
        self.sessions = PlatformSessionRepository(self.database)
        self.bridge = AuthSessionBridge()
        self.apis = self.runner.submit(self._create_apis()).result(timeout=15)
        self.login_service = PlaywrightLoginService(
            self.sessions,
            self.audit,
            options=LoginOptions(browser_channel=self.settings.browser_channel),
        )
        self.notifier = ServerChanNotifier(
            self.audit, sendkey=self.settings.serverchan_sendkey
        )
        self.monitor = PriceMonitorService(self.apis, self.tasks, self.audit)
        self.scheduler = MonitorScheduler(
            self.tasks, self.audit, self.monitor.check_once
        )
        self._configure_root()
        self._build()
        self.runner.submit(self._startup())
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    async def _create_apis(self) -> dict[PlatformName, TicketPlatformApi]:
        result: dict[PlatformName, TicketPlatformApi] = {}
        for platform in ("piaoniu", "motianlun"):
            session = await self.sessions.get(platform)
            client = (
                self.bridge.build_http_client(session)
                if session
                else httpx.AsyncClient(
                    headers={"User-Agent": "Mozilla/5.0"},
                    follow_redirects=True,
                    timeout=20,
                )
            )
            result[platform] = API_TYPES[platform](client, self.audit, self.sessions)
        return result

    def _configure_root(self) -> None:
        self.root.title("票务监控")
        self.root.geometry("1440x860")
        self.root.minsize(1100, 700)
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("PageTitle.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("DialogTitle.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Muted.TLabel", foreground="#667085")

    def _build(self) -> None:
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)
        self.login_panel = LoginPanel(
            self.notebook,
            runner=self.runner,
            session_repository=self.sessions,
            login_callback=self.login,
            clear_callback=self.clear_login,
        )
        self.task_panel = MvpTaskList(
            self.notebook,
            runner=self.runner,
            task_repository=self.tasks,
            order_repository=self.orders,
            audit_repository=self.audit,
            create_callback=self.new_task,
            edit_callback=self.edit_task,
            schedule_callback=self.schedule_task,
            check_callback=self.check_task,
            logs_callback=self.show_task_logs,
        )
        self.buyer_panel = BuyerManagerFrame(
            self.notebook, self.runner, self.buyers, self.audit
        )
        self.audit_panel = AuditPanel(self.notebook, self.runner, self.audit)
        self.notebook.add(self.login_panel, text="平台登录")
        self.notebook.add(self.task_panel, text="监控任务")
        self.notebook.add(self.buyer_panel, text="购票人")
        self.notebook.add(self.audit_panel, text="审计日志")

    async def _startup(self) -> None:
        await self.audit.append(
            AuditEntry(
                level="INFO",
                category="application",
                action="application_started",
                message="票务监控程序已启动",
            )
        )
        await self.scheduler.start()

    def new_task(self) -> None:
        self._open_task_editor(None)

    def edit_task(self, task: MonitorTask) -> None:
        self._open_task_editor(task)

    def _open_task_editor(self, task: MonitorTask | None) -> None:
        MvpTaskEditor(
            self.root,
            runner=self.runner,
            platform_apis=self.apis,
            buyer_repository=self.buyers,
            task_repository=self.tasks,
            audit_repository=self.audit,
            task=task,
            saved_callback=self.tasks_changed,
        )

    def tasks_changed(self) -> None:
        self.task_panel.refresh()
        self.runner.submit(self.scheduler.start())

    def schedule_task(self, task_id: str, enabled: bool) -> None:
        operation = (
            self.scheduler.resume(task_id) if enabled else self.scheduler.pause(task_id)
        )
        self.runner.submit(operation)

    def check_task(self, task_id: str) -> None:
        self.runner.submit(self.scheduler.immediate_check(task_id))

    def show_task_logs(self, task_id: str) -> None:
        self.audit_panel.vars["task_id"].set(task_id)
        self.audit_panel.refresh()
        self.notebook.select(self.audit_panel)

    def login(self, platform: PlatformName):
        async def operation() -> AuthSession:
            tasks = [
                task
                for task in await self.tasks.list()
                if task.ticket.platform == platform
            ]
            landing_url = tasks[0].ticket.event_url if tasks else None

            async def verify(session: AuthSession) -> bool:
                client = self.bridge.build_http_client(session)
                api = API_TYPES[platform](client, self.audit, self.sessions)
                try:
                    return await api.check_auth()
                finally:
                    await api.close()

            session = await self.login_service.login(
                platform, verify, landing_url=landing_url
            )
            await self._replace_api(platform, session)
            return session

        return self.runner.submit(operation())

    def clear_login(self, platform: PlatformName):
        async def operation() -> None:
            await self.login_service.clear_session(platform)
            await self._replace_api(platform, None)

        return self.runner.submit(operation())

    async def _replace_api(
        self, platform: PlatformName, session: AuthSession | None
    ) -> None:
        old = self.apis[platform]
        await old.close()
        client = (
            self.bridge.build_http_client(session)
            if session
            else httpx.AsyncClient(
                headers={"User-Agent": "Mozilla/5.0"},
                follow_redirects=True,
                timeout=20,
            )
        )
        self.apis[platform] = API_TYPES[platform](client, self.audit, self.sessions)

    def close(self) -> None:
        async def shutdown() -> None:
            await self.scheduler.stop()
            await self.audit.append(
                AuditEntry(
                    level="INFO",
                    category="application",
                    action="application_stopped",
                    message="票务监控程序已退出",
                )
            )
            await self.notifier.close()
            for api in self.apis.values():
                await api.close()

        try:
            self.runner.submit(shutdown()).result(timeout=15)
        finally:
            self.runner.stop()
            self.root.destroy()
