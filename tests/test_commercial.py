from creator_intel.commercial import RuleClassifier
from creator_intel.config import Settings
from creator_intel.models import CommercialStatus, CreatorVideo, EvidenceType


def test_creator_paid_partnership_text_is_not_mislabeled_as_platform_evidence() -> None:
    result = RuleClassifier(Settings().rules_path).classify(
        CreatorVideo(
            video_id="1",
            video_url="https://www.tiktok.com/@x/video/12345",
            creator_username="x",
            caption="Paid partnership with ACME",
        )
    )
    assert result.status == CommercialStatus.LIKELY
    assert EvidenceType.EXPLICIT_VERBAL_DISCLOSURE in {item.type for item in result.evidence}
    assert result.evidence[0].source == "creator_caption"


def test_actual_platform_label_is_confirmed_and_keeps_platform_source() -> None:
    result = RuleClassifier(Settings().rules_path).classify(
        CreatorVideo(
            video_id="1",
            video_url="https://www.tiktok.com/@x/video/12345",
            creator_username="x",
            platform_labels=["Paid partnership"],
        )
    )
    assert result.status == CommercialStatus.CONFIRMED
    assert result.evidence[0].type == EvidenceType.PLATFORM_PAID_PARTNERSHIP_LABEL
    assert result.evidence[0].source == "platform_label"


def test_instructional_paid_partnership_phrase_is_not_a_disclosure() -> None:
    result = RuleClassifier(Settings().rules_path).classify(
        CreatorVideo(
            video_id="1",
            video_url="https://www.tiktok.com/@x/video/12345",
            creator_username="x",
            caption="Tutorial: how to add the paid partnership label",
        )
    )
    assert result.status == CommercialStatus.UNKNOWN
    assert result.evidence == []


def test_labeled_case_fixture(load_fixture) -> None:
    classifier = RuleClassifier(Settings().rules_path)
    cases = load_fixture("commercial_cases.json")
    predicted = []
    for index, case in enumerate(cases):
        result = classifier.classify(
            CreatorVideo(
                video_id=str(index),
                video_url=f"https://www.tiktok.com/@x/video/{10000 + index}",
                creator_username="x",
                caption=case["caption"] or None,
                platform_labels=case["labels"],
            )
        )
        predicted.append(result.status.value)
    exact = sum(expected["expected"] == actual for expected, actual in zip(cases, predicted, strict=True))
    assert exact / len(cases) >= 0.90, {"expected": [c["expected"] for c in cases], "predicted": predicted}


def test_no_positive_rule_match_remains_unknown_not_certain_organic() -> None:
    classifier = RuleClassifier(Settings().rules_path)
    no_match = classifier.classify(CreatorVideo(video_id="1", video_url="x", creator_username="x", caption="my daily vlog"))
    unknown = classifier.classify(CreatorVideo(video_id="2", video_url="x", creator_username="x"))
    assert no_match.status == CommercialStatus.UNKNOWN
    assert no_match.confidence == 0
    assert unknown.status == CommercialStatus.UNKNOWN


def test_explicit_platform_ad_is_confirmed_but_product_anchor_alone_is_possible() -> None:
    classifier = RuleClassifier(Settings().rules_path)
    explicit = classifier.classify(
        CreatorVideo(video_id="1", video_url="x", creator_username="x", platform_is_ad=True)
    )
    product = classifier.classify(
        CreatorVideo(video_id="2", video_url="x", creator_username="x", has_product_anchor=True)
    )
    product_id = classifier.classify(
        CreatorVideo(video_id="3", video_url="x", creator_username="x", product_ids=["sku-1"])
    )
    generic_product_payload = classifier.classify(
        CreatorVideo(video_id="4", video_url="x", creator_username="x", product_data=[{"seller": "shop"}])
    )
    assert explicit.status == CommercialStatus.CONFIRMED
    assert explicit.explicit_ad is True
    assert product.status == CommercialStatus.POSSIBLE
    assert product.explicit_ad is None
    assert "product_anchor" in product.commercial_kinds
    assert product_id.status == CommercialStatus.POSSIBLE
    assert generic_product_payload.status == CommercialStatus.UNKNOWN
