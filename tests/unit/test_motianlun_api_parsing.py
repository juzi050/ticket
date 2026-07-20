from app.platforms.motianlun_api import _show_id, parse_event


def test_parse_motianlun_event() -> None:
    url = "https://m.motianlun.cn/pages/show-detail/show-detail?showId=show-1"
    event = parse_event(
        url,
        {
            "result": {
                "data": {
                    "showOID": "show-1",
                    "showName": "测试演出",
                    "cityOID": "3301",
                }
            }
        },
    )
    assert _show_id(url) == "show-1"
    assert event.event_name == "测试演出"
    assert event.event_url.endswith("showId=show-1")
