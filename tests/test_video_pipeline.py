from pathlib import Path

import pytest

from creator_intel.config import Settings
from creator_intel.errors import MissingCredentialError
from creator_intel.logging_utils import JsonlRunLogger
from creator_intel.models import (
    CreativeAnalysis,
    CreatorVideo,
    MultimodalAnalysis,
    PipelineStage,
    SceneVisualFinding,
    ScoreReason,
    Transcript,
    TranscriptSegment,
)
from creator_intel.storage import Database
from creator_intel.video_pipeline import analyze_video_from_metadata, analyze_video_url


def score(value: float, reason: str = "fixture evidence") -> ScoreReason:
    return ScoreReason(score=value, reasoning=reason)


class FakeAnalyzer:
    def transcribe(self, video_id: str, audio_path: Path) -> Transcript:
        return Transcript(
            video_id=video_id,
            text="See the result, then try it.",
            segments=[TranscriptSegment(start=0, end=2, text="See the result, then try it.")],
            audio_sha256="fake-hash",
            model="fixture",
        )

    def analyze_video(self, *, scenes, transcript, contact_sheets, prompt) -> MultimodalAnalysis:
        creative = CreativeAnalysis(
            content_style="result_first_demo",
            hook_type="result_first",
            hook_strength=score(8),
            storytelling=score(3),
            problem_clarity=score(5),
            product_clarity=score(8),
            demo_strength=score(7),
            proof_strength=score(7),
            before_after_strength=score(6),
            ugc_authenticity=score(7),
            native_platform_feel=score(8),
            ad_intensity=score(5),
            offer_clarity=score(2),
            cta_strength=score(4),
            editing_pace="fast",
            visual_variety=score(6),
            product_first_seen_sec=0.5,
            strengths=["fast visual proof"],
            weaknesses=["weak offer"],
        )
        return MultimodalAnalysis(
            scenes=[SceneVisualFinding(scene_id=scene.scene_id, scene_role="hook" if scene.scene_id == 1 else "result", product_visible=scene.scene_id > 1) for scene in scenes],
            creative=creative,
        )


def test_missing_api_key_stops_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(MissingCredentialError, match="OPENAI_API_KEY"):
        analyze_video_url("https://www.tiktok.com/@x/video/12345")


def test_full_local_video_pipeline_with_mocked_ai(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(project_root=Path(__file__).parents[1], output_root=tmp_path / "reports", data_root=tmp_path / "data", max_keyframes=6)
    video = CreatorVideo(
        video_id="12345",
        video_url="https://www.tiktok.com/@fixture/video/12345",
        creator_username="fixture",
        view_count=1000,
    )
    database = Database(settings.database_path)
    database.upsert_video(video, "fixture")
    logger = JsonlRunLogger(tmp_path / "run.jsonl")
    monkeypatch.setattr("creator_intel.video_pipeline.download_video", lambda *args, **kwargs: sample_video)
    result = analyze_video_from_metadata(
        video,
        settings=settings,
        database=database,
        force=True,
        no_cache=False,
        logger=logger,
        analyzer=FakeAnalyzer(),
    )
    assert result.stage == PipelineStage.SYNTHESIZED
    assert result.creative and result.creative.hook_type == "result_first"
    assert result.scenes and result.timeline
    assert (settings.creator_data_dir("fixture") / "analysis" / "12345" / "video_analysis.json").is_file()

    class MustNotRunAnalyzer:
        def transcribe(self, *args, **kwargs):
            raise AssertionError("transcription should be served from completed analysis cache")

        def analyze_video(self, *args, **kwargs):
            raise AssertionError("vision should be served from completed analysis cache")

    monkeypatch.setattr(
        "creator_intel.video_pipeline.download_video",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("download should be skipped on cache hit")),
    )
    cached = analyze_video_from_metadata(
        video,
        settings=settings,
        database=database,
        force=False,
        no_cache=False,
        logger=logger,
        analyzer=MustNotRunAnalyzer(),
    )
    assert cached == result
