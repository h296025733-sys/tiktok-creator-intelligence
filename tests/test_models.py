import pytest
from pydantic import ValidationError

from creator_intel.models import CodexCreatorReview, CreativeAnalysis, ScoreReason, Transcript, strict_json_schema


def test_transcript_fixture_schema(load_fixture) -> None:
    transcript = Transcript.model_validate(load_fixture("transcript.json"))
    assert len(transcript.segments) == 2


def test_ai_schema_rejects_out_of_range_score() -> None:
    with pytest.raises(ValidationError):
        ScoreReason(score=11, reasoning="invalid")


def test_creative_schema_requires_reasoned_scores() -> None:
    payload = {
        "content_style": "demo",
        "hook_type": "result_first",
        "hook_strength": {"score": 8, "reasoning": "Result appears immediately."},
        "storytelling": {"score": 3, "reasoning": "Minimal narrative."},
        "problem_clarity": {"score": 7, "reasoning": "Problem is stated."},
        "product_clarity": {"score": 9, "reasoning": "Product is visible."},
        "demo_strength": {"score": 8, "reasoning": "Usage is shown."},
        "proof_strength": {"score": 7, "reasoning": "Outcome is shown."},
        "before_after_strength": {"score": 5, "reasoning": "Partial comparison."},
        "ugc_authenticity": {"score": 8, "reasoning": "Casual framing."},
        "native_platform_feel": {"score": 8, "reasoning": "TikTok pacing."},
        "ad_intensity": {"score": 5, "reasoning": "Moderate selling."},
        "offer_clarity": {"score": 4, "reasoning": "No price."},
        "cta_strength": {"score": 3, "reasoning": "Weak CTA."},
        "editing_pace": "fast",
        "visual_variety": {"score": 7, "reasoning": "Several shots."}
    }
    assert CreativeAnalysis.model_validate(payload).hook_strength.score == 8


def test_codex_output_schema_forbids_extra_fields_recursively() -> None:
    schema = strict_json_schema(CodexCreatorReview)
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["CodexVideoReview"]["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["$defs"]["CodexVideoReview"]["properties"]["commercial_evidence"]["items"]["minLength"] == 1
    assert schema["$defs"]["SharedCreativePattern"]["properties"]["evidence"]["items"]["minLength"] == 1
    assert schema["$defs"]["PatternVideoEvidence"]["properties"]["evidence"]["items"]["minLength"] == 1
    from openai.lib._pydantic import to_strict_json_schema

    assert schema == to_strict_json_schema(CodexCreatorReview)
