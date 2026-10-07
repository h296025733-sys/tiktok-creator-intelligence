"""Direct public video-page hydration and WebVTT caption enrichment.

This is a URL-only supplement to yt-dlp. It does not open or automate a
browser, and failure is non-fatal because TikTok page shapes can change.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .io_utils import write_text
from .models import CreatorVideo, Transcript, TranscriptSegment
from .plugin_signals import extract_plugin_signals


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138 Safari/537.36"
)
_ALLOWED_ASSET_HOST_SUFFIXES = (
    ".tiktok.com",
    ".tiktokcdn.com",
    ".tiktokcdn-us.com",
    ".tiktokv.com",
    ".byteoversea.com",
    ".ibytedtos.com",
    ".akamaized.net",
    ".muscdn.com",
    ".byteimg.com",
)


def enrich_from_public_video_page(video: CreatorVideo, *, timeout: int = 30) -> tuple[CreatorVideo, dict[str, Any] | None]:
    """Fetch one public video URL and merge explicit TikTok hydration fields."""

    headers = {"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=timeout) as client:
        response = client.get(video.video_url)
        response.raise_for_status()
    hydration = _hydration_json(response.text)
    item = _find_video_item(hydration, video.video_id)
    if item is None:
        return video.model_copy(
            update={
                "raw_metadata": video.raw_metadata
                | {
                    "direct_hydration_attempted": True,
                    "direct_hydration": False,
                    "direct_enrichment_error": "no matching hydration item",
                }
            }
        ), None
    signals = extract_plugin_signals(item)
    stats = _first_mapping(item, ("stats", "statistics"))
    stats_v2 = _first_mapping(item, ("statsV2", "stats_v2"))
    item_video = item.get("video") if isinstance(item.get("video"), dict) else {}
    subtitle_tracks = _subtitle_tracks(item_video)
    updated = video.model_copy(
        update={
            "caption": _text(item.get("desc")) or video.caption,
            "description": _text(item.get("desc")) or video.description,
            "view_count": _first_non_none(
                _first_int(stats, ("playCount", "play_count", "play")),
                _first_int(stats_v2, ("playCount", "play_count", "play")),
                video.view_count,
            ),
            "like_count": _first_non_none(
                _first_int(stats, ("diggCount", "digg_count", "likeCount", "like_count")),
                _first_int(stats_v2, ("diggCount", "digg_count", "likeCount", "like_count")),
                video.like_count,
            ),
            "comment_count": _first_non_none(
                _first_int(stats, ("commentCount", "comment_count")),
                _first_int(stats_v2, ("commentCount", "comment_count")),
                video.comment_count,
            ),
            "share_count": _first_non_none(
                _first_int(stats, ("shareCount", "share_count")),
                _first_int(stats_v2, ("shareCount", "share_count")),
                video.share_count,
            ),
            "save_count": _first_non_none(
                _first_int(
                    stats,
                    (
                        "collectCount",
                        "collect_count",
                        "favoriteCount",
                        "favorite_count",
                        "favouriteCount",
                        "favourite_count",
                    ),
                ),
                _first_int(
                    stats_v2,
                    (
                        "collectCount",
                        "collect_count",
                        "favoriteCount",
                        "favorite_count",
                        "favouriteCount",
                        "favourite_count",
                    ),
                ),
                video.save_count,
            ),
            "platform_is_ad": _merge_three_state(video.platform_is_ad, signals.platform_is_ad),
            "has_product_anchor": _merge_three_state(video.has_product_anchor, signals.has_product_anchor),
            "product_ids": list(dict.fromkeys([*video.product_ids, *signals.product_ids])),
            "subtitle_tracks": subtitle_tracks or video.subtitle_tracks,
            "raw_metadata": video.raw_metadata
            | {
                "direct_hydration_attempted": True,
                "direct_hydration": True,
                "direct_signal_evidence": [
                    {
                        "kind": evidence.kind,
                        "key_path": evidence.key_path,
                        "value": evidence.value,
                        "source": evidence.source,
                    }
                    for evidence in signals.evidence
                ],
            },
        }
    )
    return updated, item


def download_public_caption(
    video: CreatorVideo, destination: Path, *, timeout: int = 30
) -> tuple[Transcript | None, Path | None]:
    """Download and parse the first public WebVTT track exposed by TikTok."""

    webvtt = next(
        (
            track
            for track in video.subtitle_tracks
            if str(track.get("format", "")).lower() in {"webvtt", "vtt"} and track.get("url")
        ),
        None,
    )
    if not webvtt:
        return None, None
    headers = {"User-Agent": _USER_AGENT, "Referer": "https://www.tiktok.com/"}
    caption_url = _validated_asset_url(str(webvtt["url"]))
    with httpx.Client(headers=headers, follow_redirects=True, timeout=timeout) as client:
        response = client.get(caption_url)
        response.raise_for_status()
    text = response.text
    if not text.lstrip("\ufeff\r\n ").startswith("WEBVTT"):
        return None, None
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_text(destination, text)
    segments = parse_webvtt(text)
    transcript = Transcript(
        video_id=video.video_id,
        text=" ".join(segment.text for segment in segments).strip(),
        segments=segments,
        audio_sha256="not-applicable-public-caption",
        model=f"tiktok-{webvtt.get('source') or 'caption'}-{webvtt.get('language') or 'unknown'}",
    )
    return transcript, destination


def parse_webvtt(text: str) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    blocks = re.split(r"\r?\n\s*\r?\n", text.replace("\ufeff", "").strip())
    timing = re.compile(
        r"(?P<start>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+"
        r"(?P<end>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})"
    )
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((index for index, line in enumerate(lines) if timing.search(line)), None)
        if timing_index is None:
            continue
        match = timing.search(lines[timing_index])
        if not match:
            continue
        cue = " ".join(lines[timing_index + 1 :])
        cue = re.sub(r"<[^>]+>", "", html.unescape(cue)).strip()
        if cue:
            segments.append(
                TranscriptSegment(
                    start=_timestamp(match.group("start")),
                    end=_timestamp(match.group("end")),
                    text=cue,
                )
            )
    return segments


def _hydration_json(page: str) -> dict[str, Any]:
    match = re.search(
        r'<script[^>]+id=["\']__UNIVERSAL_DATA_FOR_REHYDRATION__["\'][^>]*>(.*?)</script>',
        page,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return {}
    return json.loads(html.unescape(match.group(1)))


def _find_video_item(value: Any, video_id: str) -> dict[str, Any] | None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > 14:
            continue
        if isinstance(current, dict):
            if any(str(current.get(key, "")) == video_id for key in ("id", "aweme_id", "itemId", "item_id")):
                if isinstance(current.get("video"), dict) and (
                    any(
                        isinstance(current.get(key), dict)
                        for key in ("stats", "statistics", "statsV2", "stats_v2")
                    )
                    or current.get("desc") is not None
                ):
                    return current
            for child in current.values():
                if isinstance(child, (dict, list)):
                    stack.append((child, depth + 1))
        elif isinstance(current, list):
            for child in current[:500]:
                if isinstance(child, (dict, list)):
                    stack.append((child, depth + 1))
    return None


def _subtitle_tracks(video: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for source in (video.get("subtitleInfos"), video.get("subtitle_infos")):
        if isinstance(source, list):
            candidates.extend(item for item in source if isinstance(item, dict))
    cla = video.get("claInfo") or video.get("cla_info")
    if isinstance(cla, dict):
        for key in ("captionInfos", "caption_infos"):
            source = cla.get(key)
            if isinstance(source, list):
                candidates.extend(item for item in source if isinstance(item, dict))
    tracks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates[:40]:
        url = _first_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        tracks.append(
            {
                "url": url,
                "format": _text(item.get("Format") or item.get("captionFormat") or item.get("format")),
                "language": _text(item.get("LanguageCodeName") or item.get("language") or item.get("languageCode")),
                "source": _text(item.get("Source") or ("auto" if item.get("isAutoGen") else "creator")),
            }
        )
    return tracks


def _first_url(item: dict[str, Any]) -> str | None:
    for key in ("Url", "url"):
        if isinstance(item.get(key), str) and item[key].startswith("https://"):
            return item[key]
    for key in ("urlList", "url_list"):
        value = item.get(key)
        if isinstance(value, list):
            return next((candidate for candidate in value if isinstance(candidate, str) and candidate.startswith("https://")), None)
    return None


def _validated_asset_url(value: str) -> str:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    allowed = parsed.scheme == "https" and any(
        hostname == suffix[1:] or hostname.endswith(suffix)
        for suffix in _ALLOWED_ASSET_HOST_SUFFIXES
    )
    if not allowed or parsed.username or parsed.password:
        raise ValueError("TikTok caption URL uses an unsupported host or scheme.")
    return value


def _merge_three_state(existing: bool | None, incoming: bool | None) -> bool | None:
    if existing is True or incoming is True:
        return True
    return existing if existing is not None else incoming


def _timestamp(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    seconds = float(parts.pop())
    minutes = int(parts.pop()) if parts else 0
    hours = int(parts.pop()) if parts else 0
    return hours * 3600 + minutes * 60 + seconds


def _text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _first_mapping(value: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, dict):
            return candidate
    return {}


def _first_int(value: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        if key in value:
            parsed = _int(value[key])
            if parsed is not None:
                return parsed
    return None


def _first_non_none(*values: int | None) -> int | None:
    return next((value for value in values if value is not None), None)


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and not re.fullmatch(r"\d+", value.strip()):
        return None
    try:
        parsed = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if parsed is None or parsed < 0:
        return None
    if isinstance(value, float) and value != parsed:
        return None
    return parsed
