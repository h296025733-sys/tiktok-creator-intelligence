from pathlib import Path

from creator_intel.config import Settings
from creator_intel.errors import ProviderError
from creator_intel.models import CreatorProfile, CreatorVideo, PipelineStage, VideoAnalysis
from creator_intel.pipeline import analyze_profile
from creator_intel.providers import ProfileProvider


class TwoCandidateProvider(ProfileProvider):
    name = "fixture"

    def fetch_profile(self, profile_url: str, limit: int):
        profile = CreatorProfile(username="recovery", profile_url=profile_url, provider="fixture")
        videos = [
            CreatorVideo(
                video_id=str(20000 + index),
                video_url=f"https://www.tiktok.com/@recovery/video/{20000 + index}",
                creator_username="recovery",
                caption="#ad paid partnership",
                platform_labels=["Paid partnership"],
                timestamp=index,
                view_count=1000 * index,
            )
            for index in (1, 2)
        ]
        return profile, videos, {"fixture": True}

    def fetch_video(self, video_url: str):
        raise NotImplementedError


def test_one_video_failure_does_not_abort_creator_report(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings(
        project_root=Path(__file__).parents[1],
        output_root=tmp_path / "reports",
        data_root=tmp_path / "data",
    )
    calls = 0

    def fake_analysis(video, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderError("fixture download failure")
        return VideoAnalysis(
            video_id=video.video_id,
            video_url=video.video_url,
            model=settings.model,
            stage=PipelineStage.SYNTHESIZED,
        )

    monkeypatch.setattr("creator_intel.video_pipeline.analyze_video_from_metadata", fake_analysis)
    result = analyze_profile(
        "https://www.tiktok.com/@recovery",
        top=2,
        deep_analysis=True,
        settings=settings,
        provider=TwoCandidateProvider(),
    )
    assert result.manifest.status == "partial"
    assert result.manifest.failures == 1
    assert len(result.deep_analyses) == 2
    assert (result.report_dir / "report.md").is_file()
    assert "fixture download failure" in (result.report_dir / "report.md").read_text(encoding="utf-8")
