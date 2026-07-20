from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from decimal import Decimal, InvalidOperation

from app.config import MonitorTask
from app.models import MatchResult, TicketInfo


_PRICE_PATTERN = re.compile(r"(?<!\d)(\d[\d,]*(?:\.\d+)?)(?!\d)")
_NUMBER_PATTERN = re.compile(r"\d+")


def parse_price(value: str | int | Decimal) -> Decimal:
    """解析人民币价格；同时出现原价和现价时取最后展示的金额。"""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    matches = _PRICE_PATTERN.findall(str(value).replace("，", ","))
    if not matches:
        raise ValueError(f"无法解析价格：{value!r}")
    try:
        return Decimal(matches[-1].replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"无法解析价格：{value!r}") from exc


def _normalize(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


def _text_matches(actual: str | None, candidates: Iterable[str], mode: str) -> bool:
    values = [_normalize(item) for item in candidates if item]
    if not values:
        return True
    normalized = _normalize(actual)
    if not normalized:
        return False
    if mode == "exact":
        return normalized in values
    return any(candidate in normalized or normalized in candidate for candidate in values)


def _extract_number(value: str | None) -> int | None:
    match = _NUMBER_PATTERN.search(value or "")
    return int(match.group()) if match else None


class TicketMatcher:
    def match(self, task: MonitorTask, ticket: TicketInfo) -> MatchResult:
        reasons: list[str] = []
        if ticket.platform != task.platform and task.platform != "mock":
            reasons.append("平台不匹配")
        if _normalize(task.event_name) not in _normalize(ticket.event_name):
            reasons.append("演出不匹配")
        if task.event_id and task.event_id != ticket.event_id:
            reasons.append("商品编号不匹配")
        if task.target_session_id and task.target_session_id != ticket.session_id:
            reasons.append("场次 ID 不匹配")
        if task.target_listing_id and task.target_listing_id != ticket.listing_id:
            reasons.append("票品 ID 不匹配")
        if task.target_ticket_group_id and task.target_ticket_group_id != ticket.ticket_group_id:
            reasons.append("票组 ID 不匹配")
        if not _text_matches(ticket.session_name, task.target_sessions, task.match_mode):
            reasons.append("场次不匹配")
        if task.event_date and _normalize(task.event_date) not in _normalize(ticket.session_name):
            reasons.append("演出日期不匹配")
        if task.event_time and _normalize(task.event_time) not in _normalize(ticket.session_name):
            reasons.append("演出时间不匹配")
        if not _text_matches(ticket.ticket_level, task.target_ticket_levels, task.match_mode):
            reasons.append("票档不匹配")
        if not _text_matches(ticket.area, task.target_areas, task.match_mode):
            reasons.append("区域不匹配")
        if not _text_matches(ticket.stand, task.target_stands, task.match_mode):
            reasons.append("看台不匹配")

        location = " ".join(filter(None, [ticket.ticket_level, ticket.area, ticket.stand, ticket.row, ticket.seat]))
        if any(_normalize(word) in _normalize(location) for word in task.excluded_keywords):
            reasons.append("命中排除关键词")
        if task.area_regexes and not any(re.search(pattern, ticket.area or "") for pattern in task.area_regexes):
            reasons.append("区域正则不匹配")
        if task.target_seat_positions and not _text_matches(
            " ".join(filter(None, [ticket.row, ticket.seat])), task.target_seat_positions, task.match_mode
        ):
            reasons.append("座位位置不匹配")

        row_number = _extract_number(ticket.row)
        if task.row_min is not None and (row_number is None or row_number < task.row_min):
            reasons.append("排数低于范围")
        if task.row_max is not None and (row_number is None or row_number > task.row_max):
            reasons.append("排数高于范围")
        seat_number = _extract_number(ticket.seat)
        if task.seat_min is not None and (seat_number is None or seat_number < task.seat_min):
            reasons.append("座位号低于范围")
        if task.seat_max is not None and (seat_number is None or seat_number > task.seat_max):
            reasons.append("座位号高于范围")

        if ticket.unit_price > task.max_unit_price:
            reasons.append("单价超过上限")
        if ticket.payable_total > task.max_total_price:
            reasons.append("实际应付总价超过上限")
        if ticket.available_quantity < task.quantity:
            reasons.append("可购数量不足")
        if task.adjacent_seats_required and ticket.adjacent is not True:
            reasons.append("不满足连座要求")

        priority = task.area_priorities.get(ticket.area or "", len(task.area_priorities) + 1)
        return MatchResult(matched=not reasons, reasons=reasons, ticket=ticket, priority=priority)

    def find_best(self, task: MonitorTask, tickets: Sequence[TicketInfo]) -> MatchResult:
        matches = [self.match(task, ticket) for ticket in tickets]
        valid = [result for result in matches if result.matched and result.ticket is not None]
        if valid:
            return min(valid, key=lambda result: (result.priority, result.ticket.payable_total))
        reasons = sorted({reason for result in matches for reason in result.reasons})
        return MatchResult(False, reasons or ["没有可用票"], None)
