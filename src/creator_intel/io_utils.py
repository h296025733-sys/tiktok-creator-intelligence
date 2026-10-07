"""Small, deterministic serialization helpers."""

from __future__ import annotations

import csv
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique sibling keeps concurrent runs from replacing or deleting one
    # another's temporary file before the atomic publish step.
    temporary = _temporary_sibling(path)
    temporary.write_text(json.dumps(jsonable(value), ensure_ascii=False, indent=2), encoding="utf-8")
    _publish(temporary, path)


def write_text(path: Path, value: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_sibling(path)
    temporary.write_text(value, encoding=encoding)
    _publish(temporary, path)


def _temporary_sibling(path: Path) -> Path:
    return path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")


def _publish(temporary: Path, path: Path) -> None:
    try:
        for attempt in range(12):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows can briefly lock the destination while another
                # thread/process publishes the same canonical snapshot.
                if attempt == 11:
                    raise
                time.sleep(0.005 * (attempt + 1))
    finally:
        if temporary.exists():
            temporary.unlink()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0]) if rows else []
    temporary = _temporary_sibling(path)
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            if fieldnames:
                writer.writeheader()
                writer.writerows(rows)
        _publish(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
