from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture(fixture_dir: Path):
    def load(name: str):
        return json.loads((fixture_dir / name).read_text(encoding="utf-8"))

    return load


@pytest.fixture
def sample_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    monkeypatch.setenv("FFMPEG_BINARY", ffmpeg)
    path = tmp_path / "sample.mp4"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=320x240:r=12:d=1",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=320x240:r=12:d=1",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=880:sample_rate=16000:duration=2",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map",
        "[v]",
        "-map",
        "2:a",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
    assert path.is_file()
    return path
