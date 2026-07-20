from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs

import httpx

from app.domain import MonitorTask, OrderResult, TicketOption, utc_now
from app.notifications.serverchan import ServerChanNotifier, build_success_message


def test_serverchan_message_contains_payment_fields_but_no_buyer_secrets() -> None:
    task = MonitorTask(
        ticket=TicketOption(
            platform="piaoniu",
            event_url="https://www.piaoniu.com/activity/1",
            event_id="1",
            event_name="测试演出",
            session_id="2",
            session_name="晚场",
            listing_id="3",
            ticket_name="480票面 看台",
            unit_price=Decimal("278"),
            available_quantity=1,
        ),
        quantity=1,
        buyer_ids=["buyer-secret"],
        ideal_price=Decimal("300"),
    )
    result = OrderResult(
        success=True,
        status="payment_pending",
        order_id="order-1",
        final_total=Decimal("290"),
        payment_deadline=utc_now() + timedelta(minutes=10),
        payment_url="https://example.com/pay/order-1",
        message="待支付",
    )
    message = build_success_message(task, result)
    assert "¥290" in message
    assert "order-1" in message
    assert result.payment_url in message
    assert "buyer-secret" not in message


async def test_serverchan_posts_chinese_as_utf8_form_data() -> None:
    task = MonitorTask(
        ticket=TicketOption(
            platform="motianlun",
            event_url="https://m.motianlun.cn/show?showId=1",
            event_id="1",
            event_name="中文编码测试",
            session_id="2",
            session_name="晚场",
            listing_id="3",
            ticket_name="看台票",
            unit_price=Decimal("100"),
            available_quantity=1,
        ),
        quantity=1,
        buyer_ids=["buyer-1"],
        ideal_price=Decimal("200"),
    )
    result = OrderResult(
        success=True,
        status="payment_pending",
        order_id="order-1",
        final_total=Decimal("105"),
        message="待支付",
    )

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["content-type"].endswith("charset=utf-8")
        form = parse_qs(request.content.decode("ascii"), encoding="utf-8")
        assert form["title"] == ["抢票成功：中文编码测试"]
        assert "平台：摩天轮" in form["desp"][0]
        return httpx.Response(200, json={"code": 0, "message": "SUCCESS"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    audit = Mock(append=AsyncMock())
    notifier = ServerChanNotifier(audit, sendkey="test-key", client=client)
    try:
        assert await notifier.notify_order(task, result) is True
    finally:
        await client.aclose()

    entry = audit.append.await_args.args[0]
    assert entry.action == "serverchan_sent"
