from creator_intel.models import CommercialClassification, CommercialStatus, CreatorVideo
from creator_intel.performance import calculate_performance, rank_by_relative, rank_by_views, safe_ratio, select_candidates


def make_video(index: int, views: int | None) -> CreatorVideo:
    return CreatorVideo(video_id=str(index), video_url=f"https://www.tiktok.com/@x/video/{10000 + index}", creator_username="x", timestamp=index, view_count=views)


def test_median_local_and_relative() -> None:
    videos = [make_video(index, views) for index, views in enumerate([10, 20, 30, 40, 50, 60, 70])]
    metrics = calculate_performance(videos, neighbor_window=2, min_local_samples=2)
    assert metrics["3"].global_median == 40
    assert metrics["3"].local_median == 40
    assert metrics["6"].local_median == 55
    assert metrics["6"].local_outperformance == 70 / 55


def test_missing_and_zero_baseline() -> None:
    videos = [make_video(1, None), make_video(2, 0)]
    metrics = calculate_performance(videos)
    assert metrics["1"].global_outperformance is None
    assert metrics["2"].global_outperformance is None
    assert safe_ratio(10, 0) is None


def test_rankings_and_selection() -> None:
    videos = [make_video(1, 100), make_video(2, 1000), make_video(3, 500)]
    metrics = calculate_performance(videos, min_local_samples=1)
    classifications = {
        video.video_id: CommercialClassification(
            video_id=video.video_id,
            status=CommercialStatus.CONFIRMED if video.video_id != "1" else CommercialStatus.ORGANIC,
            probability=0.9 if video.video_id != "1" else 0,
            score=90 if video.video_id != "1" else 0,
            reasoning="fixture",
        )
        for video in videos
    }
    assert [item.video_id for item in rank_by_views(videos)] == ["2", "3", "1"]
    assert rank_by_relative(videos, metrics)[0].video_id == "2"
    assert {item.video_id for item in select_candidates(videos, classifications, metrics, 5)} == {"2", "3"}


def test_raw_view_ranking_places_missing_counts_last() -> None:
    videos = [make_video(1, None), make_video(2, 0), make_video(3, 500)]
    assert [item.video_id for item in rank_by_views(videos)] == ["3", "2", "1"]
