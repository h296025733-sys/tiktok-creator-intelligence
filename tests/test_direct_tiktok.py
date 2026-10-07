import json

import pytest

from creator_intel import direct_tiktok
from creator_intel.direct_tiktok import _validated_asset_url, enrich_from_public_video_page, parse_webvtt
from creator_intel.models import CreatorVideo


def test_parse_webvtt_supports_hours_and_markup() -> None:
    text = """WEBVTT

00:00:00.000 --> 00:00:01.300
<c>Hello</c> world

1
01:02:03.400 --> 01:02:05.000 align:start
Later cue
"""
    segments = parse_webvtt(text)
    assert [segment.text for segment in segments] == ["Hello world", "Later cue"]
    assert segments[0].start == 0
    assert segments[1].start == 3723.4


def test_caption_asset_url_is_restricted_to_tiktok_hosts() -> None:
    assert _validated_asset_url("https://v16m-webapp.tiktokcdn-us.com/path")
    assert _validated_asset_url("https://www.tiktok.com/aweme/v1/play/")
    with pytest.raises(ValueError):
        _validated_asset_url("https://evil.example/path")
    with pytest.raises(ValueError):
        _validated_asset_url("http://www.tiktok.com/path")


def test_direct_hydration_merges_each_stat_from_stats_then_v2_then_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = {
        "id": "7654321098765432100",
        "desc": "hydrated",
        "video": {},
        "stats": {
            "playCount": "900",
            "diggCount": None,
            "shareCount": None,
            "collectCount": 0,
        },
        "statsV2": {
            "like_count": "20",
            "share_count": "30",
            "favorite_count": "40",
        },
    }
    page = (
        '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        + json.dumps({"scope": item})
        + "</script>"
    )

    class FakeResponse:
        text = page

        @staticmethod
        def raise_for_status() -> None:
            return None

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        @staticmethod
        def get(_: str) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(direct_tiktok.httpx, "Client", FakeClient)
    original = CreatorVideo(
        video_id="7654321098765432100",
        video_url="https://www.tiktok.com/@creator/video/7654321098765432100",
        creator_username="creator",
        view_count=100,
        like_count=10,
        comment_count=7,
        share_count=8,
        save_count=9,
    )

    enriched, hydration = enrich_from_public_video_page(original)

    assert hydration == item
    assert enriched.view_count == 900  # stats wins
    assert enriched.like_count == 20  # missing/None stats falls through to statsV2
    assert enriched.comment_count == 7  # both hydration sources absent: preserve existing
    assert enriched.share_count == 30  # invalid/None stats falls through to statsV2
    assert enriched.save_count == 0  # zero is a real stats value and must not fall through
    assert enriched.raw_metadata["direct_hydration_attempted"] is True
    assert enriched.raw_metadata["direct_hydration"] is True


def test_direct_hydration_records_attempt_when_exact_item_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = (
        '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
        + json.dumps({"scope": {"id": "different", "video": {}, "stats": {"playCount": 1}}})
        + "</script>"
    )

    class FakeResponse:
        text = page

        @staticmethod
        def raise_for_status() -> None:
            return None

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        @staticmethod
        def get(_: str) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(direct_tiktok.httpx, "Client", FakeClient)
    original = CreatorVideo(
        video_id="7654321098765432100",
        video_url="https://www.tiktok.com/@creator/video/7654321098765432100",
        creator_username="creator",
    )
    enriched, hydration = enrich_from_public_video_page(original)
    assert hydration is None
    assert enriched.raw_metadata["direct_hydration_attempted"] is True
    assert enriched.raw_metadata["direct_hydration"] is False
    assert enriched.raw_metadata["direct_enrichment_error"] == "no matching hydration item"
