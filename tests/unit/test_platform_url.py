import pytest

from app.platform_url import UnsupportedPlatformUrl, detect_platform, normalize_event_url


def test_detects_supported_platform_without_manual_selection() -> None:
    assert detect_platform("https://www.piaoniu.com/activity/779707") == "piaoniu"
    assert (
        detect_platform(
            "https://m.motianlun.cn/pages/show-detail/show-detail?showId=show-1"
        )
        == "motianlun"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://piaoniu.com.example.org/activity/1",
        "https://evilpiaoniu.com/activity/1",
        "https://motianlun.cn.example.org/show/1",
        "javascript:alert(1)",
    ],
)
def test_rejects_similar_or_unsupported_domains(url: str) -> None:
    with pytest.raises(UnsupportedPlatformUrl):
        detect_platform(url)


def test_normalize_removes_tracking_but_keeps_event_identifier() -> None:
    url = (
        "https://m.motianlun.cn/pages/show-detail/show-detail"
        "?showId=6a2fe62c2608110001207f4d&utm_source=sem_baidu&utm_medium=cpc#share"
    )

    assert normalize_event_url(url) == (
        "https://m.motianlun.cn/pages/show-detail/show-detail"
        "?showId=6a2fe62c2608110001207f4d"
    )


def test_piaoniu_activity_path_is_preserved() -> None:
    assert normalize_event_url(
        "http://www.piaoniu.com/activity/779707?utm_source=test"
    ) == "https://www.piaoniu.com/activity/779707"
