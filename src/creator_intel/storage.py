"""Lightweight SQLite persistence for normalized and versioned analysis records."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel

from .models import (
    CommercialClassification,
    CreatorProfile,
    CreatorVideo,
    PerformanceMetrics,
    SceneAnalysis,
    Transcript,
    VideoAnalysis,
)


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS creators (
  username TEXT PRIMARY KEY, profile_json TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS videos (
  video_id TEXT PRIMARY KEY, creator_username TEXT NOT NULL, video_json TEXT NOT NULL,
  metadata_fingerprint TEXT, stage TEXT NOT NULL DEFAULT 'FETCHED', error TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_videos_creator ON videos(creator_username);
CREATE TABLE IF NOT EXISTS commercial_classifications (
  video_id TEXT NOT NULL, classifier_version TEXT NOT NULL, classification_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(video_id, classifier_version)
);
CREATE TABLE IF NOT EXISTS performance_metrics (
  video_id TEXT NOT NULL, analysis_version TEXT NOT NULL, metrics_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(video_id, analysis_version)
);
CREATE TABLE IF NOT EXISTS video_analysis (
  video_id TEXT NOT NULL, analysis_version TEXT NOT NULL, prompt_version TEXT NOT NULL,
  model TEXT, analysis_json TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(video_id, analysis_version, prompt_version, model)
);
CREATE TABLE IF NOT EXISTS scenes (
  video_id TEXT NOT NULL, analysis_version TEXT NOT NULL, scenes_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(video_id, analysis_version)
);
CREATE TABLE IF NOT EXISTS transcripts (
  video_id TEXT NOT NULL, audio_sha256 TEXT NOT NULL, model TEXT NOT NULL, transcript_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(video_id, audio_sha256, model)
);
CREATE TABLE IF NOT EXISTS analysis_runs (
  run_id TEXT PRIMARY KEY, creator_username TEXT NOT NULL, run_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def upsert_profile(self, profile: CreatorProfile) -> None:
        self._upsert("creators", "username", profile.username, "profile_json", profile)

    def upsert_video(self, video: CreatorVideo, fingerprint: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO videos(video_id, creator_username, video_json, metadata_fingerprint, stage)
                   VALUES(?, ?, ?, ?, 'FETCHED')
                   ON CONFLICT(video_id) DO UPDATE SET creator_username=excluded.creator_username,
                   video_json=excluded.video_json, metadata_fingerprint=excluded.metadata_fingerprint,
                   updated_at=CURRENT_TIMESTAMP""",
                (video.video_id, video.creator_username, _dump(video), fingerprint),
            )

    def set_stage(self, video_id: str, stage: str, error: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE videos SET stage=?, error=?, updated_at=CURRENT_TIMESTAMP WHERE video_id=?",
                (stage, error, video_id),
            )

    def upsert_classification(self, value: CommercialClassification) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO commercial_classifications(video_id, classifier_version, classification_json)
                   VALUES(?, ?, ?) ON CONFLICT(video_id, classifier_version) DO UPDATE SET
                   classification_json=excluded.classification_json, updated_at=CURRENT_TIMESTAMP""",
                (value.video_id, value.classifier_version, _dump(value)),
            )

    def upsert_performance(self, value: PerformanceMetrics, analysis_version: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO performance_metrics(video_id, analysis_version, metrics_json)
                   VALUES(?, ?, ?) ON CONFLICT(video_id, analysis_version) DO UPDATE SET
                   metrics_json=excluded.metrics_json, updated_at=CURRENT_TIMESTAMP""",
                (value.video_id, analysis_version, _dump(value)),
            )

    def get_video_analysis(
        self, video_id: str, analysis_version: str, prompt_version: str, model: str
    ) -> VideoAnalysis | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT analysis_json FROM video_analysis WHERE video_id=? AND analysis_version=?
                   AND prompt_version=? AND model=?""",
                (video_id, analysis_version, prompt_version, model),
            ).fetchone()
        return VideoAnalysis.model_validate_json(row[0]) if row else None

    def upsert_video_analysis(self, value: VideoAnalysis) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO video_analysis(video_id, analysis_version, prompt_version, model, analysis_json)
                   VALUES(?, ?, ?, ?, ?) ON CONFLICT(video_id, analysis_version, prompt_version, model)
                   DO UPDATE SET analysis_json=excluded.analysis_json, updated_at=CURRENT_TIMESTAMP""",
                (value.video_id, value.analysis_version, value.prompt_version, value.model, _dump(value)),
            )

    def get_transcript(self, video_id: str, audio_sha256: str, model: str) -> Transcript | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT transcript_json FROM transcripts WHERE video_id=? AND audio_sha256=? AND model=?",
                (video_id, audio_sha256, model),
            ).fetchone()
        return Transcript.model_validate_json(row[0]) if row else None

    def upsert_transcript(self, value: Transcript) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO transcripts(video_id, audio_sha256, model, transcript_json) VALUES(?, ?, ?, ?)
                   ON CONFLICT(video_id, audio_sha256, model) DO UPDATE SET
                   transcript_json=excluded.transcript_json, updated_at=CURRENT_TIMESTAMP""",
                (value.video_id, value.audio_sha256, value.model, _dump(value)),
            )

    def get_scenes(self, video_id: str, analysis_version: str) -> list[SceneAnalysis] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT scenes_json FROM scenes WHERE video_id=? AND analysis_version=?",
                (video_id, analysis_version),
            ).fetchone()
        if not row:
            return None
        return [SceneAnalysis.model_validate(item) for item in json.loads(row[0])]

    def upsert_scenes(self, video_id: str, analysis_version: str, scenes: list[SceneAnalysis]) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO scenes(video_id, analysis_version, scenes_json) VALUES(?, ?, ?)
                   ON CONFLICT(video_id, analysis_version) DO UPDATE SET
                   scenes_json=excluded.scenes_json, updated_at=CURRENT_TIMESTAMP""",
                (video_id, analysis_version, _dump([item.model_dump(mode="json") for item in scenes])),
            )

    def upsert_run(self, run_id: str, creator: str, value: BaseModel) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO analysis_runs(run_id, creator_username, run_json) VALUES(?, ?, ?)
                   ON CONFLICT(run_id) DO UPDATE SET run_json=excluded.run_json, updated_at=CURRENT_TIMESTAMP""",
                (run_id, creator, _dump(value)),
            )

    def _upsert(self, table: str, key_name: str, key: str, json_name: str, value: BaseModel) -> None:
        with self.connect() as connection:
            connection.execute(
                f"INSERT INTO {table}({key_name}, {json_name}) VALUES(?, ?) "
                f"ON CONFLICT({key_name}) DO UPDATE SET {json_name}=excluded.{json_name}, updated_at=CURRENT_TIMESTAMP",
                (key, _dump(value)),
            )


def _dump(value: Any) -> str:
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    return json.dumps(value, ensure_ascii=False)
