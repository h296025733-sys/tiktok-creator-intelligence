from __future__ import annotations

import json
from pathlib import Path

import pytest

from creator_intel.codex_native import (
    _compute_job_digest,
    _select_top_commercial_by_views,
    finalize_codex_review,
    prepare_codex_profile,
)
from creator_intel.config import Settings
from creator_intel.errors import CreatorIntelError
from creator_intel.io_utils import sha256_file, write_json
from creator_intel.models import (
    CommercialClassification,
    CommercialStatus,
    CreatorProfile,
    CreatorVideo,
    PerformanceMetrics,
    ProductBrief,
)
from creator_intel.providers import ProfileProvider


class CodexFixtureProvider(ProfileProvider):
    name = "fixture"

    def __init__(self) -> None:
        self.profile = CreatorProfile(
            username="fixture",
            profile_url="https://www.tiktok.com/@fixture",
            provider=self.name,
        )
        self.videos = [
            CreatorVideo(
                video_id=str(1000 + index),
                video_url=f"https://www.tiktok.com/@fixture/video/{1000 + index}",
                creator_username="fixture",
                caption=("#ad buy now" if index < 2 else "ordinary post"),
                view_count=1000 * (index + 1),
            )
            for index in range(5)
        ]

    def fetch_profile(self, profile_url: str, limit: int):
        return self.profile, self.videos[:limit], {"entries": []}

    def fetch_video(self, video_url: str):
        return self.videos[0], {}


def _review(
    creator: str,
    video_ids: list[str],
    *,
    product: bool,
    job_id: str = "fixture-job",
    job_digest: str = "fixture-digest",
) -> dict:
    return {
        "schema_version": "codex-native-v2",
        "job_id": job_id,
        "job_digest": job_digest,
        "creator": creator,
        "analysis_mode": "current-codex-native",
        "evidence_scope": "fixture contact sheets",
        "product_understanding": {
            "summary": "portable cup",
            "category": "drinkware",
            "apparent_selling_points": ["portable"],
            "likely_target_customers": ["travelers"],
            "image_observations": ["lid"],
            "unknowns": [],
        } if product else None,
        "video_reviews": [
            {
                "video_id": video_id,
                "evidence_basis": ["contact sheet"],
                "commercial_read": "commercial candidate",
                "observed_commercial_status": "likely",
                "commercial_confidence": 0.75,
                "commercial_type": "product demonstration",
                "commercial_evidence": ["product demo"],
                "content_style": "result-first demo",
                "hook_type": "result_first",
                "opening_hook": "result first",
                "hook_duration_seconds": 1.2,
                "product_first_seen_seconds": 0.4,
                "structure": [{"start": 0, "end": 1.2, "visual": "result", "speech": "", "roles": ["HOOK"]}],
                "shot_sequence": ["result", "demo"],
                "filming_techniques": [
                    {"name": "close-up", "description": "product close-up", "evidence": ["scene 1"], "confidence": "medium"}
                ],
                "product_presentation": "hand demo",
                "proof_method": "visible result",
                "camera_and_editing": "fast cuts",
                "creator_delivery": "direct",
                "on_screen_text": "unreadable",
                "call_to_action": "not visible",
                "reusable_script_beats": ["show result"],
                "strengths": ["clear result"],
                "weaknesses": ["CTA not visible"],
                "limitations": ["fixture"],
            }
            for video_id in video_ids
        ],
        "shared_patterns": ([{
            "name": "result first",
            "description": "starts with outcome",
            "video_ids": video_ids[:2],
            "evidence": ["first scenes"],
            "evidence_by_video": [
                {"video_id": video_id, "evidence": [f"{video_id} first scene"]}
                for video_id in video_ids[:2]
            ],
            "confidence": "medium",
            "performance_association": "co-occurred in fixture",
            "negotiation_use": "retain opening result",
        }] if len(video_ids) >= 2 else []),
        "product_fit": {
            "verdict": "test_fit" if product else "not_assessed",
            "fit_score": 70 if product else None,
            "confidence": "medium" if product else "not_assessed",
            "rationale": "visual-demo format is relevant" if product else "no product supplied",
            "matches": ["demo"] if product else [],
            "mismatches": [],
            "supporting_video_ids": video_ids[:1] if product else [],
            "risks": [],
            "recommended_collaboration_format": "demo test" if product else "supply a product first",
            "validation_test": "one paid pilot" if product else "provide product material",
            "unknowns": [],
        },
        "script_brief": {
            "objective": "test conversion",
            "recommended_concept": "result then demo",
            "must_keep": ["result first"],
            "suggested_sequence": ["result", "demo", "CTA"],
            "creator_freedom": ["wording"],
            "avoid": ["unsupported claims"],
            "deliverables": ["one video"],
            "questions_for_creator": ["preferred hook?"],
        },
        "limitations": ["fixture analysis"],
    }


def test_prepare_codex_without_api_key_creates_media_job(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    download_destinations: list[Path] = []

    def cached_download(video, destination, settings, **kwargs):
        download_destinations.append(destination)
        return sample_video

    monkeypatch.setattr("creator_intel.codex_native.download_video", cached_download)
    monkeypatch.setattr(
        "creator_intel.pipeline._directly_enrich_videos", lambda videos, **kwargs: (videos, {})
    )
    monkeypatch.setattr(
        "creator_intel.codex_native.enrich_from_public_video_page", lambda video, **kwargs: (video, None)
    )
    monkeypatch.setattr(
        "creator_intel.codex_native.download_public_caption", lambda *args, **kwargs: (None, None)
    )
    settings = Settings(
        project_root=Path(__file__).parents[1],
        output_root=tmp_path / "reports",
        data_root=tmp_path / "data",
        max_keyframes=4,
    )
    import cv2
    import numpy as np

    product_image = tmp_path / "product.png"
    success, encoded = cv2.imencode(".png", np.full((20, 20, 3), 128, dtype=np.uint8))
    assert success
    product_image.write_bytes(encoded.tobytes())
    prepared = prepare_codex_profile(
        "https://www.tiktok.com/@fixture",
        limit=5,
        top=2,
        context_videos=1,
        product=ProductBrief(description="travel cup", image_paths=[str(product_image)]),
        extract_audio_track=False,
        settings=settings,
        provider=CodexFixtureProvider(),
    )
    assert prepared.job_path.is_file()
    assert len(prepared.job.assets) == 3
    assert all(asset.status == "ready" for asset in prepared.job.assets)
    assert all(asset.contact_sheets for asset in prepared.job.assets)
    assert [asset.video.video_id for asset in prepared.job.assets] == ["1001", "1000", "1004"]
    assert [asset.selection_reason for asset in prepared.job.assets] == [
        "commercial_candidate",
        "commercial_candidate",
        "creator_context",
    ]
    assert download_destinations
    assert set(download_destinations) == {settings.creator_data_dir("fixture") / "videos"}
    assert all(Path(asset.media_path).is_relative_to(prepared.job_path.parent) for asset in prepared.job.assets)
    assert prepared.job.product and prepared.job.product.image_sha256
    assert not prepared.review_output_path.exists()

    review = _review(
        "@fixture",
        [asset.video.video_id for asset in prepared.job.assets],
        product=True,
        job_id=prepared.job.job_id,
        job_digest=prepared.job.job_digest,
    )
    prepared.review_output_path.write_text(json.dumps(review), encoding="utf-8")
    report = finalize_codex_review(prepared.job_path)
    assert report.is_file()
    report_text = report.read_text(encoding="utf-8")
    expected_sections = [
        "## 1. 合作的话，建议让对方发什么视频",
        "## 2. 这个达人的合作画像",
        "## 3. 主要问题和分数",
        "## 4. 数据与证据明细（需要复核时再看）",
    ]
    assert all(section in report_text for section in expected_sections)
    assert [report_text.index(section) for section in expected_sections] == sorted(
        report_text.index(section) for section in expected_sections
    )
    assert report_text.index("### 建议镜头顺序") < report_text.index("### 本次证据覆盖")
    script_path = report.parent / "script_negotiation_brief.md"
    assert script_path.is_file()
    script_text = script_path.read_text(encoding="utf-8")
    assert "# 达人视频脚本与合作沟通单" in script_text
    assert script_text.index("## 最推荐的视频方向") < script_text.index("## 合作前直接问达人")
    assert json.loads((report.parent / "completion_status.json").read_text())["status"] == "completed"


def test_finalize_rejects_one_video_shared_pattern(tmp_path: Path) -> None:
    # Pydantic-level required-field behavior is also exercised without preparing media.
    review = _review("@x", ["1"], product=False)
    review["shared_patterns"] = [{
        "name": "fake shared",
        "description": "only one video",
        "video_ids": ["1"],
        "evidence": ["one frame"],
        "evidence_by_video": [{"video_id": "1", "evidence": ["one frame"]}],
        "confidence": "low",
        "performance_association": "unknown",
        "negotiation_use": "none",
    }]
    from creator_intel.models import CodexCreatorReview

    parsed = CodexCreatorReview.model_validate(review)
    assert parsed.shared_patterns[0].video_ids == ["1"]


def test_finalize_rejects_changed_evidence_and_single_video_pattern(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("creator_intel.codex_native.download_video", lambda *args, **kwargs: sample_video)
    monkeypatch.setattr("creator_intel.pipeline._directly_enrich_videos", lambda videos, **kwargs: (videos, {}))
    monkeypatch.setattr("creator_intel.codex_native.enrich_from_public_video_page", lambda video, **kwargs: (video, None))
    monkeypatch.setattr("creator_intel.codex_native.download_public_caption", lambda *args, **kwargs: (None, None))
    settings = Settings(
        project_root=Path(__file__).parents[1],
        output_root=tmp_path / "reports",
        data_root=tmp_path / "data",
        max_keyframes=4,
    )
    prepared = prepare_codex_profile(
        "https://www.tiktok.com/@fixture",
        limit=5,
        top=2,
        context_videos=0,
        extract_audio_track=False,
        settings=settings,
        provider=CodexFixtureProvider(),
    )
    ids = [asset.video.video_id for asset in prepared.job.assets]
    changed_review = _review(
        "@fixture", ids, product=False,
        job_id=prepared.job.job_id, job_digest=prepared.job.job_digest,
    )
    prepared.review_output_path.write_text(json.dumps(changed_review), encoding="utf-8")
    sheet = Path(prepared.job.assets[0].contact_sheets[0])
    sheet.write_bytes(sheet.read_bytes() + b"changed")
    with pytest.raises(CreatorIntelError, match="changed after preparation"):
        finalize_codex_review(prepared.job_path)

    # Rebuild pristine evidence, then show the cross-video threshold is enforced by finalization.
    prepared = prepare_codex_profile(
        "https://www.tiktok.com/@fixture",
        limit=5,
        top=2,
        context_videos=0,
        extract_audio_track=False,
        force=True,
        settings=settings,
        provider=CodexFixtureProvider(),
    )
    one_video_pattern = _review(
        "@fixture", [asset.video.video_id for asset in prepared.job.assets], product=False,
        job_id=prepared.job.job_id, job_digest=prepared.job.job_digest,
    )
    one_video_pattern["shared_patterns"][0]["video_ids"] = [prepared.job.assets[0].video.video_id]
    one_video_pattern["shared_patterns"][0]["evidence_by_video"] = [{
        "video_id": prepared.job.assets[0].video.video_id,
        "evidence": ["first scene"],
    }]
    prepared.review_output_path.write_text(json.dumps(one_video_pattern), encoding="utf-8")
    with pytest.raises(CreatorIntelError, match="at least two different"):
        finalize_codex_review(prepared.job_path)


def _prepare_one(
    sample_video: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    product: ProductBrief | None = None,
):
    monkeypatch.setattr("creator_intel.codex_native.download_video", lambda *args, **kwargs: sample_video)
    monkeypatch.setattr("creator_intel.pipeline._directly_enrich_videos", lambda videos, **kwargs: (videos, {}))
    monkeypatch.setattr("creator_intel.codex_native.enrich_from_public_video_page", lambda video, **kwargs: (video, None))
    monkeypatch.setattr("creator_intel.codex_native.download_public_caption", lambda *args, **kwargs: (None, None))
    settings = Settings(
        project_root=Path(__file__).parents[1],
        output_root=tmp_path / "reports",
        data_root=tmp_path / "data",
        max_keyframes=2,
    )
    return prepare_codex_profile(
        "https://www.tiktok.com/@fixture",
        limit=2,
        top=1,
        context_videos=0,
        product=product,
        extract_audio_track=False,
        settings=settings,
        provider=CodexFixtureProvider(),
    )


def test_review_is_bound_to_exact_run_and_product(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _prepare_one(
        sample_video, tmp_path, monkeypatch,
        product=ProductBrief(description="Product A cup"),
    )
    second = _prepare_one(
        sample_video, tmp_path, monkeypatch,
        product=ProductBrief(description="Product B software"),
    )
    assert first.job_path.parent != second.job_path.parent
    assert first.job.job_id != second.job.job_id
    assert first.job.job_digest != second.job.job_digest
    stale = _review(
        "@fixture",
        [asset.video.video_id for asset in second.job.assets],
        product=True,
        job_id=first.job.job_id,
        job_digest=first.job.job_digest,
    )
    second.review_output_path.write_text(json.dumps(stale), encoding="utf-8")
    with pytest.raises(CreatorIntelError, match="stale|different preparation"):
        finalize_codex_review(second.job_path)


def test_finalize_rejects_redirected_or_unhashed_evidence_path(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare_one(sample_video, tmp_path, monkeypatch)
    asset = prepared.job.assets[0]
    redirected = Path(__file__).parents[1] / "pyproject.toml"
    hashes = dict(asset.file_sha256)
    hashes.pop(asset.contact_sheets[0])
    hashes[str(redirected.resolve())] = sha256_file(redirected)
    changed_asset = asset.model_copy(
        update={"contact_sheets": [str(redirected.resolve())], "file_sha256": hashes}
    )
    changed_job = prepared.job.model_copy(update={"assets": [changed_asset], "job_digest": "pending"})
    changed_job = changed_job.model_copy(update={"job_digest": _compute_job_digest(changed_job)})
    write_json(prepared.job_path, changed_job)
    review = _review(
        "@fixture",
        [changed_asset.video.video_id],
        product=False,
        job_id=changed_job.job_id,
        job_digest=changed_job.job_digest,
    )
    prepared.review_output_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(CreatorIntelError, match="outside its run-scoped asset directory"):
        finalize_codex_review(prepared.job_path)


def test_finalize_requires_evidence_for_positive_commercial_read(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare_one(sample_video, tmp_path, monkeypatch)
    review = _review(
        "@fixture",
        [asset.video.video_id for asset in prepared.job.assets],
        product=False,
        job_id=prepared.job.job_id,
        job_digest=prepared.job.job_digest,
    )
    review["video_reviews"][0]["commercial_evidence"] = []
    prepared.review_output_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(CreatorIntelError, match="requires non-empty evidence"):
        finalize_codex_review(prepared.job_path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda review: review["video_reviews"][0].update(evidence_basis=["   "]),
        lambda review: review["video_reviews"][0].update(commercial_evidence=["   "]),
        lambda review: review["video_reviews"][0]["filming_techniques"][0].update(evidence=["   "]),
    ],
)
def test_finalize_rejects_blank_evidence_strings(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate
) -> None:
    prepared = _prepare_one(sample_video, tmp_path, monkeypatch)
    review = _review(
        "@fixture",
        [asset.video.video_id for asset in prepared.job.assets],
        product=False,
        job_id=prepared.job.job_id,
        job_digest=prepared.job.job_digest,
    )
    mutate(review)
    prepared.review_output_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(CreatorIntelError, match="does not match the required schema"):
        finalize_codex_review(prepared.job_path)


def test_shared_pattern_rejects_blank_cross_video_evidence() -> None:
    from pydantic import ValidationError
    from creator_intel.models import CodexCreatorReview

    review = _review("@fixture", ["1", "2"], product=False)
    review["shared_patterns"][0]["evidence"] = ["   "]
    with pytest.raises(ValidationError):
        CodexCreatorReview.model_validate(review)
    review = _review("@fixture", ["1", "2"], product=False)
    review["shared_patterns"][0]["evidence_by_video"][0]["evidence"] = ["\t"]
    with pytest.raises(ValidationError):
        CodexCreatorReview.model_validate(review)


def test_context_zero_is_a_hard_limit(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare_one(sample_video, tmp_path, monkeypatch)
    assert len(prepared.job.assets) == 1
    assert prepared.job.assets[0].selection_reason == "commercial_candidate"


def test_finalize_rejects_changed_individual_keyframe(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare_one(sample_video, tmp_path, monkeypatch)
    asset = prepared.job.assets[0]
    frame = Path(asset.scenes[0].keyframes[0])
    assert str(frame.resolve()) in asset.file_sha256
    frame.write_bytes(frame.read_bytes() + b"changed")
    review = _review(
        "@fixture",
        [asset.video.video_id],
        product=False,
        job_id=prepared.job.job_id,
        job_digest=prepared.job.job_digest,
    )
    prepared.review_output_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(CreatorIntelError, match="changed after preparation"):
        finalize_codex_review(prepared.job_path)


def test_finalize_rejects_product_claims_without_product_and_low_confidence(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = _prepare_one(sample_video, tmp_path, monkeypatch)
    asset_id = prepared.job.assets[0].video.video_id
    review = _review(
        "@fixture",
        [asset_id],
        product=False,
        job_id=prepared.job.job_id,
        job_digest=prepared.job.job_digest,
    )
    review["product_fit"]["matches"] = ["unsupported perfect product match"]
    prepared.review_output_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(CreatorIntelError, match="no product matches or mismatches"):
        finalize_codex_review(prepared.job_path)

    review["product_fit"]["matches"] = []
    review["video_reviews"][0]["observed_commercial_status"] = "confirmed"
    review["video_reviews"][0]["commercial_confidence"] = 0.01
    prepared.review_output_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(CreatorIntelError, match="confidence >= 0.75"):
        finalize_codex_review(prepared.job_path)


def test_finalize_marks_partial_when_a_selected_asset_failed(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def partly_failing_download(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CreatorIntelError("fixture download failed")
        return sample_video

    monkeypatch.setattr("creator_intel.codex_native.download_video", partly_failing_download)
    monkeypatch.setattr("creator_intel.pipeline._directly_enrich_videos", lambda videos, **kwargs: (videos, {}))
    monkeypatch.setattr("creator_intel.codex_native.enrich_from_public_video_page", lambda video, **kwargs: (video, None))
    monkeypatch.setattr("creator_intel.codex_native.download_public_caption", lambda *args, **kwargs: (None, None))
    settings = Settings(
        project_root=Path(__file__).parents[1],
        output_root=tmp_path / "reports",
        data_root=tmp_path / "data",
        max_keyframes=2,
    )
    prepared = prepare_codex_profile(
        "https://www.tiktok.com/@fixture",
        limit=5,
        top=2,
        context_videos=0,
        extract_audio_track=False,
        settings=settings,
        provider=CodexFixtureProvider(),
    )
    ready_ids = [asset.video.video_id for asset in prepared.job.assets if asset.status == "ready"]
    review = _review(
        "@fixture",
        ready_ids,
        product=False,
        job_id=prepared.job.job_id,
        job_digest=prepared.job.job_digest,
    )
    prepared.review_output_path.write_text(json.dumps(review), encoding="utf-8")
    finalize_codex_review(prepared.job_path)
    completion = json.loads((prepared.job_path.parent / "completion_status.json").read_text())
    assert completion["status"] == "partial"
    assert completion["ready_assets"] == 1
    assert completion["failed_assets"] == 1


def test_commercial_selection_respects_hard_top_limit_and_raw_view_order() -> None:
    from types import SimpleNamespace

    videos = [
        CreatorVideo(video_id="raw", video_url="https://x/raw", creator_username="x", view_count=1_000_000),
        CreatorVideo(video_id="relative", video_url="https://x/relative", creator_username="x", view_count=100),
        CreatorVideo(video_id="explicit", video_url="https://x/explicit", creator_username="x", view_count=500_000, platform_is_ad=True),
        CreatorVideo(video_id="anchor", video_url="https://x/anchor", creator_username="x", view_count=200, has_product_anchor=True),
        CreatorVideo(video_id="composite", video_url="https://x/composite", creator_username="x", view_count=300),
    ]
    classifications = {
        video.video_id: CommercialClassification(
            video_id=video.video_id,
            status=CommercialStatus.LIKELY,
            probability=0.6,
            score=60,
            reasoning="fixture",
        )
        for video in videos
    }
    relative = {"raw": 1, "relative": 10, "explicit": 2, "anchor": 8, "composite": 3}
    performance = {
        video.video_id: PerformanceMetrics(
            video_id=video.video_id,
            raw_views=video.view_count,
            local_outperformance=relative[video.video_id],
        )
        for video in videos
    }
    result = SimpleNamespace(
        classifications=classifications,
        performance=performance,
        candidates=[videos[-1]],
    )
    selected = _select_top_commercial_by_views(result, videos, top=1)
    assert len(selected) == 1
    assert selected[0][0].video_id == "raw"
    assert selected[0][1] == ["commercial_raw_views"]


def test_commercial_selection_does_not_let_relative_rank_displace_high_views() -> None:
    from types import SimpleNamespace

    videos = [
        CreatorVideo(video_id="raw", video_url="https://x/raw", creator_username="x", view_count=1_000_000),
        CreatorVideo(video_id="relative", video_url="https://x/relative", creator_username="x", view_count=100),
        CreatorVideo(video_id="explicit", video_url="https://x/explicit", creator_username="x", view_count=500_000, platform_is_ad=True),
        CreatorVideo(video_id="anchor", video_url="https://x/anchor", creator_username="x", view_count=200, has_product_anchor=True),
        CreatorVideo(video_id="composite", video_url="https://x/composite", creator_username="x", view_count=300),
    ]
    classifications = {
        video.video_id: CommercialClassification(
            video_id=video.video_id,
            status=CommercialStatus.LIKELY,
            probability=0.6,
            score=60,
            reasoning="fixture",
        )
        for video in videos
    }
    relative = {"raw": 1, "relative": 10, "explicit": 2, "anchor": 8, "composite": 3}
    performance = {
        video.video_id: PerformanceMetrics(
            video_id=video.video_id,
            raw_views=video.view_count,
            local_outperformance=relative[video.video_id],
        )
        for video in videos
    }
    result = SimpleNamespace(
        classifications=classifications,
        performance=performance,
        candidates=[videos[-1]],
    )
    selected = _select_top_commercial_by_views(result, videos, top=3)
    assert [video.video_id for video, _ in selected] == ["raw", "explicit", "composite"]
    assert all(bases == ["commercial_raw_views"] for _, bases in selected)
