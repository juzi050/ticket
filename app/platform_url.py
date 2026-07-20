from __future__ import annotations

from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


PlatformName = Literal["piaoniu", "motianlun"]
_EVENT_QUERY_KEYS = {
    "piaoniu": {"activityId", "id"},
    "motianlun": {"showId", "id"},
}


class UnsupportedPlatformUrl(ValueError):
    pass


def _matches_domain(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


def detect_platform(url: str) -> PlatformName:
    parts = urlsplit(url.strip())
    hostname = (parts.hostname or "").rstrip(".").casefold()
    if parts.scheme not in {"http", "https"} or not hostname or parts.username:
        raise UnsupportedPlatformUrl("请输入票牛或摩天轮的官方演出网址")
    if _matches_domain(hostname, "piaoniu.com"):
        return "piaoniu"
    if _matches_domain(hostname, "motianlun.cn"):
        return "motianlun"
    raise UnsupportedPlatformUrl("仅支持 piaoniu.com 和 motianlun.cn 官方域名")


def normalize_event_url(url: str) -> str:
    platform = detect_platform(url)
    parts = urlsplit(url.strip())
    allowed = _EVENT_QUERY_KEYS[platform]
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=False)
        if key in allowed
    ]
    hostname = (parts.hostname or "").rstrip(".").casefold()
    return urlunsplit(
        (
            "https",
            hostname,
            parts.path or "/",
            urlencode(query),
            "",
        )
    )
