from app.auth.session_bridge import (
    CSRF_MARKERS,
    DEVICE_MARKERS,
    AuthSessionBridge,
    _first_matching_value,
)
from app.domain import AuthSession, utc_now


class FakePage:
    url = "https://m.motianlun.cn/pages/mine/mine"

    def is_closed(self) -> bool:
        return False

    async def evaluate(self, _script: str):
        return {
            "local": {
                "auth$_$local_token": "fake-access-token",
                "auth$_$local_tsid": "fake-tsession-id",
            },
            "session": {},
            "userAgent": "browser-agent",
            "language": "zh-CN",
        }


class FakeBrowserContext:
    pages = [FakePage()]

    async def cookies(self):
        return [{"name": "SESSION", "value": "fake-session"}]


def test_extracts_known_session_markers_without_guessing_values() -> None:
    values = {
        "unrelated": "visible",
        "XSRF-TOKEN": "fake-xsrf",
        "device_id": "fake-device",
    }
    assert _first_matching_value(values, CSRF_MARKERS) == "fake-xsrf"
    assert _first_matching_value(values, DEVICE_MARKERS) == "fake-device"


async def test_capture_motianlun_storage_auth_as_official_headers() -> None:
    session = await AuthSessionBridge().capture_from_browser(
        "motianlun", FakeBrowserContext()
    )

    assert session.cookies == {"SESSION": "fake-session"}
    assert session.headers["Access-Token"] == "fake-access-token"
    assert session.headers["TSessionId"] == "fake-tsession-id"
    assert session.headers["X-Requested-With"] == "XMLHttpRequest"
    assert session.headers["source"] == "m_web"
    assert session.headers["src"] == "m_web"
    assert session.headers["ver"] == "6.76.1"


async def test_build_http_client_reuses_cookies_and_browser_headers() -> None:
    session = AuthSession(
        platform="motianlun",
        cookies={"fake_session": "fake-value"},
        headers={"User-Agent": "browser-agent", "Origin": "https://example.com"},
        csrf_token=None,
        device_id=None,
        created_at=utc_now(),
    )
    client = AuthSessionBridge().build_http_client(session)
    try:
        assert client.cookies.get("fake_session") == "fake-value"
        assert client.headers["User-Agent"] == "browser-agent"
        assert client.headers["Origin"] == "https://example.com"
    finally:
        await client.aclose()
