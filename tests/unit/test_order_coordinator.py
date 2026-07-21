from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.domain import BuyerProfile, MonitorTask, OrderPreview, OrderResult, TicketOption
from app.services.order_coordinator import OrderCoordinator
from app.storage.order_repository import build_idempotency_key


def ticket() -> TicketOption:
    return TicketOption(
        platform="motianlun",
        event_url="https://m.motianlun.cn/pages/show-detail/show-detail?showId=show-1",
        event_id="show-1",
        event_name="测试演出",
        session_id="session-1",
        session_name="测试场次",
        listing_id="ticket-1",
        ticket_group_id="plan-1",
        ticket_name="看台票",
        unit_price=Decimal("100"),
        available_quantity=1,
    )


def task(current: TicketOption) -> MonitorTask:
    return MonitorTask(
        ticket=current,
        quantity=1,
        buyer_ids=["buyer-1"],
        ideal_price=Decimal("200"),
    )


async def test_reuses_platform_pending_order_before_create() -> None:
    current = ticket()
    monitor_task = task(current)
    buyer = BuyerProfile(
        buyer_id="buyer-1",
        name="测试用户",
        certificate_type="身份证",
        certificate_number="000000000000000000",
    )
    preview = OrderPreview(
        platform="motianlun",
        preview_id="preview-1",
        event_id="show-1",
        session_id="session-1",
        listing_id="ticket-1",
        quantity=1,
        buyer_ids=["buyer-1"],
        remote_buyer_ids=["remote-1"],
        unit_price=Decimal("100"),
        ticket_total=Decimal("100"),
        fee_total=Decimal("5"),
        final_total=Decimal("105"),
    )
    pending = OrderResult(
        success=True,
        status="payment_pending",
        order_id="order-1",
        final_total=Decimal("105"),
        payment_url="https://example.com/order-1",
        message="待支付",
    )
    api = Mock()
    api.get_exact_ticket = AsyncMock(return_value=current)
    api.preview_order = AsyncMock(return_value=preview)
    api.find_recent_order = AsyncMock(return_value=pending)
    api.create_order = AsyncMock()
    buyers = Mock(get=AsyncMock(return_value=buyer))
    tasks = Mock(update_runtime=AsyncMock(), set_enabled=AsyncMock())
    orders = Mock(
        find_blocking=AsyncMock(return_value=None),
        claim_creating=AsyncMock(return_value=(True, None)),
        save_result=AsyncMock(),
    )
    audit = Mock(append=AsyncMock())
    notifier = Mock(notify_order=AsyncMock())
    coordinator = OrderCoordinator(
        {"motianlun": api}, buyers, tasks, orders, audit, notifier
    )

    result = await coordinator.handle_price_match(monitor_task, current)

    assert result == pending
    api.create_order.assert_not_awaited()
    orders.save_result.assert_awaited_once_with(
        build_idempotency_key(monitor_task), pending
    )
    tasks.set_enabled.assert_awaited_once_with(
        monitor_task.task_id, False, "payment_pending"
    )
    notifier.notify_order.assert_awaited_once_with(monitor_task, pending)


async def test_platform_pending_order_wins_over_concurrent_empty_local_claim() -> None:
    current = ticket()
    monitor_task = task(current)
    buyer = BuyerProfile(
        buyer_id="buyer-1",
        name="测试用户",
        certificate_type="身份证",
        certificate_number="000000000000000000",
    )
    preview = OrderPreview(
        platform="motianlun",
        preview_id="preview-1",
        event_id="show-1",
        session_id="session-1",
        listing_id="ticket-1",
        quantity=1,
        buyer_ids=["buyer-1"],
        remote_buyer_ids=["remote-1"],
        unit_price=Decimal("100"),
        ticket_total=Decimal("100"),
        fee_total=Decimal("5"),
        final_total=Decimal("105"),
    )
    pending = OrderResult(
        success=True,
        status="payment_pending",
        order_id="order-1",
        message="待支付",
    )
    api = Mock(
        get_exact_ticket=AsyncMock(return_value=current),
        preview_order=AsyncMock(return_value=preview),
        find_recent_order=AsyncMock(return_value=pending),
        create_order=AsyncMock(),
    )
    coordinator = OrderCoordinator(
        {"motianlun": api},
        Mock(get=AsyncMock(return_value=buyer)),
        Mock(update_runtime=AsyncMock(), set_enabled=AsyncMock()),
        Mock(
            find_blocking=AsyncMock(return_value=None),
            claim_creating=AsyncMock(
                return_value=(False, SimpleNamespace(result=None, status="creating"))
            ),
            save_result=AsyncMock(),
        ),
        Mock(append=AsyncMock()),
        Mock(notify_order=AsyncMock()),
    )

    result = await coordinator.handle_price_match(monitor_task, current)

    assert result == pending
    api.create_order.assert_not_awaited()


async def test_closed_local_pending_order_no_longer_blocks_monitor() -> None:
    current = ticket()
    monitor_task = task(current)
    pending = OrderResult(
        success=True,
        status="payment_pending",
        order_id="order-1",
        message="待支付",
    )
    closed = OrderResult(
        success=False,
        status="closed",
        order_id="order-1",
        message="已关闭",
    )
    api = Mock(
        get_order_detail=AsyncMock(return_value=closed),
        get_exact_ticket=AsyncMock(return_value=None),
    )
    tasks = Mock(update_runtime=AsyncMock(), set_enabled=AsyncMock())
    orders = Mock(
        find_blocking=AsyncMock(
            return_value=SimpleNamespace(
                status="payment_pending", order_id="order-1", result=pending
            )
        ),
        save_result=AsyncMock(),
    )
    coordinator = OrderCoordinator(
        {"motianlun": api},
        Mock(),
        tasks,
        orders,
        Mock(append=AsyncMock()),
        Mock(),
    )

    result = await coordinator.handle_price_match(monitor_task, current)

    assert result is None
    orders.save_result.assert_awaited_once_with(
        build_idempotency_key(monitor_task), closed
    )
    api.get_exact_ticket.assert_awaited_once_with(current, 1)
    tasks.set_enabled.assert_not_awaited()


async def test_current_local_pending_order_still_blocks_duplicate() -> None:
    current = ticket()
    monitor_task = task(current)
    pending = OrderResult(
        success=True,
        status="payment_pending",
        order_id="order-1",
        message="待支付",
    )
    api = Mock(
        get_order_detail=AsyncMock(return_value=pending),
        get_exact_ticket=AsyncMock(),
    )
    tasks = Mock(update_runtime=AsyncMock(), set_enabled=AsyncMock())
    orders = Mock(
        find_blocking=AsyncMock(
            return_value=SimpleNamespace(
                status="payment_pending", order_id="order-1", result=pending
            )
        ),
        save_result=AsyncMock(),
    )
    coordinator = OrderCoordinator(
        {"motianlun": api},
        Mock(),
        tasks,
        orders,
        Mock(append=AsyncMock()),
        Mock(),
    )

    result = await coordinator.handle_price_match(monitor_task, current)

    assert result == pending
    orders.save_result.assert_awaited_once_with(
        build_idempotency_key(monitor_task), pending
    )
    api.get_exact_ticket.assert_not_awaited()
    tasks.set_enabled.assert_awaited_once_with(
        monitor_task.task_id, False, "payment_pending"
    )
