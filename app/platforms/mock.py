from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.config import MonitorTask, PurchaseProfile
from app.models import (
    AudienceCreateRequest,
    LockOrderRequest,
    LockOrderResult,
    LockStage,
    LockStatus,
    MatchResult,
    PlatformAudienceOption,
    TicketInfo,
)
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
        self.audiences: dict[str, PlatformAudienceOption] = {
            "mock-audience-1": PlatformAudienceOption(
                self.name, "mock-audience-1", "测试甲", "身份证 310***********0001"
            ),
            "mock-audience-2": PlatformAudienceOption(
                self.name, "mock-audience-2", "测试乙", "身份证 320***********0002"
            ),
            "mock-audience-3": PlatformAudienceOption(
                self.name, "mock-audience-3", "测试丙", "身份证 330***********0003"
            ),
        }
        self.last_selected_audience_ids: list[str] = []
        self.simulate_selected_count_mismatch = False

    async def initialize(self) -> None:
        await asyncio.sleep(0)
        self.logged_in = True

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
            listing_id=f"mock-listing-{task.task_id}",
            ticket_group_id=f"mock-group-{task.task_id}",
            seller_id="mock-seller",
            raw={"mock": True, "selected_quantity": task.quantity, "run_id": self.run_id},
        )

    async def query_tickets(self, task: MonitorTask) -> Sequence[TicketInfo]:
        async with self.normal_operation():
            await asyncio.sleep(0)
            self.query_counts[task.task_id] += 1
            # 前两轮不满足价格/数量，第三轮开始出现目标票。
            return [self._ticket(task, good=self.query_counts[task.task_id] >= 3)]

    async def preflight_tickets(self, task: MonitorTask) -> Sequence[TicketInfo]:
        return [self._ticket(task, good=True)]

    async def validate_purchase_profile(
        self, profile: PurchaseProfile, quantity: int
    ) -> tuple[bool | None, str]:
        valid = (
            len(profile.audiences) == quantity
            and profile.has_contact
            and profile.has_address
            and profile.accept_purchase_notice
        )
        return valid, "Mock 已保存资料完整" if valid else "Mock 已保存资料不完整"

    async def open_audience_management(self) -> None:
        await asyncio.sleep(0)

    async def list_audiences(self) -> list[PlatformAudienceOption]:
        await asyncio.sleep(0)
        return [
            PlatformAudienceOption(
                option.platform,
                option.option_id,
                option.display_name,
                option.masked_identity,
                option.enabled,
            )
            for option in self.audiences.values()
        ]

    @staticmethod
    def _masked_identity(certificate_type: str, certificate_number: str) -> str:
        if len(certificate_number) <= 8:
            masked = "*" * len(certificate_number)
        else:
            masked = certificate_number[:3] + "*" * (len(certificate_number) - 7) + certificate_number[-4:]
        return f"{certificate_type} {masked}"

    async def create_audience(
        self, request: AudienceCreateRequest
    ) -> PlatformAudienceOption:
        certificate_number = request.certificate_number.get_secret_value()
        option = PlatformAudienceOption(
            platform=self.name,
            option_id=f"mock-audience-{uuid4().hex}",
            display_name=request.name,
            masked_identity=self._masked_identity(request.certificate_type, certificate_number),
        )
        self.audiences[option.option_id] = option
        await asyncio.sleep(0)
        return option

    async def validate_audience_ids(self, audience_ids: list[str]) -> tuple[bool, str]:
        if len(set(audience_ids)) != len(audience_ids):
            return False, "不能重复选择同一购票人"
        invalid = [
            option_id
            for option_id in audience_ids
            if option_id not in self.audiences or not self.audiences[option_id].enabled
        ]
        if invalid:
            return False, "Mock 购票人已删除或失效"
        return True, f"Mock 平台账号中存在 {len(audience_ids)} 位指定购票人"

    def invalidate_audience(self, option_id: str) -> None:
        option = self.audiences.get(option_id)
        if option is not None:
            option.enabled = False

    async def select_order_audiences(
        self, page: Any, audience_ids: list[str], quantity: int
    ) -> tuple[bool, str]:
        valid, message = await self.validate_audience_ids(audience_ids)
        if not valid:
            return False, message
        if len(audience_ids) != quantity:
            return False, f"页面要求 {quantity} 张票，但任务指定了 {len(audience_ids)} 位购票人"
        selected = list(audience_ids)
        if self.simulate_selected_count_mismatch and selected:
            selected.pop()
        self.last_selected_audience_ids = selected
        if len(selected) != quantity or selected != audience_ids:
            return False, "Mock 订单页实际选中人数或人员不一致"
        return True, f"已按 option_id 精确选择 {quantity} 位购票人"

    async def has_pending_order(
        self, task: MonitorTask, ticket: TicketInfo, account_alias: str
    ) -> bool | None:
        return False

    async def match_ticket(self, task: MonitorTask, tickets: Sequence[TicketInfo]) -> MatchResult:
        return self.matcher.find_best(task, tickets)

    async def lock_order(self, task: MonitorTask, request: LockOrderRequest) -> LockOrderResult:
        await request.transition(LockStage.SELECTING_QUANTITY, "Mock 精确选择购票数量")
        await request.transition(LockStage.SELECTING_AUDIENCE, "Mock 按 option_id 精确选择购票人")
        selected, message = await self.select_order_audiences(
            None, request.audience_ids, request.quantity
        )
        if not selected:
            return LockOrderResult(
                LockStatus.MANUAL_PROFILE_MISSING,
                message,
                requires_manual_action=True,
                stage=LockStage.SELECTING_AUDIENCE,
            )
        for stage in (
            LockStage.SELECTING_CONTACT,
            LockStage.VERIFYING_FINAL_PRICE,
            LockStage.READY_TO_SUBMIT,
            LockStage.SUBMITTING,
            LockStage.PAYMENT_PENDING,
        ):
            await request.transition(stage, "Mock 阶段验证通过")
            await asyncio.sleep(0)
        return LockOrderResult(
            status=LockStatus.PAYMENT_PENDING,
            message="Mock 库存已锁定，请手动完成付款",
            order_id=f"MOCK-{task.task_id}-{self.query_counts[task.task_id]}",
            final_total=request.ticket.payable_total,
            payment_deadline=datetime.now(timezone.utc) + timedelta(minutes=15),
            order_url=request.ticket.detail_url,
            requires_manual_action=True,
            stage=LockStage.PAYMENT_PENDING,
        )

    async def close(self) -> None:
        await asyncio.sleep(0)
