"""Robust creator baselines and deterministic rankings."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable

from .models import CommercialClassification, CommercialStatus, CreatorVideo, PerformanceMetrics


def _valid_views(videos: Iterable[CreatorVideo]) -> list[int]:
    return [video.view_count for video in videos if video.view_count is not None and video.view_count >= 0]


def _percentile(values: list[int], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def safe_ratio(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return float(numerator) / float(denominator)


def calculate_performance(
    videos: list[CreatorVideo], *, neighbor_window: int = 10, min_local_samples: int = 5
) -> dict[str, PerformanceMetrics]:
    views = _valid_views(videos)
    global_median = float(statistics.median(views)) if views else None
    percentiles = {value: _percentile(views, value) for value in (0.25, 0.5, 0.75, 0.9)}
    ordered = sorted(
        enumerate(videos),
        key=lambda item: (
            item[1].timestamp is None,
            item[1].timestamp if item[1].timestamp is not None else item[0],
        ),
    )
    output: dict[str, PerformanceMetrics] = {}
    for index, (_, video) in enumerate(ordered):
        start = max(0, index - neighbor_window)
        end = min(len(ordered), index + neighbor_window + 1)
        neighbors = [item[1] for position, item in enumerate(ordered[start:end], start=start) if position != index]
        local_values = _valid_views(neighbors)
        if len(local_values) >= min_local_samples:
            local_median = float(statistics.median(local_values))
        else:
            local_median = global_median
        output[video.video_id] = PerformanceMetrics(
            video_id=video.video_id,
            raw_views=video.view_count,
            global_median=global_median,
            global_outperformance=safe_ratio(video.view_count, global_median),
            local_median=local_median,
            local_outperformance=safe_ratio(video.view_count, local_median),
            local_sample_size=len(local_values),
            p25=percentiles[0.25],
            p50=percentiles[0.5],
            p75=percentiles[0.75],
            p90=percentiles[0.9],
        )
    return output


def rank_by_views(videos: list[CreatorVideo]) -> list[CreatorVideo]:
    return sorted(videos, key=lambda video: (video.view_count is not None, video.view_count or -1), reverse=True)


def rank_by_relative(
    videos: list[CreatorVideo], metrics: dict[str, PerformanceMetrics]
) -> list[CreatorVideo]:
    return sorted(
        videos,
        key=lambda video: (
            metrics[video.video_id].local_outperformance is not None,
            metrics[video.video_id].local_outperformance or -1,
            video.view_count or -1,
        ),
        reverse=True,
    )


def select_candidates(
    videos: list[CreatorVideo],
    classifications: dict[str, CommercialClassification],
    metrics: dict[str, PerformanceMetrics],
    top: int,
) -> list[CreatorVideo]:
    allowed = {CommercialStatus.CONFIRMED, CommercialStatus.LIKELY}
    candidates = [
        video
        for video in videos
        if classifications[video.video_id].status in allowed
        or video.has_product_anchor is True
        or (
            classifications[video.video_id].status == CommercialStatus.POSSIBLE
            and classifications[video.video_id].probability >= 0.4
        )
    ]

    def score(video: CreatorVideo) -> tuple[float, int]:
        classification = classifications[video.video_id]
        metric = metrics[video.video_id]
        raw_component = min(math.log10((video.view_count or 0) + 1) / 8.0, 1.0)
        relative_component = min((metric.local_outperformance or 0) / 5.0, 1.0)
        anchor_component = 0.08 if video.has_product_anchor is True else 0.0
        combined = classification.probability * 0.4 + raw_component * 0.22 + relative_component * 0.30 + anchor_component
        return combined, video.view_count or -1

    return sorted(candidates, key=score, reverse=True)[:top]
