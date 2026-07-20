from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.domain import AuthSession, PlatformName, utc_now


CSRF_MARKERS = ("csrf", "xsrf")
DEVICE_MARKERS = ("device", "deviceid", "did", "fingerprint", "uuid")


def _first_matching_value(
    values: Mapping[str, str], markers: tuple[str, ...]
) -> str | None:
    for key, value in values.items():
        normalized = "".join(character for character in key.lower() if character.isalnum())
        if value and any(marker in normalized for marker in markers):
            return value
    return None


class AuthSessionBridge:
    async def capture_from_browser(
        self,
        platform: PlatformName,
        browser_context: Any,
    ) -> AuthSession:
        raw_cookies = await browser_context.cookies()
        cookies = {
            str(item["name"]): str(item["value"])
            for item in raw_cookies
            if item.get("name") and item.get("value") is not None
        }
        storage: dict[str, str] = {}
        user_agent = ""
        language = "zh-CN"
        current_url = ""
        pages = [page for page in browser_context.pages if not page.is_closed()]
        if pages:
            page = pages[-1]
            current_url = page.url
            browser_values = await page.evaluate(
                """() => ({
                    local: Object.fromEntries(Object.entries(localStorage)),
                    session: Object.fromEntries(Object.entries(sessionStorage)),
                    userAgent: navigator.userAgent,
                    language: navigator.language
                })"""
            )
            storage.update(
                {str(key): str(value) for key, value in browser_values["local"].items()}
            )
            storage.update(
                {str(key): str(value) for key, value in browser_values["session"].items()}
            )
            user_agent = str(browser_values.get("userAgent") or "")
            language = str(browser_values.get("language") or language)

        headers = {"Accept-Language": f"{language},zh;q=0.9"}
        if user_agent:
            headers["User-Agent"] = user_agent
        if current_url:
            headers["Referer"] = current_url
            parsed = urlsplit(current_url)
            if parsed.scheme and parsed.netloc:
                headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"

        candidates = {**cookies, **storage}
        return AuthSession(
            platform=platform,
            cookies=cookies,
            headers=headers,
            csrf_token=_first_matching_value(candidates, CSRF_MARKERS),
            device_id=_first_matching_value(candidates, DEVICE_MARKERS),
            created_at=utc_now(),
        )

    def build_http_client(
        self,
        auth_session: AuthSession,
        *,
        timeout_seconds: float = 20,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            cookies=auth_session.cookies,
            headers=auth_session.headers,
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
        )
