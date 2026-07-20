from app.auth.session_bridge import (
    CSRF_MARKERS,
    DEVICE_MARKERS,
    AuthSessionBridge,
    _first_matching_value,
)
from app.domain import AuthSession, utc_now


def test_extracts_known_session_markers_without_guessing_values() -> None:
    values = {
        "unrelated": "visible",
        "XSRF-TOKEN": "fake-xsrf",
        "device_id": "fake-device",
    }
    assert _first_matching_value(values, CSRF_MARKERS) == "fake-xsrf"
    assert _first_matching_value(values, DEVICE_MARKERS) == "fake-device"


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
