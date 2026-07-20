from __future__ import annotations

import asyncio
import hashlib

from app.config import MonitorTask
from app.database import Database
from app.exceptions import AdapterNotImplementedError
from app.models import LockOrderRequest, LockOrderResult, LockStatus, TicketInfo
from app.platforms.base import TicketPlatform


class OrderService:
    def __init__(self, database: Database, cooldown_seconds: int = 60) -> None:
        self.database = database
        self.cooldown_seconds = cooldown_seconds
        self._task_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, task_id: str) -> asyncio.Lock:
        return self._task_locks.setdefault(task_id, asyncio.Lock())

    @staticmethod
    def idempotency_key(task: MonitorTask, ticket: TicketInfo) -> str:
        raw = "|".join(
            [
                ticket.platform, str(ticket.raw.get("idempotency_scope", "default-profile")),
                ticket.event_id, ticket.session_id, ticket.ticket_level,
                ticket.area or "", task.task_id,
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def lock(self, task: MonitorTask, ticket: TicketInfo, platform: TicketPlatform) -> LockOrderResult:
        async with self._lock_for(task.task_id):
            if not await platform.check_login_status():
                return LockOrderResult(LockStatus.NOT_LOGGED_IN, "锁单前登录状态失效")

            revalidated = await platform.revalidate_ticket(task, ticket)
            if not revalidated.matched or revalidated.ticket is None:
                return LockOrderResult(
                    LockStatus.FAILED,
                    "锁单前复核失败：" + "、".join(revalidated.reasons),
                )
            current = revalidated.ticket
            if current.unit_price > task.max_unit_price or current.payable_total > task.max_total_price:
                return LockOrderResult(
                    LockStatus.PRICE_CHANGED,
                    "订单确认前价格已超过配置上限",
                    final_total=current.payable_total,
                )
            key = self.idempotency_key(task, current)
            request = LockOrderRequest(
                task_id=task.task_id,
                ticket=current,
                quantity=task.quantity,
                max_unit_price=task.max_unit_price,
                max_total_price=task.max_total_price,
                idempotency_key=key,
            )
            claimed = await self.database.claim_lock(
                request, task.max_lock_attempts, self.cooldown_seconds
            )
            if not claimed:
                return LockOrderResult(LockStatus.ORDER_EXISTS, "相同锁单已存在、正在执行或达到重试上限")
            try:
                result = await platform.lock_order(task, request)
                if result.final_total is not None and result.final_total > task.max_total_price:
                    result = LockOrderResult(
                        LockStatus.PRICE_CHANGED,
                        "最终订单应付金额超过配置上限，已停止提交",
                        final_total=result.final_total,
                    )
            except AdapterNotImplementedError as exc:
                result = LockOrderResult(LockStatus.ADAPTER_UNAVAILABLE, str(exc))
            except TimeoutError as exc:
                result = LockOrderResult(LockStatus.TIMEOUT, str(exc))
            except Exception as exc:
                result = LockOrderResult(LockStatus.FAILED, str(exc))
            await self.database.complete_lock(key, result)
            return result
