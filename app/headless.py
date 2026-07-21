from __future__ import annotations

import asyncio
import logging
import signal

import httpx

from app.auth.session_bridge import AuthSessionBridge
from app.domain import PlatformName
from app.monitor_scheduler import MonitorScheduler
from app.notifications import ServerChanNotifier
from app.platforms.http_api import TicketPlatformApi
from app.platforms.motianlun_api import MotianlunApi
from app.platforms.piaoniu_api import PiaoniuApi
from app.services.order_coordinator import OrderCoordinator
from app.services.price_monitor_service import PriceMonitorService
from app.settings import AppSettings
from app.storage.audit_repository import AuditEntry, AuditRepository
from app.storage.buyer_repository import BuyerBindingRepository, BuyerRepository
from app.storage.database import MvpDatabase
from app.storage.order_repository import OrderRepository
from app.storage.session_repository import PlatformSessionRepository
from app.storage.task_repository import TaskRepository


LOGGER = logging.getLogger(__name__)
API_TYPES = {"piaoniu": PiaoniuApi, "motianlun": MotianlunApi}


class HeadlessApplication:
    """在没有桌面环境的服务器上运行现有监控与下单流程。"""

    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or AppSettings.load()
        self.database = MvpDatabase(self.settings.database_path)
        self.audit = AuditRepository(self.database)
        self.buyers = BuyerRepository(self.database)
        self.buyer_bindings = BuyerBindingRepository(self.database)
        self.tasks = TaskRepository(self.database)
        self.orders = OrderRepository(self.database)
        self.sessions = PlatformSessionRepository(self.database)
        self.bridge = AuthSessionBridge()
        self.apis: dict[PlatformName, TicketPlatformApi] = {}
        self.notifier: ServerChanNotifier | None = None
        self.scheduler: MonitorScheduler | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await self.database.initialize()
        self.apis = await self._create_apis()
        self.notifier = ServerChanNotifier(
            self.audit, sendkey=self.settings.serverchan_sendkey
        )
        order_coordinator = OrderCoordinator(
            self.apis,
            self.buyers,
            self.tasks,
            self.orders,
            self.audit,
            self.notifier,
        )
        monitor = PriceMonitorService(
            self.apis,
            self.tasks,
            self.audit,
            matched_callback=order_coordinator.handle_price_match,
        )
        self.scheduler = MonitorScheduler(self.tasks, self.audit, monitor.check_once)
        await self.audit.append(
            AuditEntry(
                level="INFO",
                category="application",
                action="headless_started",
                message="服务器监控程序已启动",
            )
        )
        await self.scheduler.start()
        self._started = True
        LOGGER.info("服务器监控程序已启动")

    async def close(self) -> None:
        if self.scheduler is not None:
            await self.scheduler.stop()
        if self._started:
            await self.audit.append(
                AuditEntry(
                    level="INFO",
                    category="application",
                    action="headless_stopped",
                    message="服务器监控程序已停止",
                )
            )
        if self.notifier is not None:
            await self.notifier.close()
        for api in self.apis.values():
            await api.close()
        self._started = False
        LOGGER.info("服务器监控程序已停止")

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
            result[platform] = API_TYPES[platform](
                client, self.audit, self.sessions, self.buyer_bindings
            )
        return result


async def run() -> None:
    application = HeadlessApplication()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for handled_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(handled_signal, stopped.set)
        except NotImplementedError:
            signal.signal(
                handled_signal,
                lambda *_: loop.call_soon_threadsafe(stopped.set),
            )
    try:
        await application.start()
        await stopped.wait()
    finally:
        await application.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
