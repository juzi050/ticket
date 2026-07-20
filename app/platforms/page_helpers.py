from __future__ import annotations

import hashlib
import re
from decimal import Decimal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.models import LockStatus


SAFE_ORDER_SUBMIT_PATTERN = re.compile(r"^(提交订单|确认订单|确认下单)$")
PAYMENT_ACTION_PATTERN = re.compile(r"(立即支付|确认支付|去支付|付款)")


def final_price_is_safe(final_total: object | None, max_total: object) -> bool:
    return final_total is not None and final_total <= max_total


def is_safe_order_submit_label(label: str) -> bool:
    text = compact_text(label)
    return bool(SAFE_ORDER_SUBMIT_PATTERN.fullmatch(text)) and not PAYMENT_ACTION_PATTERN.search(text)


def listing_fingerprint(
    session_id: str, ticket_level: str, price: object, seat_description: str, seller_label: str
) -> str:
    """页面无真实票品 ID 时，使用已验证可读字段生成稳定指纹。"""
    raw = "|".join(
        compact_text(str(value))
        for value in (session_id, ticket_level, price, seat_description, seller_label)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
from app.services.ticket_matcher import parse_price


_WEEKDAY_PATTERN = re.compile(r"周[一二三四五六日天]")
_SENSITIVE_QUERY_KEYS = {"accesstoken", "token", "authorization", "cookie", "sessiontoken"}


def compact_text(value: str | None) -> str:
    text = _WEEKDAY_PATTERN.sub("", value or "")
    return re.sub(r"[\s年月日./:：\-_]+", "", text).casefold()


def matches_text(actual: str, targets: list[str], mode: str = "contains") -> bool:
    if not targets:
        return True
    normalized = compact_text(actual)
    candidates = [compact_text(item) for item in targets if item]
    if mode == "exact":
        return normalized in candidates
    return any(candidate in normalized or normalized in candidate for candidate in candidates)


def matches_session(
    actual: str,
    targets: list[str],
    event_date: str | None,
    event_time: str | None,
    mode: str = "contains",
) -> bool:
    if not matches_text(actual, targets, mode):
        return False
    normalized = compact_text(actual)
    return all(
        compact_text(value) in normalized
        for value in (event_date, event_time)
        if value and compact_text(value)
    )


def event_id_from_url(url: str, query_name: str | None = None) -> str:
    parts = urlsplit(url)
    if query_name:
        values = dict(parse_qsl(parts.query, keep_blank_values=True))
        if values.get(query_name):
            return values[query_name]
    path_value = parts.path.rstrip("/").rsplit("/", 1)[-1]
    return path_value if path_value not in {"", "show-detail"} else ""


def safe_page_url(url: str) -> str:
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in _SENSITIVE_QUERY_KEYS
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def parse_labelled_amount(text: str) -> Decimal | None:
    labels = ("实际应付", "应付金额", "实付金额", "订单总额", "合计", "总计")
    for label in labels:
        match = re.search(
            rf"{label}\s*[：:]?\s*[¥￥]?\s*(\d[\d,]*(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )
        if match:
            return parse_price(match.group(1))
    return None


async def visible_body_text(page: object) -> str:
    try:
        return await page.locator("body").inner_text(timeout=3_000)
    except Exception:
        return ""


async def detect_interruption(page: object) -> tuple[LockStatus, str] | None:
    url = str(getattr(page, "url", ""))
    body = await visible_body_text(page)
    if "account-login" in url or "请输入手机号码" in body or "手机号登录" in body:
        return LockStatus.NOT_LOGGED_IN, "平台登录状态已失效"
    if any(word in body for word in ("短信验证码", "手机验证码", "获取验证码")):
        return LockStatus.SMS_REQUIRED, "平台要求短信验证，请在浏览器中手动完成"
    if any(word in body for word in ("滑块验证", "人机验证", "图形验证码", "安全验证")):
        return LockStatus.CAPTCHA_REQUIRED, "平台要求验证码或风控验证，请在浏览器中手动完成"
    return None
