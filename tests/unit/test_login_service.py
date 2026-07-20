from app.auth.login_service import PLATFORM_LOGIN_URLS, LoginOptions


def test_login_service_uses_official_login_pages() -> None:
    assert PLATFORM_LOGIN_URLS == {
        "piaoniu": "https://www.piaoniu.com/",
        "motianlun": (
            "https://m.motianlun.cn/package-functional-pages/"
            "account-login/account-login"
        ),
    }


def test_login_options_keep_browser_visible() -> None:
    options = LoginOptions()
    assert options.headless is False
    assert options.timeout_seconds == 600
