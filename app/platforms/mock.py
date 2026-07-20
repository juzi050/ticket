from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.config import MonitorTask
from app.models import LockOrderRequest, LockOrderResult, LockStatus, MatchResult, TicketInfo
from app.platforms.base import TicketPlatform
from app.services.ticket_matcher import TicketMatcher


class MockPlatform(TicketPlatform):
    display_name = "Mock 票务平台"

    def __init__(self, platform_name: str = "mock") -> None:
        self.name = platform_name
        self.display_name = f"Mock({platform_name})"
        self.logged_in = False
        self.run_id = uuid4().hex[:8]
        self.query_counts: dict[str, int] = defaultdict(int)
        self.matcher = TicketMatcher()

    async def initialize(self) -> None:
        await asyncio.sleep(0)

    async def check_login_status(self) -> bool:
        await asyncio.sleep(0)
        return self.logged_in

    async def open_login_page(self) -> None:
        # 演示人工登录完成，不启动真实浏览器。
        await asyncio.sleep(0.02)
        self.logged_in = True

    async def search_event(self, task: MonitorTask) -> Any:
        return {"event_id": task.event_id or f"mock-{task.task_id}", "url": task.event_url}

    def _ticket(self, task: MonitorTask, *, good: bool) -> TicketInfo:
        quantity = task.quantity if good else max(0, task.quantity - 1)
        unit_price = max(Decimal("1"), task.max_unit_price - Decimal("10"))
        if not good:
            unit_price = task.max_unit_price + Decimal("100")
        total = unit_price * task.quantity
        return TicketInfo(
            platform=task.platform,
            event_id=task.event_id or f"mock-{task.task_id}",
            event_name=task.event_name,
            session_id="mock-session-1",
            session_name=task.target_sessions[0] if task.target_sessions else "Mock 场次",
            ticket_level=task.target_ticket_levels[0] if task.target_ticket_levels else "Mock 票档",
            area=task.target_areas[0] if task.target_areas else "Mock 区域",
            stand=task.target_stands[0] if task.target_stands else None,
            row=f"第{task.row_min or 1}排",
            seat=f"{task.seat_min or 1}号",
            adjacent=True,
            unit_price=unit_price,
            total_price=total,
            final_total=total,
            available_quantity=quantity,
            detail_url=task.event_url,
            raw={"mock": True, "idempotency_scope": self.run_id},
        )

    async def query_tickets(self, task: MonitorTask) -> Sequence[TicketInfo]:
        await asyncio.sleep(0)
        self.query_counts[task.task_id] += 1
        # 前两轮不满足价格/数量，第三轮开始出现目标票。
        return [self._ticket(task, good=self.query_counts[task.task_id] >= 3)]

    async def match_ticket(self, task: MonitorTask, tickets: Sequence[TicketInfo]) -> MatchResult:
        return self.matcher.find_best(task, tickets)

    async def lock_order(self, task: MonitorTask, request: LockOrderRequest) -> LockOrderResult:
        await asyncio.sleep(0.02)
        return LockOrderResult(
            status=LockStatus.SUCCESS,
            message="Mock 库存已锁定，请手动完成付款",
            order_id=f"MOCK-{task.task_id}-{self.query_counts[task.task_id]}",
            final_total=request.ticket.payable_total,
            payment_deadline=datetime.now(timezone.utc) + timedelta(minutes=15),
            order_url=request.ticket.detail_url,
            requires_manual_action=True,
        )

    async def close(self) -> None:
        await asyncio.sleep(0)
