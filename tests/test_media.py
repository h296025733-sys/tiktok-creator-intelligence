from pathlib import Path

from creator_intel.ai import align_timeline
from creator_intel.config import Settings
from creator_intel.media import create_contact_sheets, detect_scenes, extract_audio, extract_keyframes, probe_media
from creator_intel.models import SceneAnalysis, Transcript, TranscriptSegment


def test_real_local_media_preprocessing(sample_video: Path, tmp_path: Path) -> None:
    settings = Settings(project_root=Path(__file__).parents[1], output_root=tmp_path / "reports", data_root=tmp_path / "data", max_keyframes=6)
    media = probe_media(sample_video, settings)
    assert media.duration is not None and 1.8 <= media.duration <= 2.2
    assert media.width == 320
    assert media.height == 240
    audio = extract_audio(sample_video, tmp_path / "audio.wav", settings)
    assert audio.stat().st_size > 1000
    scenes = detect_scenes(sample_video, media)
    assert scenes
    frames = extract_keyframes(sample_video, scenes, tmp_path / "中文帧目录", settings)
    assert 1 <= sum(len(scene.keyframes) for scene in frames) <= 6
    sheets = create_contact_sheets(frames, tmp_path / "中文输出目录")
    assert sheets and all(path.stat().st_size > 1000 for path in sheets)


def test_timeline_alignment_uses_overlap() -> None:
    scenes = [
        SceneAnalysis(scene_id=1, start_time=0, end_time=2, duration=2),
        SceneAnalysis(scene_id=2, start_time=2, end_time=4, duration=2),
    ]
    transcript = Transcript(
        video_id="v",
        text="first overlap second",
        audio_sha256="x",
        model="fixture",
        segments=[
            TranscriptSegment(start=0.5, end=2.2, text="first overlap"),
            TranscriptSegment(start=2.5, end=3.5, text="second"),
        ],
    )
    timeline = align_timeline(scenes, transcript)
    assert timeline[0]["speech"] == "first overlap"
    assert timeline[1]["speech"] == "first overlap second"
