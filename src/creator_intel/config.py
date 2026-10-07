"""Runtime configuration and path discovery."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent


@dataclass(slots=True)
class Settings:
    project_root: Path = field(default_factory=Path.cwd)
    output_root: Path = Path(os.getenv("CREATOR_INTEL_OUTPUT", "reports"))
    data_root: Path = Path(os.getenv("CREATOR_INTEL_DATA", "data"))
    model: str = os.getenv("CREATOR_INTEL_MODEL", "gpt-5.6")
    transcription_model: str = os.getenv("CREATOR_INTEL_TRANSCRIPTION_MODEL", "whisper-1")
    max_keyframes: int = int(os.getenv("MAX_KEYFRAMES_PER_VIDEO", "24"))
    ytdlp_timeout: int = int(os.getenv("CREATOR_INTEL_YTDLP_TIMEOUT", "30"))
    ffmpeg_timeout: int = int(os.getenv("CREATOR_INTEL_FFMPEG_TIMEOUT", "180"))
    analysis_version: str = "v1"
    prompt_version: str = "v1"

    def __post_init__(self) -> None:
        if not self.output_root.is_absolute():
            self.output_root = self.project_root / self.output_root
        if not self.data_root.is_absolute():
            self.data_root = self.project_root / self.data_root

    @property
    def database_path(self) -> Path:
        return self.data_root / "creator_intel.sqlite3"

    @property
    def rules_path(self) -> Path:
        return PACKAGE_ROOT / "resources" / "config" / "commercial_rules.yaml"

    @property
    def prompts_path(self) -> Path:
        return PACKAGE_ROOT / "resources" / "prompts"

    def creator_data_dir(self, username: str) -> Path:
        return self.data_root / "creators" / username

    def creator_report_dir(self, username: str, output_override: Path | None = None) -> Path:
        root = output_override or self.output_root
        if not root.is_absolute():
            root = self.project_root / root
        return root / username

    def find_ffmpeg(self) -> str | None:
        explicit = os.getenv("FFMPEG_BINARY")
        if explicit and Path(explicit).is_file():
            return explicit
        system = shutil.which("ffmpeg")
        if system:
            return system
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, RuntimeError):
            return None

    def find_ffprobe(self) -> str | None:
        explicit = os.getenv("FFPROBE_BINARY")
        if explicit and Path(explicit).is_file():
            return explicit
        return shutil.which("ffprobe")
