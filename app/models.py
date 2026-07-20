from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class LoginState(str, Enum):
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    WAITING = "waiting_login"
    UNKNOWN = "unknown"


class LockStatus(str, Enum):
    SUCCESS = "success"
    PAYMENT_PENDING = "payment_pending"
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
    MANUAL_PROFILE_MISSING = "manual_profile_missing"
    ORDER_EXISTS = "order_exists"
    PAGE_CHANGED = "page_changed"
    TIMEOUT = "timeout"
    REJECTED = "rejected"
    ADAPTER_UNAVAILABLE = "adapter_unavailable"
    FAILED = "failed"


class LockStage(str, Enum):
    PREFLIGHT = "PREFLIGHT"
    WATCHING = "WATCHING"
    MATCHED = "MATCHED"
    REVALIDATING = "REVALIDATING"
    SELECTING_QUANTITY = "SELECTING_QUANTITY"
    SELECTING_AUDIENCE = "SELECTING_AUDIENCE"
    SELECTING_CONTACT = "SELECTING_CONTACT"
    VERIFYING_FINAL_PRICE = "VERIFYING_FINAL_PRICE"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    SUBMITTING = "SUBMITTING"
    PAYMENT_PENDING = "PAYMENT_PENDING"


class FailureKind(str, Enum):
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    ORDER_EXISTS = "order_exists"
    MANUAL_ACTION = "manual_action"


@dataclass(slots=True)
class PlatformAudienceOption:
    """平台账号中的远程购票人选项；仅携带稳定引用与平台已脱敏信息。"""

    platform: str
    option_id: str
    display_name: str
    masked_identity: str | None = None
    enabled: bool = True


class AudienceCreateRequest(BaseModel):
    """只允许存在于当前进程内存中的新增购票人请求。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1)
    certificate_type: str = Field(min_length=1)
    certificate_number: SecretStr
    phone: SecretStr | None = None

    def clear_sensitive(self) -> None:
        """提交结束后主动清除请求对象持有的全部表单值。"""

        self.name = ""
        self.certificate_type = ""
        self.certificate_number = SecretStr("")
        self.phone = None

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise TypeError("AudienceCreateRequest 是临时敏感对象，禁止序列化")

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        raise TypeError("AudienceCreateRequest 是临时敏感对象，禁止序列化")


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
    listing_id: str = ""
    ticket_group_id: str = ""
    seller_id: str = ""
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
    account_alias: str = ""
    audience_ids: list[str] = field(default_factory=list)
    audience_labels: list[str] = field(default_factory=list)
    # 兼容旧调用者；新任务不再向这里写入本地购票档案。
    purchase_profile: dict[str, Any] = field(default_factory=dict)
    stage_callback: Callable[[LockStage, str], Awaitable[None]] | None = None

    async def transition(self, stage: LockStage, message: str = "") -> None:
        if self.stage_callback is not None:
            await self.stage_callback(stage, message)


@dataclass(slots=True)
class LockOrderResult:
    status: LockStatus
    message: str
    order_id: str | None = None
    final_total: Decimal | None = None
    payment_deadline: datetime | None = None
    order_url: str | None = None
    requires_manual_action: bool = False
    failure_kind: FailureKind | None = None
    stage: LockStage | None = None

    @property
    def success(self) -> bool:
        return self.status in {LockStatus.SUCCESS, LockStatus.PAYMENT_PENDING}


@dataclass(slots=True)
class NotificationMessage:
    message_type: str
    title: str
    content: str


@dataclass(slots=True)
class PreflightCheck:
    name: str
    passed: bool
    message: str


@dataclass(slots=True)
class PreflightResult:
    task_id: str
    checks: list[PreflightCheck]
    ticket: TicketInfo | None = None

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)


@dataclass(slots=True)
class TaskRuntimeState:
    task_id: str
    status: str = "pending"
    last_run_at: datetime | None = None
    consecutive_errors: int = 0
    lock_attempts: int = 0
    last_error: str | None = None
