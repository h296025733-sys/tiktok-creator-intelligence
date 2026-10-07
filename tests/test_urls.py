import pytest

from creator_intel.errors import InvalidTikTokUrl
from creator_intel.urls import TikTokUrlKind, parse_tiktok_url
from creator_intel.providers import YtDlpProvider


def test_profile_url() -> None:
    parsed = parse_tiktok_url("https://www.tiktok.com/@creator_name?lang=en")
    assert parsed.kind == TikTokUrlKind.PROFILE
    assert parsed.username == "creator_name"
    assert parsed.canonical_url == "https://www.tiktok.com/@creator_name"


def test_video_url() -> None:
    parsed = parse_tiktok_url("https://www.tiktok.com/@creator/video/1234567890123456789")
    assert parsed.kind == TikTokUrlKind.VIDEO
    assert parsed.video_id == "1234567890123456789"


@pytest.mark.parametrize("value", ["not a url", "https://example.com/@x", "https://vm.tiktok.com/abc", "https://www.tiktok.com/explore"])
def test_invalid_urls(value: str) -> None:
    with pytest.raises(InvalidTikTokUrl):
        parse_tiktok_url(value)


def test_normalizer_keeps_url_username_when_uploader_id_is_numeric() -> None:
    video = YtDlpProvider()._normalize_video(
        {"id": "12345", "uploader_id": "999999", "description": "fixture"}, "real_username"
    )
    assert video.creator_username == "real_username"
    assert video.creator_id == "999999"


@pytest.mark.parametrize("key", ["paid_partnership", "commercial_content", "brandedContent"])
def test_normalizer_promotes_exact_commercial_boolean_to_structured_ad(key: str) -> None:
    video = YtDlpProvider()._normalize_video(
        {
            "id": "12345",
            "description": "ordinary words",
            key: True,
        },
        "creator",
    )
    assert video.platform_is_ad is True
    assert video.raw_metadata["provider_structured_ad_paths"] == [f"$.{key}"]


def test_normalizer_does_not_promote_commercial_words_as_boolean_flags() -> None:
    video = YtDlpProvider()._normalize_video(
        {"id": "12345", "paid_partnership": "paid partnership tutorial"},
        "creator",
    )
    assert video.platform_is_ad is None


def test_normalizer_does_not_leak_nested_author_commercial_flag() -> None:
    video = YtDlpProvider()._normalize_video(
        {"id": "12345", "author": {"paid_partnership": True}},
        "creator",
    )
    assert video.platform_is_ad is None


def test_normalizer_does_not_treat_generic_commerce_info_as_product_anchor() -> None:
    video = YtDlpProvider()._normalize_video(
        {"id": "12345", "commerce_info": {"seller": "example", "region": "US"}},
        "creator",
    )
    assert video.has_product_anchor is None
    assert video.product_ids == []
    assert video.product_data == []
