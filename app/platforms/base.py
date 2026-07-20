from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Any

from app.config import MonitorTask, PurchaseProfile
from app.exceptions import AdapterNotImplementedError
from app.models import (
    AudienceCreateRequest,
    LockOrderRequest,
    LockOrderResult,
    MatchResult,
    PlatformAudienceOption,
    TicketInfo,
)


class TicketPlatform(ABC):
    name: str
    display_name: str

    def _ensure_operation_gate(self) -> None:
        if not hasattr(self, "_operation_condition"):
            self._operation_condition = asyncio.Condition()
            self._active_normal_operations = 0
            self._priority_waiters = 0
            self._priority_owner = None

    @asynccontextmanager
    async def normal_operation(self):
        """普通查询共享入口；锁单等待者存在时不再放入新查询。"""
        self._ensure_operation_gate()
        current = asyncio.current_task()
        if self._priority_owner is current:
            yield
            return
        async with self._operation_condition:
            await self._operation_condition.wait_for(
                lambda: self._priority_owner is None and self._priority_waiters == 0
            )
            self._active_normal_operations += 1
        try:
            yield
        finally:
            async with self._operation_condition:
                self._active_normal_operations -= 1
                self._operation_condition.notify_all()

    @asynccontextmanager
    async def priority_operation(self):
        """锁单独占入口；等待现有查询结束并阻止后续查询穿插。"""
        self._ensure_operation_gate()
        current = asyncio.current_task()
        if self._priority_owner is current:
            yield
            return
        async with self._operation_condition:
            self._priority_waiters += 1
            try:
                await self._operation_condition.wait_for(
                    lambda: self._priority_owner is None and self._active_normal_operations == 0
                )
                self._priority_owner = current
            finally:
                self._priority_waiters -= 1
        try:
            yield
        finally:
            async with self._operation_condition:
                self._priority_owner = None
                self._operation_condition.notify_all()

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

    async def list_audiences(self) -> list[PlatformAudienceOption]:
        """实时读取当前平台唯一登录账号中的购票人。"""

        raise AdapterNotImplementedError(f"{self.display_name}购票人列表尚未可靠适配")

    async def open_audience_management(self) -> None:
        """打开平台官方购票人管理页面，供用户人工管理。"""

        raise AdapterNotImplementedError(f"{self.display_name}购票人管理入口尚未可靠适配")

    async def create_audience(
        self, request: AudienceCreateRequest
    ) -> PlatformAudienceOption:
        """通过平台官方页面新增购票人，不持久化请求内容。"""

        raise AdapterNotImplementedError(f"{self.display_name}新增购票人页面尚未可靠适配")

    async def validate_audience_ids(self, audience_ids: list[str]) -> tuple[bool, str]:
        """重新读取平台账号并验证每个稳定引用仍然存在。"""

        if len(set(audience_ids)) != len(audience_ids):
            return False, "不能重复选择同一购票人"
        try:
            options = await self.list_audiences()
        except AdapterNotImplementedError as exc:
            return False, str(exc)
        available = {
            option.option_id: option
            for option in options
            if option.platform == self.name and option.enabled
        }
        missing = [option_id for option_id in audience_ids if option_id not in available]
        if missing:
            return False, "任务中的购票人已被删除、停用或不属于当前平台"
        return True, f"平台账号中存在 {len(audience_ids)} 位指定购票人"

    async def select_order_audiences(
        self, page: Any, audience_ids: list[str], quantity: int
    ) -> tuple[bool, str]:
        """在订单确认页按稳定 ID 精确选人；未适配时禁止猜测。"""

        return False, f"{self.display_name}订单页尚未验证稳定购票人 ID，已暂停人工处理"

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
