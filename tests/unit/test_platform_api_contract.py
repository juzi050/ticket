from app.platforms.http_api import TicketPlatformApi


def test_http_platform_contract_covers_mvp_workflow() -> None:
    assert TicketPlatformApi.__abstractmethods__ == {
        "check_auth",
        "get_event",
        "list_sessions",
        "list_tickets",
        "get_exact_ticket",
        "ensure_remote_buyers",
        "preview_order",
        "create_order",
        "get_order_detail",
        "find_recent_order",
    }
