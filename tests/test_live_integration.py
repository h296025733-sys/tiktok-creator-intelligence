import os
from pathlib import Path

import pytest

from creator_intel.providers import YtDlpProvider
from creator_intel.direct_tiktok import download_public_caption, enrich_from_public_video_page


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("RUN_TIKTOK_INTEGRATION") != "1", reason="live TikTok integration is opt-in")
def test_live_public_profile_fetch() -> None:
    profile, videos, _ = YtDlpProvider(timeout=30).fetch_profile("https://www.tiktok.com/@tiktok", 2)
    assert profile.username == "tiktok"
    assert videos


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("RUN_TIKTOK_INTEGRATION") != "1", reason="live TikTok integration is opt-in")
def test_live_direct_hydration_and_public_caption(tmp_path: Path) -> None:
    _, videos, _ = YtDlpProvider(timeout=30).fetch_profile("https://www.tiktok.com/@tiktok", 3)
    enriched = []
    for video in videos:
        item, hydration = enrich_from_public_video_page(video, timeout=30)
        assert hydration is not None
        enriched.append(item)
    caption_video = next((video for video in enriched if video.subtitle_tracks), None)
    assert caption_video is not None, "No public caption track was exposed on the three-video live sample."
    transcript, caption_path = download_public_caption(caption_video, tmp_path / "caption.vtt", timeout=30)
    assert caption_path and caption_path.is_file()
    assert transcript and transcript.text and transcript.segments
