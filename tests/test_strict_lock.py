from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import PurchaseAudience, PurchaseProfile
from app.exceptions import QuantityUnavailableError
from app.models import LockStatus, TicketInfo
from app.platforms.motianlun import require_exact_quantity
from app.platforms.page_helpers import final_price_is_safe, is_safe_order_submit_label
from app.platforms.piaoniu import PiaoniuPlatform
from app.platforms.mock import MockPlatform
from app.services.ticket_matcher import TicketMatcher


def test_motianlun_does_not_replace_two_with_four() -> None:
    with pytest.raises(QuantityUnavailableError):
        require_exact_quantity([1, 4], 2)


def test_missing_exact_quantity_cannot_continue_to_order() -> None:
    with pytest.raises(QuantityUnavailableError):
        require_exact_quantity([1, 3, 4], 2)


def test_same_name_and_price_cannot_replace_listing_id(sample_task: object) -> None:
    task = sample_task.model_copy(update={"target_listing_id": "listing-a"})  # type: ignore[attr-defined]
    ticket = TicketInfo(
        platform="mock",
        event_id="event-1",
        event_name="测试演唱会",
        session_id="session-1",
        session_name="2026-08-01 19:30",
        ticket_level="1280",
        unit_price=Decimal("1000"),
        total_price=Decimal("2000"),
        available_quantity=2,
        detail_url="https://example.com/event",
        listing_id="listing-b",
        area="内场A区",
        adjacent=True,
    )
    result = TicketMatcher().match(task, ticket)
    assert not result.matched
    assert "票品 ID 不匹配" in result.reasons


async def test_revalidation_rejects_similar_replacement(sample_task: object) -> None:
    platform = MockPlatform("mock")
    original = platform._ticket(sample_task, good=True)  # type: ignore[arg-type]

    async def replacement_query(task: object):
        replacement = platform._ticket(task, good=True)  # type: ignore[arg-type]
        replacement.listing_id = "similar-but-different"
        return [replacement]

    platform.query_tickets = replacement_query  # type: ignore[method-assign]
    result = await platform.revalidate_ticket(sample_task, original)  # type: ignore[arg-type]
    assert not result.matched
    assert "禁止自动替换" in result.reasons[0]


def test_piaoniu_same_price_group_uses_only_active_id() -> None:
    groups = [
        {"id": "group-a", "saleprice": "1000"},
        {"id": "group-b", "saleprice": "1000"},
    ]
    active = PiaoniuPlatform._active_group(groups, Decimal("1000"))
    assert active["id"] == "group-a"
    assert active["id"] != "group-b"


def test_unknown_or_excess_final_price_is_not_safe() -> None:
    assert not final_price_is_safe(None, Decimal("2000"))
    assert not final_price_is_safe(Decimal("2001"), Decimal("2000"))
    assert final_price_is_safe(Decimal("2000"), Decimal("2000"))


def test_payment_buttons_are_never_safe_submit_buttons() -> None:
    for label in ("立即支付", "确认支付", "去支付", "付款"):
        assert not is_safe_order_submit_label(label)
    assert is_safe_order_submit_label("提交订单")


def test_purchase_profile_does_not_contain_sensitive_payment_fields() -> None:
    profile = PurchaseProfile(
        profile_id="safe",
        account_alias="account",
        audiences=[PurchaseAudience(name="甲", phone_last4="1234")],
        contact_id="contact",
        address_id="address",
        accept_purchase_notice=True,
    )
    dumped = profile.model_dump()
    assert "identity_number" not in dumped
    assert "password" not in dumped
    assert "payment" not in dumped
