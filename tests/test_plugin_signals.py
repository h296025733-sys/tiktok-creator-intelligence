import json

import pytest

from creator_intel.plugin_signals import extract_plugin_signals


def test_explicit_ad_true_wins_over_false_at_candidate_root() -> None:
    signals = extract_plugin_signals({"isAd": False, "promote_type": "2"})
    assert signals.platform_is_ad is True


def test_explicit_false_is_preserved_when_no_positive_signal() -> None:
    assert extract_plugin_signals({"is_ad": "false"}).platform_is_ad is False
    assert extract_plugin_signals({}).platform_is_ad is None


def test_arbitrary_nested_ad_markers_do_not_leak_from_author_or_metadata() -> None:
    raw = {
        "author": {"isAd": True, "adId": "author-ad"},
        "metadata": {"promoteType": 2},
    }
    signals = extract_plugin_signals(raw)
    assert signals.platform_is_ad is None


def test_shop_anchor_does_not_equal_ad_without_label_version() -> None:
    raw = {
        "anchors": [
            {"component_key": "anchor_shop", "type": 33, "product_id": "p-123"}
        ]
    }
    signals = extract_plugin_signals(raw)
    assert signals.has_product_anchor is True
    assert signals.product_ids == ["p-123"]
    assert signals.platform_is_ad is None


def test_shop_anchor_plus_ad_label_is_explicit_ad() -> None:
    raw = {
        "adLabelVersion": 1,
        "anchors": [
            {"componentKey": "anchor_shop", "type": "33", "productId": "99"}
        ],
    }
    signals = extract_plugin_signals(raw)
    assert signals.platform_is_ad is True
    assert signals.has_product_anchor is True


def test_strict_shop_anchor_is_found_inside_anchor_extra_json() -> None:
    extra = json.dumps(
        {
            "payload": {
                "shop": {
                    "componentKey": " ANCHOR_SHOP ",
                    "type": "33",
                    "id": "sku-extra",
                }
            }
        }
    )
    signals = extract_plugin_signals(
        {"ad_label_version": "2", "anchorList": [{"extraInfo": extra}]}
    )
    assert signals.platform_is_ad is True
    assert signals.has_product_anchor is True
    assert signals.product_ids == ["sku-extra"]


def test_branded_video_tag_recurses_and_supports_compiled_key_aliases() -> None:
    signals = extract_plugin_signals(
        {
            "video_tag": {
                "wrapper": [
                    {"details": {"videoTagType": "Branded", "videoTagNumber": "7"}}
                ]
            }
        }
    )
    assert signals.platform_is_ad is True


@pytest.mark.parametrize(
    "label",
    [
        "Advertisement",
        "Sponsored",
        "Paid partnership",
        "Promotional content",
        "Creator earns commission",
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
    ],
)
def test_all_compiled_structured_disclosure_labels(label: str) -> None:
    signals = extract_plugin_signals({"hybridLabel": {"label_text": f"\x00 {label}  "}})
    assert signals.platform_is_ad is True


@pytest.mark.parametrize(
    "payload",
    [
        {"level": {"is_paid_partnership": "true"}},
        {"level": {"campaignId": 123}},
        {"level": {"disclosureText": "Sponsored"}},
        {"level": {"branded_content_type": "1"}},
    ],
)
def test_commercial_container_recurses_compiled_boolean_id_and_label_keys(
    payload: dict[str, object],
) -> None:
    assert extract_plugin_signals({"commercialVideoInfo": payload}).platform_is_ad is True


def test_nested_product_extra_and_unrelated_sensitive_container() -> None:
    raw = {
        "anchors": [{"extra": '{"payload":{"product_id":"abc"}}'}],
        "authorization": {"isAd": True},
    }
    signals = extract_plugin_signals(raw)
    assert signals.has_product_anchor is True
    assert signals.product_ids == ["abc"]
    assert signals.platform_is_ad is None
