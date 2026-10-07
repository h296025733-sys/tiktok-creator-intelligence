"""Media download, FFmpeg preprocessing, scene detection, and bounded keyframes."""

from __future__ import annotations

import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any

import cv2
from scenedetect import ContentDetector, detect
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .config import Settings
from .errors import MediaToolError, ProviderError
from .models import CreatorVideo, MediaInfo, SceneAnalysis


def download_video(video: CreatorVideo, destination: Path, settings: Settings, *, force: bool = False) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    existing = next(destination.glob(f"{video.video_id}.*"), None)
    if existing and existing.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"} and not force:
        return existing
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "best[ext=mp4]/best",
        "outtmpl": str(destination / f"{video.video_id}.%(ext)s"),
        "socket_timeout": settings.ytdlp_timeout,
        "retries": 3,
        "fragment_retries": 3,
        "continuedl": True,
        "overwrites": force,
    }
    cookie_file = os.getenv("TIKTOK_COOKIES_FILE")
    if cookie_file:
        if not Path(cookie_file).is_file():
            raise ProviderError(f"Configured TIKTOK_COOKIES_FILE does not exist: {cookie_file}")
        options["cookiefile"] = cookie_file
    try:
        with YoutubeDL(options) as ydl:
            result = ydl.extract_info(video.video_url, download=True)
            prepared = Path(ydl.prepare_filename(result))
    except (DownloadError, OSError, ValueError) as exc:
        raise ProviderError(
            f"yt-dlp failed to download video {video.video_id}. The video may be unavailable, "
            f"region/login restricted, or the extractor may have changed. Original error: {exc}"
        ) from exc
    if prepared.is_file():
        return prepared
    found = next(destination.glob(f"{video.video_id}.*"), None)
    if not found:
        raise ProviderError(f"yt-dlp reported success but no media file was found for {video.video_id}.")
    return found


def probe_media(video_path: Path, settings: Settings) -> MediaInfo:
    ffprobe = settings.find_ffprobe()
    if ffprobe:
        command = [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height,r_frame_rate",
            "-of",
            "json",
            str(video_path),
        ]
        try:
            completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
            payload = json.loads(completed.stdout)
            video_stream = next((item for item in payload.get("streams", []) if item.get("codec_type") == "video"), {})
            audio_present = any(item.get("codec_type") == "audio" for item in payload.get("streams", []))
            return MediaInfo(
                duration=_float((payload.get("format") or {}).get("duration")),
                width=_int(video_stream.get("width")),
                height=_int(video_stream.get("height")),
                fps=_parse_rate(video_stream.get("r_frame_rate")),
                has_audio=audio_present,
                probe="ffprobe",
            )
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
            pass
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise MediaToolError(f"Could not open media file for probing: {video_path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or None
    frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)) or None
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) or None
    capture.release()
    return MediaInfo(
        duration=(frames / fps if fps and frames else None),
        width=width,
        height=height,
        fps=fps,
        has_audio=None,
        probe="opencv_fallback_no_ffprobe",
    )


def extract_audio(video_path: Path, audio_path: Path, settings: Settings, *, force: bool = False) -> Path:
    if audio_path.is_file() and not force:
        return audio_path
    ffmpeg = settings.find_ffmpeg()
    if not ffmpeg:
        raise MediaToolError(
            "FFmpeg was not found. Install FFmpeg or set FFMPEG_BINARY to an authorized local executable."
        )
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", str(audio_path)]
    _run(command, timeout=settings.ffmpeg_timeout, action="extract audio")
    if not audio_path.is_file() or audio_path.stat().st_size == 0:
        raise MediaToolError(f"FFmpeg did not create usable audio: {audio_path}")
    return audio_path


def detect_scenes(video_path: Path, media_info: MediaInfo, *, threshold: float = 27.0) -> list[SceneAnalysis]:
    try:
        pairs = detect(str(video_path), ContentDetector(threshold=threshold), show_progress=False)
    except Exception as exc:
        raise MediaToolError(f"PySceneDetect failed for {video_path.name}: {exc}") from exc
    scenes = [
        SceneAnalysis(
            scene_id=index,
            start_time=start.get_seconds(),
            end_time=end.get_seconds(),
            duration=max(0.0, end.get_seconds() - start.get_seconds()),
        )
        for index, (start, end) in enumerate(pairs, 1)
    ]
    if not scenes and media_info.duration:
        scenes = [SceneAnalysis(scene_id=1, start_time=0.0, end_time=media_info.duration, duration=media_info.duration)]
    return scenes


def extract_keyframes(
    video_path: Path,
    scenes: list[SceneAnalysis],
    frames_dir: Path,
    settings: Settings,
    *,
    force: bool = False,
) -> list[SceneAnalysis]:
    ffmpeg = settings.find_ffmpeg()
    if not ffmpeg:
        raise MediaToolError("FFmpeg was not found; keyframe extraction cannot run.")
    frames_dir.mkdir(parents=True, exist_ok=True)
    candidates: list[tuple[int, float, str]] = []
    for scene in scenes:
        if scene.duration <= 8:
            candidates.append((scene.scene_id, scene.start_time + scene.duration / 2, "mid"))
        else:
            candidates.extend(
                [
                    (scene.scene_id, scene.start_time + min(0.5, scene.duration * 0.1), "start"),
                    (scene.scene_id, scene.start_time + scene.duration / 2, "mid"),
                    (scene.scene_id, max(scene.start_time, scene.end_time - min(0.5, scene.duration * 0.1)), "end"),
                ]
            )
    selected = _evenly_limit(candidates, settings.max_keyframes)
    paths_by_scene: dict[int, list[str]] = {scene.scene_id: [] for scene in scenes}
    for scene_id, timestamp, label in selected:
        frame_path = frames_dir / f"scene_{scene_id:03d}_{label}_{timestamp:07.3f}.jpg"
        if force or not frame_path.is_file():
            command = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(frame_path),
            ]
            _run(command, timeout=30, action=f"extract keyframe at {timestamp:.3f}s")
        if frame_path.is_file():
            paths_by_scene[scene_id].append(str(frame_path))
    return [scene.model_copy(update={"keyframes": paths_by_scene[scene.scene_id]}) for scene in scenes]


def create_contact_sheets(
    scenes: list[SceneAnalysis], output_dir: Path, *, max_frames_per_sheet: int = 12
) -> list[Path]:
    frames: list[tuple[Path, str]] = []
    for scene in scenes:
        for path in scene.keyframes:
            timestamp = Path(path).stem.rsplit("_", 1)[-1]
            frames.append((Path(path), f"scene {scene.scene_id} @ {timestamp}s"))
    output_dir.mkdir(parents=True, exist_ok=True)
    sheets: list[Path] = []
    for sheet_index in range(0, len(frames), max_frames_per_sheet):
        batch = frames[sheet_index : sheet_index + max_frames_per_sheet]
        images = [(_read_image_unicode(path), label) for path, label in batch]
        images = [(image, label) for image, label in images if image is not None]
        if not images:
            continue
        cell_w, cell_h = 320, 600
        columns = min(3, len(images))
        rows = math.ceil(len(images) / columns)
        import numpy as np

        canvas = np.full((rows * cell_h, columns * cell_w, 3), 245, dtype=np.uint8)
        for position, (image, label) in enumerate(images):
            height, width = image.shape[:2]
            scale = min(cell_w / width, (cell_h - 40) / height)
            resized = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))))
            row, column = divmod(position, columns)
            x = column * cell_w + (cell_w - resized.shape[1]) // 2
            y = row * cell_h + 34
            canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
            cv2.putText(canvas, label, (column * cell_w + 8, row * cell_h + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (15, 15, 15), 1, cv2.LINE_AA)
        path = output_dir / f"contact_sheet_{len(sheets) + 1:02d}.jpg"
        _write_image_unicode(path, canvas, quality=88)
        sheets.append(path)
    return sheets


def _run(command: list[str], *, timeout: int, action: str) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MediaToolError(f"FFmpeg timed out while trying to {action} after {timeout}s.") from exc
    except subprocess.CalledProcessError as exc:
        error = (exc.stderr or exc.stdout or "unknown FFmpeg error").strip()
        raise MediaToolError(f"FFmpeg failed to {action}: {error[-1000:]}") from exc
    except OSError as exc:
        raise MediaToolError(f"Could not start FFmpeg while trying to {action}: {exc}") from exc


def _read_image_unicode(path: Path):
    """Read an image on Windows paths that OpenCV's imread cannot decode directly."""
    import numpy as np

    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def _write_image_unicode(path: Path, image, *, quality: int) -> None:
    success, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not success:
        raise MediaToolError(f"OpenCV could not encode contact sheet: {path}")
    try:
        encoded.tofile(path)
    except OSError as exc:
        raise MediaToolError(f"Could not write contact sheet {path}: {exc}") from exc


def _evenly_limit(values: list[tuple[int, float, str]], limit: int) -> list[tuple[int, float, str]]:
    if len(values) <= limit:
        return values
    if limit <= 1:
        return [values[len(values) // 2]]
    indexes = {round(index * (len(values) - 1) / (limit - 1)) for index in range(limit)}
    return [values[index] for index in sorted(indexes)]


def _parse_rate(value: Any) -> float | None:
    if not value:
        return None
    try:
        numerator, denominator = str(value).split("/", 1)
        return float(numerator) / float(denominator) if float(denominator) else None
    except (ValueError, ZeroDivisionError):
        return _float(value)


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
