from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from app.config import BrowserSettings, MonitorTask, PlatformAutomationSettings
from app.exceptions import AdapterNotImplementedError, PlatformError, QuantityUnavailableError
from app.models import (
    AudienceCreateRequest,
    FailureKind,
    LockOrderRequest,
    LockOrderResult,
    LockStage,
    LockStatus,
    MatchResult,
    PlatformAudienceOption,
    TicketInfo,
)
from app.platforms.base import TicketPlatform
from app.platforms.page_helpers import (
    compact_text,
    detect_interruption,
    event_id_from_url,
    final_price_is_safe,
    SAFE_ORDER_SUBMIT_PATTERN,
    listing_fingerprint,
    matches_session,
    safe_page_url,
    visible_body_text,
)
from app.services.session_service import BrowserSessionService
from app.services.ticket_matcher import TicketMatcher, parse_price


def require_exact_quantity(values: list[int], requested: int) -> int:
    if requested not in values:
        raise QuantityUnavailableError(
            f"当前场次没有精确的 {requested} 张选项，可选数量：{values or '无'}"
        )
    return requested


class MotianlunPlatform(TicketPlatform):
    name = "motianlun"
    display_name = "摩天轮票务"
    audience_management_url = "https://m.motianlun.cn/package-user/audiences/audiences"

    def __init__(
        self, browser: BrowserSettings, automation: PlatformAutomationSettings | None = None
    ) -> None:
        rules = automation or PlatformAutomationSettings(
            home_url="https://m.motianlun.cn/",
            login_url="https://m.motianlun.cn/package-functional-pages/account-login/account-login",
            auth_check_url="https://m.motianlun.cn/pages/mine/mine",
            authenticated_selectors=["text=我的订单"],
            unauthenticated_selectors=["text=点击登录"],
        )
        self.session = BrowserSessionService(self.name, browser, rules)
        self.matcher = TicketMatcher()
        self._page_lock = asyncio.Lock()

    async def initialize(self) -> None:
        await self.session.initialize()

    async def check_login_status(self) -> bool:
        async with self.normal_operation(), self._page_lock:
            return await self.session.check_login_status()

    async def open_login_page(self) -> None:
        await self.session.open_login_page()

    async def on_login_success(self) -> None:
        if self.session.settings.close_after_login:
            await self.session.close()

    async def search_event(self, task: MonitorTask) -> Any:
        return {
            "event_id": event_id_from_url(task.event_url, "showId"),
            "url": task.event_url,
        }

    async def open_audience_management(self) -> None:
        async with self.normal_operation(), self._page_lock:
            page = await self._page()
            await self._open_audience_management_unlocked(page)

    async def _open_audience_management_unlocked(self, page: Any) -> None:
        await page.bring_to_front()
        await page.goto(self.audience_management_url, wait_until="domcontentloaded")
        await page.get_by_text("我的观演人", exact=True).wait_for(state="visible")
        await page.wait_for_function(
            """() => {
              const text = document.body.innerText || '';
              return text.includes('暂无观演人') || text.includes('手机号');
            }""",
            timeout=15_000,
        )

    async def list_audiences(self) -> list[PlatformAudienceOption]:
        async with self.normal_operation(), self._page_lock:
            page = await self._page()
            await self._open_audience_management_unlocked(page)
            return await self._list_audiences_unlocked(page)

    async def _list_audiences_unlocked(self, page: Any) -> list[PlatformAudienceOption]:
        body = await visible_body_text(page)
        if "暂无观演人" in body:
            return []
        rows = page.locator(".audience_item")
        options: list[PlatformAudienceOption] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            name_locator = row.locator(".name").first
            display_name = (
                (await name_locator.inner_text()).strip()
                if await name_locator.count()
                else ""
            )
            text = (await row.inner_text()).strip()
            masked_lines = [
                line.strip()
                for line in text.splitlines()
                if "*" in line and line.strip()
            ]
            masked = " · ".join(masked_lines)
            option_id = await row.evaluate(
                """
                element => {
                  const nodes = [element, ...element.querySelectorAll('*')];
                  for (const node of nodes) {
                    for (const name of ['data-audience-id', 'data-performer-id', 'data-id']) {
                      const value = node.getAttribute && node.getAttribute(name);
                      if (value) return value;
                    }
                    const href = node.getAttribute && node.getAttribute('href');
                    const match = href && href.match(/[?&](?:audienceId|audience_id|id)=([^&#]+)/);
                    if (match) return decodeURIComponent(match[1]);
                  }
                  return '';
                }
                """
            )
            if display_name:
                options.append(
                    PlatformAudienceOption(
                        self.name,
                        str(option_id or ""),
                        display_name,
                        masked or None,
                        bool(option_id),
                    )
                )
        if not options:
            raise AdapterNotImplementedError(
                "摩天轮观演人列表结构已变化，无法安全读取"
            )
        return options

    async def create_audience(
        self, request: AudienceCreateRequest
    ) -> PlatformAudienceOption:
        async with self.priority_operation(), self._page_lock:
            page = await self._page()
            await self._open_audience_management_unlocked(page)
            before_options = await self._list_audiences_unlocked(page)
            before = {option.option_id for option in before_options if option.option_id}
            certificate = request.certificate_number.get_secret_value()
            phone = request.phone.get_secret_value() if request.phone else ""
            inputs: list[Any] = []
            try:
                await page.locator("uni-button").filter(
                    has_text=re.compile(r"^新增观演人$")
                ).last.click()
                await page.get_by_text("证件类型", exact=True).wait_for(state="visible")
                await page.get_by_text("选择证件类型", exact=True).click()
                await page.get_by_text(request.certificate_type, exact=True).last.click()
                textboxes = page.get_by_role("textbox")
                certificate_input = textboxes.nth(0)
                name_input = textboxes.nth(1)
                phone_input = page.get_by_role("spinbutton").first
                inputs = [certificate_input, name_input, phone_input]
                await certificate_input.fill(certificate)
                await name_input.fill(request.name)
                if phone:
                    await phone_input.fill(phone)
                # 实页中的“保存”是可点击 uni-view 内的文字节点，事件需要冒泡到父容器。
                await page.get_by_text("保存", exact=True).last.click()
                interruption = await detect_interruption(page)
                if interruption:
                    await page.bring_to_front()
                await page.get_by_text("证件号码", exact=True).wait_for(
                    state="hidden", timeout=60_000
                )
            except Exception:
                for input_box in inputs:
                    try:
                        await input_box.fill("")
                    except Exception:
                        pass
                raise
            after = await self._list_audiences_unlocked(page)
            added = [
                option
                for option in after
                if option.option_id and option.option_id not in before
            ]
            if not added and len(after) == len(before_options) + 1:
                # 新增成功但页面不暴露稳定 ID：只确认本次多出的展示项，禁止用于自动锁单。
                previous = Counter(
                    (option.display_name, option.masked_identity)
                    for option in before_options
                )
                display_added: list[PlatformAudienceOption] = []
                for option in after:
                    key = (option.display_name, option.masked_identity)
                    if previous[key]:
                        previous[key] -= 1
                    else:
                        display_added.append(option)
                if len(display_added) == 1:
                    return display_added[0]
            if len(added) != 1:
                raise AdapterNotImplementedError(
                    "平台已保存表单，但无法从页面新增结果中唯一确认稳定 option_id"
                )
            return added[0]

    async def query_tickets(self, task: MonitorTask) -> Sequence[TicketInfo]:
        async with self.normal_operation(), self._page_lock:
            page = await self._page()
            await self._goto_detail(page, task.event_url)
            sessions = await self._session_choices(page, task)
            result: list[TicketInfo] = []
            for session_name in sessions:
                try:
                    actual_count = await self._open_ticket_list(
                        page, task.event_url, session_name, task.quantity
                    )
                except QuantityUnavailableError:
                    continue
                result.extend(await self._read_ticket_list(page, task, session_name, actual_count))
            return result

    async def match_ticket(self, task: MonitorTask, tickets: Sequence[TicketInfo]) -> MatchResult:
        return self.matcher.find_best(task, tickets)

    async def lock_order(self, task: MonitorTask, request: LockOrderRequest) -> LockOrderResult:
        async with self._page_lock:
            page = await self._page()
            try:
                await request.transition(LockStage.SELECTING_QUANTITY, "选择精确购票数量")
                actual_count = await self._open_ticket_list(
                    page,
                    request.ticket.detail_url,
                    str(request.ticket.raw.get("session_name", request.ticket.session_name)),
                    request.quantity,
                )
                if actual_count != request.quantity:
                    return LockOrderResult(
                        LockStatus.QUANTITY_INSUFFICIENT,
                        "页面实际选择数量与任务配置不一致，已停止锁单",
                        failure_kind=FailureKind.NON_RETRYABLE,
                        stage=LockStage.SELECTING_QUANTITY,
                    )
                item = await self._find_listing(page, request.ticket)
                if item is None:
                    return LockOrderResult(
                        LockStatus.OUT_OF_STOCK,
                        "原目标票品已不可购买，未使用相似票品替代",
                        failure_kind=FailureKind.RETRYABLE,
                    )
                current_price = parse_price(
                    await item.locator(".price-display").first.inner_text()
                )
                estimated_total = current_price * request.quantity
                if current_price > request.max_unit_price or estimated_total > request.max_total_price:
                    return LockOrderResult(
                        LockStatus.PRICE_CHANGED,
                        "进入订单前价格已超过配置上限",
                        final_total=estimated_total,
                    )

                await item.locator(".buy-button .mtl-button").click()
                popup = page.locator(".ticket-notes-popup").first
                await popup.wait_for(state="visible")
                popup_price = parse_price(
                    await popup.locator(".ticket-basic-infos .price-display").inner_text()
                )
                if popup_price != current_price:
                    return LockOrderResult(
                        LockStatus.PRICE_CHANGED,
                        "票品确认弹层价格发生变化，已停止操作",
                        final_total=popup_price * request.quantity,
                    )
                await popup.locator(".buy-button-container .mtl-button").click()
                await page.wait_for_url(re.compile(r"/order-confirm/"))
                interruption = await detect_interruption(page)
                if interruption:
                    status, message = interruption
                    return LockOrderResult(status, message, requires_manual_action=True)

                await request.transition(LockStage.SELECTING_AUDIENCE, "核对已保存观演人")
                audience_selected, audience_message = await self.select_order_audiences(
                    page, request.audience_ids, request.quantity
                )
                if not audience_selected:
                    await page.bring_to_front()
                    return LockOrderResult(
                        LockStatus.MANUAL_PROFILE_MISSING,
                        audience_message,
                        order_url=safe_page_url(page.url),
                        requires_manual_action=True,
                        failure_kind=FailureKind.MANUAL_ACTION,
                        stage=LockStage.SELECTING_AUDIENCE,
                    )

                await request.transition(LockStage.SELECTING_CONTACT, "核对已保存联系人和地址")
                await request.transition(LockStage.VERIFYING_FINAL_PRICE, "读取订单最终应付金额")
                total_locator = page.locator(".total .price-text").first
                await total_locator.wait_for(state="visible")
                try:
                    final_total = parse_price(await total_locator.inner_text())
                except ValueError:
                    return LockOrderResult(
                        LockStatus.PRICE_CHANGED,
                        "无法可靠读取最终应付金额，已停止提交",
                        order_url=safe_page_url(page.url),
                        failure_kind=FailureKind.RETRYABLE,
                        stage=LockStage.VERIFYING_FINAL_PRICE,
                    )
                order_url = safe_page_url(page.url)
                if not final_price_is_safe(final_total, request.max_total_price):
                    return LockOrderResult(
                        LockStatus.PRICE_CHANGED,
                        "订单确认页实际应付金额超过配置上限，已停止提交",
                        final_total=final_total,
                        order_url=order_url,
                    )

                body = await visible_body_text(page)
                if "立即支付" in body or "确认支付" in body:
                    return LockOrderResult(
                        LockStatus.MANUAL_CONFIRMATION,
                        "最终金额已核对，下一步是支付操作，已按规则暂停等待人工处理",
                        final_total=final_total,
                        order_url=order_url,
                        requires_manual_action=True,
                        failure_kind=FailureKind.MANUAL_ACTION,
                        stage=LockStage.READY_TO_SUBMIT,
                    )

                await request.transition(LockStage.READY_TO_SUBMIT, "资料与金额校验完成")
                submit = page.locator("button, uni-button").filter(
                    has_text=SAFE_ORDER_SUBMIT_PATTERN
                ).first
                if not await submit.count() or not await submit.is_visible():
                    return LockOrderResult(
                        LockStatus.MANUAL_CONFIRMATION,
                        "最终金额已核对，但未找到独立的提交订单按钮，请人工继续",
                        final_total=final_total,
                        order_url=order_url,
                        requires_manual_action=True,
                        failure_kind=FailureKind.MANUAL_ACTION,
                        stage=LockStage.READY_TO_SUBMIT,
                    )
                await request.transition(LockStage.SUBMITTING, "提交订单，不进入支付操作")
                await submit.click()
                await page.wait_for_timeout(1_500)
                interruption = await detect_interruption(page)
                if interruption:
                    status, message = interruption
                    return LockOrderResult(
                        status,
                        message,
                        final_total=final_total,
                        order_url=safe_page_url(page.url),
                        requires_manual_action=True,
                    )
                result_text = await visible_body_text(page)
                order_match = re.search(r"订单(?:号|编号)\s*[：:]?\s*([A-Za-z0-9-]+)", result_text)
                if order_match or "待支付" in result_text or "/order-detail/" in page.url:
                    await request.transition(LockStage.PAYMENT_PENDING, "订单已进入待支付")
                    return LockOrderResult(
                        LockStatus.PAYMENT_PENDING,
                        "摩天轮订单已提交并停留在待支付阶段，请手动付款",
                        order_id=order_match.group(1) if order_match else None,
                        final_total=final_total,
                        order_url=safe_page_url(page.url),
                        requires_manual_action=True,
                        stage=LockStage.PAYMENT_PENDING,
                    )
                return LockOrderResult(
                    LockStatus.MANUAL_CONFIRMATION,
                    "已点击提交订单，但未出现可确认的待支付状态，请人工检查",
                    final_total=final_total,
                    order_url=safe_page_url(page.url),
                    requires_manual_action=True,
                    failure_kind=FailureKind.MANUAL_ACTION,
                    stage=LockStage.SUBMITTING,
                )
            except QuantityUnavailableError as exc:
                return LockOrderResult(
                    LockStatus.QUANTITY_INSUFFICIENT,
                    str(exc),
                    failure_kind=FailureKind.RETRYABLE,
                    stage=LockStage.SELECTING_QUANTITY,
                )
            except Exception as exc:
                error_url = safe_page_url(page.url)
                return LockOrderResult(
                    LockStatus.PAGE_CHANGED,
                    f"摩天轮页面操作失败：{exc}；错误页面：{error_url}",
                    order_url=error_url,
                    failure_kind=FailureKind.RETRYABLE,
                )

    async def _page(self) -> Any:
        page = await self.session.page()
        if page.viewport_size != {"width": 390, "height": 844}:
            await page.set_viewport_size({"width": 390, "height": 844})
        return page

    async def _goto_detail(self, page: Any, url: str) -> None:
        await page.goto(url, wait_until="domcontentloaded")
        interruption = await detect_interruption(page)
        if interruption:
            raise PlatformError(interruption[1])
        try:
            await page.get_by_text("立即购买", exact=True).first.wait_for(state="visible")
        except Exception as exc:
            raise PlatformError(
                f"摩天轮详情页结构已变化或演出不可购买；错误页面：{safe_page_url(page.url)}"
            ) from exc

    async def _open_session_selector(self, page: Any) -> None:
        await page.get_by_text("立即购买", exact=True).first.click()
        await page.locator(".session-selecter").first.wait_for(state="visible")
        # 弹层会先显示骨架，再异步填充场次；只等容器会偶发读到空列表。
        await page.locator(".session-selecter .session-card .show-name").first.wait_for(
            state="visible"
        )

    async def _session_choices(self, page: Any, task: MonitorTask) -> list[str]:
        await self._open_session_selector(page)
        sessions = page.locator(".session-selecter .session-card")
        result: list[str] = []
        for index in range(await sessions.count()):
            name = (await sessions.nth(index).locator(".show-name").inner_text()).strip()
            if matches_session(
                name, task.target_sessions, task.event_date, task.event_time, task.match_mode
            ):
                result.append(name)
        return result

    async def _open_ticket_list(
        self, page: Any, detail_url: str, session_name: str, quantity: int
    ) -> int:
        await self._goto_detail(page, detail_url)
        await self._open_session_selector(page)
        sessions = page.locator(".session-selecter .session-card")
        selected = None
        for index in range(await sessions.count()):
            item = sessions.nth(index)
            current_name = await item.locator(".show-name").inner_text()
            if compact_text(current_name) == compact_text(session_name):
                selected = item
                break
        if selected is None:
            raise PlatformError("目标场次已不可购买")
        await selected.click()
        await page.locator("#count-1").wait_for(state="visible")
        await page.wait_for_function(
            """selector => {
              const element = document.querySelector(selector);
              return element && !element.classList.contains('disabled');
            }""",
            arg=f"#count-{quantity}",
        )
        counts = page.locator('.ticket-number-container [id^="count-"]')
        values: list[int] = []
        for index in range(await counts.count()):
            value = await counts.nth(index).get_attribute("id") or ""
            match = re.fullmatch(r"count-(\d+)", value)
            if match:
                values.append(int(match.group(1)))
        actual_count = require_exact_quantity(values, quantity)
        await page.locator(f"#count-{quantity}").click()
        await page.wait_for_function(
            "selector => document.querySelector(selector)?.classList.contains('selected')",
            arg=f"#count-{quantity}",
        )
        await page.wait_for_timeout(250)
        next_button = page.locator(
            ".session-selecter .button-container .mtl-button"
        ).first
        await next_button.click()
        try:
            await page.wait_for_url(
                re.compile(r"seat-and-seatplan|account-login"), timeout=10_000
            )
        except Exception:
            # H5 偶尔会吞掉弹层首次点击；仍停留在同一安全选票弹层时只重试一次。
            if "show-detail" not in page.url or not await next_button.is_visible():
                raise
            await page.wait_for_timeout(500)
            await next_button.click()
            await page.wait_for_url(re.compile(r"seat-and-seatplan|account-login"))
        interruption = await detect_interruption(page)
        if interruption:
            raise PlatformError(interruption[1])
        await page.locator(".ticket-container, .empty-container").first.wait_for(state="visible")
        try:
            await page.locator(".ticket-container .ticket-item").first.wait_for(
                state="visible", timeout=10_000
            )
        except Exception as exc:
            body = await visible_body_text(page)
            if "暂时缺票" not in body and "暂无票品" not in body:
                raise PlatformError("摩天轮票品列表加载失败或页面结构已变化") from exc
        return actual_count

    async def _read_ticket_list(
        self, page: Any, task: MonitorTask, session_name: str, actual_count: int
    ) -> list[TicketInfo]:
        title = (await page.title()).split("|", 1)[-1].strip()
        query = dict(parse_qsl(urlsplit(page.url).query, keep_blank_values=True))
        event_id = query.get("showId") or event_id_from_url(task.event_url, "showId")
        session_id = query.get("sessionId") or session_name
        body = await visible_body_text(page)
        adjacent = True if actual_count == 1 or "保证连座票品" in body else None
        items = page.locator(".ticket-container .ticket-item")
        result: list[TicketInfo] = []
        for index in range(await items.count()):
            item = items.nth(index)
            level = (await item.locator(".ticket-display-name").inner_text()).strip()
            seat_description = (await item.locator(".ticket-seat-desc").inner_text()).strip()
            tags = ""
            tag_locator = item.locator(".ticket-tag-container")
            if await tag_locator.count():
                tags = (await tag_locator.inner_text()).strip()
            unit_price = parse_price(await item.locator(".price-display").inner_text())
            listing_id = listing_fingerprint(
                str(session_id), level, unit_price, seat_description, tags
            )
            result.append(
                TicketInfo(
                    platform=self.name,
                    event_id=str(event_id),
                    event_name=title,
                    session_id=str(session_id),
                    session_name=session_name,
                    ticket_level=level,
                    unit_price=unit_price,
                    total_price=unit_price * task.quantity,
                    available_quantity=actual_count,
                    detail_url=task.event_url,
                    listing_id=listing_id,
                    seller_id=tags,
                    area=level,
                    stand="看台" if "看台" in level else None,
                    seat=seat_description or None,
                    adjacent=adjacent,
                    raw={
                        "session_id": str(session_id),
                        "session_name": session_name,
                        "ticket_name": level,
                        "seat_description": seat_description,
                        "seller_tags": tags,
                        "listing_index": index,
                        "unit_price": str(unit_price),
                        "ticket_count": actual_count,
                        "listing_id": listing_id,
                        "idempotency_scope": "motianlun-default-profile",
                    },
                )
            )
        return result

    async def _find_listing(self, page: Any, ticket: TicketInfo) -> Any | None:
        items = page.locator(".ticket-container .ticket-item")
        expected_name = str(ticket.raw.get("ticket_name", ticket.ticket_level))
        for index in range(await items.count()):
            item = items.nth(index)
            name = await item.locator(".ticket-display-name").inner_text()
            seat = await item.locator(".ticket-seat-desc").inner_text()
            price = parse_price(await item.locator(".price-display").inner_text())
            tag_locator = item.locator(".ticket-tag-container")
            tags = (await tag_locator.inner_text()).strip() if await tag_locator.count() else ""
            current_id = listing_fingerprint(
                ticket.session_id, name, price, seat, tags
            )
            if current_id == ticket.listing_id and compact_text(name) == compact_text(expected_name):
                return item
        return None

    async def close(self) -> None:
        await self.session.close()
