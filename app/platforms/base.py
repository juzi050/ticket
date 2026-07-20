from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from app.config import MonitorTask, PurchaseProfile
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

    async def preflight_tickets(self, task: MonitorTask) -> Sequence[TicketInfo]:
        """预检所用的只读票品；真实平台默认执行一次正常查询。"""
        return await self.query_tickets(task)

    async def validate_purchase_profile(
        self, profile: PurchaseProfile, quantity: int
    ) -> tuple[bool | None, str]:
        """验证平台已保存资料；None 表示真实页面选择器尚未确认。"""
        return None, "平台已保存资料的页面选择器尚未验证"

    async def has_pending_order(
        self, task: MonitorTask, ticket: TicketInfo, account_alias: str
    ) -> bool | None:
        """检查平台订单列表；None 表示无法可靠判定。"""
        return None

    @abstractmethod
    async def match_ticket(self, task: MonitorTask, tickets: Sequence[TicketInfo]) -> MatchResult:
        """匹配最合适的票。"""

    async def revalidate_ticket(self, task: MonitorTask, ticket: TicketInfo) -> MatchResult:
        """锁单前重新查询，并只接受同一场次、同一稳定票品。"""
        latest = await self.query_tickets(task)
        if not ticket.listing_id:
            return MatchResult(False, ["原票品缺少稳定 listing_id，禁止相似替换"])
        exact = [
            item
            for item in latest
            if item.session_id == ticket.session_id and item.listing_id == ticket.listing_id
        ]
        if not exact:
            return MatchResult(False, ["原票品已消失，禁止自动替换为相似票品"])
        return await self.match_ticket(task, exact)

    async def on_login_success(self) -> None:
        """登录态保存并复核后执行平台清理，默认保持资源。"""
        return None

    @abstractmethod
    async def lock_order(self, task: MonitorTask, request: LockOrderRequest) -> LockOrderResult:
        """进入平台正常订单确认或锁库存流程，绝不自动付款。"""

    @abstractmethod
    async def close(self) -> None:
        """释放平台资源。"""
