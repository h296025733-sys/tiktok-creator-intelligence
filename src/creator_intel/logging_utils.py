"""Structured JSONL event logging."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path


class JsonlRunLogger:
    def __init__(self, path: Path, *, verbose: bool = False) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose

    def event(
        self,
        *,
        creator: str,
        stage: str,
        status: str,
        video_id: str | None = None,
        duration: float | None = None,
        error: str | None = None,
    ) -> None:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "creator": creator,
            "video_id": video_id,
            "stage": stage,
            "duration": duration,
            "status": status,
            "error": error,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        if self.verbose:
            logging.getLogger("creator_intel").info(json.dumps(payload, ensure_ascii=False))


def configure_console_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(message)s")

