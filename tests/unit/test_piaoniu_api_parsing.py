from app.platforms.piaoniu_api import _activity_id, parse_event, parse_sessions


def test_parse_piaoniu_event() -> None:
    url = "https://www.piaoniu.com/activity/779707"
    payload = {
        "id": 779707,
        "name": "测试演出",
        "events": [
            {"id": 14944160, "specification": "2026.07.25 周六 19:12", "start": 1784977920000}
        ],
    }
    assert _activity_id(url) == "779707"
    event = parse_event(url, payload)
    assert event.event_name == "测试演出"
    sessions = parse_sessions(event.event_id, payload)
    assert sessions[0].session_id == "14944160"
    assert sessions[0].start_time is not None
