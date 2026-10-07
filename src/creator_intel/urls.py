"""TikTok URL parsing without network calls."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

from .errors import InvalidTikTokUrl


class TikTokUrlKind(StrEnum):
    PROFILE = "profile"
    VIDEO = "video"


@dataclass(frozen=True, slots=True)
class ParsedTikTokUrl:
    original_url: str
    canonical_url: str
    username: str
    kind: TikTokUrlKind
    video_id: str | None = None


_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_VIDEO_ID_RE = re.compile(r"^\d{5,30}$")
_ALLOWED_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}


def parse_tiktok_url(value: str) -> ParsedTikTokUrl:
    """Parse a canonical profile or video URL and reject short/redirect URLs."""
    raw = value.strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in _ALLOWED_HOSTS:
        raise InvalidTikTokUrl(
            "Expected a public https://www.tiktok.com/@username profile or video URL. "
            "Short redirect URLs are intentionally not resolved by the parser."
        )

    parts = [part for part in parsed.path.split("/") if part]
    if not parts or not parts[0].startswith("@"):
        raise InvalidTikTokUrl("TikTok URL must contain /@username.")
    username = parts[0][1:]
    if not _USERNAME_RE.fullmatch(username):
        raise InvalidTikTokUrl("TikTok username contains unsupported characters.")

    if len(parts) == 1:
        return ParsedTikTokUrl(
            original_url=raw,
            canonical_url=f"https://www.tiktok.com/@{username}",
            username=username,
            kind=TikTokUrlKind.PROFILE,
        )

    if len(parts) >= 3 and parts[1] == "video" and _VIDEO_ID_RE.fullmatch(parts[2]):
        video_id = parts[2]
        return ParsedTikTokUrl(
            original_url=raw,
            canonical_url=f"https://www.tiktok.com/@{username}/video/{video_id}",
            username=username,
            kind=TikTokUrlKind.VIDEO,
            video_id=video_id,
        )
    raise InvalidTikTokUrl("Supported TikTok URLs are profile URLs and /@username/video/ID URLs.")

