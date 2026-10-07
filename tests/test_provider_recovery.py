from __future__ import annotations

from creator_intel import providers
from creator_intel.providers import YtDlpProvider


def test_profile_provider_keeps_good_entries_when_one_entry_fails(monkeypatch) -> None:
    payload = {
        "entries": [
            {
                "id": "1",
                "webpage_url": "https://www.tiktok.com/@fixture/video/1",
                "description": "first",
                "view_count": 10,
            },
            None,
            {
                "id": "3",
                "webpage_url": "https://www.tiktok.com/@fixture/video/3",
                "description": "third",
                "view_count": 30,
            },
        ],
        "playlist_count": 3,
    }
    seen_options = {}

    class FakeYdl:
        def __init__(self, options):
            seen_options.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def extract_info(self, *_args, **_kwargs):
            seen_options["logger"].error("ERROR: [TikTok] one entry has no formats")
            return payload

        @staticmethod
        def sanitize_info(value):
            return dict(value)

    monkeypatch.setattr(providers, "YoutubeDL", FakeYdl)
    profile, videos, raw = YtDlpProvider().fetch_profile(
        "https://www.tiktok.com/@fixture", 100
    )
    assert seen_options["ignoreerrors"] is True
    assert profile.username == "fixture"
    assert [video.video_id for video in videos] == ["1", "3"]
    assert raw["_creator_intel_provider_failures"] == [
        "ERROR: [TikTok] one entry has no formats"
    ]
