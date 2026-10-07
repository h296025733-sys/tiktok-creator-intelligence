"""OpenAI transcription and multimodal structured-output integration."""

from __future__ import annotations

import base64
import os
from pathlib import Path

from openai import APIConnectionError, APITimeoutError, OpenAI, OpenAIError
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import Settings
from .errors import MissingCredentialError
from .errors import CreatorIntelError
from .models import (
    MultimodalAnalysis,
    SceneAnalysis,
    Transcript,
    TranscriptSegment,
)
from .io_utils import sha256_file


class OpenAIAnalyzer:
    """Use current OpenAI SDK APIs with schema validation supplied by Pydantic."""

    def __init__(self, settings: Settings, client: OpenAI | None = None) -> None:
        if client is None and not os.getenv("OPENAI_API_KEY"):
            raise MissingCredentialError(
                "Deep analysis requires OPENAI_API_KEY for transcription and visual creative analysis. "
                "Metadata-only analysis remains available without a key."
            )
        self.settings = settings
        self.client = client or OpenAI(timeout=120, max_retries=2)

    def transcribe(self, video_id: str, audio_path: Path) -> Transcript:
        try:
            return self._transcribe_with_retry(video_id, audio_path)
        except OpenAIError as exc:
            raise CreatorIntelError(
                f"OpenAI transcription failed for {video_id} using {self.settings.transcription_model}: {exc}"
            ) from exc

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((APIConnectionError, APITimeoutError, ValidationError)),
        reraise=True,
    )
    def _transcribe_with_retry(self, video_id: str, audio_path: Path) -> Transcript:
        audio_hash = sha256_file(audio_path)
        with audio_path.open("rb") as handle:
            if self.settings.transcription_model == "whisper-1":
                response = self.client.audio.transcriptions.create(
                    file=handle,
                    model="whisper-1",
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )
                segments = [
                    TranscriptSegment(start=float(item.start), end=float(item.end), text=item.text)
                    for item in (response.segments or [])
                ]
                text = response.text
            else:
                response = self.client.audio.transcriptions.create(
                    file=handle,
                    model=self.settings.transcription_model,
                    response_format="json",
                )
                text = response.text
                segments = []
        return Transcript(
            video_id=video_id,
            text=text,
            segments=segments,
            audio_sha256=audio_hash,
            model=self.settings.transcription_model,
        )

    def analyze_video(
        self,
        *,
        scenes: list[SceneAnalysis],
        transcript: Transcript,
        contact_sheets: list[Path],
        prompt: str,
    ) -> MultimodalAnalysis:
        try:
            return self._analyze_video_with_retry(
                scenes=scenes,
                transcript=transcript,
                contact_sheets=contact_sheets,
                prompt=prompt,
            )
        except (OpenAIError, ValidationError) as exc:
            raise CreatorIntelError(
                f"OpenAI visual structured analysis failed using {self.settings.model}: {exc}"
            ) from exc

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((APIConnectionError, APITimeoutError, ValidationError)),
        reraise=True,
    )
    def _analyze_video_with_retry(
        self,
        *,
        scenes: list[SceneAnalysis],
        transcript: Transcript,
        contact_sheets: list[Path],
        prompt: str,
    ) -> MultimodalAnalysis:
        timeline = "\n".join(
            f"Scene {scene.scene_id}: {scene.start_time:.2f}-{scene.end_time:.2f}s; "
            f"speech={_speech_for_scene(scene, transcript)!r}"
            for scene in scenes
        )
        content: list[dict[str, str]] = [
            {
                "type": "input_text",
                "text": f"{prompt}\n\nTimestamped timeline:\n{timeline}",
            }
        ]
        for sheet in contact_sheets:
            encoded = base64.b64encode(sheet.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{encoded}",
                    "detail": "low",
                }
            )
        response = self.client.responses.parse(
            model=self.settings.model,
            input=[{"role": "user", "content": content}],
            text_format=MultimodalAnalysis,
        )
        if response.output_parsed is None:
            raise RuntimeError("OpenAI returned no parsed multimodal analysis.")
        return response.output_parsed


def align_timeline(scenes: list[SceneAnalysis], transcript: Transcript) -> list[dict[str, object]]:
    return [
        {
            "start": scene.start_time,
            "end": scene.end_time,
            "visual": scene.scene_role,
            "speech": _speech_for_scene(scene, transcript),
            "roles": [scene.scene_role] if scene.scene_role else [],
        }
        for scene in scenes
    ]


def _speech_for_scene(scene: SceneAnalysis, transcript: Transcript) -> str:
    matches = [
        segment.text
        for segment in transcript.segments
        if segment.end > scene.start_time and segment.start < scene.end_time
    ]
    return " ".join(matches).strip()
