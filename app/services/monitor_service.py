from __future__ import annotations

import asyncio
import random
from datetime import datetime

from app.config import MonitorSettings, MonitorTask
from app.database import Database
from app.exceptions import AdapterNotImplementedError, LoginRequiredError, RateLimitError
from app.logger import task_logger
from app.models import LockStage, MatchResult, NotificationMessage, TicketInfo
from app.platforms.base import TicketPlatform
from app.services.login_service import LoginService
from app.services.notification_service import NotificationService
from app.services.order_service import OrderService


def _ticket_content(task: MonitorTask, ticket: TicketInfo) -> str:
    position = " ".join(filter(None, [ticket.stand, ticket.row, ticket.seat])) or "未提供"
    return (
        f"平台：{ticket.platform}\n任务：{task.task_id}\n演出：{ticket.event_name}\n"
        f"场次：{ticket.session_name}\n票档：{ticket.ticket_level}\n区域：{ticket.area or '未提供'}\n"
        f"位置：{position}\n数量：{task.quantity}张\n当前单价：{ticket.unit_price}\n"
        f"当前实际总价：{ticket.payable_total}\n目标最高单价：{task.max_unit_price}\n"
        f"目标最高总价：{task.max_total_price}\n状态：{'准备尝试锁单' if task.auto_lock else '仅通知'}\n"
        f"发现时间：{datetime.now().astimezone().isoformat(timespec='seconds')}\n页面：{ticket.detail_url}"
    )


class MonitorService:
    def __init__(
        self,
        database: Database,
        login_service: LoginService,
        order_service: OrderService,
        notifications: NotificationService,
        settings: MonitorSettings,
    ) -> None:
        self.database = database
        self.login_service = login_service
        self.order_service = order_service
        self.notifications = notifications
        self.settings = settings
        self._platform_pause_until: dict[str, float] = {}

    def _delay_range(self, task: MonitorTask) -> tuple[float, float]:
        minimum = (
            task.random_delay_min_seconds
            if task.random_delay_min_seconds is not None
            else self.settings.random_delay_min_seconds
        )
        maximum = (
            task.random_delay_max_seconds
            if task.random_delay_max_seconds is not None
            else self.settings.random_delay_max_seconds
        )
        return minimum, maximum

    async def run_task(
        self, task: MonitorTask, platform: TicketPlatform, *, max_cycles: int | None = None
    ) -> None:
        logger = task_logger("app.monitor", task.task_id, task.platform)
        errors = 0
        cycles = 0
        initial_min, initial_max = self._delay_range(task)
        if initial_max > 0:
            await asyncio.sleep(random.uniform(initial_min, initial_max))
        logger.info("监控任务启动")
        await self.database.update_task_state(task.task_id, "WATCHING")
        await self.database.record_lock_stage(
            f"task:{task.task_id}", task.task_id, LockStage.WATCHING, "监控任务启动"
        )
        while True:
            control = await self.database.get_task_control(task.task_id)
            if control is not None and not control[0]:
                logger.info("任务已动态禁用")
                await self.database.update_task_state(task.task_id, "disabled")
                return
            try:
                pause_until = self._platform_pause_until.get(task.platform, 0)
                remaining_pause = pause_until - asyncio.get_running_loop().time()
                if remaining_pause > 0:
                    logger.warning("同平台因限流/风控暂停，剩余 %.1f 秒", remaining_pause)
                    await asyncio.sleep(remaining_pause)
                if not await self.login_service.ensure_logged_in(platform, notify=errors == 0):
                    await self.database.update_task_state(task.task_id, "waiting_login", consecutive_errors=errors)
                    await asyncio.sleep(self.login_service.settings.retry_interval_seconds)
                    continue

                logger.info("开始查询票务")
                tickets = list(await platform.query_tickets(task))
                cycles += 1
                logger.info("查询完成，共 %s 条", len(tickets))
                for ticket in tickets:
                    await self.database.record_price(task.task_id, ticket)
                result: MatchResult = await platform.match_ticket(task, tickets)
                errors = 0
                await self.database.update_task_state(task.task_id, "WATCHING", last_run=True)
                if result.matched and result.ticket is not None:
                    ticket = result.ticket
                    await self.database.update_task_state(task.task_id, "MATCHED", last_run=True)
                    await self.database.record_lock_stage(
                        f"task:{task.task_id}", task.task_id, LockStage.MATCHED, "发现符合条件的票品"
                    )
                    logger.info("发现符合条件的票：%s / %s", ticket.ticket_level, ticket.area)
                    await self.database.record_match(task, result, task.auto_lock)
                    if task.notify:
                        self.notifications.dispatch(
                            NotificationMessage("ticket_found", "发现符合条件的票", _ticket_content(task, ticket))
                        )
                    if task.auto_lock:
                        lock_result = await self.order_service.lock(task, ticket, platform)
                        logger.info("锁单结果：%s - %s", lock_result.status.value, lock_result.message)
                        if task.notify:
                            self.notifications.dispatch(
                                NotificationMessage(
                                    "lock_result",
                                    "锁单成功，请手动付款" if lock_result.success else "锁单未成功",
                                    (
                                        f"平台：{ticket.platform}\n任务：{task.task_id}\n演出：{ticket.event_name}\n"
                                        f"状态：{lock_result.status.value}\n原因：{lock_result.message}\n"
                                        f"订单号：{lock_result.order_id or '无'}\n"
                                        f"最终价格：{lock_result.final_total if lock_result.final_total is not None else '未知'}\n"
                                        f"票数：{task.quantity}\n支付截止：{lock_result.payment_deadline or '以订单页为准'}\n"
                                        f"订单页面：{lock_result.order_url or ticket.detail_url}\n"
                                        + ("请人工核对并付款，程序不会自动支付。" if lock_result.success else "任务将按配置继续处理。")
                                    ),
                                )
                            )
                        if lock_result.success and task.stop_after_lock_success:
                            await self.database.update_task_state(task.task_id, "completed", last_run=True)
                            logger.info("锁单成功，按配置停止任务")
                            return
                else:
                    logger.info("本轮无匹配：%s", "、".join(result.reasons))
            except LoginRequiredError:
                errors += 1
                await self.database.update_task_state(task.task_id, "waiting_login", consecutive_errors=errors)
            except AdapterNotImplementedError as exc:
                logger.error("真实平台适配尚未完成：%s", exc)
                await self.database.update_task_state(task.task_id, "adapter_unavailable", consecutive_errors=errors)
                self.notifications.dispatch(
                    NotificationMessage(
                        "adapter_unavailable", "真实平台适配不可用",
                        f"平台：{task.platform}\n任务：{task.task_id}\n原因：{exc}",
                    )
                )
                return
            except RateLimitError as exc:
                errors += 1
                logger.error("平台触发限流/风控，暂停请求：%s", exc)
                pause_seconds = max(task.interval_seconds or self.settings.default_interval_seconds, 60)
                self._platform_pause_until[task.platform] = asyncio.get_running_loop().time() + pause_seconds
                await self.notifications.send(
                    NotificationMessage("risk_control", "平台限流或风控", f"平台：{task.platform}\n任务：{task.task_id}\n原因：{exc}\n已暂停自动请求")
                )
                await asyncio.sleep(pause_seconds)
            except asyncio.CancelledError:
                await self.database.update_task_state(task.task_id, "stopped", consecutive_errors=errors)
                logger.info("监控任务已取消")
                raise
            except Exception as exc:
                errors += 1
                logger.exception("任务第 %s 次连续异常：%s", errors, exc)
                threshold = task.max_consecutive_errors or self.settings.max_consecutive_errors
                await self.database.update_task_state(task.task_id, "error", consecutive_errors=errors, last_run=True)
                if errors == threshold:
                    self.notifications.dispatch(
                        NotificationMessage(
                            "task_error", "监控任务连续异常",
                            f"平台：{task.platform}\n任务：{task.task_id}\n连续异常：{errors} 次\n原因：{exc}\n系统已降低监控频率",
                        )
                    )

            if max_cycles is not None and cycles >= max_cycles:
                await self.database.update_task_state(task.task_id, "demo_finished", consecutive_errors=errors)
                logger.info("达到演示轮数，停止任务")
                return
            base_interval = task.interval_seconds or self.settings.default_interval_seconds
            threshold = task.max_consecutive_errors or self.settings.max_consecutive_errors
            if errors > 0:
                base_interval = min(base_interval * (2 ** min(errors, 4)), 300)
            delay_min, delay_max = self._delay_range(task)
            await asyncio.sleep(base_interval + random.uniform(delay_min, delay_max))
