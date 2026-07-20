from __future__ import annotations

import os
from typing import Any

import httpx

from app.domain import MonitorTask, OrderResult
from app.storage.audit_repository import AuditEntry, AuditRepository


def build_success_message(task: MonitorTask, result: OrderResult) -> str:
    deadline = (
        result.payment_deadline.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        if result.payment_deadline
        else "平台未返回"
    )
    payment_link = result.payment_url or task.ticket.event_url
    return "\n".join(
        (
            "## 抢票成功",
            "",
            f"- 平台：{'票牛' if task.ticket.platform == 'piaoniu' else '摩天轮'}",
            f"- 演出：{task.ticket.event_name}",
            f"- 场次：{task.ticket.session_name}",
            f"- 票品：{task.ticket.ticket_name}",
            f"- 数量：{task.quantity} 张",
            f"- 实际应付：¥{result.final_total}",
            f"- 支付截止时间：{deadline}",
            f"- 订单号：{result.order_id}",
            "",
            f"[立即前往支付]({payment_link})",
        )
    )


class ServerChanNotifier:
    def __init__(
        self,
        audit_repository: AuditRepository,
        sendkey: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.audit = audit_repository
        self.sendkey = sendkey if sendkey is not None else os.getenv("SERVERCHAN_SENDKEY", "")
        self.client = client or httpx.AsyncClient(timeout=20)
        self._owns_client = client is None

    async def notify_order(self, task: MonitorTask, result: OrderResult) -> bool:
        if not self.sendkey:
            await self.audit.append(
                AuditEntry(
                    level="WARNING",
                    category="notification",
                    action="serverchan_skipped",
                    platform=task.ticket.platform,
                    task_id=task.task_id,
                    order_id=result.order_id,
                    message="未配置 SERVERCHAN_SENDKEY，未发送微信通知",
                )
            )
            return False
        response: httpx.Response | None = None
        try:
            response = await self.client.post(
                f"https://sctapi.ftqq.com/{self.sendkey}.send",
                data={
                    "title": f"抢票成功：{task.ticket.event_name}",
                    "desp": build_success_message(task, result),
                },
            )
            response.raise_for_status()
            body: Any
            try:
                body = response.json()
            except ValueError:
                body = response.text
            await self.audit.append(
                AuditEntry(
                    level="INFO",
                    category="notification",
                    action="serverchan_sent",
                    platform=task.ticket.platform,
                    task_id=task.task_id,
                    order_id=result.order_id,
                    message="Server酱微信通知已发送",
                    request_url="https://sctapi.ftqq.com/[REDACTED].send",
                    request_method="POST",
                    response_status=response.status_code,
                    response_body=body,
                )
            )
            return True
        except Exception as exc:
            await self.audit.append(
                AuditEntry(
                    level="ERROR",
                    category="notification",
                    action="serverchan_failed",
                    platform=task.ticket.platform,
                    task_id=task.task_id,
                    order_id=result.order_id,
                    message="Server酱微信通知发送失败",
                    request_url="https://sctapi.ftqq.com/[REDACTED].send",
                    request_method="POST",
                    response_status=response.status_code if response else None,
                    exception_type=type(exc).__name__,
                    exception_message=str(exc),
                )
            )
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
