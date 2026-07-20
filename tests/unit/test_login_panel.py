from app.gui.login_panel import display_login_status


def test_display_login_status_uses_three_required_states() -> None:
    assert display_login_status("logged_in") == "已登录"
    assert display_login_status("logged_out") == "未登录"
    assert display_login_status("auth_expired") == "登录失效"
