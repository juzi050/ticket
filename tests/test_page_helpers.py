from decimal import Decimal

from app.platforms.page_helpers import (
    event_id_from_url,
    matches_session,
    parse_labelled_amount,
    safe_page_url,
)
from app.platforms.piaoniu import parse_ticket_groups


def test_matches_session_ignores_date_separators_and_weekday() -> None:
    assert matches_session(
        "2026.07.31 周五 19:00",
        ["2026-07-31 19:00"],
        None,
        None,
    )


def test_matches_session_checks_separate_date_and_time() -> None:
    assert matches_session("2026.08.01 周六 19:30", [], "2026-08-01", "19:30")
    assert not matches_session("2026.08.01 周六 19:30", [], "2026-08-02", "19:30")


def test_parse_labelled_amount() -> None:
    assert parse_labelled_amount("合计：¥1,446.90 明细") == Decimal("1446.90")
    assert parse_labelled_amount("页面仅展示单价 ¥689") is None


def test_event_id_and_sensitive_url_cleanup() -> None:
    url = (
        "https://m.motianlun.cn/pages/show-detail/show-detail"
        "?showId=show-1&accessToken=secret&ticketCount=2"
    )
    assert event_id_from_url(url, "showId") == "show-1"
    cleaned = safe_page_url(url)
    assert "accessToken" not in cleaned
    assert "secret" not in cleaned
    assert "showId=show-1" in cleaned


def test_parse_piaoniu_broken_ticket_group_attribute() -> None:
    attributes = [
        ["class", "item selected"],
        ["data-num", "2"],
        ["data-ticket-groups", "[{"],
        [
            'id":1,"saleprice":689,"areaname":"区域随机',
            "",
        ],
        ["排数随机", ""],
        [
            '座位随机","addition":{"iscontinuousseat":true,"nummax":4},"count":2}]"',
            "",
        ],
    ]
    groups = parse_ticket_groups(attributes)
    assert groups[0]["saleprice"] == 689
    assert groups[0]["areaname"] == "区域随机 排数随机 座位随机"
    assert groups[0]["addition"]["iscontinuousseat"] is True
