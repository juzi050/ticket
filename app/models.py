from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


class LoginState(str, Enum):
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    WAITING = "waiting_login"
    UNKNOWN = "unknown"


class LockStatus(str, Enum):
    SUCCESS = "success"
    IN_PROGRESS = "in_progress"
    NOT_LOGGED_IN = "not_logged_in"
    PRICE_CHANGED = "price_changed"
    OUT_OF_STOCK = "out_of_stock"
    AREA_MISMATCH = "area_mismatch"
    SESSION_MISMATCH = "session_mismatch"
    QUANTITY_INSUFFICIENT = "quantity_insufficient"
    NOT_ADJACENT = "not_adjacent"
    CAPTCHA_REQUIRED = "captcha_required"
    SMS_REQUIRED = "sms_required"
    MANUAL_CONFIRMATION = "manual_confirmation"
    ORDER_EXISTS = "order_exists"
    PAGE_CHANGED = "page_changed"
    TIMEOUT = "timeout"
    REJECTED = "rejected"
    ADAPTER_UNAVAILABLE = "adapter_unavailable"
    FAILED = "failed"


@dataclass(slots=True)
class SessionInfo:
    session_id: str
    name: str
    start_time: datetime | None = None


@dataclass(slots=True)
class SeatInfo:
    area: str | None = None
    stand: str | None = None
    row: str | None = None
    seat: str | None = None
    adjacent: bool | None = None


@dataclass(slots=True)
class TicketInfo:
    platform: str
    event_id: str
    event_name: str
    session_id: str
    session_name: str
    ticket_level: str
    unit_price: Decimal
    total_price: Decimal
    available_quantity: int
    detail_url: str
    area: str | None = None
    stand: str | None = None
    row: str | None = None
    seat: str | None = None
    adjacent: bool | None = None
    service_fee: Decimal = Decimal("0")
    delivery_fee: Decimal = Decimal("0")
    platform_fee: Decimal = Decimal("0")
    final_total: Decimal | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def payable_total(self) -> Decimal:
        if self.final_total is not None:
            return self.final_total
        return self.total_price + self.service_fee + self.delivery_fee + self.platform_fee


@dataclass(slots=True)
class MatchResult:
    matched: bool
    reasons: list[str] = field(default_factory=list)
    ticket: TicketInfo | None = None
    priority: int = 0


@dataclass(slots=True)
class LoginStatus:
    platform: str
    state: LoginState
    checked_at: datetime
    message: str = ""


@dataclass(slots=True)
class LockOrderRequest:
    task_id: str
    ticket: TicketInfo
    quantity: int
    max_unit_price: Decimal
    max_total_price: Decimal
    idempotency_key: str


@dataclass(slots=True)
class LockOrderResult:
    status: LockStatus
    message: str
    order_id: str | None = None
    final_total: Decimal | None = None
    payment_deadline: datetime | None = None
    order_url: str | None = None
    requires_manual_action: bool = False

    @property
    def success(self) -> bool:
        return self.status is LockStatus.SUCCESS


@dataclass(slots=True)
class NotificationMessage:
    message_type: str
    title: str
    content: str


@dataclass(slots=True)
class TaskRuntimeState:
    task_id: str
    status: str = "pending"
    last_run_at: datetime | None = None
    consecutive_errors: int = 0
    lock_attempts: int = 0
    last_error: str | None = None
