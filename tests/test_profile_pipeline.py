from pathlib import Path

from creator_intel.config import Settings
from creator_intel.models import CreatorProfile, CreatorVideo
from creator_intel.pipeline import analyze_profile
from creator_intel.providers import ProfileProvider


class FixtureProvider(ProfileProvider):
    name = "fixture"

    def __init__(self, profile, videos) -> None:
        self.profile = CreatorProfile.model_validate(profile)
        self.videos = [CreatorVideo.model_validate(item) for item in videos]

    def fetch_profile(self, profile_url: str, limit: int):
        return self.profile, self.videos[:limit], {"fixture": True, "entries": self.videos[:limit]}

    def fetch_video(self, video_url: str):
        return self.videos[0], {"fixture": True}


class PartialFixtureProvider(FixtureProvider):
    def fetch_profile(self, profile_url: str, limit: int):
        return self.profile, self.videos[:limit], {
            "fixture": True,
            "entries": self.videos[:limit],
            "_creator_intel_provider_failures": ["one unavailable public entry"],
        }


def test_metadata_pipeline_writes_required_outputs(tmp_path: Path, load_fixture) -> None:
    settings = Settings(project_root=Path(__file__).parents[1], output_root=tmp_path / "reports", data_root=tmp_path / "data")
    result = analyze_profile(
        "https://www.tiktok.com/@fixturecreator",
        limit=12,
        top=5,
        metadata_only=True,
        settings=settings,
        provider=FixtureProvider(load_fixture("tiktok_profile.json"), load_fixture("videos.json")),
    )
    assert len(result.videos) == 12
    assert result.manifest.status == "completed"
    for name in ["creator_profile.json", "videos.csv", "videos.json", "commercial_candidates.csv", "performance_rankings.csv", "report.md", "report.html", "run.json"]:
        assert (result.report_dir / name).is_file(), name
    report = (result.report_dir / "report.md").read_text(encoding="utf-8")
    assert "Data Coverage" in report
    assert "Videos discovered: 12" in report


def test_metadata_pipeline_records_skipped_provider_entry_as_partial(tmp_path: Path, load_fixture) -> None:
    settings = Settings(
        project_root=Path(__file__).parents[1],
        output_root=tmp_path / "reports",
        data_root=tmp_path / "data",
    )
    result = analyze_profile(
        "https://www.tiktok.com/@fixturecreator",
        limit=3,
        top=2,
        metadata_only=True,
        settings=settings,
        provider=PartialFixtureProvider(
            load_fixture("tiktok_profile.json"), load_fixture("videos.json")
        ),
    )
    assert len(result.videos) == 3
    assert result.manifest.status == "partial"
    assert result.manifest.failures == 1
    assert result.manifest.errors == [
        "Profile entry skipped: one unavailable public entry"
    ]
    report = (result.report_dir / "report.md").read_text(encoding="utf-8")
    assert "Profile entry skipped: one unavailable public entry" in report
