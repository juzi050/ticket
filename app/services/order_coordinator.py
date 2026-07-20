from __future__ import annotations

import asyncio
from collections.abc import Mapping

from app.domain import MonitorTask, OrderResult, PlatformName, TicketOption
from app.notifications.serverchan import ServerChanNotifier
from app.platforms.http_api import PlatformApiError, TicketPlatformApi
from app.storage.audit_repository import AuditEntry, AuditRepository
from app.storage.buyer_repository import BuyerRepository
from app.storage.order_repository import (
    OrderRepository,
    build_idempotency_key,
)
from app.storage.task_repository import TaskRepository


class OrderCoordinator:
    def __init__(
        self,
        platform_apis: Mapping[PlatformName, TicketPlatformApi],
        buyer_repository: BuyerRepository,
        task_repository: TaskRepository,
        order_repository: OrderRepository,
        audit_repository: AuditRepository,
        notifier: ServerChanNotifier,
        *,
        create_timeout_seconds: float = 45,
    ) -> None:
        self.apis = platform_apis
        self.buyers = buyer_repository
        self.tasks = task_repository
        self.orders = order_repository
        self.audit = audit_repository
        self.notifier = notifier
        self.create_timeout_seconds = create_timeout_seconds
        self._locks: dict[PlatformName, asyncio.Lock] = {}

    async def handle_price_match(
        self, task: MonitorTask, monitored_ticket: TicketOption
    ) -> OrderResult | None:
        lock = self._locks.setdefault(task.ticket.platform, asyncio.Lock())
        async with lock:
            blocking = await self.orders.find_blocking(task)
            if blocking:
                await self.audit.append(
                    AuditEntry(
                        level="WARNING",
                        category="order",
                        action="idempotency_blocked",
                        platform=task.ticket.platform,
                        task_id=task.task_id,
                        order_id=blocking.order_id,
                        message="已有相同订单记录，已阻止重复下单",
                        context={"status": blocking.status},
                    )
                )
                await self.tasks.set_enabled(task.task_id, False, blocking.status)
                return blocking.result

            api = self.apis[task.ticket.platform]
            current_ticket = await api.get_exact_ticket(task.ticket, task.quantity)
            if current_ticket is None:
                await self.tasks.update_runtime(
                    task.task_id,
                    status="ticket_unavailable",
                    last_error="创建订单前目标精确票品已消失",
                )
                return None
            if (
                current_ticket.listing_id != monitored_ticket.listing_id
                or current_ticket.event_id != task.ticket.event_id
                or current_ticket.session_id != task.ticket.session_id
                or current_ticket.available_quantity < task.quantity
            ):
                raise PlatformApiError("创建订单前精确票品或余量发生变化")

            buyers = []
            for buyer_id in task.buyer_ids:
                buyer = await self.buyers.get(buyer_id)
                if buyer is None:
                    raise PlatformApiError(f"本地购票人不存在：{buyer_id}")
                buyers.append(buyer)
            preview = await api.preview_order(current_ticket, task.quantity, buyers)
            await self.tasks.update_runtime(
                task.task_id,
                status="order_previewed",
                last_unit_price=current_ticket.unit_price,
                last_estimated_total=current_ticket.estimated_total(task.quantity),
                last_final_total=preview.final_total,
            )
            await self.audit.append(
                AuditEntry(
                    level="INFO",
                    category="order",
                    action="final_price_checked",
                    platform=task.ticket.platform,
                    task_id=task.task_id,
                    message="已通过官方预下单接口确认最终应付金额",
                    context={
                        "listing_id": task.ticket.listing_id,
                        "quantity": task.quantity,
                        "final_total": str(preview.final_total),
                        "ideal_price": str(task.ideal_price),
                    },
                )
            )
            if preview.final_total > task.ideal_price:
                await self.tasks.update_runtime(
                    task.task_id,
                    status="final_price_above_limit",
                    last_final_total=preview.final_total,
                    last_error="最终应付金额高于理想订单总价",
                )
                return None

            recent = await api.find_recent_order(task)
            if recent is not None and recent.status == "payment_pending":
                claimed, existing = await self.orders.claim_creating(task, preview)
                if claimed:
                    await self.orders.save_result(
                        build_idempotency_key(task), recent
                    )
                await self.tasks.set_enabled(task.task_id, False, "payment_pending")
                await self.audit.append(
                    AuditEntry(
                        level="WARNING",
                        category="order",
                        action="platform_order_reused",
                        platform=task.ticket.platform,
                        task_id=task.task_id,
                        order_id=recent.order_id,
                        message="平台已存在相同待支付订单，已阻止再次创建",
                    )
                )
                result = existing.result if existing and existing.result else recent
                if claimed:
                    await self.notifier.notify_order(task, result)
                return result

            claimed, existing = await self.orders.claim_creating(task, preview)
            if not claimed:
                await self.tasks.set_enabled(
                    task.task_id, False, existing.status if existing else "duplicate"
                )
                return existing.result if existing else None

            key = build_idempotency_key(task)
            try:
                result = await asyncio.wait_for(
                    api.create_order(preview), timeout=self.create_timeout_seconds
                )
            except Exception as exc:
                recent = None
                try:
                    recent = await api.find_recent_order(task)
                except Exception:
                    pass
                if recent is not None:
                    result = recent
                else:
                    await self.orders.mark_unknown_after_timeout(
                        key, "创建请求后无法确认订单状态，禁止自动重试"
                    )
                    await self.tasks.set_enabled(
                        task.task_id, False, "unknown_after_timeout"
                    )
                    await self.audit.append(
                        AuditEntry(
                            level="ERROR",
                            category="order",
                            action="create_order_unknown",
                            platform=task.ticket.platform,
                            task_id=task.task_id,
                            message="创建请求结果未知，已停止任务并阻止重复提交",
                            exception_type=type(exc).__name__,
                            exception_message=str(exc),
                        )
                    )
                    raise

            await self.orders.save_result(key, result)
            if result.status != "payment_pending" or not result.success:
                await self.tasks.set_enabled(task.task_id, False, result.status)
                raise PlatformApiError(f"订单未进入待支付状态：{result.status}")
            await self.tasks.update_runtime(
                task.task_id,
                status="payment_pending",
                last_unit_price=current_ticket.unit_price,
                last_estimated_total=current_ticket.estimated_total(task.quantity),
                last_final_total=result.final_total or preview.final_total,
            )
            await self.tasks.set_enabled(task.task_id, False, "payment_pending")
            await self.audit.append(
                AuditEntry(
                    level="INFO",
                    category="order",
                    action="pending_order_created",
                    platform=task.ticket.platform,
                    task_id=task.task_id,
                    order_id=result.order_id,
                    message="真实待支付订单创建成功，监控任务已停止",
                    context={
                        "final_total": str(result.final_total or preview.final_total),
                        "payment_deadline": result.payment_deadline,
                        "payment_url": result.payment_url,
                    },
                )
            )
            await self.notifier.notify_order(task, result)
            return result
