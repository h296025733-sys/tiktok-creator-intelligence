"""Markdown/HTML reports and normalized tabular outputs."""

from __future__ import annotations

import html
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

from .io_utils import write_csv, write_json, write_text
from .models import (
    CommercialClassification,
    CommercialStatus,
    CreatorCommercialProfile,
    CreatorProfile,
    CreatorVideo,
    DataCoverage,
    PerformanceMetrics,
    VideoAnalysis,
)
from .performance import rank_by_relative, rank_by_views


def write_profile_outputs(
    report_dir: Path,
    profile: CreatorProfile,
    videos: list[CreatorVideo],
    classifications: dict[str, CommercialClassification],
    metrics: dict[str, PerformanceMetrics],
    candidates: list[CreatorVideo],
    coverage: DataCoverage,
    deep_analyses: list[VideoAnalysis] | None = None,
    creator_commercial_profile: CreatorCommercialProfile | None = None,
) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / "creator_profile.json", profile)
    write_json(report_dir / "videos.json", videos)
    video_rows = [_video_row(video, classifications[video.video_id], metrics[video.video_id]) for video in videos]
    write_csv(report_dir / "videos.csv", video_rows)
    candidate_rows = [
        _video_row(video, classifications[video.video_id], metrics[video.video_id]) for video in candidates
    ]
    write_json(
        report_dir / "commercial_candidates.json",
        [
            {
                "video": video,
                "classification": classifications[video.video_id],
                "performance": metrics[video.video_id],
            }
            for video in candidates
        ],
    )
    write_csv(report_dir / "commercial_candidates.csv", candidate_rows, fieldnames=list(video_rows[0]) if video_rows else [])
    ranking_rows: list[dict[str, object]] = []
    for rank, video in enumerate(rank_by_views(candidates), 1):
        ranking_rows.append({"ranking": "raw_views", "rank": rank, **_video_row(video, classifications[video.video_id], metrics[video.video_id])})
    for rank, video in enumerate(rank_by_relative(candidates, metrics), 1):
        ranking_rows.append({"ranking": "local_outperformance", "rank": rank, **_video_row(video, classifications[video.video_id], metrics[video.video_id])})
    write_csv(report_dir / "performance_rankings.csv", ranking_rows)
    if creator_commercial_profile:
        write_json(report_dir / "creator_commercial_profile.json", creator_commercial_profile)
    markdown = render_markdown_report(
        profile,
        videos,
        classifications,
        metrics,
        candidates,
        coverage,
        deep_analyses or [],
        creator_commercial_profile,
    )
    write_text(report_dir / "report.md", markdown)
    write_text(
        report_dir / "report.html",
        render_html(markdown, profile, candidates, classifications, metrics),
    )


def render_markdown_report(
    profile: CreatorProfile,
    videos: list[CreatorVideo],
    classifications: dict[str, CommercialClassification],
    metrics: dict[str, PerformanceMetrics],
    candidates: list[CreatorVideo],
    coverage: DataCoverage,
    deep_analyses: list[VideoAnalysis],
    creator_commercial_profile: CreatorCommercialProfile | None,
) -> str:
    counts = Counter(value.status.value for value in classifications.values())
    timestamps = [video.published_at for video in videos if video.published_at]
    period = "unknown"
    if timestamps:
        period = f"{min(timestamps).date()} to {max(timestamps).date()}"
    valid_views = [video.view_count for video in videos if video.view_count is not None]
    median_views = statistics.median(valid_views) if valid_views else None
    lines = [
        "# Creator Commercial Intelligence Report",
        "",
        "## Creator Overview",
        "",
        f"- Username: @{profile.username}",
        f"- Videos discovered: {len(videos)}",
        f"- Analysis period: {period}",
        f"- Median views: {_num(median_views)}",
        f"- Commercial candidates: {len(candidates)}",
        "",
        "## Commercial Content Breakdown",
        "",
    ]
    for status in CommercialStatus:
        lines.append(f"- {status.value.title()}: {counts[status.value]}")
    lines.extend(["", "## Top Commercial Videos by Raw Views", "", *_table(rank_by_views(candidates), classifications, metrics)])
    lines.extend(["", "## Top Commercial Videos by Relative Performance", "", *_table(rank_by_relative(candidates, metrics), classifications, metrics)])
    lines.extend(["", "## Deep Analysis Videos", ""])
    if not deep_analyses:
        lines.append("No videos were deep analyzed in this run.")
    for analysis in deep_analyses:
        creative = analysis.creative
        lines.extend(
            [
                f"### {analysis.video_id}",
                "",
                f"- URL: {analysis.video_url}",
                f"- Stage: {analysis.stage.value}",
                f"- Hook: {creative.hook_type if creative else 'unavailable'}",
                f"- Product reveal: {creative.product_first_seen_sec if creative else 'unavailable'}",
                f"- Strengths: {', '.join(creative.strengths) if creative else 'unavailable'}",
                f"- Weaknesses: {', '.join(creative.weaknesses) if creative else 'unavailable'}",
                f"- Error: {analysis.error or 'none'}",
                "",
            ]
        )
    lines.extend(["## Best Performing Commercial Patterns", ""])
    if creator_commercial_profile and creator_commercial_profile.best_patterns:
        for item in creator_commercial_profile.best_patterns:
            lines.append(f"- {item.pattern}: n={item.video_count}, median local performance={_ratio(item.median_local_outperformance)}")
    else:
        lines.append("Insufficient deep-analysis observations.")
    lines.extend(["", "## Weak Performing Commercial Patterns", ""])
    if creator_commercial_profile and creator_commercial_profile.weak_patterns:
        for item in creator_commercial_profile.weak_patterns:
            lines.append(f"- {item.pattern}: n={item.video_count}, median local performance={_ratio(item.median_local_outperformance)}")
    else:
        lines.append("Insufficient deep-analysis observations.")
    strength_lines = [
        f"- {item}" for item in (creator_commercial_profile.commercial_strengths if creator_commercial_profile else [])
    ] or ["Not established."]
    weakness_lines = [
        f"- {item}" for item in (creator_commercial_profile.commercial_weaknesses if creator_commercial_profile else [])
    ] or ["Not established."]
    archetype_lines = [
        f"- {item}" for item in (creator_commercial_profile.best_suited_archetypes if creator_commercial_profile else [])
    ] or ["Not established."]
    limitation_lines = [f"  - {item}" for item in coverage.known_limitations] or ["  - None recorded."]
    lines.extend(
        [
            "",
            "## Creator Commercial Strengths",
            "",
            *strength_lines,
            "",
            "## Creator Commercial Weaknesses",
            "",
            *weakness_lines,
            "",
            "## Best-Suited Creative Archetypes",
            "",
            *archetype_lines,
            "",
            "## Confidence / Limitations",
            "",
            "Commercial classification is probabilistic unless TikTok provides an explicit disclosure signal. Creative-pattern findings are observed associations, not causal claims.",
            "",
            "### Data Coverage",
            "",
            f"- Videos discovered: {coverage.videos_discovered}",
            f"- Videos classified: {coverage.videos_classified}",
            f"- Videos deep analyzed: {coverage.videos_deep_analyzed}",
            f"- Videos failed: {coverage.videos_failed}",
            f"- Commercial classification confidence: {_ratio(coverage.commercial_classification_confidence)}",
            f"- Performance data availability: {coverage.performance_data_availability:.1%}",
            f"- Direct hydration enrichment: {coverage.direct_hydration_succeeded}/{coverage.direct_hydration_attempted} attempted",
            f"- Public caption tracks discovered: {coverage.public_caption_tracks_discovered}",
            "- Known limitations:",
            *limitation_lines,
            "",
        ]
    )
    return "\n".join(lines)


def render_html(
    markdown: str,
    profile: CreatorProfile,
    candidates: list[CreatorVideo],
    classifications: dict[str, CommercialClassification],
    metrics: dict[str, PerformanceMetrics],
) -> str:
    cards = "".join(
        f'<article><a href="{html.escape(video.video_url)}">{html.escape(video.video_id)}</a>'
        f'<p>{_num(video.view_count)} views · {_ratio(metrics[video.video_id].local_outperformance)} · '
        f'{classifications[video.video_id].status.value} ({classifications[video.video_id].probability:.0%})</p>'
        + (f'<img loading="lazy" src="{html.escape(video.thumbnail)}" alt="video thumbnail">' if video.thumbnail else "")
        + "</article>"
        for video in candidates
    )
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>@{html.escape(profile.username)} Creator Intelligence</title>
<style>body{{font:16px/1.5 system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#17202a}}main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1rem}}article{{border:1px solid #ddd;border-radius:12px;padding:1rem}}img{{width:100%;max-height:360px;object-fit:cover;border-radius:8px}}pre{{white-space:pre-wrap;background:#f6f7f8;padding:1rem;border-radius:12px}}</style>
<h1>@{html.escape(profile.username)} Creator Commercial Intelligence</h1><main>{cards or '<p>No commercial candidates.</p>'}</main>
<h2>Full Markdown Report</h2><pre>{html.escape(markdown)}</pre></html>"""


def _video_row(video: CreatorVideo, classification: CommercialClassification, metric: PerformanceMetrics) -> dict[str, object]:
    return {
        "video_id": video.video_id,
        "video_url": video.video_url,
        "published_at": video.published_at.isoformat() if video.published_at else None,
        "caption": video.caption,
        "view_count": video.view_count,
        "like_count": video.like_count,
        "comment_count": video.comment_count,
        "share_count": video.share_count,
        "commercial_status": classification.status.value,
        "commercial_probability": classification.probability,
        "commercial_score": classification.score,
        "commercial_evidence": "; ".join(item.type.value for item in classification.evidence),
        "platform_is_ad": classification.explicit_ad,
        "has_product_anchor": video.has_product_anchor,
        "commercial_kinds": "; ".join(classification.commercial_kinds),
        "global_median": metric.global_median,
        "global_outperformance": metric.global_outperformance,
        "local_median": metric.local_median,
        "local_outperformance": metric.local_outperformance,
    }


def _table(
    videos: list[CreatorVideo], classifications: dict[str, CommercialClassification], metrics: dict[str, PerformanceMetrics]
) -> list[str]:
    if not videos:
        return ["No commercial candidates."]
    rows = ["| Rank | Video | Views | Local relative | Status | Confidence |", "|---:|---|---:|---:|---|---:|"]
    for rank, video in enumerate(videos[:20], 1):
        classification = classifications[video.video_id]
        rows.append(
            f"| {rank} | [{video.video_id}]({video.video_url}) | {_num(video.view_count)} | "
            f"{_ratio(metrics[video.video_id].local_outperformance)} | {classification.status.value} | "
            f"{classification.probability:.0%} |"
        )
    return rows


def _num(value: float | int | None) -> str:
    return "N/A" if value is None else f"{value:,.0f}"


def _ratio(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}x"
