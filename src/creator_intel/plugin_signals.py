"""Explicit commercial signals mirrored from TDA Chrome v0.1.28.

The extension distinguishes a platform advertising marker from a product or
TikTok Shop anchor.  It also reads ordinary ad keys only from the normalized
video-candidate root; that boundary prevents fields such as ``author.isAd``
from being attributed to the video.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


_TRUE_VALUES = (True, 1, "1", "true")
_FALSE_VALUES = (False, 0, "0", "false")
_AD_BOOLEAN_KEYS = ("isAd", "is_ad", "ad", "advertisement", "promoted")
_AD_ID_KEYS = ("adId", "ad_id", "advertiser", "adInfo", "ad_info")
_PROMOTE_KEYS = ("promoteType", "promote_type")
_AD_LABEL_VERSION_KEYS = ("adLabelVersion", "ad_label_version")
_PRODUCT_KEYS = ("productData", "product_data")
_ANCHOR_KEYS = ("anchors", "anchorList", "anchor_list")
_COMMERCIAL_CONTAINERS = ("commercialVideoInfo", "commercial_video_info", "hybridLabel", "hybrid_label")

_COMMERCIAL_BOOLEAN_MARKER = re.compile(
    r"^(?:is_?ad|paid_?partnership|is_?paid_?partnership|promotional_?content|"
    r"is_?promotional_?content|creator_?earns_?commission|commission_?eligible|"
    r"is_?commission_?eligible|commercial_?content|is_?commercial_?content)$",
    re.IGNORECASE,
)
_COMMERCIAL_IDENTIFIER = re.compile(
    r"^(?:ad_?id|advertiser_?id|campaign_?id|commercial_?content_?id|commercial_?video_?id)$",
    re.IGNORECASE,
)
_COMMERCIAL_LABEL_KEY = re.compile(
    r"^(?:label|label_?text|text|title|tag|disclosure|disclosure_?text)$",
    re.IGNORECASE,
)

# Kept as escapes so this source remains stable across Windows code pages.
_DISCLOSURE_LABELS = {
    "advertisement",
    "sponsored",
    "paid partnership",
    "promotional content",
    "creator earns commission",
    "\u5e7f\u544a",
    "\u5ee3\u544a",
    "\u4ed8\u8d39\u5408\u4f5c\u5173\u7cfb",
    "\u4ed8\u8cbb\u5408\u4f5c\u95dc\u4fc2",
    "\u4ed8\u8d39\u5408\u4f5c\u4f19\u4f34\u5173\u7cfb",
    "\u4ed8\u8cbb\u5408\u4f5c\u5925\u4f34\u95dc\u4fc2",
    "\u63a8\u5e7f\u5185\u5bb9",
    "\u63a8\u5ee3\u5167\u5bb9",
    "\u521b\u4f5c\u8005\u8d5a\u53d6\u4f63\u91d1",
    "\u5275\u4f5c\u8005\u8cfa\u53d6\u4f63\u91d1",
    "\u7b26\u5408\u4f63\u91d1\u9886\u53d6\u8d44\u683c",
    "\u7b26\u5408\u4f63\u91d1\u9818\u53d6\u8cc7\u683c",
    "\u7b26\u5408\u9886\u53d6\u4f63\u91d1\u8d44\u683c",
    "\u7b26\u5408\u9818\u53d6\u4f63\u91d1\u8cc7\u683c",
    "\u30d7\u30ed\u30e2\u30fc\u30b7\u30e7\u30f3\u30b3\u30f3\u30c6\u30f3\u30c4",
    "\u6709\u6599\u30d1\u30fc\u30c8\u30ca\u30fc\u30b7\u30c3\u30d7",
    "\u6709\u511f\u30d1\u30fc\u30c8\u30ca\u30fc\u30b7\u30c3\u30d7",
    "\uad11\uace0",
    "\uc720\ub8cc \ud30c\ud2b8\ub108\uc2ed",
    "\ud504\ub85c\ubaa8\uc158 \ucf58\ud150\uce20",
}


@dataclass(frozen=True, slots=True)
class StructuredSignalEvidence:
    kind: str
    key_path: str
    value: str
    source: str = "structured_metadata"


@dataclass(slots=True)
class PluginSignals:
    platform_is_ad: bool | None = None
    has_product_anchor: bool | None = None
    product_ids: list[str] = field(default_factory=list)
    evidence: list[StructuredSignalEvidence] = field(default_factory=list)


def extract_plugin_signals(raw: Any) -> PluginSignals:
    """Read one normalized video-candidate root using extension boundaries."""

    result = PluginSignals()
    if not isinstance(raw, dict):
        return result

    result.platform_is_ad, ad_evidence = _read_ad_marker(raw, "$")
    result.evidence.extend(ad_evidence)
    result.has_product_anchor, result.product_ids, product_evidence = _read_product_marker(raw, "$")
    result.evidence.extend(product_evidence)
    return result


def _read_ad_marker(node: dict[str, Any], path: str) -> tuple[bool | None, list[StructuredSignalEvidence]]:
    explicit_false = False
    evidence: list[StructuredSignalEvidence] = []
    for key in _AD_BOOLEAN_KEYS:
        if key not in node:
            continue
        marker = node[key]
        if _marker_equals(marker, _TRUE_VALUES):
            evidence.append(_evidence("platform_ad_boolean", path, key, marker))
            return True, evidence
        if _marker_equals(marker, _FALSE_VALUES):
            explicit_false = True
    for key in _AD_ID_KEYS:
        marker = node.get(key)
        # Exact extension behavior: only missing/null/the empty string are absent.
        if marker is not None and marker != "":
            evidence.append(_evidence("platform_ad_identifier", path, key, marker))
            return True, evidence
    for key in _PROMOTE_KEYS:
        marker = node.get(key)
        if _positive_integer(marker):
            evidence.append(_evidence("platform_promote_type", path, key, marker))
            return True, evidence
    label_version = _first_defined(node, _AD_LABEL_VERSION_KEYS)
    if _positive_integer(label_version) and _has_strict_shop_anchor(node):
        evidence.append(_evidence("shop_ad_label", path, "adLabelVersion+anchor_shop", label_version))
        return True, evidence
    video_tag = _first_defined(node, ("videoTag", "video_tag"))
    if _has_branded_video_tag(video_tag, 0):
        evidence.append(_evidence("branded_video_tag", path, "videoTag", video_tag))
        return True, evidence
    commerce = _first_mapping(node, ("commerceInfo", "commerce_info"))
    if commerce is not None:
        branded = _first_defined(commerce, ("brandedContentType", "branded_content_type"))
        if _positive_integer(branded):
            evidence.append(_evidence("branded_content_type", path, "commerceInfo.brandedContentType", branded))
            return True, evidence
    for key in _COMMERCIAL_CONTAINERS:
        if key in node and _contains_disclosure(node[key], 0):
            evidence.append(_evidence("commercial_disclosure", path, key, node[key]))
            return True, evidence
    return (False if explicit_false else None), evidence


def _read_product_marker(
    node: dict[str, Any], path: str
) -> tuple[bool | None, list[str], list[StructuredSignalEvidence]]:
    ids: list[str] = []
    evidence: list[StructuredSignalEvidence] = []
    for key in _PRODUCT_KEYS:
        found = _product_ids(node.get(key), 0)
        if found:
            ids.extend(found)
            evidence.append(_evidence("product_anchor", path, key, found))
    for key in _ANCHOR_KEYS:
        anchors = node.get(key)
        if not isinstance(anchors, list):
            continue
        for index, anchor in enumerate(anchors[:40]):
            if not isinstance(anchor, dict):
                continue
            found = _product_ids(anchor, 0)
            found.extend(_shop_anchor_ids(anchor))
            extra = _first_defined(anchor, ("extra", "extraInfo", "extra_info"))
            parsed = _safe_json(extra)
            found.extend(_product_ids(parsed, 0))
            found = list(dict.fromkeys(found))
            if found:
                ids.extend(found)
                evidence.append(_evidence("product_anchor", path, f"{key}[{index}]", found))
    unique_ids = list(dict.fromkeys(ids))
    return (True if unique_ids else None), unique_ids, evidence


def _contains_disclosure(value: Any, depth: int) -> bool:
    if depth > 3 or value is None:
        return False
    if isinstance(value, str):
        if _normalize_label(value) in _DISCLOSURE_LABELS:
            return True
        parsed = _safe_json(value)
        return isinstance(parsed, (dict, list)) and _contains_disclosure(parsed, depth + 1)
    if isinstance(value, list):
        return any(_contains_disclosure(item, depth + 1) for item in value[:40])
    if not isinstance(value, dict):
        return False
    entries = list(value.items())[:80]
    for key, child in entries:
        if _COMMERCIAL_BOOLEAN_MARKER.fullmatch(key) and _marker_equals(child, _TRUE_VALUES):
            return True
        if _COMMERCIAL_IDENTIFIER.fullmatch(key) and _nonempty_identifier(child):
            return True
        if _COMMERCIAL_LABEL_KEY.fullmatch(key) and isinstance(child, str):
            if _normalize_label(child) in _DISCLOSURE_LABELS:
                return True
        if key in {"brandedContentType", "branded_content_type"} and _positive_integer(child):
            return True
    return any(
        isinstance(child, (dict, list)) and _contains_disclosure(child, depth + 1)
        for _, child in entries
    )


def _has_branded_video_tag(value: Any, depth: int) -> bool:
    if depth > 3 or value is None:
        return False
    if isinstance(value, list):
        return any(_has_branded_video_tag(item, depth + 1) for item in value[:40])
    if not isinstance(value, dict):
        return False
    raw_type = _first_defined(value, ("type", "videoTagType", "video_tag_type"))
    tag_type = _normalize_label(raw_type) if isinstance(raw_type, str) else ""
    number = _first_defined(value, ("number", "videoTagNumber", "video_tag_number"))
    if tag_type in {"branded", "branded type"} and _integer(number) in {1, 7}:
        return True
    return any(
        isinstance(child, (dict, list)) and _has_branded_video_tag(child, depth + 1)
        for child in list(value.values())[:40]
    )


def _has_strict_shop_anchor(node: dict[str, Any]) -> bool:
    for key in _ANCHOR_KEYS:
        anchors = node.get(key)
        if not isinstance(anchors, list):
            continue
        if any(isinstance(anchor, dict) and bool(_shop_anchor_ids(anchor)) for anchor in anchors[:40]):
            return True
    return False


def _shop_anchor_ids(anchor: dict[str, Any]) -> list[str]:
    candidates: list[Any] = [anchor]
    for key in ("extra", "extraInfo", "extra_info"):
        if key in anchor:
            candidates.append(_safe_json(anchor[key]))
    found: list[str] = []
    for candidate in candidates:
        found.extend(_strict_shop_anchor_ids(candidate, 0))
    return list(dict.fromkeys(found))


def _strict_shop_anchor_ids(value: Any, depth: int) -> list[str]:
    if depth > 3 or value is None:
        return []
    if isinstance(value, list):
        found: list[str] = []
        for item in value[:40]:
            found.extend(_strict_shop_anchor_ids(item, depth + 1))
        return found
    if not isinstance(value, dict):
        return []
    found = []
    if _is_strict_shop_anchor(value):
        identifier = _first_defined(value, ("id", "product_id", "productId"))
        found.append(str(identifier).strip())
    for child in list(value.values())[:80]:
        if isinstance(child, (dict, list)):
            found.extend(_strict_shop_anchor_ids(child, depth + 1))
    return found


def _is_strict_shop_anchor(anchor: dict[str, Any]) -> bool:
    component = _first_defined(anchor, ("component_key", "componentKey"))
    component = _normalize_label(component) if isinstance(component, str) else ""
    anchor_type = _integer(anchor.get("type"))
    identifier = _first_defined(anchor, ("id", "product_id", "productId"))
    return component == "anchor_shop" and anchor_type == 33 and _nonempty_identifier(identifier)


def _product_ids(value: Any, depth: int) -> list[str]:
    if depth > 3 or value is None:
        return []
    if isinstance(value, list):
        found: list[str] = []
        for item in value[:40]:
            found.extend(_product_ids(item, depth + 1))
        return found
    if not isinstance(value, dict):
        return []
    found = []
    for key, child in list(value.items())[:80]:
        if re.fullmatch(r"product_?id", key, re.IGNORECASE) and _nonempty_identifier(child):
            found.append(str(child).strip())
        elif isinstance(child, (dict, list)):
            found.extend(_product_ids(child, depth + 1))
    return found


def _safe_json(value: Any) -> Any:
    if not isinstance(value, str) or len(value) > 20_000:
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _positive_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        try:
            return value == int(value) and 0 < int(value) <= 9_007_199_254_740_991
        except (OverflowError, ValueError):
            return False
    if isinstance(value, str):
        stripped = value.strip()
        return bool(re.fullmatch(r"\d+", stripped)) and any(character != "0" for character in stripped)
    return False


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            parsed = int(value)
        except (OverflowError, ValueError):
            return None
        if value == parsed:
            return parsed if 0 <= parsed <= 9_007_199_254_740_991 else None
    if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        try:
            parsed = int(value.strip())
        except ValueError:
            return None
        return parsed if parsed <= 9_007_199_254_740_991 else None
    return None


def _nonempty_identifier(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    parsed = _integer(value)
    return parsed is not None and parsed > 0


def _marker_equals(value: Any, candidates: tuple[Any, ...]) -> bool:
    for candidate in candidates:
        if isinstance(candidate, bool):
            if value is candidate:
                return True
        elif isinstance(candidate, int):
            if not isinstance(value, bool) and isinstance(value, (int, float)) and value == candidate:
                return True
        elif isinstance(candidate, str) and isinstance(value, str) and value == candidate:
            return True
    return False


def _normalize_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"[\x00-\x1f\x7f]+", " ", normalized)
    return " ".join(normalized.lower().split())


def _first_defined(node: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in node and node[key] is not None:
            return node[key]
    return None


def _first_mapping(node: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any] | None:
    for key in keys:
        value = node.get(key)
        if isinstance(value, dict):
            return value
    return None


def _evidence(kind: str, path: str, key: str, value: Any) -> StructuredSignalEvidence:
    rendered = json.dumps(value, ensure_ascii=False, default=str)
    return StructuredSignalEvidence(kind=kind, key_path=f"{path}.{key}", value=rendered[:300])
