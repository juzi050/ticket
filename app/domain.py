from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


PlatformName = Literal["piaoniu", "motianlun"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_buyer_id() -> str:
    return f"buyer_{uuid4().hex[:12]}"


def new_task_id() -> str:
    return f"task_{uuid4().hex[:12]}"


class DomainModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class BuyerProfile(DomainModel):
    buyer_id: str = Field(default_factory=new_buyer_id, min_length=1)
    name: str = Field(min_length=1)
    certificate_type: str = Field(min_length=1)
    certificate_number: str = Field(min_length=1)
    phone: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BuyerPlatformBinding(DomainModel):
    buyer_id: str = Field(min_length=1)
    platform: PlatformName
    remote_buyer_id: str = Field(min_length=1)
    remote_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class EventInfo(DomainModel):
    platform: PlatformName
    event_url: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    event_name: str = Field(min_length=1)
    raw_data: dict[str, Any] = Field(default_factory=dict)


class SessionInfo(DomainModel):
    platform: PlatformName
    event_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    session_name: str = Field(min_length=1)
    start_time: datetime | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)


class TicketOption(DomainModel):
    platform: PlatformName
    event_url: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    event_name: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    session_name: str = Field(min_length=1)
    listing_id: str = Field(min_length=1)
    ticket_group_id: str | None = None
    sku_id: str | None = None
    seller_id: str | None = None
    ticket_name: str = Field(min_length=1)
    area: str | None = None
    seat_description: str | None = None
    unit_price: Decimal = Field(ge=0)
    available_quantity: int = Field(ge=0)
    known_fee: Decimal = Field(default=Decimal("0"), ge=0)
    raw_data: dict[str, Any] = Field(default_factory=dict)

    def estimated_total(self, quantity: int) -> Decimal:
        return self.unit_price * quantity + self.known_fee


class MonitorTask(DomainModel):
    task_id: str = Field(default_factory=new_task_id, min_length=1)
    ticket: TicketOption
    quantity: int = Field(ge=1)
    buyer_ids: list[str]
    ideal_price: Decimal = Field(gt=0)
    query_interval_seconds: float = Field(default=10, ge=1, le=86400)
    enabled: bool = True
    status: str = "monitoring"
    last_unit_price: Decimal | None = None
    last_estimated_total: Decimal | None = None
    last_final_total: Decimal | None = None
    last_checked_at: datetime | None = None
    next_check_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_buyers(self) -> "MonitorTask":
        if len(self.buyer_ids) != self.quantity:
            raise ValueError("购票人数必须与购票数量一致")
        if len(set(self.buyer_ids)) != len(self.buyer_ids):
            raise ValueError("不能重复选择同一购票人")
        return self


class OrderPreview(DomainModel):
    platform: PlatformName
    preview_id: str | None = None
    event_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    listing_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    buyer_ids: list[str]
    remote_buyer_ids: list[str]
    unit_price: Decimal = Field(ge=0)
    ticket_total: Decimal = Field(ge=0)
    fee_total: Decimal = Field(ge=0)
    final_total: Decimal = Field(ge=0)
    expires_at: datetime | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_people(self) -> "OrderPreview":
        if len(self.buyer_ids) != self.quantity:
            raise ValueError("本地购票人数与购票数量不一致")
        if len(self.remote_buyer_ids) != self.quantity:
            raise ValueError("远程购票人数与购票数量不一致")
        return self


class OrderResult(DomainModel):
    success: bool
    status: str
    order_id: str | None = None
    final_total: Decimal | None = None
    payment_deadline: datetime | None = None
    payment_url: str | None = None
    message: str
    raw_data: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class AuthSession:
    platform: PlatformName
    cookies: dict[str, str]
    headers: dict[str, str]
    csrf_token: str | None
    device_id: str | None
    created_at: datetime
