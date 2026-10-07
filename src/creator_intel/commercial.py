"""Explainable, configurable metadata-first commercial classification."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import (
    CommercialClassification,
    CommercialEvidence,
    CommercialStatus,
    CreatorVideo,
    EvidenceType,
)


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    evidence_type: EvidenceType
    weight: float
    patterns: tuple[re.Pattern[str], ...]
    source: str = "metadata"


class RuleClassifier:
    """Score distinct commercial signals without confusing text with platform facts."""

    def __init__(self, config_path: Path) -> None:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.thresholds: dict[str, float] = raw["thresholds"]
        self.max_score = float(raw.get("max_score", 100))
        self.rules = tuple(self._build_rule(item) for item in raw["rules"])

    @staticmethod
    def _build_rule(item: dict[str, Any]) -> Rule:
        return Rule(
            name=item["name"],
            evidence_type=EvidenceType(item["evidence_type"]),
            weight=float(item["weight"]),
            patterns=tuple(re.compile(pattern, re.IGNORECASE) for pattern in item["patterns"]),
            source=item.get("source", "metadata"),
        )

    def classify(self, video: CreatorVideo) -> CommercialClassification:
        evidence: list[CommercialEvidence] = []
        score = 0.0
        commercial_kinds: list[str] = []
        if video.platform_is_ad is True:
            evidence.append(
                CommercialEvidence(
                    type=EvidenceType.PLATFORM_PAID_PARTNERSHIP_LABEL,
                    value="TikTok structured metadata explicitly marked this video as an ad or promotion",
                    weight=self.max_score,
                    source="platform_structured_metadata",
                )
            )
            score = self.max_score
            commercial_kinds.append("explicit_platform_ad")
        # Product payloads vary widely between providers. Only the strict
        # structured parser's decision (or the product IDs it extracted) is a
        # product-anchor signal; a generic commerce_info dictionary is not.
        if video.has_product_anchor is True or bool(video.product_ids):
            evidence.append(
                CommercialEvidence(
                    type=EvidenceType.TIKTOK_SHOP_PRODUCT,
                    value="explicit product/TikTok Shop anchor",
                    weight=20,
                    source="platform_structured_metadata",
                )
            )
            score += 20
            commercial_kinds.append("product_anchor")

        surfaces = self._text_surfaces(video)
        matched_regions: list[tuple[int, int, int]] = []
        for rule in self.rules:
            matched = self._match_rule(rule, surfaces)
            if matched is None:
                continue
            match, source, surface_index = matched
            if any(
                existing_surface == surface_index and match.start() < end and start < match.end()
                for existing_surface, start, end in matched_regions
            ):
                # Configurable rules can contain nested synonyms (for example
                # "creator earns commission" and "earns commission"). One
                # phrase is one signal, not two independent confirmations.
                continue
            matched_regions.append((surface_index, match.start(), match.end()))
            evidence_type = rule.evidence_type
            weight = rule.weight
            if source != "platform_label" and rule.source == "platform":
                # Wording such as "paid partnership" in creator-authored text
                # is a disclosure claim, not proof that TikTok supplied a
                # platform label. Keep it useful but below confirmed strength.
                weight = min(weight, self.thresholds["likely"])
                if evidence_type == EvidenceType.PLATFORM_PAID_PARTNERSHIP_LABEL:
                    evidence_type = EvidenceType.EXPLICIT_VERBAL_DISCLOSURE
            evidence.append(
                CommercialEvidence(
                    type=evidence_type,
                    value=match.group(0)[:160],
                    weight=weight,
                    source=source,
                )
            )
            score += weight
            kind = evidence_type.value
            if kind not in commercial_kinds:
                commercial_kinds.append(kind)

        score = min(score, self.max_score)
        status = self._status(score)
        probability = min(score / self.max_score, 1.0)
        if evidence:
            reason = "Matched signals: " + ", ".join(f"{item.type.value} (+{item.weight:g})" for item in evidence)
        elif video.platform_is_ad is False:
            reason = (
                "TikTok structured metadata did not mark this video as an ad, but that negative marker alone "
                "does not prove the content is organic; no positive commercial signal matched."
            )
        elif surfaces:
            reason = (
                "No configured positive commercial signal matched the available metadata; absence of a match "
                "is not evidence that the video is organic."
            )
        else:
            reason = "Metadata did not contain enough positive evidence to classify."
        return CommercialClassification(
            video_id=video.video_id,
            status=status,
            probability=probability,
            score=score,
            evidence=evidence,
            reasoning=reason,
            explicit_ad=video.platform_is_ad,
            commercial_kinds=commercial_kinds,
        )

    @staticmethod
    def _text_surfaces(video: CreatorVideo) -> list[tuple[str, str]]:
        """Return auditable text surfaces in evidence-precedence order.

        Provider normalization commonly puts the same title in both caption
        and description. Deduplicating prevents one creator-authored phrase
        from being counted twice.
        """

        surfaces: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add(source: str, value: str | None) -> None:
            normalized = " ".join((value or "").split())
            fingerprint = normalized.casefold()
            if not normalized or fingerprint in seen:
                return
            seen.add(fingerprint)
            surfaces.append((source, normalized))

        for label in video.platform_labels:
            add("platform_label", label)
        for hashtag in video.hashtags:
            add("creator_hashtag", f"#{hashtag.lstrip('#')}")
        add("creator_caption", video.caption)
        add("creator_description", video.description)
        for mention in video.mentions:
            add("creator_mention", f"@{mention.lstrip('@')}")
        return surfaces

    @staticmethod
    def _match_rule(
        rule: Rule, surfaces: list[tuple[str, str]]
    ) -> tuple[re.Match[str], str, int] | None:
        for surface_index, (source, text) in enumerate(surfaces):
            for pattern in rule.patterns:
                match = pattern.search(text)
                if match is None:
                    continue
                if source != "platform_label" and rule.source == "platform" and _instructional_context(text):
                    # A tutorial that mentions a UI label is not a disclosure.
                    continue
                return match, source, surface_index
        return None

    def benchmark(self, cases: list[dict[str, Any]]) -> dict[str, Any]:
        labels = [status.value for status in CommercialStatus]
        matrix = {expected: {actual: 0 for actual in labels} for expected in labels}
        correct = 0
        positive_labels = {
            CommercialStatus.CONFIRMED.value,
            CommercialStatus.LIKELY.value,
            CommercialStatus.POSSIBLE.value,
        }
        binary_tp = binary_fp = binary_fn = binary_tn = 0
        for index, case in enumerate(cases):
            video = CreatorVideo(
                video_id=str(index),
                video_url=f"fixture://{index}",
                creator_username="fixture",
                caption=case.get("caption") or None,
                platform_labels=case.get("labels") or [],
            )
            actual = self.classify(video).status.value
            expected = case["expected"]
            matrix[expected][actual] += 1
            correct += actual == expected
            expected_positive = expected in positive_labels
            actual_positive = actual in positive_labels
            if expected_positive and actual_positive:
                binary_tp += 1
            elif not expected_positive and actual_positive:
                binary_fp += 1
            elif expected_positive and not actual_positive:
                binary_fn += 1
            else:
                binary_tn += 1
        precision = binary_tp / (binary_tp + binary_fp) if binary_tp + binary_fp else 0.0
        recall = binary_tp / (binary_tp + binary_fn) if binary_tp + binary_fn else 0.0
        return {
            "cases": len(cases),
            "correct": correct,
            "accuracy": (correct / len(cases) if cases else 0.0),
            "matrix": matrix,
            "commercial_binary": {
                "true_positive": binary_tp,
                "false_positive": binary_fp,
                "false_negative": binary_fn,
                "true_negative": binary_tn,
                "precision": precision,
                "recall": recall,
                "f1": (2 * precision * recall / (precision + recall) if precision + recall else 0.0),
            },
        }

    def _status(self, score: float) -> CommercialStatus:
        if score >= self.thresholds["confirmed"]:
            return CommercialStatus.CONFIRMED
        if score >= self.thresholds["likely"]:
            return CommercialStatus.LIKELY
        if score >= self.thresholds["possible"]:
            return CommercialStatus.POSSIBLE
        return CommercialStatus.UNKNOWN


_INSTRUCTIONAL_DISCLOSURE = re.compile(
    r"\b(?:how\s+to|tutorial|guide|explainer|example|what\s+is|add(?:ing)?|remov(?:e|ing)|"
    r"enabl(?:e|ing)|disabl(?:e|ing)|turn(?:ing)?\s+(?:on|off))\b",
    re.IGNORECASE,
)


def _instructional_context(text: str) -> bool:
    return bool(_INSTRUCTIONAL_DISCLOSURE.search(text))
