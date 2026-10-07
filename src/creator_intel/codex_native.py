"""Two-phase current-Codex workflow with deterministic evidence preparation.

Phase one downloads public media and creates bounded visual evidence without an
API key. The active Codex agent reviews those local assets and writes a strict
JSON record. Phase two validates that record against the prepared evidence and
renders a decision report.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from .config import Settings
from .direct_tiktok import download_public_caption, enrich_from_public_video_page
from .errors import CreatorIntelError
from .io_utils import read_json, sha256_file, write_json, write_text
from .logging_utils import JsonlRunLogger
from .media import (
    create_contact_sheets,
    detect_scenes,
    download_video,
    extract_audio,
    extract_keyframes,
    probe_media,
)
from .models import (
    CodexCreatorReview,
    CodexPreparationJob,
    CodexVideoAsset,
    CreatorMetadataSummary,
    CreatorProfile,
    CreatorVideo,
    PipelineStage,
    ProductBrief,
    ReviewTimelineBeat,
    VideoRankingEntry,
    strict_json_schema,
)
from .pipeline import ProfileRunResult, analyze_profile
from .performance import rank_by_relative, rank_by_views, select_candidates
from .storage import Database


@dataclass(slots=True)
class CodexPreparationResult:
    job_path: Path
    instructions_path: Path
    schema_path: Path
    review_output_path: Path
    profile_result: ProfileRunResult
    job: CodexPreparationJob


def _select_top_commercial_by_views(
    result: ProfileRunResult, commercial_pool: list[CreatorVideo], top: int
) -> list[tuple[CreatorVideo, list[str]]]:
    """Select the highest-view commercial candidates under the user's hard cap."""

    if not commercial_pool:
        return []
    target = min(len(commercial_pool), max(1, top))
    return [
        (video, ["commercial_raw_views"])
        for video in rank_by_views(commercial_pool)[:target]
    ]


def _metadata_summary(
    result: ProfileRunResult, hydration_attempted: int, hydration_succeeded: int
) -> CreatorMetadataSummary:
    statuses = Counter(value.status.value for value in result.classifications.values())
    platform_true = sum(video.platform_is_ad is True for video in result.videos)
    platform_false = sum(video.platform_is_ad is False for video in result.videos)
    return CreatorMetadataSummary(
        videos_discovered=len(result.videos),
        videos_classified=len(result.classifications),
        platform_ad_true=platform_true,
        platform_ad_false=platform_false,
        platform_ad_unknown=len(result.videos) - platform_true - platform_false,
        product_anchor_true=sum(video.has_product_anchor is True for video in result.videos),
        confirmed_commercial=statuses["confirmed"],
        likely_commercial=statuses["likely"],
        possible_commercial=statuses["possible"],
        organic=statuses["organic"],
        unknown=statuses["unknown"],
        direct_hydration_attempted=hydration_attempted,
        direct_hydration_succeeded=hydration_succeeded,
        public_caption_tracks_discovered=sum(len(video.subtitle_tracks) for video in result.videos),
    )


def _ranking_entry(video: CreatorVideo, result: ProfileRunResult) -> VideoRankingEntry:
    classification = result.classifications[video.video_id]
    performance = result.performance[video.video_id]
    return VideoRankingEntry(
        video_id=video.video_id,
        video_url=video.video_url,
        views=video.view_count,
        local_outperformance=performance.local_outperformance,
        system_status=classification.status,
        system_probability=classification.probability,
        platform_is_ad=video.platform_is_ad,
        has_product_anchor=video.has_product_anchor,
    )


def _compute_job_digest(job: CodexPreparationJob) -> str:
    material = job.model_dump(mode="json", exclude={"job_digest"})
    canonical = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def prepare_codex_profile(
    profile_url: str,
    *,
    limit: int = 100,
    top: int = 10,
    product: ProductBrief | None = None,
    force: bool = False,
    no_cache: bool = False,
    output: Path | None = None,
    verbose: bool = False,
    extract_audio_track: bool = True,
    context_videos: int = 5,
    settings: Settings | None = None,
    provider=None,
) -> CodexPreparationResult:
    """Collect a creator and prepare top commercial candidates for active Codex."""

    settings = settings or Settings()
    profile_result = analyze_profile(
        profile_url,
        limit=limit,
        top=top,
        metadata_only=True,
        force=force,
        no_cache=no_cache,
        output=output,
        verbose=verbose,
        settings=settings,
        provider=provider,
        direct_enrichment=True,
    )
    # Every preparation is immutable and run-scoped. Reusing a creator-level
    # review path can silently attach an old product judgment to a new job.
    job_dir = profile_result.report_dir / "codex_native" / profile_result.manifest.run_id
    job_dir.mkdir(parents=True, exist_ok=True)
    product = _materialize_product(product, job_dir)
    database = Database(settings.database_path)
    logger = JsonlRunLogger(
        settings.data_root / "logs" / f"codex_prepare_{profile_result.profile.username}_{profile_result.manifest.run_id}.jsonl",
        verbose=verbose,
    )
    assets: list[CodexVideoAsset] = []
    limitations: list[str] = []
    hydration_attempted = sum(
        video.raw_metadata.get("direct_hydration_attempted") is True
        or "direct_hydration" in video.raw_metadata
        or "direct_enrichment_error" in video.raw_metadata
        for video in profile_result.videos
    )
    hydration_succeeded = sum(
        video.raw_metadata.get("direct_hydration") is True for video in profile_result.videos
    )
    if hydration_attempted and hydration_succeeded < hydration_attempted:
        limitations.append(
            f"Direct hydration enrichment succeeded for {hydration_succeeded}/{hydration_attempted} videos; platform ad/product/caption fields may be incomplete on failed items."
        )
    commercial_pool = select_candidates(
        profile_result.videos,
        profile_result.classifications,
        profile_result.performance,
        top=len(profile_result.videos),
    )
    if not commercial_pool:
        limitations.append(
            "No video met the deterministic commercial-candidate threshold in the collected window; prepared videos are creator-context evidence, not claimed top ads."
        )
    selected_commercial = _select_top_commercial_by_views(profile_result, commercial_pool, top)
    selected: list[tuple[CreatorVideo, str, list[str]]] = [
        (video, "commercial_candidate", bases)
        for video, bases in selected_commercial
    ]
    selected_ids = {video.video_id for video, _, _ in selected}
    context_pool: list[CreatorVideo] = []
    if context_videos > 0:
        commercial_ids = {video.video_id for video in commercial_pool}
        noncommercial = [video for video in profile_result.videos if video.video_id not in commercial_ids]
        for video in rank_by_views(noncommercial):
            if video.video_id not in selected_ids and all(item.video_id != video.video_id for item in context_pool):
                context_pool.append(video)
            if len(context_pool) >= context_videos:
                break
    for video in context_pool:
        bases: list[str] = []
        if video in rank_by_views(profile_result.videos)[:context_videos]:
            bases.append("creator_context_raw_views")
        selected.append((video, "creator_context", bases or ["creator_context_raw_views"]))
    for video, selection_reason, selection_basis in selected:
        classification = profile_result.classifications[video.video_id]
        performance = profile_result.performance[video.video_id]
        asset = _prepare_video_asset(
            video,
            classification=classification,
            performance=performance,
            settings=settings,
            database=database,
            logger=logger,
            job_dir=job_dir,
            force=force,
            no_cache=no_cache,
            extract_audio_track=extract_audio_track,
            selection_reason=selection_reason,
            selection_basis=selection_basis,
        )
        assets.append(asset)
        if asset.error:
            limitations.append(f"{video.video_id}: {asset.error}")

    review_output = job_dir / "codex_review.json"
    schema_path = job_dir / "codex_review.schema.json"
    instructions_path = job_dir / "codex_job.md"
    metadata_summary = _metadata_summary(profile_result, hydration_attempted, hydration_succeeded)
    top_by_views = [
        _ranking_entry(video, profile_result)
        for video in rank_by_views(commercial_pool)[:10]
    ]
    top_by_relative = [
        _ranking_entry(video, profile_result)
        for video in rank_by_relative(commercial_pool, profile_result.performance)[:10]
    ]
    job = CodexPreparationJob(
        schema_version="codex-native-v2",
        job_id=profile_result.manifest.run_id,
        job_digest="pending",
        created_at=datetime.now(UTC),
        creator=profile_result.profile,
        product=product,
        assets=assets,
        metadata_summary=metadata_summary,
        top_commercial_by_views=top_by_views,
        top_commercial_by_relative=top_by_relative,
        source_report_dir=str(profile_result.report_dir.resolve()),
        review_output_path=str(review_output.resolve()),
        review_schema_path=str(schema_path.resolve()),
        instructions_path=str(instructions_path.resolve()),
        limitations=limitations,
    )
    job = job.model_copy(update={"job_digest": _compute_job_digest(job)})
    job_path = job_dir / "codex_job.json"
    write_json(job_path, job)
    write_json(schema_path, strict_json_schema(CodexCreatorReview))
    write_text(instructions_path, _render_job_instructions(job, job_path))
    write_json(
        job_dir / "preparation_status.json",
        {
            "status": "prepared" if any(asset.status == "ready" for asset in assets) else "no_ready_assets",
            "creator": profile_result.profile.username,
            "job_id": job.job_id,
            "job_digest": job.job_digest,
            "selected_assets": len(assets),
            "ready_assets": sum(asset.status == "ready" for asset in assets),
            "failed_assets": sum(asset.status == "failed" for asset in assets),
            "prepared_at": datetime.now(UTC).isoformat(),
            "analysis_executed": False,
        },
    )
    write_json(
        job_dir.parent / "latest_job.json",
        {
            "job_id": job.job_id,
            "job_digest": job.job_digest,
            "job_path": str(job_path.resolve()),
            "prepared_at": job.created_at.isoformat(),
        },
    )
    return CodexPreparationResult(
        job_path=job_path,
        instructions_path=instructions_path,
        schema_path=schema_path,
        review_output_path=review_output,
        profile_result=profile_result,
        job=job,
    )


def finalize_codex_review(job_path: Path, review_path: Path | None = None) -> Path:
    """Validate a current-Codex review and render evidence-linked outputs."""

    job_path = job_path.resolve()
    if not job_path.is_file():
        raise CreatorIntelError(f"Codex job file does not exist: {job_path}")
    try:
        job = CodexPreparationJob.model_validate(read_json(job_path))
    except (ValidationError, json.JSONDecodeError, OSError) as exc:
        raise CreatorIntelError(f"Codex job is invalid: {exc}") from exc
    if job.job_digest != _compute_job_digest(job):
        raise CreatorIntelError("Codex job contents do not match its immutable job digest.")
    job_dir = job_path.parent
    canonical_review = (job_dir / "codex_review.json").resolve()
    if Path(job.review_output_path).resolve() != canonical_review:
        raise CreatorIntelError("Codex job review_output_path is not bound to its run directory.")
    selected_review = (review_path.resolve() if review_path else canonical_review)
    if not selected_review.is_file():
        raise CreatorIntelError(
            f"Codex review file does not exist: {selected_review}. The preparation phase alone is not a completed analysis."
        )
    try:
        review = CodexCreatorReview.model_validate(read_json(selected_review))
    except (ValidationError, json.JSONDecodeError, OSError) as exc:
        raise CreatorIntelError(f"Codex review does not match the required schema: {exc}") from exc
    _validate_review_evidence(job, review, job_path)

    # Re-serialize the validated shape to a destination derived from the trusted
    # job location, never from a mutable path inside the JSON document.
    write_json(canonical_review, review)
    write_json(job_dir / "product_fit.json", review.product_fit)
    write_text(job_dir / "script_negotiation_brief.md", _render_script_brief(review))
    report_path = job_dir / "creator_decision_report.md"
    write_text(report_path, _render_decision_report(job, review))
    failed_assets = sum(asset.status == "failed" for asset in job.assets)
    completion_status = "partial" if failed_assets else "completed"
    write_json(
        job_dir / "completion_status.json",
        {
            "status": completion_status,
            "creator": job.creator.username,
            "job_id": job.job_id,
            "job_digest": job.job_digest,
            "prepared_assets": len(job.assets),
            "ready_assets": sum(asset.status == "ready" for asset in job.assets),
            "failed_assets": failed_assets,
            "reviewed_assets": len(review.video_reviews),
            "analysis_mode": review.analysis_mode,
            "finished_at": datetime.now(UTC).isoformat(),
        },
    )
    database = Database(Settings(project_root=_project_root_from_job(job_path)).database_path)
    for item in review.video_reviews:
        database.set_stage(item.video_id, PipelineStage.CODEX_REVIEWED.value)
    return report_path


def _prepare_video_asset(
    video: CreatorVideo,
    *,
    classification,
    performance,
    settings: Settings,
    database: Database,
    logger: JsonlRunLogger,
    job_dir: Path,
    force: bool,
    no_cache: bool,
    extract_audio_track: bool,
    selection_reason: str,
    selection_basis: list[str],
) -> CodexVideoAsset:
    started = time.monotonic()
    try:
        asset_dir = job_dir / "assets" / video.video_id
        enrichment_error: str | None = None
        if not video.raw_metadata.get("direct_hydration"):
            try:
                video, hydration = enrich_from_public_video_page(video, timeout=settings.ytdlp_timeout)
                if hydration:
                    write_json(
                        settings.creator_data_dir(video.creator_username) / "raw" / f"video_{video.video_id}_hydration.json",
                        hydration,
                    )
            except Exception as exc:
                enrichment_error = f"Direct hydration unavailable: {exc}"
        # Reuse the immutable-by-video-id media cache, then bind a byte-for-byte
        # copy into this run so later cache refreshes cannot mutate evidence.
        media_cache = settings.creator_data_dir(video.creator_username) / "videos"
        video_path = download_video(video, media_cache, settings, force=force)
        if not video_path.resolve().is_relative_to(asset_dir.resolve()):
            # Providers/test doubles may return a cached external path. Bind a
            # byte-for-byte copy into this immutable evidence run before use.
            bound_media = asset_dir / "media" / video_path.name
            bound_media.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(video_path, bound_media)
            video_path = bound_media
        media_info = probe_media(video_path, settings)
        database.set_stage(video.video_id, PipelineStage.DOWNLOADED.value)
        scenes = None if no_cache or force else database.get_scenes(video.video_id, settings.analysis_version)
        if scenes is None:
            scenes = detect_scenes(video_path, media_info)
        scenes = extract_keyframes(
            video_path,
            scenes,
            asset_dir / "frames",
            settings,
            force=force,
        )
        # Individual frames are exposed to Codex when a contact sheet is
        # unclear, so bind and hash them as first-class evidence as well.
        scenes = [
            scene.model_copy(
                update={"keyframes": [str(Path(path).resolve()) for path in scene.keyframes]}
            )
            for scene in scenes
        ]
        frame_paths = [Path(path) for scene in scenes for path in scene.keyframes]
        database.upsert_scenes(video.video_id, settings.analysis_version, scenes)
        sheets = create_contact_sheets(scenes, asset_dir / "contact_sheets")
        if not sheets:
            raise CreatorIntelError("No contact sheet could be created from the downloaded video.")
        audio_path: Path | None = None
        audio_error: str | None = None
        transcript_path: Path | None = None
        transcript_source: str | None = None
        try:
            transcript, caption_path = download_public_caption(
                video, asset_dir / "caption.vtt", timeout=settings.ytdlp_timeout
            )
            if transcript and caption_path:
                transcript_path = asset_dir / "transcript.json"
                write_json(transcript_path, transcript)
                write_text(asset_dir / "transcript.txt", transcript.text)
                transcript_source = transcript.model
        except Exception as exc:
            enrichment_error = "; ".join(filter(None, [enrichment_error, f"Public caption unavailable: {exc}"]))
        if extract_audio_track:
            try:
                audio_path = extract_audio(
                    video_path,
                    asset_dir / "audio.wav",
                    settings,
                    force=force,
                )
            except Exception as exc:  # Visual review remains usable without audio.
                audio_error = f"Audio extraction unavailable: {exc}"
        asset = CodexVideoAsset(
            video=video,
            classification=classification,
            performance=performance,
            status="ready",
            selection_reason=selection_reason,
            selection_basis=selection_basis,
            media_path=str(video_path.resolve()),
            audio_path=str(audio_path.resolve()) if audio_path else None,
            media_info=media_info,
            scenes=scenes,
            contact_sheets=[str(path.resolve()) for path in sheets],
            transcript_path=str(transcript_path.resolve()) if transcript_path else None,
            transcript_source=transcript_source,
            file_sha256=_hash_existing_files(
                [
                    video_path,
                    *frame_paths,
                    *sheets,
                    *([audio_path] if audio_path else []),
                    *([transcript_path] if transcript_path else []),
                ]
            ),
            error="; ".join(filter(None, [enrichment_error, audio_error])) or None,
        )
        database.set_stage(video.video_id, PipelineStage.CODEX_PREPARED.value, asset.error)
        write_json(asset_dir / "asset.json", asset)
        logger.event(
            creator=video.creator_username,
            video_id=video.video_id,
            stage=PipelineStage.CODEX_PREPARED.value,
            status="ok",
            duration=time.monotonic() - started,
            error=asset.error,
        )
        return asset
    except Exception as exc:
        error = str(exc)
        database.set_stage(video.video_id, PipelineStage.FAILED.value, error)
        logger.event(
            creator=video.creator_username,
            video_id=video.video_id,
            stage=PipelineStage.CODEX_PREPARED.value,
            status="failed",
            duration=time.monotonic() - started,
            error=error,
        )
        return CodexVideoAsset(
            video=video,
            classification=classification,
            performance=performance,
            status="failed",
            selection_reason=selection_reason,
            selection_basis=selection_basis,
            error=error,
        )


def _materialize_product(product: ProductBrief | None, job_dir: Path) -> ProductBrief | None:
    if product is None:
        return None
    copied: list[str] = []
    hashes: dict[str, str] = {}
    target_dir = job_dir / "product_inputs"
    for index, raw_path in enumerate(product.image_paths, 1):
        source = Path(raw_path).expanduser().resolve()
        if not source.is_file():
            raise CreatorIntelError(f"Product image does not exist: {source}")
        if source.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}:
            raise CreatorIntelError(f"Unsupported product image type: {source.suffix}")
        _validate_product_image(source)
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / f"{index:02d}_{source.name}"
        shutil.copy2(source, destination)
        resolved = str(destination.resolve())
        copied.append(resolved)
        hashes[resolved] = sha256_file(destination)
    return product.model_copy(update={"image_paths": copied, "image_sha256": hashes})


def _validate_review_evidence(
    job: CodexPreparationJob, review: CodexCreatorReview, job_path: Path
) -> None:
    if review.job_id != job.job_id or review.job_digest != job.job_digest:
        raise CreatorIntelError(
            "Codex review is stale or belongs to a different preparation/product job."
        )
    creator_names = {job.creator.username, f"@{job.creator.username}"}
    if review.creator not in creator_names:
        raise CreatorIntelError(
            f"Review creator {review.creator!r} does not match prepared creator @{job.creator.username}."
        )
    ready_ids = {asset.video.video_id for asset in job.assets if asset.status == "ready"}
    if not ready_ids:
        raise CreatorIntelError(
            "No video asset was prepared successfully; a completed creator review would be unsupported."
        )
    _validate_file_hashes(job, job_path)
    reviewed_ids = [item.video_id for item in review.video_reviews]
    reviewed_commercial_ids = {
        item.video_id
        for item in review.video_reviews
        if item.observed_commercial_status.value in {"confirmed", "likely", "possible"}
    }
    if len(reviewed_ids) != len(set(reviewed_ids)):
        raise CreatorIntelError("Codex review contains duplicate video IDs.")
    if set(reviewed_ids) != ready_ids:
        missing = sorted(ready_ids - set(reviewed_ids))
        unexpected = sorted(set(reviewed_ids) - ready_ids)
        raise CreatorIntelError(
            f"Codex review must cover every ready asset exactly once; missing={missing}, unexpected={unexpected}."
        )
    for item in review.video_reviews:
        if not item.evidence_basis:
            raise CreatorIntelError(f"Video {item.video_id} has no evidence_basis.")
        if item.observed_commercial_status.value in {"confirmed", "likely", "possible"}:
            minimum_confidence = {
                "confirmed": 0.75,
                "likely": 0.50,
                "possible": 0.20,
            }[item.observed_commercial_status.value]
            if not item.commercial_evidence or item.commercial_confidence < minimum_confidence:
                raise CreatorIntelError(
                    f"Commercial read for video {item.video_id} requires non-empty evidence and confidence >= {minimum_confidence:.2f} for status {item.observed_commercial_status.value}."
                )
        for technique in item.filming_techniques:
            if not technique.evidence:
                raise CreatorIntelError(
                    f"Filming technique {technique.name!r} for video {item.video_id} has no evidence."
                )
    references = set(review.product_fit.supporting_video_ids)
    references.update(video_id for pattern in review.shared_patterns for video_id in pattern.video_ids)
    if not references.issubset(ready_ids):
        raise CreatorIntelError(
            f"Codex review cites videos outside the prepared evidence: {sorted(references - ready_ids)}"
        )
    for pattern in review.shared_patterns:
        if len(set(pattern.video_ids)) < 2:
            raise CreatorIntelError(
                f"Shared pattern {pattern.name!r} must cite at least two different prepared videos."
            )
        if not set(pattern.video_ids).issubset(reviewed_commercial_ids):
            raise CreatorIntelError(
                f"Shared commercial pattern {pattern.name!r} cites a video the Codex review did not classify as commercial."
            )
        if not pattern.evidence:
            raise CreatorIntelError(f"Shared pattern {pattern.name!r} has no cross-video evidence.")
        per_video_ids = [item.video_id for item in pattern.evidence_by_video]
        if len(per_video_ids) != len(set(per_video_ids)) or set(per_video_ids) != set(pattern.video_ids):
            raise CreatorIntelError(
                f"Shared pattern {pattern.name!r} must provide exactly one evidence_by_video entry for every supporting video."
            )
        if any(not item.evidence for item in pattern.evidence_by_video):
            raise CreatorIntelError(
                f"Shared pattern {pattern.name!r} contains an empty per-video evidence entry."
            )
    if job.product is None:
        if review.product_understanding is not None:
            raise CreatorIntelError("Review cannot infer a product when no product material was supplied.")
        if (
            review.product_fit.verdict != "not_assessed"
            or review.product_fit.fit_score is not None
            or review.product_fit.confidence != "not_assessed"
            or review.product_fit.supporting_video_ids
            or review.product_fit.matches
            or review.product_fit.mismatches
        ):
            raise CreatorIntelError(
                "Without product material, fit must be not_assessed with null score, not_assessed confidence, no supporting video IDs, and no product matches or mismatches."
            )
    else:
        if review.product_understanding is None:
            raise CreatorIntelError("Product understanding is required when product material was supplied.")
        if (
            review.product_fit.verdict == "not_assessed"
            or review.product_fit.fit_score is None
            or review.product_fit.confidence == "not_assessed"
            or not review.product_fit.supporting_video_ids
        ):
            raise CreatorIntelError(
                "With product material, product fit requires an assessed verdict, numeric score, assessed confidence, and at least one supporting video ID."
            )
        if job.product.image_paths and not review.product_understanding.image_observations:
            raise CreatorIntelError(
                "Product images were supplied, but product_understanding has no image observations."
            )


def _render_job_instructions(job: CodexPreparationJob, job_path: Path) -> str:
    ready = [asset for asset in job.assets if asset.status == "ready"]
    product_lines = (
        [f"- Product description: {job.product.description or 'not provided'}"]
        + [f"- Product image: {path}" for path in job.product.image_paths]
        if job.product
        else ["- No product was supplied; product fit must remain `not_assessed`."]
    )
    asset_lines: list[str] = []
    for asset in ready:
        asset_lines.extend(
            [
                f"### {asset.video.video_id}",
                f"- URL: {asset.video.video_url}",
                f"- Caption: {asset.video.caption or 'unavailable'}",
                f"- Views: {asset.video.view_count}",
                f"- Local performance: {asset.performance.local_outperformance}",
                f"- Commercial status: {asset.classification.status.value} ({asset.classification.probability:.0%})",
                f"- Selection role: {asset.selection_reason}",
                f"- Selection basis: {', '.join(asset.selection_basis)}",
                *[f"- Contact sheet: {path}" for path in asset.contact_sheets],
                f"- Public transcript: {asset.transcript_path or 'unavailable'}",
                f"- Transcript source: {asset.transcript_source or 'unavailable'}",
                f"- Audio file (not speech evidence by itself): {asset.audio_path or 'unavailable'}",
                "",
            ]
        )
    return "\n".join(
        [
            "# Current Codex creator-review job",
            "",
            f"Job JSON: {job_path.resolve()}",
            f"Job ID: {job.job_id}",
            f"Job digest: {job.job_digest}",
            f"Output JSON: {job.review_output_path}",
            f"Required schema: {job.review_schema_path}",
            "",
            "## Required workflow",
            "",
            "1. Read the job JSON and schema completely. Copy job_id and job_digest exactly into the review; never reuse another run's review.",
            "2. Inspect every product image and every contact sheet with the environment's image-viewing tool.",
            "3. Write one review for every asset whose status is `ready`; never review a failed asset.",
            "4. Use only visible frames, a supplied public transcript, deterministic metrics, and explicit metadata. Audio-file existence is not speech evidence.",
            "5. Separate observations from inferences. Put unreadable text, missing speech, sparse frames, or ambiguous product identity in limitations.",
            "6. Reassess broad commercial status from visible/public-caption evidence even for creator-context videos. A positive commercial read needs explicit, localizable commercial_evidence. Minimum confidence is 0.75 for confirmed, 0.50 for likely, and 0.20 for possible. Shared commercial patterns need at least two videos you classified as confirmed/likely/possible, plus evidence_by_video for every supporter; never manufacture a pattern from organic context. Describe performance as association/co-occurrence, never causation.",
            "7. Product fit must be `not_assessed` with a null score and empty matches/mismatches when no product was supplied.",
            "8. Unless the user explicitly requests another language, write every narrative review field in plain Simplified Chinese. Use business language a merchant can act on; explain technical evidence without jargon.",
            "9. Assume the user is preparing to collaborate. Make script_brief concrete and execution-first: what video to make, the opening hook, shot order, required real-product proof, deliverables, commercial terms, and the smallest useful performance test. Put creator profile, risks, and score after the filming recommendation.",
            "10. Write strict JSON to the output path, validate it by running `creator-intel finalize-codex <job-json>`, and fix any validation error.",
            "",
            "## Product evidence",
            "",
            *product_lines,
            "",
            "## Prepared video evidence",
            "",
            *(asset_lines or ["No video assets were prepared successfully."]),
        ]
    )


def _render_decision_report(job: CodexPreparationJob, review: CodexCreatorReview) -> str:
    assets = {asset.video.video_id: asset for asset in job.assets}
    fit = review.product_fit
    brief = review.script_brief
    summary = job.metadata_summary
    ready_count = sum(asset.status == "ready" for asset in job.assets)
    failed_count = sum(asset.status == "failed" for asset in job.assets)
    verdict = _verdict_label(fit.verdict)
    confidence = _confidence_label(fit.confidence)
    completion = "部分完成" if failed_count else "完成"
    lines = [
        "# TikTok 达人合作执行报告",
        "",
        f"- 达人：@{job.creator.username}",
        f"- 本次状态：{completion}",
        f"- 实际范围：选中 {len(job.assets)} 条，成功准备并逐条看完 {len(review.video_reviews)} 条，失败 {failed_count} 条",
        f"- 合作判断：{verdict}",
        f"- 匹配分：{_score(fit.fit_score)}；判断把握：{confidence}",
        "",
        "## 1. 合作的话，建议让对方发什么视频",
        "",
        "### 最推荐的视频方向",
        "",
        brief.recommended_concept,
        "",
        "### 这条视频要达到什么目的",
        "",
        brief.objective,
        "",
        "### 建议镜头顺序",
        "",
    ]
    lines.extend(
        [f"{index}. {item}" for index, item in enumerate(brief.suggested_sequence, 1)]
        or ["1. 暂无可执行的镜头顺序。"]
    )
    lines.extend(["", "### 必须拍到", ""])
    lines.extend([f"- {item}" for item in brief.must_keep] or ["- 暂无明确要求。"])
    lines.extend(["", "### 可以让达人自由发挥", ""])
    lines.extend([f"- {item}" for item in brief.creator_freedom] or ["- 暂无明确范围。"])
    lines.extend(["", "### 不要这样拍", ""])
    lines.extend([f"- {item}" for item in brief.avoid] or ["- 暂无明确禁区。"])
    lines.extend(["", "### 交付清单", ""])
    lines.extend([f"- {item}" for item in brief.deliverables] or ["- 待双方确认。"])
    lines.extend(
        [
            "",
            "### 建议怎么付费和测试",
            "",
            f"- 合作方式：{fit.recommended_collaboration_format}",
            f"- 测试方法：{fit.validation_test}",
            "",
            "### 合作前直接问达人",
            "",
        ]
    )
    lines.extend([f"- {item}" for item in brief.questions_for_creator] or ["- 暂无待确认问题。"])
    lines.extend(
        [
            "",
            "## 2. 这个达人的合作画像",
            "",
            "### 一句话画像",
            "",
            fit.rationale,
            "",
            "### 适合合作的地方",
            "",
        ]
    )
    lines.extend([f"- {item}" for item in fit.matches] or ["- 暂未建立明确匹配点。"])
    lines.extend(["", "### 他反复在用的拍法", ""])
    if review.shared_patterns:
        for pattern in review.shared_patterns:
            lines.extend(
                [
                    f"#### {pattern.name}",
                    "",
                    f"- 大白话：{pattern.description}",
                    f"- 可以怎么用在你的产品上：{pattern.negotiation_use}",
                    f"- 数据上怎么看：{pattern.performance_association}",
                    f"- 依据视频：{', '.join(pattern.video_ids)}",
                    "",
                ]
            )
    else:
        lines.extend(["- 现有证据不足以提炼至少两条广告共同出现的拍法。", ""])
    lines.extend(
        [
            "### 商业发布熟练度",
            "",
            f"- 共读取并分类 {summary.videos_discovered} / {summary.videos_classified} 条公开视频。",
            f"- TikTok 明示广告：{summary.platform_ad_true} 条；商品锚点：{summary.product_anchor_true} 条。",
            f"- 广义商业判断：确认 {summary.confirmed_commercial} 条，较可能 {summary.likely_commercial} 条，可能 {summary.possible_commercial} 条，未知 {summary.unknown} 条。",
            "",
            "## 3. 主要问题和分数",
            "",
            f"- **结论：{verdict}**",
            f"- **匹配分：{_score(fit.fit_score)}**",
            f"- **判断把握：{confidence}**",
            "",
            "### 主要不匹配点",
            "",
        ]
    )
    lines.extend([f"- {item}" for item in fit.mismatches] or ["- 暂未发现明确不匹配点。"])
    lines.extend(["", "### 合作风险", ""])
    lines.extend([f"- {item}" for item in fit.risks] or ["- 暂未记录额外风险。"])
    lines.extend(["", "### 目前还不知道的事", ""])
    lines.extend([f"- {item}" for item in fit.unknowns] or ["- 暂无。"])
    lines.extend(["", "## 4. 数据与证据明细（需要复核时再看）", "", "### 产品理解", ""])
    if review.product_understanding:
        product = review.product_understanding
        lines.extend(
            [
                f"- 产品：{product.summary}",
                f"- 类目：{product.category or '未知'}",
                f"- 可能卖点：{_join(product.apparent_selling_points)}",
                f"- 可能人群：{_join(product.likely_target_customers)}",
                f"- 产品信息缺口：{_join(product.unknowns)}",
            ]
        )
    else:
        lines.append("没有提供产品资料，因此没有做产品匹配判断。")
    lines.extend(
        [
            "",
            "### 本次证据覆盖",
            "",
            f"- 主页视频读取 / 分类：{summary.videos_discovered} / {summary.videos_classified}",
            f"- 视频直链补全成功 / 尝试：{summary.direct_hydration_succeeded} / {summary.direct_hydration_attempted}",
            f"- 发现公开字幕轨道：{summary.public_caption_tracks_discovered}",
            f"- TikTok 明示广告：是 {summary.platform_ad_true}；否 {summary.platform_ad_false}；未知 {summary.platform_ad_unknown}",
            f"- 商品锚点：{summary.product_anchor_true}",
            "",
            "### 广告视频播放量榜单",
            "",
            *_render_ranking_table(job.top_commercial_by_views),
            "",
            "### 达人账号内相对表现榜单",
            "",
            *_render_ranking_table(job.top_commercial_by_relative),
        ]
    )
    lines.extend(
        [
            "",
            "### 实际逐条看过的视频",
            "",
            "| 视频 | 播放量 | 高于账号近期中位数 | 系统商业判断 | 画面复核 | 平台明示广告 |",
            "|---|---:|---:|---|---|---|",
        ]
    )
    for video_review in review.video_reviews:
        asset = assets[video_review.video_id]
        metric = asset.performance.local_outperformance
        relative = f"{metric:.2f}x" if metric is not None else "N/A"
        lines.append(
            f"| [{video_review.video_id}]({asset.video.video_url}) | {_number(asset.video.view_count)} | "
            f"{relative} | {_commercial_status_label(asset.classification.status.value)} ({asset.classification.probability:.0%}) | "
            f"{_commercial_status_label(video_review.observed_commercial_status.value)} ({video_review.commercial_confidence:.0%}) | "
            f"{_tri_state_label(asset.classification.explicit_ad)} |"
        )
    lines.extend(["", "### 每条视频的拍法明细", ""])
    for item in review.video_reviews:
        lines.extend(
            [
                f"### {item.video_id}",
                "",
                f"- 开头：{item.opening_hook}",
                f"- 商业判断：{_commercial_status_label(item.observed_commercial_status.value)}（{item.commercial_confidence:.0%}）— {item.commercial_type}",
                f"- 判断依据：{_join(item.commercial_evidence)}",
                f"- 开头类型：{item.hook_type}",
                f"- 开头时长：{item.hook_duration_seconds}",
                f"- 内容风格：{item.content_style}",
                f"- 产品首次出现：{item.product_first_seen_seconds}",
                f"- 时间线：{_format_timeline(item.structure)}",
                f"- 镜头顺序：{_join(item.shot_sequence)}",
                f"- 拍摄手法：{_format_techniques(item.filming_techniques)}",
                f"- 产品怎么呈现：{item.product_presentation}",
                f"- 怎么证明卖点：{item.proof_method}",
                f"- 镜头与剪辑：{item.camera_and_editing}",
                f"- 达人怎么表现：{item.creator_delivery}",
                f"- 屏幕文字：{item.on_screen_text}",
                f"- 购买引导：{item.call_to_action}",
                f"- 可复用脚本节点：{_join(item.reusable_script_beats)}",
                f"- 优点：{_join(item.strengths)}",
                f"- 问题：{_join(item.weaknesses)}",
                f"- 局限：{_join(item.limitations)}",
                "",
            ]
        )
    lines.extend(
        [
            "### 证据限制",
            "",
            "这份报告结合公开视频数据、平台明示广告信号、字幕和抽帧画面。商品锚点不等于已经证明存在付费合作；只有单独提供字幕时才把语音当作已转写证据；拍法与播放量之间只能说同时出现，不能说一定是因果。",
            "",
            *([f"- {item}" for item in review.limitations + job.limitations] or ["- 暂无额外限制。"]),
            "",
        ]
    )
    return "\n".join(lines)


def _render_script_brief(review: CodexCreatorReview) -> str:
    brief = review.script_brief
    return "\n".join(
        [
            "# 达人视频脚本与合作沟通单",
            "",
            f"## 最推荐的视频方向\n\n{brief.recommended_concept}",
            f"\n## 这条视频要达到什么目的\n\n{brief.objective}",
            "\n## 建议镜头顺序",
            *([f"{index}. {item}" for index, item in enumerate(brief.suggested_sequence, 1)] or ["1. 暂无可执行的镜头顺序。"]),
            "\n## 必须拍到",
            *([f"- {item}" for item in brief.must_keep] or ["- 暂无明确要求。"]),
            "\n## 可以让达人自由发挥",
            *([f"- {item}" for item in brief.creator_freedom] or ["- 暂无明确范围。"]),
            "\n## 不要这样拍",
            *([f"- {item}" for item in brief.avoid] or ["- 暂无明确禁区。"]),
            "\n## 交付清单",
            *([f"- {item}" for item in brief.deliverables] or ["- 待双方确认。"]),
            "\n## 合作前直接问达人",
            *([f"- {item}" for item in brief.questions_for_creator] or ["- 暂无待确认问题。"]),
            "",
        ]
    )


def _project_root_from_job(job_path: Path) -> Path:
    # Expected layout:
    # <project>/reports/<creator>/codex_native/<run_id>/codex_job.json.
    # If an output override changed that layout, using cwd is safer than guessing a
    # broad parent for any filesystem mutation.
    for parent in job_path.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "creator_intel").is_dir():
            return parent
    return Path.cwd()


def _join(values: list[str]) -> str:
    return "; ".join(values) if values else "暂无明确结论"


def _verdict_label(value: str) -> str:
    return {
        "strong_fit": "很适合，建议推进",
        "test_fit": "可以合作，但先小规模测试",
        "weak_fit": "匹配偏弱，谨慎测试",
        "not_fit": "不建议合作",
        "not_assessed": "产品信息不足，暂时无法判断",
    }.get(str(value), str(value))


def _confidence_label(value: str) -> str:
    return {
        "high": "高",
        "medium": "中等",
        "low": "低",
        "not_assessed": "未评估",
    }.get(str(value), str(value))


def _commercial_status_label(value: str) -> str:
    return {
        "confirmed": "确认是商业内容",
        "likely": "很可能是商业内容",
        "possible": "可能是商业内容",
        "organic": "自然内容",
        "unknown": "无法确定",
    }.get(str(value), str(value))


def _score(value: float | None) -> str:
    if value is None:
        return "未评估"
    return f"{value:g}/100"


def _render_ranking_table(entries: list[VideoRankingEntry]) -> list[str]:
    if not entries:
        return ["没有广告候选达到当前证据门槛。"]
    lines = [
        "| 视频 | 播放量 | 高于账号近期中位数 | 商业判断 | 平台明示广告 | 商品锚点 |",
        "|---|---:|---:|---|---|---|",
    ]
    for entry in entries:
        relative = (
            f"{entry.local_outperformance:.2f}x"
            if entry.local_outperformance is not None
            else "N/A"
        )
        lines.append(
            f"| [{entry.video_id}]({entry.video_url}) | {_number(entry.views)} | {relative} | "
            f"{_commercial_status_label(entry.system_status.value)} ({entry.system_probability:.0%}) | "
            f"{_tri_state_label(entry.platform_is_ad)} | "
            f"{_tri_state_label(entry.has_product_anchor)} |"
        )
    return lines


def _tri_state_label(value: bool | None) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "未知"


def _format_techniques(techniques) -> str:
    if not techniques:
        return "暂无明确结论"
    return "; ".join(
        f"{item.name} ({item.confidence}): {item.description} [evidence: {_join(item.evidence)}]"
        for item in techniques
    )


def _format_pattern_evidence(items) -> str:
    if not items:
        return "暂无明确结论"
    return "; ".join(f"{item.video_id}: {_join(item.evidence)}" for item in items)


def _number(value: int | None) -> str:
    return f"{value:,}" if value is not None else "N/A"


def _format_timeline(beats: list[ReviewTimelineBeat]) -> str:
    if not beats:
        return "暂无明确结论"
    return "; ".join(
        f"{beat.start:.1f}-{beat.end:.1f}s {','.join(beat.roles) or 'OTHER'}: {beat.visual}"
        for beat in beats
    )


def _hash_existing_files(paths: list[Path]) -> dict[str, str]:
    output: dict[str, str] = {}
    for path in paths:
        if path.is_file():
            output[str(path.resolve())] = sha256_file(path)
    return output


def _validate_file_hashes(job: CodexPreparationJob, job_path: Path) -> None:
    job_dir = job_path.parent.resolve()
    expected: dict[str, str] = {}
    if job.product:
        product_paths = set(job.product.image_paths)
        if product_paths != set(job.product.image_sha256):
            raise CreatorIntelError(
                "Product image paths do not exactly match the product evidence hash manifest."
            )
        if any(not Path(path).resolve().is_relative_to(job_dir / "product_inputs") for path in product_paths):
            raise CreatorIntelError("Product evidence must stay inside this preparation run directory.")
        expected.update(job.product.image_sha256)
    for asset in job.assets:
        if asset.status != "ready":
            if asset.file_sha256 or asset.media_path or asset.audio_path or asset.scenes or asset.contact_sheets or asset.transcript_path:
                raise CreatorIntelError(
                    f"Failed asset {asset.video.video_id} cannot declare prepared evidence files."
                )
            continue
        keyframes = {path for scene in asset.scenes for path in scene.keyframes}
        declared = {
            path
            for path in [
                asset.media_path,
                asset.audio_path,
                asset.transcript_path,
                *keyframes,
                *asset.contact_sheets,
            ]
            if path
        }
        if not asset.media_path or not asset.contact_sheets or not keyframes:
            raise CreatorIntelError(
                f"Ready asset {asset.video.video_id} is missing media, keyframe, or contact-sheet evidence."
            )
        if declared != set(asset.file_sha256):
            raise CreatorIntelError(
                f"Ready asset {asset.video.video_id} evidence paths do not exactly match its hash manifest."
            )
        asset_dir = job_dir / "assets" / asset.video.video_id
        frames_dir = asset_dir / "frames"
        if any(not Path(path).resolve().is_relative_to(frames_dir) for path in keyframes):
            raise CreatorIntelError(
                f"Ready asset {asset.video.video_id} points keyframe evidence outside its run-scoped frames directory."
            )
        if any(not Path(path).resolve().is_relative_to(asset_dir) for path in declared):
            raise CreatorIntelError(
                f"Ready asset {asset.video.video_id} points evidence outside its run-scoped asset directory."
            )
        expected.update(asset.file_sha256)
    for raw_path, digest in expected.items():
        path = Path(raw_path)
        if not path.is_absolute():
            raise CreatorIntelError(f"Prepared evidence path must be absolute: {path}")
        if not path.is_file():
            raise CreatorIntelError(f"Prepared evidence file is missing: {path}")
        if sha256_file(path) != digest:
            raise CreatorIntelError(f"Prepared evidence file changed after preparation: {path}")


def _validate_product_image(path: Path) -> None:
    import cv2
    import numpy as np

    try:
        encoded = np.fromfile(path, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED) if encoded.size else None
    except (OSError, ValueError) as exc:
        raise CreatorIntelError(f"Could not read product image {path}: {exc}") from exc
    if image is None or image.size == 0:
        raise CreatorIntelError(f"Product image could not be decoded: {path}")
