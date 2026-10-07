"""Profile analysis orchestration; expensive video stages are delegated to video_pipeline."""

from __future__ import annotations

import hashlib
import json
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .commercial import RuleClassifier
from .config import Settings
from .io_utils import write_json
from .logging_utils import JsonlRunLogger
from .models import (
    CommercialClassification,
    CreatorCommercialProfile,
    CreatorProfile,
    CreatorVideo,
    DataCoverage,
    PerformanceMetrics,
    PatternPerformance,
    PipelineStage,
    RunManifest,
    VideoAnalysis,
)
from .performance import calculate_performance, select_candidates
from .providers import ProfileProvider, YtDlpProvider
from .reports import write_profile_outputs
from .storage import Database


@dataclass(slots=True)
class ProfileRunResult:
    report_dir: Path
    profile: CreatorProfile
    videos: list[CreatorVideo]
    classifications: dict[str, CommercialClassification]
    performance: dict[str, PerformanceMetrics]
    candidates: list[CreatorVideo]
    deep_analyses: list[VideoAnalysis]
    manifest: RunManifest


def analyze_profile(
    profile_url: str,
    *,
    limit: int = 100,
    top: int = 10,
    deep_analysis: bool = False,
    metadata_only: bool = False,
    force: bool = False,
    no_cache: bool = False,
    output: Path | None = None,
    verbose: bool = False,
    settings: Settings | None = None,
    provider: ProfileProvider | None = None,
    direct_enrichment: bool = False,
    direct_enrichment_workers: int = 6,
) -> ProfileRunResult:
    settings = settings or Settings()
    provider = provider or YtDlpProvider(timeout=settings.ytdlp_timeout)
    database = Database(settings.database_path)
    classifier = RuleClassifier(settings.rules_path)
    started_at = datetime.now(UTC)
    run_id = started_at.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    username_hint = profile_url.split("@", 1)[-1].split("/", 1)[0]
    run_log = JsonlRunLogger(settings.data_root / "logs" / f"run_{run_id}.jsonl", verbose=verbose)
    manifest = RunManifest(
        run_id=run_id,
        creator=username_hint,
        started_at=started_at,
        profile_provider=provider.name,
        model=settings.model if deep_analysis else None,
        prompt_version=settings.prompt_version,
        analysis_version=settings.analysis_version,
    )

    fetch_start = time.monotonic()
    try:
        profile, videos, raw = provider.fetch_profile(profile_url, limit)
    except Exception as exc:
        run_log.event(creator=username_hint, stage="FETCHED", status="failed", duration=time.monotonic() - fetch_start, error=str(exc))
        manifest.errors.append(str(exc))
        manifest.failures = 1
        manifest.status = "failed"
        manifest.finished_at = datetime.now(UTC)
        database.upsert_run(run_id, username_hint, manifest)
        raise
    provider_failures = (
        raw.get("_creator_intel_provider_failures", []) if isinstance(raw, dict) else []
    )
    for failure in provider_failures:
        manifest.errors.append(f"Profile entry skipped: {str(failure)[:2000]}")
        manifest.failures += 1
    direct_hydration: dict[str, dict] = {}
    if direct_enrichment and videos:
        videos, direct_hydration = _directly_enrich_videos(
            videos,
            workers=direct_enrichment_workers,
            timeout=settings.ytdlp_timeout,
        )
    run_log.event(creator=profile.username, stage="FETCHED", status="ok", duration=time.monotonic() - fetch_start)
    manifest.creator = profile.username
    manifest.videos_discovered = len(videos)
    database.upsert_profile(profile)
    raw_dir = settings.creator_data_dir(profile.username) / "raw"
    write_json(raw_dir / f"profile_{run_id}.json", raw)
    for video_id, hydration in direct_hydration.items():
        write_json(raw_dir / f"video_{video_id}_hydration.json", hydration)
    normalized_dir = settings.creator_data_dir(profile.username) / "normalized"
    write_json(normalized_dir / "creator_profile.json", profile)
    write_json(normalized_dir / "videos.json", videos)
    (settings.creator_data_dir(profile.username) / "cache").mkdir(parents=True, exist_ok=True)

    classifications: dict[str, CommercialClassification] = {}
    for video in videos:
        fingerprint = _metadata_fingerprint(video)
        database.upsert_video(video, fingerprint)
        classification = classifier.classify(video)
        classifications[video.video_id] = classification
        database.upsert_classification(classification)
        database.set_stage(video.video_id, PipelineStage.CLASSIFIED.value)
    performance = calculate_performance(videos)
    for value in performance.values():
        database.upsert_performance(value, settings.analysis_version)
    candidates = select_candidates(videos, classifications, performance, top)
    manifest.commercial_candidates = len(candidates)

    deep_analyses: list[VideoAnalysis] = []
    if deep_analysis and not metadata_only:
        from .video_pipeline import analyze_video_from_metadata

        for video in candidates:
            try:
                result = analyze_video_from_metadata(
                    video,
                    settings=settings,
                    database=database,
                    force=force,
                    no_cache=no_cache,
                    logger=run_log,
                )
                deep_analyses.append(result)
            except Exception as exc:
                manifest.errors.append(f"{video.video_id}: {exc}")
                manifest.failures += 1
                database.set_stage(video.video_id, PipelineStage.FAILED.value, str(exc))
                run_log.event(
                    creator=profile.username,
                    video_id=video.video_id,
                    stage="FAILED",
                    status="failed",
                    error=str(exc),
                )
                deep_analyses.append(
                    VideoAnalysis(
                        video_id=video.video_id,
                        video_url=video.video_url,
                        stage=PipelineStage.FAILED,
                        error=str(exc),
                    )
                )
    manifest.videos_deep_analyzed = sum(item.stage == PipelineStage.SYNTHESIZED for item in deep_analyses)
    creator_commercial_profile = synthesize_creator_profile(
        profile, classifications, performance, deep_analyses
    )
    coverage = _coverage(videos, classifications, performance, deep_analyses, manifest.errors)
    report_dir = settings.creator_report_dir(profile.username, output)
    video_analysis_dir = report_dir / "video_analysis"
    for result in deep_analyses:
        write_json(video_analysis_dir / f"{result.video_id}.json", result)
    write_profile_outputs(
        report_dir,
        profile,
        videos,
        classifications,
        performance,
        candidates,
        coverage,
        deep_analyses,
        creator_commercial_profile,
    )
    manifest.status = "partial" if manifest.errors else "completed"
    manifest.finished_at = datetime.now(UTC)
    write_json(report_dir / "run.json", manifest)
    database.upsert_run(run_id, profile.username, manifest)
    return ProfileRunResult(
        report_dir=report_dir,
        profile=profile,
        videos=videos,
        classifications=classifications,
        performance=performance,
        candidates=candidates,
        deep_analyses=deep_analyses,
        manifest=manifest,
    )


def synthesize_creator_profile(
    profile: CreatorProfile,
    classifications: dict[str, CommercialClassification],
    performance: dict[str, PerformanceMetrics],
    analyses: list[VideoAnalysis],
) -> CreatorCommercialProfile:
    from collections import Counter

    statuses = Counter(value.status.value for value in classifications.values())
    view_medians = [value.global_median for value in performance.values() if value.global_median is not None]
    complete = [item for item in analyses if item.creative]
    styles = Counter(item.creative.content_style for item in complete if item.creative)
    hooks = Counter(item.creative.hook_type for item in complete if item.creative)
    reveal_times = [
        item.creative.product_first_seen_sec
        for item in complete
        if item.creative and item.creative.product_first_seen_sec is not None
    ]
    strengths = Counter(value for item in complete if item.creative for value in item.creative.strengths)
    weaknesses = Counter(value for item in complete if item.creative for value in item.creative.weaknesses)
    style_performance: dict[str, list[float]] = {}
    for item in complete:
        if not item.creative:
            continue
        relative = performance.get(item.video_id)
        if relative and relative.local_outperformance is not None:
            style_performance.setdefault(item.creative.content_style, []).append(relative.local_outperformance)
    patterns = [
        PatternPerformance(
            pattern=style,
            video_count=len(values),
            median_local_outperformance=statistics.median(values),
        )
        for style, values in style_performance.items()
    ]
    best_patterns = sorted(
        patterns, key=lambda item: item.median_local_outperformance or 0, reverse=True
    )[:5]
    weak_patterns = sorted(
        patterns, key=lambda item: item.median_local_outperformance or 0
    )[:5]
    commercial_count = statuses["confirmed"] + statuses["likely"] + statuses["possible"]
    limitations = []
    if len(complete) < 3:
        limitations.append("Fewer than three completed deep analyses; creative pattern claims are not stable.")
    return CreatorCommercialProfile(
        creator=f"@{profile.username}",
        commercial_video_count=commercial_count,
        confirmed_commercial=statuses["confirmed"],
        likely_commercial=statuses["likely"],
        possible_commercial=statuses["possible"],
        creator_median_views=statistics.median(view_medians) if view_medians else None,
        top_commercial_styles=[name for name, _ in styles.most_common(5)],
        best_hook_types=[name for name, _ in hooks.most_common(5)],
        average_product_first_seen_sec=(statistics.mean(reveal_times) if reveal_times else None),
        best_patterns=best_patterns,
        weak_patterns=weak_patterns,
        commercial_strengths=[name for name, _ in strengths.most_common(5)],
        commercial_weaknesses=[name for name, _ in weaknesses.most_common(5)],
        best_suited_archetypes=[name for name, _ in styles.most_common(3)],
        limitations=limitations,
    )


def _metadata_fingerprint(video: CreatorVideo) -> str:
    material = video.model_dump(
        mode="json",
        include={"video_id", "caption", "hashtags", "view_count", "like_count", "comment_count", "share_count", "duration"},
    )
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


def _coverage(
    videos: list[CreatorVideo],
    classifications: dict[str, CommercialClassification],
    performance: dict[str, PerformanceMetrics],
    analyses: list[VideoAnalysis],
    errors: list[str],
) -> DataCoverage:
    confidence = [item.confidence for item in classifications.values()]
    available = sum(value.raw_views is not None for value in performance.values())
    hydration_attempted = sum(
        video.raw_metadata.get("direct_hydration_attempted") is True
        or "direct_hydration" in video.raw_metadata
        or "direct_enrichment_error" in video.raw_metadata
        for video in videos
    )
    hydration_succeeded = sum(video.raw_metadata.get("direct_hydration") is True for video in videos)
    caption_tracks = sum(len(video.subtitle_tracks) for video in videos)
    limitations = list(errors)
    if videos and available < len(videos):
        limitations.append(f"View counts were unavailable for {len(videos) - available} videos.")
    if hydration_attempted and hydration_succeeded < hydration_attempted:
        limitations.append(
            f"Direct public video-page enrichment succeeded for {hydration_succeeded}/{hydration_attempted} attempted videos; explicit platform ad/product/caption coverage is incomplete for the rest."
        )
    return DataCoverage(
        videos_discovered=len(videos),
        videos_classified=len(classifications),
        videos_deep_analyzed=sum(item.stage == PipelineStage.SYNTHESIZED for item in analyses),
        videos_failed=sum(item.stage == PipelineStage.FAILED for item in analyses),
        commercial_classification_confidence=(statistics.mean(confidence) if confidence else None),
        performance_data_availability=(available / len(videos) if videos else 0.0),
        direct_hydration_attempted=hydration_attempted,
        direct_hydration_succeeded=hydration_succeeded,
        public_caption_tracks_discovered=caption_tracks,
        known_limitations=limitations,
    )


def _directly_enrich_videos(
    videos: list[CreatorVideo], *, workers: int, timeout: int
) -> tuple[list[CreatorVideo], dict[str, dict]]:
    """Best-effort public video-page enrichment without a browser or session."""

    from .direct_tiktok import enrich_from_public_video_page

    output = list(videos)
    hydration: dict[str, dict] = {}
    positions = {video.video_id: index for index, video in enumerate(videos)}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 12))) as executor:
        futures = {
            executor.submit(enrich_from_public_video_page, video, timeout=timeout): video
            for video in videos
        }
        for future in as_completed(futures):
            video = futures[future]
            try:
                enriched, item = future.result()
                output[positions[video.video_id]] = enriched
                if item:
                    hydration[video.video_id] = item
            except Exception as exc:
                output[positions[video.video_id]] = video.model_copy(
                    update={
                        "raw_metadata": video.raw_metadata
                        | {
                            "direct_hydration_attempted": True,
                            "direct_hydration": False,
                            "direct_enrichment_error": str(exc)[:1000],
                        }
                    }
                )
    return output, hydration
