from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal

from app.domain import MonitorTask, PlatformName, TicketOption, utc_now
from app.platforms.http_api import (
    PlatformAuthExpiredError,
    PlatformCapabilityUnavailable,
    TicketPlatformApi,
)
from app.storage.audit_repository import AuditEntry, AuditRepository
from app.storage.task_repository import TaskRepository


@dataclass(frozen=True, slots=True)
class PriceEvaluation:
    status: str
    estimated_total: Decimal | None
    matched: bool
    message: str


def evaluate_price(
    task: MonitorTask, ticket: TicketOption | None
) -> PriceEvaluation:
    if ticket is None:
        return PriceEvaluation(
            "ticket_unavailable", None, False, "目标精确票品当前不存在"
        )
    if ticket.available_quantity < task.quantity:
        return PriceEvaluation(
            "quantity_insufficient",
            ticket.estimated_total(task.quantity),
            False,
            "目标精确票品余量不足",
        )
    estimated = ticket.estimated_total(task.quantity)
    if estimated > task.ideal_price:
        return PriceEvaluation(
            "monitoring", estimated, False, "当前预估总价高于理想订单总价"
        )
    return PriceEvaluation(
        "price_matched", estimated, True, "当前预估总价满足条件，准备预下单"
    )


MatchedCallback = Callable[[MonitorTask, TicketOption], Awaitable[None]]


class PriceMonitorService:
    def __init__(
        self,
        platform_apis: dict[PlatformName, TicketPlatformApi],
        task_repository: TaskRepository,
        audit_repository: AuditRepository,
        matched_callback: MatchedCallback | None = None,
    ) -> None:
        self.apis = platform_apis
        self.tasks = task_repository
        self.audit = audit_repository
        self.matched_callback = matched_callback

    async def check_once(self, task: MonitorTask) -> None:
        api = self.apis[task.ticket.platform]
        checked_at = utc_now()
        try:
            ticket = await api.get_exact_ticket(task.ticket, task.quantity)
        except PlatformAuthExpiredError:
            await self.tasks.set_enabled(task.task_id, False, "auth_expired")
            await self.audit.append(
                AuditEntry(
                    level="ERROR",
                    category="auth",
                    action="monitor_auth_expired",
                    platform=task.ticket.platform,
                    task_id=task.task_id,
                    message="登录状态失效，任务已暂停",
                )
            )
            return

        evaluation = evaluate_price(task, ticket)
        await self.tasks.update_runtime(
            task.task_id,
            status="order_preparing" if evaluation.matched else evaluation.status,
            last_unit_price=ticket.unit_price if ticket else None,
            last_estimated_total=evaluation.estimated_total,
            last_final_total=task.last_final_total,
            last_checked_at=checked_at,
            next_check_at=task.next_check_at,
            last_error=None,
        )
        await self.audit.append(
            AuditEntry(
                level="INFO",
                category="monitor",
                action="check_price",
                platform=task.ticket.platform,
                task_id=task.task_id,
                message=evaluation.message,
                context={
                    "listing_id": task.ticket.listing_id,
                    "quantity": task.quantity,
                    "current_unit_price": str(ticket.unit_price) if ticket else None,
                    "current_estimated_total": str(evaluation.estimated_total)
                    if evaluation.estimated_total is not None
                    else None,
                    "ideal_price": str(task.ideal_price),
                    "available_quantity": ticket.available_quantity if ticket else 0,
                },
            )
        )
        if evaluation.matched and ticket and self.matched_callback:
            try:
                await self.matched_callback(task, ticket)
            except PlatformAuthExpiredError as exc:
                await self._stop_failed_order(task, "auth_expired", exc)
            except PlatformCapabilityUnavailable as exc:
                await self._stop_failed_order(task, "order_unavailable", exc)
            except Exception as exc:
                current = await self.tasks.get(task.task_id)
                if current is None or current.enabled:
                    await self._stop_failed_order(task, "order_failed", exc)

    async def _stop_failed_order(
        self, task: MonitorTask, status: str, error: Exception
    ) -> None:
        current = await self.tasks.get(task.task_id) or task
        await self.tasks.update_runtime(
            task.task_id,
            status=status,
            last_unit_price=current.last_unit_price,
            last_estimated_total=current.last_estimated_total,
            last_final_total=current.last_final_total,
            last_checked_at=current.last_checked_at,
            next_check_at=None,
            last_error=str(error),
        )
        await self.tasks.set_enabled(task.task_id, False, status)
        await self.audit.append(
            AuditEntry(
                level="ERROR",
                category="order",
                action=status,
                platform=task.ticket.platform,
                task_id=task.task_id,
                message="命中价格后的下单流程失败，任务已暂停",
                exception_type=type(error).__name__,
                exception_message=str(error),
            )
        )
