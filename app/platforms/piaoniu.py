from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.config import BrowserSettings, MonitorTask, PlatformAutomationSettings
from app.exceptions import AdapterNotImplementedError
from app.models import LockOrderRequest, LockOrderResult, MatchResult, TicketInfo
from app.platforms.base import TicketPlatform
from app.services.session_service import BrowserSessionService
from app.services.ticket_matcher import TicketMatcher


class PiaoniuPlatform(TicketPlatform):
    name = "piaoniu"
    display_name = "票牛"

    def __init__(
        self, browser: BrowserSettings, automation: PlatformAutomationSettings | None = None
    ) -> None:
        # “登录/退出登录”文字来自官方首页；页面改版后应在 config.yaml 覆盖选择器。
        rules = automation or PlatformAutomationSettings(
            home_url="https://www.piaoniu.com/",
            login_trigger_text="登录",
            authenticated_selectors=["text=退出登录"],
            unauthenticated_selectors=["text=登录"],
        )
        self.session = BrowserSessionService(self.name, browser, rules)
        self.matcher = TicketMatcher()

    async def initialize(self) -> None:
        await self.session.initialize()

    async def check_login_status(self) -> bool:
        return await self.session.check_login_status()

    async def open_login_page(self) -> None:
        await self.session.open_login_page()

    async def on_login_success(self) -> None:
        if self.session.settings.close_after_login:
            await self.session.close()

    async def search_event(self, task: MonitorTask) -> Any:
        return {"event_id": task.event_id, "url": task.event_url}

    async def query_tickets(self, task: MonitorTask) -> Sequence[TicketInfo]:
        raise AdapterNotImplementedError(
            "票牛真实票务列表的页面结构/公开接口尚未确认；请提供脱敏页面快照或稳定选择器后适配"
        )

    async def match_ticket(self, task: MonitorTask, tickets: Sequence[TicketInfo]) -> MatchResult:
        return self.matcher.find_best(task, tickets)

    async def lock_order(self, task: MonitorTask, request: LockOrderRequest) -> LockOrderResult:
        raise AdapterNotImplementedError(
            "票牛真实订单确认流程尚未适配，系统不会猜测选择器或调用未知内部接口"
        )

    async def close(self) -> None:
        await self.session.close()
