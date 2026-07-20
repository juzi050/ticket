from __future__ import annotations

import asyncio
import hashlib
import logging
from decimal import Decimal

from app.config import MonitorTask, PurchaseProfile
from app.database import Database
from app.exceptions import AdapterNotImplementedError
from app.models import (
    FailureKind,
    LockOrderRequest,
    LockOrderResult,
    LockStage,
    LockStatus,
    TicketInfo,
)
from app.platforms.base import TicketPlatform


class OrderService:
    def __init__(
        self,
        database: Database,
        cooldown_seconds: int = 60,
        purchase_profiles: list[PurchaseProfile] | None = None,
        stage_timeout_seconds: int = 30,
        max_price_slippage: Decimal = Decimal("0"),
    ) -> None:
        self.database = database
        self.cooldown_seconds = cooldown_seconds
        self.purchase_profiles = {
            profile.profile_id: profile for profile in (purchase_profiles or [])
        }
        self.stage_timeout_seconds = stage_timeout_seconds
        self.max_price_slippage = max_price_slippage
        self._task_locks: dict[str, asyncio.Lock] = {}
        self.logger = logging.getLogger("app.order")

    def _lock_for(self, task_id: str) -> asyncio.Lock:
        return self._task_locks.setdefault(task_id, asyncio.Lock())

    @staticmethod
    def idempotency_key(
        task: MonitorTask, ticket: TicketInfo, account_alias: str = ""
    ) -> str:
        raw = "|".join(
            [
                account_alias,
                ticket.platform,
                ticket.event_id,
                ticket.session_id,
                ticket.listing_id,
                str(task.quantity),
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _failure_kind(status: LockStatus) -> FailureKind:
        if status is LockStatus.ORDER_EXISTS:
            return FailureKind.ORDER_EXISTS
        if status in {
            LockStatus.TIMEOUT,
            LockStatus.NOT_LOGGED_IN,
            LockStatus.OUT_OF_STOCK,
            LockStatus.PRICE_CHANGED,
            LockStatus.PAGE_CHANGED,
            LockStatus.CAPTCHA_REQUIRED,
            LockStatus.MANUAL_PROFILE_MISSING,
        }:
            return FailureKind.RETRYABLE
        if status in {LockStatus.MANUAL_CONFIRMATION, LockStatus.SMS_REQUIRED}:
            return FailureKind.MANUAL_ACTION
        return FailureKind.NON_RETRYABLE

    async def lock(self, task: MonitorTask, ticket: TicketInfo, platform: TicketPlatform) -> LockOrderResult:
        async with self._lock_for(task.task_id):
            profile = self.purchase_profiles.get(task.purchase_profile_id)
            if profile is None:
                return LockOrderResult(
                    LockStatus.MANUAL_PROFILE_MISSING,
                    "任务未绑定有效购票档案",
                    requires_manual_action=True,
                    failure_kind=FailureKind.RETRYABLE,
                    stage=LockStage.PREFLIGHT,
                )
            if (
                len(profile.audiences) != task.quantity
                or not profile.has_contact
                or not profile.has_address
                or not profile.accept_purchase_notice
            ):
                return LockOrderResult(
                    LockStatus.MANUAL_PROFILE_MISSING,
                    "购票档案人数、联系人、地址或购票须知未完成确定性配置",
                    requires_manual_action=True,
                    failure_kind=FailureKind.RETRYABLE,
                    stage=LockStage.PREFLIGHT,
                )
            key = self.idempotency_key(task, ticket, profile.account_alias)
            loop = asyncio.get_running_loop()
            last_transition_at = loop.time()

            async def transition(stage: LockStage, message: str = "") -> None:
                nonlocal last_transition_at
                last_transition_at = loop.time()
                self.logger.info(
                    "task=%s lock_stage=%s %s", task.task_id, stage.value, message
                )
                await self.database.record_lock_stage(key, task.task_id, stage, message)

            await transition(LockStage.PREFLIGHT, "开始锁单前校验")
            if not await platform.check_login_status():
                return LockOrderResult(
                    LockStatus.NOT_LOGGED_IN,
                    "锁单前登录状态失效",
                    failure_kind=FailureKind.RETRYABLE,
                    stage=LockStage.PREFLIGHT,
                )

            await transition(LockStage.REVALIDATING, "重新查询原票品")
            revalidated = await platform.revalidate_ticket(task, ticket)
            if not revalidated.matched or revalidated.ticket is None:
                return LockOrderResult(
                    LockStatus.OUT_OF_STOCK,
                    "锁单前复核失败：" + "、".join(revalidated.reasons),
                    failure_kind=FailureKind.RETRYABLE,
                    stage=LockStage.REVALIDATING,
                )
            current = revalidated.ticket
            allowed_total = task.max_total_price + self.max_price_slippage
            selected_quantity = int(
                current.raw.get("selected_quantity", current.raw.get("ticket_count", 0))
            )
            if selected_quantity != task.quantity:
                return LockOrderResult(
                    LockStatus.QUANTITY_INSUFFICIENT,
                    "查询、复核与锁单数量不一致，已停止锁单",
                    failure_kind=FailureKind.NON_RETRYABLE,
                    stage=LockStage.REVALIDATING,
                )
            if current.unit_price > task.max_unit_price or current.payable_total > allowed_total:
                return LockOrderResult(
                    LockStatus.PRICE_CHANGED,
                    "订单确认前价格已超过配置上限",
                    final_total=current.payable_total,
                    failure_kind=FailureKind.RETRYABLE,
                    stage=LockStage.REVALIDATING,
                )
            request = LockOrderRequest(
                task_id=task.task_id,
                ticket=current,
                quantity=task.quantity,
                max_unit_price=task.max_unit_price,
                max_total_price=allowed_total,
                idempotency_key=key,
                account_alias=profile.account_alias,
                purchase_profile=profile.model_dump(mode="json"),
                stage_callback=transition,
            )
            claimed = await self.database.claim_lock(
                request, task.max_lock_attempts, self.cooldown_seconds
            )
            if not claimed:
                record = await self.database.get_lock_record(key)
                status = str(record.get("status", "")) if record else ""
                if status in {"success", "payment_pending", "order_exists"}:
                    return LockOrderResult(
                        LockStatus.ORDER_EXISTS,
                        "相同待支付或已完成订单已存在，禁止重复提交",
                        failure_kind=FailureKind.ORDER_EXISTS,
                        stage=LockStage.PREFLIGHT,
                    )
                return LockOrderResult(
                    LockStatus.REJECTED,
                    "相同锁单正在执行、处于冷却期或不可重试",
                    failure_kind=FailureKind.RETRYABLE,
                    stage=LockStage.PREFLIGHT,
                )
            try:
                async def watch_stage_timeout() -> None:
                    while True:
                        remaining = self.stage_timeout_seconds - (loop.time() - last_transition_at)
                        if remaining <= 0:
                            raise TimeoutError("锁单阶段超时")
                        await asyncio.sleep(remaining)

                lock_future = asyncio.create_task(platform.lock_order(task, request))
                timeout_future = asyncio.create_task(watch_stage_timeout())
                done, _ = await asyncio.wait(
                    {lock_future, timeout_future}, return_when=asyncio.FIRST_COMPLETED
                )
                if timeout_future in done:
                    lock_future.cancel()
                    await asyncio.gather(lock_future, return_exceptions=True)
                    await timeout_future
                    raise TimeoutError("锁单阶段超时")
                timeout_future.cancel()
                await asyncio.gather(timeout_future, return_exceptions=True)
                result = await lock_future
                if result.success and result.final_total is None:
                    result = LockOrderResult(
                        LockStatus.PRICE_CHANGED,
                        "最终应付金额未知，已拒绝订单结果",
                        failure_kind=FailureKind.RETRYABLE,
                        stage=LockStage.VERIFYING_FINAL_PRICE,
                    )
                elif result.final_total is not None and result.final_total > allowed_total:
                    result = LockOrderResult(
                        LockStatus.PRICE_CHANGED,
                        "最终订单应付金额超过配置上限，已停止提交",
                        final_total=result.final_total,
                        failure_kind=FailureKind.RETRYABLE,
                        stage=LockStage.VERIFYING_FINAL_PRICE,
                    )
            except AdapterNotImplementedError as exc:
                result = LockOrderResult(LockStatus.ADAPTER_UNAVAILABLE, str(exc))
            except TimeoutError as exc:
                result = LockOrderResult(
                    LockStatus.TIMEOUT,
                    str(exc) or "锁单阶段超时",
                    failure_kind=FailureKind.RETRYABLE,
                )
            except Exception as exc:
                result = LockOrderResult(LockStatus.FAILED, str(exc))
            if result.failure_kind is None and not result.success:
                result.failure_kind = self._failure_kind(result.status)
            await self.database.complete_lock(key, result)
            return result
