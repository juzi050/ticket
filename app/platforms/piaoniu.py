from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from app.config import BrowserSettings, MonitorTask, PlatformAutomationSettings
from app.exceptions import PlatformError
from app.models import LockOrderRequest, LockOrderResult, LockStatus, MatchResult, TicketInfo
from app.platforms.base import TicketPlatform
from app.platforms.page_helpers import (
    compact_text,
    detect_interruption,
    event_id_from_url,
    matches_session,
    parse_labelled_amount,
    safe_page_url,
    visible_body_text,
)
from app.services.session_service import BrowserSessionService
from app.services.ticket_matcher import TicketMatcher, parse_price


_DATE_PATTERN = re.compile(r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})")


def parse_ticket_groups(attributes: list[list[str]] | list[tuple[str, str]]) -> list[dict[str, Any]]:
    """兼容票牛当前页面中未转义、被浏览器拆成多个属性的 JSON。"""
    for index, (name, value) in enumerate(attributes):
        if name != "data-ticket-groups":
            continue
        candidates = [value]
        suffix = " ".join(part[0] for part in attributes[index + 1 :])
        if suffix:
            rebuilt = value + '"' + suffix
            candidates.append(rebuilt[:-1] if rebuilt.endswith('"') else rebuilt)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue
            return parsed if isinstance(parsed, list) else [parsed]
    return []


class PiaoniuPlatform(TicketPlatform):
    name = "piaoniu"
    display_name = "票牛"

    def __init__(
        self, browser: BrowserSettings, automation: PlatformAutomationSettings | None = None
    ) -> None:
        rules = automation or PlatformAutomationSettings(
            home_url="https://www.piaoniu.com/",
            login_trigger_text="登录",
            authenticated_selectors=[".right-funcs .item-user:visible"],
            unauthenticated_selectors=[".right-funcs .item-login:visible"],
        )
        self.session = BrowserSessionService(self.name, browser, rules)
        self.matcher = TicketMatcher()
        self._page_lock = asyncio.Lock()

    async def initialize(self) -> None:
        await self.session.initialize()

    async def check_login_status(self) -> bool:
        page = await self.session.page()
        # 首页会通过 display 切换右上角的登录/用户入口。直接观察当前页，
        # 避免等待短信或图形验证码时反复刷新并关闭登录弹窗。
        if "piaoniu.com" in page.url:
            user_entry = page.locator(".right-funcs .item-user:visible").first
            if await user_entry.count() and await user_entry.is_visible():
                await self.session.save_state()
                return True
            login_entry = page.locator(".right-funcs .item-login:visible").first
            if await login_entry.count() and await login_entry.is_visible():
                return False
        return await self.session.check_login_status()

    async def open_login_page(self) -> None:
        page = await self.session.page()
        await page.bring_to_front()
        await page.goto(self.session.automation.home_url, wait_until="domcontentloaded")
        trigger = page.locator(".right-funcs .item-login:visible").first
        if not await trigger.count() or not await trigger.is_visible():
            return
        await trigger.click()
        await page.locator(".light-login:visible").first.wait_for(state="visible")

    async def on_login_success(self) -> None:
        if self.session.settings.close_after_login:
            await self.session.close()

    async def search_event(self, task: MonitorTask) -> Any:
        return {"event_id": task.event_id or event_id_from_url(task.event_url), "url": task.event_url}

    async def query_tickets(self, task: MonitorTask) -> Sequence[TicketInfo]:
        async with self._page_lock:
            page = await self.session.page()
            await self._goto_detail(page, task.event_url)
            sessions = await self._candidate_sessions(page, task)
            tickets: list[TicketInfo] = []
            for session_data in sessions:
                await self._select_session(page, session_data)
                tickets.extend(await self._read_selected_session(page, task, session_data))
            return tickets

    async def match_ticket(self, task: MonitorTask, tickets: Sequence[TicketInfo]) -> MatchResult:
        return self.matcher.find_best(task, tickets)

    async def lock_order(self, task: MonitorTask, request: LockOrderRequest) -> LockOrderResult:
        async with self._page_lock:
            page = await self.session.page()
            try:
                await self._goto_detail(page, request.ticket.detail_url)
                await self._select_session(page, request.ticket.raw)
                category = await self._find_category(page, request.ticket.raw)
                if category is None:
                    return LockOrderResult(LockStatus.OUT_OF_STOCK, "目标票档已不可购买")
                await category.click()
                await page.wait_for_timeout(200)
                quantity = page.locator(
                    f'.b2c-num-picker .items .item[data-num="{request.quantity}"]:not(.disabled)'
                ).first
                if not await quantity.count():
                    return LockOrderResult(LockStatus.QUANTITY_INSUFFICIENT, "当前可购数量不足")
                await quantity.click()
                unit_price = await self._visible_price(page)
                estimated_total = unit_price * request.quantity
                if unit_price > request.max_unit_price or estimated_total > request.max_total_price:
                    return LockOrderResult(
                        LockStatus.PRICE_CHANGED,
                        "进入订单前价格已超过配置上限",
                        final_total=estimated_total,
                    )

                buy = page.locator(".b2c-submit .btn-submit:not(.disabled)").first
                if not await buy.count() or not await buy.is_visible():
                    return LockOrderResult(LockStatus.OUT_OF_STOCK, "购买按钮不可用，票品可能已售罄")
                await buy.click()
                await page.wait_for_timeout(1_000)
                interruption = await detect_interruption(page)
                if interruption:
                    status, message = interruption
                    return LockOrderResult(status, message, requires_manual_action=True)

                body = await visible_body_text(page)
                final_total = parse_labelled_amount(body)
                if final_total is None:
                    return LockOrderResult(
                        LockStatus.MANUAL_CONFIRMATION,
                        "已进入票牛订单流程，但未能可靠读取最终应付金额，请人工确认",
                        order_url=safe_page_url(page.url),
                        requires_manual_action=True,
                    )
                if final_total > request.max_total_price:
                    return LockOrderResult(
                        LockStatus.PRICE_CHANGED,
                        "订单确认页实际应付金额超过配置上限，已停止提交",
                        final_total=final_total,
                        order_url=safe_page_url(page.url),
                    )
                if re.search(r"(请选择|添加|填写).{0,8}(观演人|联系人|收货地址|实名)", body):
                    return LockOrderResult(
                        LockStatus.MANUAL_CONFIRMATION,
                        "订单确认页需要补充实名、联系人或地址信息",
                        final_total=final_total,
                        order_url=safe_page_url(page.url),
                        requires_manual_action=True,
                    )

                submit = page.get_by_role(
                    "button", name=re.compile(r"^(提交订单|确认订单|确认下单)$")
                ).first
                if not await submit.count() or not await submit.is_visible():
                    return LockOrderResult(
                        LockStatus.MANUAL_CONFIRMATION,
                        "最终金额已核对；页面没有独立的提交订单按钮，请人工继续",
                        final_total=final_total,
                        order_url=safe_page_url(page.url),
                        requires_manual_action=True,
                    )
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
                if order_match or "待支付" in result_text or re.search(r"/(?:order|pay)", page.url):
                    return LockOrderResult(
                        LockStatus.SUCCESS,
                        "票牛订单已提交并停留在待支付阶段，请手动付款",
                        order_id=order_match.group(1) if order_match else None,
                        final_total=final_total,
                        order_url=safe_page_url(page.url),
                        requires_manual_action=True,
                    )
                return LockOrderResult(
                    LockStatus.MANUAL_CONFIRMATION,
                    "已点击提交订单，但页面未出现可确认的待支付状态，请人工检查",
                    final_total=final_total,
                    order_url=safe_page_url(page.url),
                    requires_manual_action=True,
                )
            except Exception as exc:
                return LockOrderResult(LockStatus.PAGE_CHANGED, f"票牛页面操作失败：{exc}")

    async def _goto_detail(self, page: Any, url: str) -> None:
        await page.goto(url, wait_until="domcontentloaded")
        try:
            await page.locator(".ticket-info").first.wait_for(state="visible")
        except Exception as exc:
            interruption = await detect_interruption(page)
            if interruption:
                raise PlatformError(interruption[1]) from exc
            raise PlatformError("票牛详情页结构已变化或页面加载失败") from exc

    async def _candidate_sessions(self, page: Any, task: MonitorTask) -> list[dict[str, Any]]:
        normal = page.locator(".events-picker:not(.calendar-event-picker) .items .item:not(.disabled)")
        if await normal.count():
            result: list[dict[str, Any]] = []
            for index in range(await normal.count()):
                item = normal.nth(index)
                text = (await item.inner_text()).strip()
                if matches_session(
                    text, task.target_sessions, task.event_date, task.event_time, task.match_mode
                ):
                    result.append(
                        {
                            "session_id": await item.get_attribute("data-id") or text,
                            "session_name": text,
                            "session_kind": "normal",
                        }
                    )
            return result

        dates = self._target_dates(task)
        if not dates:
            selected_month = await page.locator(".calendar-title .month.selected").first.inner_text()
            selected_day = await page.locator(
                ".ui-calendar-date.has-ticket.has-event.selected"
            ).first.inner_text()
            month_match = re.search(r"(20\d{2})[.-](\d{1,2})", selected_month)
            if month_match:
                dates = [
                    f"{int(month_match.group(1)):04d}-{int(month_match.group(2)):02d}-{int(selected_day):02d}"
                ]

        result = []
        for date in dates:
            if not await self._select_calendar_date(page, date):
                continue
            times = page.locator(".eventtime-picker .items .item:not(.disabled)")
            for index in range(await times.count()):
                item = times.nth(index)
                time_text = (await item.inner_text()).strip()
                session_name = time_text if _DATE_PATTERN.search(time_text) else f"{date} {time_text}".strip()
                if matches_session(
                    session_name,
                    task.target_sessions,
                    task.event_date,
                    task.event_time,
                    task.match_mode,
                ):
                    result.append(
                        {
                            "session_id": await item.get_attribute("data-id") or session_name,
                            "session_name": session_name,
                            "session_kind": "calendar",
                            "session_date": date,
                            "session_time": time_text,
                        }
                    )
        return result

    @staticmethod
    def _target_dates(task: MonitorTask) -> list[str]:
        values = [task.event_date or "", *task.target_sessions]
        result: list[str] = []
        for value in values:
            match = _DATE_PATTERN.search(value)
            if match:
                date = f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
                if date not in result:
                    result.append(date)
        return result

    async def _select_calendar_date(self, page: Any, date: str) -> bool:
        year, month, day = (int(part) for part in date.split("-"))
        month_text = f"{year:04d}-{month:02d}"
        months = page.locator(".calendar-event-picker .calendar-title .month")
        for index in range(await months.count()):
            item = months.nth(index)
            if compact_text(await item.inner_text()) == compact_text(month_text):
                await item.click()
                await page.wait_for_timeout(100)
                break
        dates = page.locator(
            ".calendar-event-picker .ui-calendar-date.has-ticket.has-event:not(.next_month):not(.last_month)"
        )
        for index in range(await dates.count()):
            item = dates.nth(index)
            if (await item.inner_text()).strip() == str(day):
                await item.click()
                await page.wait_for_timeout(150)
                return True
        return False

    async def _select_session(self, page: Any, session_data: dict[str, Any]) -> None:
        if session_data.get("session_kind") == "calendar" or session_data.get("session_date"):
            date = str(session_data.get("session_date", ""))
            if date and not await self._select_calendar_date(page, date):
                raise PlatformError("目标日期已不可购买")
            times = page.locator(".eventtime-picker .items .item:not(.disabled)")
            expected = str(session_data.get("session_time") or session_data.get("session_name", ""))
            for index in range(await times.count()):
                item = times.nth(index)
                if compact_text(await item.inner_text()) in compact_text(expected):
                    await item.click()
                    await page.wait_for_timeout(150)
                    return
            raise PlatformError("目标场次已不可购买")

        session_id = str(session_data.get("session_id", ""))
        sessions = page.locator(".events-picker:not(.calendar-event-picker) .items .item:not(.disabled)")
        for index in range(await sessions.count()):
            item = sessions.nth(index)
            item_id = await item.get_attribute("data-id") or ""
            text = await item.inner_text()
            if (session_id and item_id == session_id) or compact_text(text) == compact_text(
                str(session_data.get("session_name", ""))
            ):
                await item.click()
                await page.wait_for_timeout(150)
                return
        raise PlatformError("目标场次已不可购买")

    async def _read_selected_session(
        self, page: Any, task: MonitorTask, session_data: dict[str, Any]
    ) -> list[TicketInfo]:
        title = (await page.locator(".main .head .title").first.inner_text()).strip()
        event_id = task.event_id or event_id_from_url(task.event_url)
        categories = page.locator(".ticket-category .items .item:not(.disabled)")
        result: list[TicketInfo] = []
        for index in range(await categories.count()):
            category = page.locator(".ticket-category .items .item:not(.disabled)").nth(index)
            level = (await category.inner_text()).strip()
            category_id = await category.get_attribute("data-id") or level
            await category.click()
            await page.wait_for_timeout(150)
            unit_price = await self._visible_price(page)
            quantities = page.locator(".b2c-num-picker .items .item[data-num]:not(.disabled)")
            values = [
                int(await quantities.nth(i).get_attribute("data-num") or 0)
                for i in range(await quantities.count())
            ]
            available = max(values, default=0)
            desired = task.quantity if task.quantity in values else available
            if desired:
                await page.locator(
                    f'.b2c-num-picker .items .item[data-num="{desired}"]:not(.disabled)'
                ).first.click()
                await page.wait_for_timeout(100)

            selected_quantity = page.locator(
                ".b2c-num-picker .items .item.selected[data-ticket-groups]"
            ).first
            attributes = await selected_quantity.evaluate(
                "el => Array.from(el.attributes).map(attribute => [attribute.name, attribute.value])"
            )
            groups = parse_ticket_groups(attributes)
            group = self._closest_group(groups, unit_price)
            area_text = str(group.get("areaname", "") or level)
            addition = group.get("addition") if isinstance(group.get("addition"), dict) else {}
            adjacent_value = addition.get("iscontinuousseat")
            adjacent = bool(adjacent_value) if adjacent_value is not None else (True if task.quantity == 1 else None)
            group_max = addition.get("nummax")
            if isinstance(group_max, int):
                available = min(available or group_max, group_max)
            service_fee = self._decimal_or_zero(group.get("splitorderfee"))
            if not service_fee:
                fee = page.locator(".split-order-fee .text:visible").first
                if await fee.count():
                    try:
                        service_fee = parse_price(await fee.inner_text())
                    except ValueError:
                        pass
            row_match = re.search(r"第?\d+排", area_text)
            seat_match = re.search(r"\d+号", area_text)
            result.append(
                TicketInfo(
                    platform=self.name,
                    event_id=event_id,
                    event_name=title,
                    session_id=str(session_data.get("session_id", session_data["session_name"])),
                    session_name=str(session_data["session_name"]),
                    ticket_level=level,
                    unit_price=unit_price,
                    total_price=unit_price * task.quantity,
                    available_quantity=available,
                    detail_url=task.event_url,
                    area=area_text,
                    stand="看台" if "看台" in area_text else None,
                    row=row_match.group() if row_match else None,
                    seat=seat_match.group() if seat_match else None,
                    adjacent=adjacent,
                    service_fee=service_fee,
                    raw={
                        **session_data,
                        "category_id": category_id,
                        "category_name": level,
                        "ticket_group_id": str(group.get("id", "")),
                        "origin_price": str(group.get("originprice", "")),
                        "idempotency_scope": "piaoniu-default-profile",
                    },
                )
            )
        return result

    async def _visible_price(self, page: Any) -> Decimal:
        locator = page.locator(".b2c-result .price-part .value:visible").first
        if not await locator.count():
            raise PlatformError("票牛可购价格元素不存在，页面结构可能已变化")
        return parse_price(await locator.inner_text())

    async def _find_category(self, page: Any, raw: dict[str, Any]) -> Any | None:
        categories = page.locator(".ticket-category .items .item:not(.disabled)")
        category_id = str(raw.get("category_id", ""))
        category_name = str(raw.get("category_name", ""))
        for index in range(await categories.count()):
            item = categories.nth(index)
            if (category_id and await item.get_attribute("data-id") == category_id) or compact_text(
                await item.inner_text()
            ) == compact_text(category_name):
                return item
        return None

    @staticmethod
    def _closest_group(groups: list[dict[str, Any]], unit_price: Decimal) -> dict[str, Any]:
        def distance(group: dict[str, Any]) -> Decimal:
            try:
                return abs(parse_price(str(group.get("saleprice", ""))) - unit_price)
            except ValueError:
                return Decimal("999999999")

        return min(groups, key=distance) if groups else {}

    @staticmethod
    def _decimal_or_zero(value: Any) -> Decimal:
        if value in (None, ""):
            return Decimal("0")
        try:
            return parse_price(str(value))
        except ValueError:
            return Decimal("0")

    async def close(self) -> None:
        await self.session.close()
