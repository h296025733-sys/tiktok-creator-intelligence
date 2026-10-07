"""Independent single-video deep-analysis pipeline with stage-level caching."""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .ai import OpenAIAnalyzer, align_timeline
from .config import Settings
from .errors import MissingCredentialError
from .io_utils import sha256_file, write_json, write_text
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
    CreatorVideo,
    PipelineStage,
    SceneAnalysis,
    Transcript,
    TimelineSegment,
    VideoAnalysis,
)
from .providers import YtDlpProvider
from .storage import Database


def analyze_video_url(
    video_url: str,
    *,
    settings: Settings | None = None,
    force: bool = False,
    no_cache: bool = False,
    output: Path | None = None,
    verbose: bool = False,
) -> tuple[VideoAnalysis, Path]:
    settings = settings or Settings()
    if not os.getenv("OPENAI_API_KEY"):
        raise MissingCredentialError(
            "analyze-video requires OPENAI_API_KEY. The command performs transcription and multimodal analysis; "
            "use analyze-profile --metadata-only when no key is configured."
        )
    provider = YtDlpProvider(timeout=settings.ytdlp_timeout)
    video, raw = provider.fetch_video(video_url)
    data_dir = settings.creator_data_dir(video.creator_username)
    write_json(data_dir / "raw" / f"video_{video.video_id}.json", raw)
    database = Database(settings.database_path)
    logger = JsonlRunLogger(
        settings.data_root / "logs" / f"video_{video.video_id}_{uuid.uuid4().hex[:8]}.jsonl",
        verbose=verbose,
    )
    result = analyze_video_from_metadata(
        video,
        settings=settings,
        database=database,
        force=force,
        no_cache=no_cache,
        logger=logger,
    )
    report_dir = output or (settings.output_root / video.creator_username / "video_analysis")
    if not report_dir.is_absolute():
        report_dir = settings.project_root / report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"{video.video_id}.json"
    write_json(json_path, result)
    report_path = report_dir / f"{video.video_id}.md"
    write_text(report_path, _single_video_report(result))
    return result, report_path


def analyze_video_from_metadata(
    video: CreatorVideo,
    *,
    settings: Settings,
    database: Database,
    force: bool,
    no_cache: bool,
    logger: JsonlRunLogger,
    analyzer: OpenAIAnalyzer | None = None,
) -> VideoAnalysis:
    if not force and not no_cache:
        cached = database.get_video_analysis(
            video.video_id, settings.analysis_version, settings.prompt_version, settings.model
        )
        if cached and cached.stage == PipelineStage.SYNTHESIZED:
            return cached
    if analyzer is None and not os.getenv("OPENAI_API_KEY"):
        raise MissingCredentialError(
            "Deep analysis requires OPENAI_API_KEY. No video was downloaded for this candidate."
        )
    analyzer = analyzer or OpenAIAnalyzer(settings)
    creator_dir = settings.creator_data_dir(video.creator_username)
    videos_dir = creator_dir / "videos"
    audio_dir = creator_dir / "audio"
    frames_dir = creator_dir / "frames" / video.video_id
    analysis_dir = creator_dir / "analysis" / video.video_id

    started = time.monotonic()
    video_path = download_video(video, videos_dir, settings, force=force)
    database.set_stage(video.video_id, PipelineStage.DOWNLOADED.value)
    logger.event(creator=video.creator_username, video_id=video.video_id, stage="DOWNLOADED", status="ok", duration=time.monotonic() - started)
    media_info = probe_media(video_path, settings)

    scene_started = time.monotonic()
    scenes = None if no_cache or force else database.get_scenes(video.video_id, settings.analysis_version)
    if scenes is None:
        scenes = detect_scenes(video_path, media_info)
        scenes = extract_keyframes(video_path, scenes, frames_dir, settings, force=force)
        database.upsert_scenes(video.video_id, settings.analysis_version, scenes)
    database.set_stage(video.video_id, PipelineStage.SCENE_DETECTED.value)
    logger.event(creator=video.creator_username, video_id=video.video_id, stage="SCENE_DETECTED", status="ok", duration=time.monotonic() - scene_started)

    transcript_started = time.monotonic()
    audio_path = extract_audio(video_path, audio_dir / f"{video.video_id}.wav", settings, force=force)
    audio_hash = sha256_file(audio_path)
    transcript = None if no_cache or force else database.get_transcript(video.video_id, audio_hash, settings.transcription_model)
    if transcript is None:
        transcript = analyzer.transcribe(video.video_id, audio_path)
        database.upsert_transcript(transcript)
        write_json(analysis_dir / "transcript.json", transcript)
        write_text(analysis_dir / "transcript.txt", transcript.text)
    database.set_stage(video.video_id, PipelineStage.TRANSCRIBED.value)
    logger.event(creator=video.creator_username, video_id=video.video_id, stage="TRANSCRIBED", status="ok", duration=time.monotonic() - transcript_started)

    vision_started = time.monotonic()
    sheets = create_contact_sheets(scenes, analysis_dir / "contact_sheets")
    prompt = (settings.prompts_path / "video_synthesizer.md").read_text(encoding="utf-8")
    multimodal = analyzer.analyze_video(scenes=scenes, transcript=transcript, contact_sheets=sheets, prompt=prompt)
    findings = {item.scene_id: item for item in multimodal.scenes}
    enriched_scenes: list[SceneAnalysis] = []
    for scene in scenes:
        finding = findings.get(scene.scene_id)
        enriched_scenes.append(scene.model_copy(update=(finding.model_dump(exclude={"scene_id"}) if finding else {})))
    aligned = [TimelineSegment.model_validate(item) for item in align_timeline(enriched_scenes, transcript)]
    result = VideoAnalysis(
        video_id=video.video_id,
        video_url=video.video_url,
        media_info=media_info,
        scenes=enriched_scenes,
        transcript=transcript,
        timeline=aligned,
        creative=multimodal.creative,
        analysis_version=settings.analysis_version,
        prompt_version=settings.prompt_version,
        model=settings.model,
        stage=PipelineStage.SYNTHESIZED,
    )
    database.set_stage(video.video_id, PipelineStage.VISION_ANALYZED.value)
    database.upsert_video_analysis(result)
    database.set_stage(video.video_id, PipelineStage.SYNTHESIZED.value)
    write_json(analysis_dir / "video_analysis.json", result)
    logger.event(creator=video.creator_username, video_id=video.video_id, stage="SYNTHESIZED", status="ok", duration=time.monotonic() - vision_started)
    return result


def _single_video_report(result: VideoAnalysis) -> str:
    creative = result.creative
    lines = [
        "# TikTok Video Commercial Intelligence Report",
        "",
        f"- Video: [{result.video_id}]({result.video_url})",
        f"- Stage: {result.stage.value}",
        f"- Model: {result.model or 'N/A'}",
        f"- Scenes: {len(result.scenes)}",
        f"- Transcript segments: {len(result.transcript.segments) if result.transcript else 0}",
        "",
        "## Creative Analysis",
        "",
    ]
    if not creative:
        lines.append(f"Unavailable. Error: {result.error or 'unknown'}")
    else:
        lines.extend(
            [
                f"- Content style: {creative.content_style}",
                f"- Hook: {creative.hook_type} ({creative.hook_strength.score}/10) — {creative.hook_strength.reasoning}",
                f"- Product first seen: {creative.product_first_seen_sec}",
                f"- Demo strength: {creative.demo_strength.score}/10 — {creative.demo_strength.reasoning}",
                f"- CTA strength: {creative.cta_strength.score}/10 — {creative.cta_strength.reasoning}",
                f"- Strengths: {', '.join(creative.strengths)}",
                f"- Weaknesses: {', '.join(creative.weaknesses)}",
            ]
        )
    lines.extend(["", "## Limitations", "", "Creative interpretation is model-generated; observed performance must be evaluated separately."])
    return "\n".join(lines) + "\n"
