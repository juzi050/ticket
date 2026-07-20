from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from app.config import MonitorTask
from app.models import LockOrderRequest, LockOrderResult, MatchResult, TicketInfo


class TicketPlatform(ABC):
    name: str
    display_name: str

    @abstractmethod
    async def initialize(self) -> None:
        """初始化浏览器、HTTP 客户端和平台资源。"""

    @abstractmethod
    async def check_login_status(self) -> bool:
        """真实验证当前登录状态。"""

    @abstractmethod
    async def open_login_page(self) -> None:
        """打开官方登录入口并等待人工登录。"""

    @abstractmethod
    async def search_event(self, task: MonitorTask) -> Any:
        """定位演出。"""

    @abstractmethod
    async def query_tickets(self, task: MonitorTask) -> Sequence[TicketInfo]:
        """查询当前票务列表。"""

    @abstractmethod
    async def match_ticket(self, task: MonitorTask, tickets: Sequence[TicketInfo]) -> MatchResult:
        """匹配最合适的票。"""

    async def revalidate_ticket(self, task: MonitorTask, ticket: TicketInfo) -> MatchResult:
        """锁单前重新查询，禁止直接信任旧监控数据。"""
        latest = await self.query_tickets(task)
        return await self.match_ticket(task, latest)

    async def on_login_success(self) -> None:
        """登录态保存并复核后执行平台清理，默认保持资源。"""
        return None

    @abstractmethod
    async def lock_order(self, task: MonitorTask, request: LockOrderRequest) -> LockOrderResult:
        """进入平台正常订单确认或锁库存流程，绝不自动付款。"""

    @abstractmethod
    async def close(self) -> None:
        """释放平台资源。"""
