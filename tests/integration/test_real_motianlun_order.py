from __future__ import annotations

import os
from decimal import Decimal

import pytest

from app.auth import AuthSessionBridge
from app.notifications.serverchan import ServerChanNotifier
from app.platforms.motianlun_api import MotianlunApi
from app.services.order_coordinator import OrderCoordinator
from app.storage.audit_repository import AuditRepository
from app.storage.buyer_repository import BuyerBindingRepository, BuyerRepository
from app.storage.database import MvpDatabase
from app.storage.order_repository import OrderRepository
from app.storage.session_repository import PlatformSessionRepository
from app.storage.task_repository import TaskRepository


CONFIRMATION = "CREATE_REAL_PENDING_ORDER"


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise AssertionError(f"缺少真实订单测试配置：{name}")
    return value


async def test_real_motianlun_pending_order() -> None:
    if os.getenv("REAL_ORDER_TEST_ENABLED", "").strip().lower() != "true":
        pytest.skip("真实待支付订单测试默认关闭")
    assert required_env("REAL_ORDER_TEST_PLATFORM") == "motianlun"
    assert required_env("REAL_ORDER_TEST_CONFIRMATION") == CONFIRMATION
    event_url = required_env("REAL_ORDER_TEST_EVENT_URL")
    session_id = required_env("REAL_ORDER_TEST_SESSION_ID")
    listing_id = required_env("REAL_ORDER_TEST_LISTING_ID")
    quantity = int(required_env("REAL_ORDER_TEST_QUANTITY"))
    buyer_ids = [
        item.strip()
        for item in required_env("REAL_ORDER_TEST_BUYER_IDS").split(",")
        if item.strip()
    ]
    max_total = Decimal(required_env("REAL_ORDER_TEST_MAX_TOTAL"))
    assert quantity > 0
    assert len(buyer_ids) == quantity
    assert max_total > 0

    database = MvpDatabase(os.getenv("DATABASE_PATH", "data/ticket.db"))
    await database.initialize()
    sessions = PlatformSessionRepository(database)
    session = await sessions.get("motianlun")
    assert session is not None, "摩天轮尚未登录"
    audit = AuditRepository(database)
    buyers = BuyerRepository(database)
    tasks = TaskRepository(database)
    orders = OrderRepository(database)
    api = MotianlunApi(
        AuthSessionBridge().build_http_client(session),
        audit,
        sessions,
        BuyerBindingRepository(database),
    )
    notifier = ServerChanNotifier(audit, sendkey="")
    try:
        event = await api.get_event(event_url)
        sessions_for_event = await api.list_sessions(event.event_id)
        assert any(item.session_id == session_id for item in sessions_for_event)
        task = next(
            (
                item
                for item in await tasks.list()
                if item.ticket.platform == "motianlun"
                and item.ticket.event_id == event.event_id
                and item.ticket.session_id == session_id
                and item.ticket.listing_id == listing_id
            ),
            None,
        )
        assert task is not None, "本地没有对应摩天轮监控任务"
        assert task.buyer_ids == buyer_ids
        exact_ticket = await api.get_exact_ticket(task.ticket, quantity)
        assert exact_ticket is not None, "目标精确票品当前不存在"
        task = task.model_copy(
            update={
                "ticket": exact_ticket,
                "quantity": quantity,
                "ideal_price": max_total,
                "enabled": True,
                "status": "real_order_testing",
            }
        )
        coordinator = OrderCoordinator(
            {"motianlun": api},
            buyers,
            tasks,
            orders,
            audit,
            notifier,
        )

        result = await coordinator.handle_price_match(task, exact_ticket)

        assert result is not None
        assert result.success is True
        assert result.status == "payment_pending"
        assert result.order_id
        assert result.final_total is not None
        assert result.final_total <= max_total
        assert result.payment_deadline is not None
        assert result.payment_url

        detail = await api.get_order_detail(result.order_id)
        assert detail.order_id == result.order_id
        assert detail.status == "payment_pending"

        duplicate = await coordinator.handle_price_match(task, exact_ticket)
        assert duplicate is not None
        assert duplicate.order_id == result.order_id
    finally:
        await notifier.close()
        await api.close()
