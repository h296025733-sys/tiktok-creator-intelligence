"""Replaceable TikTok metadata providers with yt-dlp as the primary path."""

from __future__ import annotations

import asyncio
import os
import re
from abc import ABC, abstractmethod
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .errors import ProviderError
from .models import CreatorProfile, CreatorVideo
from .plugin_signals import extract_plugin_signals
from .urls import ParsedTikTokUrl, TikTokUrlKind, parse_tiktok_url


class ProfileProvider(ABC):
    name: str

    @abstractmethod
    def fetch_profile(self, profile_url: str, limit: int) -> tuple[CreatorProfile, list[CreatorVideo], dict[str, Any]]:
        """Fetch a public creator profile and normalize its videos."""

    @abstractmethod
    def fetch_video(self, video_url: str) -> tuple[CreatorVideo, dict[str, Any]]:
        """Fetch one public video without downloading media."""


class _YtDlpErrorCapture:
    """Capture yt-dlp item errors while its ignore-errors mode continues."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def debug(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        normalized = re.sub(r"\s+", " ", str(message)).strip()
        if normalized:
            self.errors.append(normalized[:2000])


class YtDlpProvider(ProfileProvider):
    name = "yt-dlp"

    def __init__(self, *, timeout: int = 30, cookies_file: Path | None = None) -> None:
        self.timeout = timeout
        self.cookies_file = cookies_file or (
            Path(os.environ["TIKTOK_COOKIES_FILE"]) if os.getenv("TIKTOK_COOKIES_FILE") else None
        )

    def _options(self, *, limit: int | None = None) -> dict[str, Any]:
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": self.timeout,
            "retries": 3,
            "fragment_retries": 3,
            "noplaylist": False,
            # Full metadata extraction without media download. Flat entries often omit view stats.
            "extract_flat": False,
        }
        if limit is not None:
            options["playlistend"] = limit
        if self.cookies_file:
            if not self.cookies_file.is_file():
                raise ProviderError(f"Configured TIKTOK_COOKIES_FILE does not exist: {self.cookies_file}")
            options["cookiefile"] = str(self.cookies_file)
        return options

    def fetch_profile(self, profile_url: str, limit: int) -> tuple[CreatorProfile, list[CreatorVideo], dict[str, Any]]:
        parsed = parse_tiktok_url(profile_url)
        if parsed.kind != TikTokUrlKind.PROFILE:
            raise ProviderError("analyze-profile requires a TikTok profile URL, not a video URL.")
        error_capture = _YtDlpErrorCapture()
        options = self._options(limit=limit)
        # A creator playlist can contain one deleted, region-blocked, or
        # temporarily format-less entry. Keep the usable public entries and
        # report the skipped item instead of aborting the whole creator run.
        options.update({"ignoreerrors": True, "logger": error_capture})
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(parsed.canonical_url, download=False)
                if not isinstance(info, dict):
                    detail = error_capture.errors[-1] if error_capture.errors else "no profile payload returned"
                    raise ProviderError(
                        "yt-dlp could not return a usable TikTok creator profile. " + detail
                    )
                sanitized = ydl.sanitize_info(info)
        except (DownloadError, OSError, ValueError) as exc:
            raise ProviderError(
                "yt-dlp failed to fetch the TikTok creator profile. Possible reasons: region restriction, "
                "login/session required, rate limiting, or a TikTok extractor change. "
                "Update yt-dlp first; if access legitimately requires a session, set TIKTOK_COOKIES_FILE "
                "to a user-provided cookie file. Original error: " + str(exc)
            ) from exc

        entries_payload = sanitized.get("entries") or []
        entries = [entry for entry in entries_payload if isinstance(entry, dict)]
        skipped_placeholders = sum(entry is None for entry in entries_payload)
        provider_failures = list(dict.fromkeys(error_capture.errors))
        if skipped_placeholders > len(provider_failures):
            provider_failures.append(
                f"yt-dlp skipped {skipped_placeholders} profile entries without item-level metadata."
            )
        sanitized["_creator_intel_provider_failures"] = provider_failures
        videos = [self._normalize_video(entry, parsed.username) for entry in entries]
        profile = CreatorProfile(
            username=parsed.username,
            profile_url=parsed.canonical_url,
            creator_id=_first(sanitized, "channel_id", "uploader_id", "creator_id"),
            display_name=_first(sanitized, "channel", "uploader", "creator"),
            bio=_first(sanitized, "description", "channel_description"),
            follower_count=_int_or_none(_first(sanitized, "channel_follower_count", "follower_count")),
            video_count=_int_or_none(sanitized.get("playlist_count")) or len(videos),
            avatar_url=_first(sanitized, "thumbnail", "channel_thumbnail"),
            provider=self.name,
        )
        return profile, videos, sanitized

    def fetch_video(self, video_url: str) -> tuple[CreatorVideo, dict[str, Any]]:
        parsed = parse_tiktok_url(video_url)
        if parsed.kind != TikTokUrlKind.VIDEO:
            raise ProviderError("analyze-video requires a TikTok /video/ URL.")
        try:
            with YoutubeDL(self._options()) as ydl:
                info = ydl.extract_info(parsed.canonical_url, download=False)
                sanitized = ydl.sanitize_info(info)
        except (DownloadError, OSError, ValueError) as exc:
            raise ProviderError(
                "yt-dlp failed to fetch TikTok video metadata. The public video may be unavailable, "
                "region-restricted, login-gated, or the extractor may have changed. Original error: " + str(exc)
            ) from exc
        return self._normalize_video(sanitized, parsed.username), sanitized

    def _normalize_video(self, raw: dict[str, Any], username: str) -> CreatorVideo:
        video_id = str(_first(raw, "id", "video_id") or "")
        webpage_url = _first(raw, "webpage_url", "original_url")
        if not webpage_url and raw.get("url", "").startswith("http") and "/video/" in raw.get("url", ""):
            webpage_url = raw["url"]
        if not webpage_url and video_id:
            webpage_url = f"https://www.tiktok.com/@{username}/video/{video_id}"
        description = _first(raw, "description", "title")
        hashtags = _normalize_tags(raw.get("tags"), description)
        mentions = sorted(set(re.findall(r"(?<!\w)@([A-Za-z0-9._-]+)", description or "")))
        timestamp = _int_or_none(raw.get("timestamp"))
        plugin_signals = extract_plugin_signals(raw)
        provider_ad_paths = _explicit_commercial_true_paths(raw)
        platform_is_ad = True if plugin_signals.platform_is_ad is True or provider_ad_paths else plugin_signals.platform_is_ad
        return CreatorVideo(
            video_id=video_id or _id_from_url(str(webpage_url or raw.get("url") or "")),
            video_url=str(webpage_url or raw.get("url") or ""),
            # The URL parser is the authoritative username source. yt-dlp uploader_id may be numeric.
            creator_username=username,
            creator_id=_string_or_none(_first(raw, "creator_id", "channel_id", "uploader_id")),
            description=_string_or_none(description),
            caption=_string_or_none(description),
            hashtags=hashtags,
            mentions=mentions,
            timestamp=timestamp,
            published_at=datetime.fromtimestamp(timestamp, tz=UTC) if timestamp else None,
            duration=_float_or_none(raw.get("duration")),
            view_count=_int_or_none(_first(raw, "view_count", "play_count")),
            like_count=_int_or_none(raw.get("like_count")),
            comment_count=_int_or_none(raw.get("comment_count")),
            share_count=_int_or_none(raw.get("repost_count") or raw.get("share_count")),
            save_count=_int_or_none(raw.get("save_count")),
            thumbnail=_string_or_none(raw.get("thumbnail")),
            music=_string_or_none(_first(raw, "track", "artist", "music")),
            channel_metadata={
                key: raw.get(key)
                for key in ("channel", "channel_id", "uploader", "uploader_id", "channel_follower_count")
                if raw.get(key) is not None
            },
            platform_labels=_platform_labels(raw),
            product_data=_product_data(raw),
            platform_is_ad=platform_is_ad,
            has_product_anchor=plugin_signals.has_product_anchor,
            product_ids=plugin_signals.product_ids,
            is_pinned=raw.get("is_pinned") if isinstance(raw.get("is_pinned"), bool) else None,
            provider=self.name,
            raw_metadata={
                key: raw.get(key)
                for key in ("availability", "age_limit", "categories", "tags", "live_status")
                if raw.get(key) is not None
            }
            | ({"plugin_signal_evidence": [asdict(item) for item in plugin_signals.evidence]} if plugin_signals.evidence else {})
            | ({"provider_structured_ad_paths": provider_ad_paths} if provider_ad_paths else {}),
        )


class TikTokApiProvider(ProfileProvider):
    """Optional fallback requiring an explicitly provided ms_token and Playwright browser."""

    name = "TikTokApi"

    def __init__(self, *, ms_token: str | None = None) -> None:
        self.ms_token = ms_token or os.getenv("TIKTOK_MS_TOKEN")

    def fetch_profile(self, profile_url: str, limit: int) -> tuple[CreatorProfile, list[CreatorVideo], dict[str, Any]]:
        return asyncio.run(self._fetch_profile(profile_url, limit))

    async def _fetch_profile(
        self, profile_url: str, limit: int
    ) -> tuple[CreatorProfile, list[CreatorVideo], dict[str, Any]]:
        if not self.ms_token:
            raise ProviderError("TikTokApi fallback requires a user-provided TIKTOK_MS_TOKEN.")
        try:
            from TikTokApi import TikTokApi
        except ImportError as exc:
            raise ProviderError("Install the optional dependency with: pip install '.[tiktok-api]'") from exc
        parsed = parse_tiktok_url(profile_url)
        raw_videos: list[dict[str, Any]] = []
        try:
            async with TikTokApi() as api:
                await api.create_sessions(
                    ms_tokens=[self.ms_token],
                    num_sessions=1,
                    sleep_after=3,
                    browser=os.getenv("TIKTOK_BROWSER", "chromium"),
                )
                user = api.user(username=parsed.username)
                async for video in user.videos(count=limit):
                    raw_videos.append(video.as_dict)
        except Exception as exc:
            raise ProviderError(f"TikTokApi fallback failed: {exc}") from exc
        normalizer = YtDlpProvider()
        videos = [normalizer._normalize_video(_tiktok_api_to_ytdlp(item), parsed.username) for item in raw_videos]
        profile = CreatorProfile(
            username=parsed.username,
            profile_url=parsed.canonical_url,
            video_count=len(videos),
            provider=self.name,
        )
        return profile, videos, {"entries": raw_videos, "provider": self.name}

    def fetch_video(self, video_url: str) -> tuple[CreatorVideo, dict[str, Any]]:
        raise ProviderError("TikTokApi single-video fallback is not enabled in the MVP; use yt-dlp.")


def _first(raw: dict[str, Any], *keys: str) -> Any:
    return next((raw[key] for key in keys if raw.get(key) not in (None, "")), None)


def _string_or_none(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _id_from_url(url: str) -> str:
    match = re.search(r"/video/(\d+)", url)
    return match.group(1) if match else url.rsplit("/", 1)[-1]


def _normalize_tags(tags: Any, description: str | None) -> list[str]:
    values = [str(item).lstrip("#") for item in tags] if isinstance(tags, list) else []
    values.extend(re.findall(r"(?<!\w)#([\w.-]+)", description or ""))
    return sorted({value.lower() for value in values if value})


def _platform_labels(raw: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for key in ("label", "labels"):
        value = raw.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str) and item.strip():
                labels.append(item.strip())
    return labels


def _product_data(raw: dict[str, Any]) -> list[dict[str, Any]]:
    # commerce_info is often an unrelated container (seller/account/branded
    # metadata) and is deliberately not normalized as an attached product.
    for key in ("products", "productData", "product_data"):
        value = raw.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [value]
    return []


_EXPLICIT_COMMERCIAL_BOOLEAN_KEYS = {
    "paid_partnership",
    "paidPartnership",
    "is_paid_partnership",
    "isPaidPartnership",
    "commercial_content",
    "commercialContent",
    "is_commercial_content",
    "isCommercialContent",
    "promotional_content",
    "promotionalContent",
    "is_promotional_content",
    "isPromotionalContent",
    "creator_earns_commission",
    "creatorEarnsCommission",
    "commission_eligible",
    "commissionEligible",
    "is_commission_eligible",
    "isCommissionEligible",
    "branded_content",
    "brandedContent",
}


def _explicit_commercial_true_paths(raw: Any) -> list[str]:
    """Find exact true flags at the normalized video-candidate root.

    Nested author/account metadata belongs to another entity and must not be
    attributed to the video.
    """

    if not isinstance(raw, dict):
        return []
    return [
        f"$.{key}"
        for key, value in raw.items()
        if key in _EXPLICIT_COMMERCIAL_BOOLEAN_KEYS and _is_strict_true(value)
    ]


def _is_strict_true(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return value == 1
    return isinstance(value, str) and value.strip().casefold() in {"true", "1"}


def _tiktok_api_to_ytdlp(raw: dict[str, Any]) -> dict[str, Any]:
    stats = raw.get("stats") or {}
    author = raw.get("author") or {}
    return {
        "id": raw.get("id"),
        "webpage_url": f"https://www.tiktok.com/@{author.get('uniqueId', '')}/video/{raw.get('id', '')}",
        "uploader_id": author.get("uniqueId"),
        "creator_id": author.get("id"),
        "description": raw.get("desc"),
        "timestamp": raw.get("createTime"),
        "duration": (raw.get("video") or {}).get("duration"),
        "view_count": stats.get("playCount"),
        "like_count": stats.get("diggCount"),
        "comment_count": stats.get("commentCount"),
        "share_count": stats.get("shareCount"),
    }
