from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


STATUS_LABELS = {
    "pending": "待启动",
    "paused": "已暂停",
    "stopped": "已停止",
    "PREFLIGHT": "正在预检",
    "WATCHING": "监控中",
    "MATCHED": "发现目标票",
    "REVALIDATING": "正在重新校验",
    "SELECTING_QUANTITY": "正在选择数量",
    "SELECTING_AUDIENCE": "正在选择观演人",
    "SELECTING_CONTACT": "正在选择联系人",
    "VERIFYING_FINAL_PRICE": "正在核对金额",
    "READY_TO_SUBMIT": "订单待提交",
    "SUBMITTING": "正在提交订单",
    "PAYMENT_PENDING": "已进入待支付",
}


def display_status(value: object) -> str:
    text = str(value or "待启动")
    return STATUS_LABELS.get(text, text)


@dataclass(slots=True)
class UiEvent:
    event_type: str
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
