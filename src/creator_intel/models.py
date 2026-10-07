"""Validated domain schemas independent of provider response formats."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


EvidenceText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommercialStatus(StrEnum):
    CONFIRMED = "confirmed"
    LIKELY = "likely"
    POSSIBLE = "possible"
    ORGANIC = "organic"
    UNKNOWN = "unknown"


class EvidenceType(StrEnum):
    PLATFORM_PAID_PARTNERSHIP_LABEL = "platform_paid_partnership_label"
    CREATOR_EARNS_COMMISSION = "creator_earns_commission"
    TIKTOK_SHOP_PRODUCT = "tiktok_shop_product"
    DISCOUNT_CODE = "discount_code"
    AFFILIATE_LINK_LANGUAGE = "affiliate_link_language"
    SPONSORED_HASHTAG = "sponsored_hashtag"
    BRAND_MENTION = "brand_mention"
    EXPLICIT_VERBAL_DISCLOSURE = "explicit_verbal_disclosure"
    EXPLICIT_VISUAL_DISCLOSURE = "explicit_visual_disclosure"
    PRODUCT_DEMO = "product_demo"
    PURCHASE_CTA = "purchase_cta"
    BRAND_TAG = "brand_tag"
    GIFTED_LANGUAGE = "gifted_language"
    OTHER = "other"


class PipelineStage(StrEnum):
    FETCHED = "FETCHED"
    CLASSIFIED = "CLASSIFIED"
    DOWNLOADED = "DOWNLOADED"
    TRANSCRIBED = "TRANSCRIBED"
    SCENE_DETECTED = "SCENE_DETECTED"
    VISION_ANALYZED = "VISION_ANALYZED"
    SYNTHESIZED = "SYNTHESIZED"
    CODEX_PREPARED = "CODEX_PREPARED"
    CODEX_REVIEWED = "CODEX_REVIEWED"
    FAILED = "FAILED"


class CreatorProfile(StrictModel):
    username: str
    profile_url: str
    creator_id: str | None = None
    display_name: str | None = None
    bio: str | None = None
    follower_count: int | None = None
    following_count: int | None = None
    video_count: int | None = None
    avatar_url: str | None = None
    provider: str
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CreatorVideo(StrictModel):
    video_id: str
    video_url: str
    creator_username: str
    creator_id: str | None = None
    description: str | None = None
    caption: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    mentions: list[str] = Field(default_factory=list)
    timestamp: int | None = None
    published_at: datetime | None = None
    duration: float | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    share_count: int | None = None
    save_count: int | None = None
    thumbnail: str | None = None
    music: str | None = None
    channel_metadata: dict[str, Any] = Field(default_factory=dict)
    platform_labels: list[str] = Field(default_factory=list)
    product_data: list[dict[str, Any]] = Field(default_factory=list)
    platform_is_ad: bool | None = None
    has_product_anchor: bool | None = None
    product_ids: list[str] = Field(default_factory=list)
    subtitle_tracks: list[dict[str, Any]] = Field(default_factory=list)
    is_pinned: bool | None = None
    provider: str = "yt-dlp"
    raw_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("view_count", "like_count", "comment_count", "share_count", "save_count")
    @classmethod
    def non_negative_counts(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            return None
        return value


class CommercialEvidence(StrictModel):
    type: EvidenceType
    value: str
    weight: float
    source: str = "metadata"


class CommercialClassification(StrictModel):
    video_id: str
    status: CommercialStatus
    probability: float = Field(ge=0.0, le=1.0)
    score: float = Field(ge=0.0)
    evidence: list[CommercialEvidence] = Field(default_factory=list)
    reasoning: str
    classifier_version: str = "rules-v1"
    explicit_ad: bool | None = None
    commercial_kinds: list[str] = Field(default_factory=list)

    @property
    def confidence(self) -> float:
        return self.probability if self.status != CommercialStatus.ORGANIC else 1 - self.probability


class PerformanceMetrics(StrictModel):
    video_id: str
    raw_views: int | None = None
    global_median: float | None = None
    global_outperformance: float | None = None
    local_median: float | None = None
    local_outperformance: float | None = None
    local_sample_size: int = 0
    p25: float | None = None
    p50: float | None = None
    p75: float | None = None
    p90: float | None = None


class MediaInfo(StrictModel):
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    has_audio: bool | None = None
    probe: str


class SceneAnalysis(StrictModel):
    scene_id: int
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    duration: float = Field(ge=0)
    keyframes: list[str] = Field(default_factory=list)
    scene_role: str | None = None
    people_present: bool | None = None
    creator_face_visible: bool | None = None
    product_visible: bool | None = None
    product_category: str | None = None
    product_usage: str | None = None
    product_demo: bool | None = None
    before_after: bool | None = None
    visual_proof: bool | None = None
    text_overlay: str | None = None
    price_visible: bool | None = None
    discount_visible: bool | None = None
    brand_visible: str | None = None
    cta_visible: bool | None = None
    environment: str | None = None
    camera_style: str | None = None
    transition_type: str | None = None
    ugc_feel: float | None = Field(default=None, ge=0, le=10)


class SceneVisualFinding(StrictModel):
    scene_id: int
    scene_role: str | None = None
    people_present: bool | None = None
    creator_face_visible: bool | None = None
    product_visible: bool | None = None
    product_category: str | None = None
    product_usage: str | None = None
    product_demo: bool | None = None
    before_after: bool | None = None
    visual_proof: bool | None = None
    text_overlay: str | None = None
    price_visible: bool | None = None
    discount_visible: bool | None = None
    brand_visible: str | None = None
    cta_visible: bool | None = None
    environment: str | None = None
    camera_style: str | None = None
    transition_type: str | None = None
    ugc_feel: float | None = Field(default=None, ge=0, le=10)


class TranscriptSegment(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str


class Transcript(StrictModel):
    video_id: str
    text: str
    segments: list[TranscriptSegment] = Field(default_factory=list)
    audio_sha256: str
    model: str


class TimelineSegment(StrictModel):
    start: float
    end: float
    visual: str | None = None
    speech: str = ""
    roles: list[str] = Field(default_factory=list)


class ScoreReason(StrictModel):
    score: float = Field(ge=0, le=10)
    reasoning: str


class CreativeAnalysis(StrictModel):
    content_style: str
    hook_type: str
    hook_text: str | None = None
    hook_visual: str | None = None
    hook_duration: float | None = None
    hook_strength: ScoreReason
    storytelling: ScoreReason
    problem_clarity: ScoreReason
    product_clarity: ScoreReason
    demo_strength: ScoreReason
    proof_strength: ScoreReason
    before_after_strength: ScoreReason
    ugc_authenticity: ScoreReason
    native_platform_feel: ScoreReason
    ad_intensity: ScoreReason
    offer_clarity: ScoreReason
    cta_strength: ScoreReason
    editing_pace: str
    visual_variety: ScoreReason
    product_first_seen_sec: float | None = None
    product_total_screen_time_estimate: float | None = None
    cta_start_sec: float | None = None
    commercial_type: str | None = None
    brand: str | None = None
    product_category: str | None = None
    structure: list[TimelineSegment] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class MultimodalAnalysis(StrictModel):
    scenes: list[SceneVisualFinding] = Field(default_factory=list)
    creative: CreativeAnalysis


class VideoAnalysis(StrictModel):
    video_id: str
    video_url: str
    media_info: MediaInfo | None = None
    scenes: list[SceneAnalysis] = Field(default_factory=list)
    transcript: Transcript | None = None
    timeline: list[TimelineSegment] = Field(default_factory=list)
    creative: CreativeAnalysis | None = None
    analysis_version: str = "v1"
    prompt_version: str = "v1"
    model: str | None = None
    stage: PipelineStage
    error: str | None = None


class PatternPerformance(StrictModel):
    pattern: str
    video_count: int
    median_local_outperformance: float | None = None


class CreatorCommercialProfile(StrictModel):
    creator: str
    commercial_video_count: int
    confirmed_commercial: int
    likely_commercial: int
    possible_commercial: int
    creator_median_views: float | None = None
    top_commercial_styles: list[str] = Field(default_factory=list)
    best_hook_types: list[str] = Field(default_factory=list)
    average_product_first_seen_sec: float | None = None
    best_patterns: list[PatternPerformance] = Field(default_factory=list)
    weak_patterns: list[PatternPerformance] = Field(default_factory=list)
    commercial_strengths: list[str] = Field(default_factory=list)
    commercial_weaknesses: list[str] = Field(default_factory=list)
    best_suited_archetypes: list[str] = Field(default_factory=list)
    synthesis: str | None = None
    limitations: list[str] = Field(default_factory=list)


class ProductProfile(StrictModel):
    name: str
    description: str | None = None
    url: str | None = None
    price: float | None = None
    category: str | None = None
    selling_points: list[str] = Field(default_factory=list)
    target_audience: list[str] = Field(default_factory=list)


class CreatorProductFit(StrictModel):
    creator: str
    product: ProductProfile
    fit_score: float = Field(ge=0, le=10)
    reasoning: str
    evidence: list[str] = Field(default_factory=list)


class ProductBrief(StrictModel):
    """User-provided product material copied into a Codex-native analysis job."""

    name: str | None = None
    description: str | None = None
    category: str | None = None
    price: float | None = None
    selling_points: list[str] = Field(default_factory=list)
    target_audience: list[str] = Field(default_factory=list)
    image_paths: list[str] = Field(default_factory=list)
    image_sha256: dict[str, str] = Field(default_factory=dict)


class CodexVideoAsset(StrictModel):
    """Deterministically prepared evidence for one current-Codex review."""

    video: CreatorVideo
    classification: CommercialClassification
    performance: PerformanceMetrics
    status: Literal["ready", "failed"]
    selection_reason: Literal["commercial_candidate", "creator_context"]
    selection_basis: list[
        Literal[
            "platform_explicit_ad_raw_views",
            "commercial_raw_views",
            "commercial_local_relative",
            "product_anchor_local_relative",
            "composite_commercial_score",
            "creator_context_relative",
            "creator_context_raw_views",
        ]
    ] = Field(default_factory=list)
    media_path: str | None = None
    audio_path: str | None = None
    media_info: MediaInfo | None = None
    scenes: list[SceneAnalysis] = Field(default_factory=list)
    contact_sheets: list[str] = Field(default_factory=list)
    transcript_path: str | None = None
    transcript_source: str | None = None
    file_sha256: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


class CreatorMetadataSummary(StrictModel):
    videos_discovered: int = Field(ge=0)
    videos_classified: int = Field(ge=0)
    platform_ad_true: int = Field(ge=0)
    platform_ad_false: int = Field(ge=0)
    platform_ad_unknown: int = Field(ge=0)
    product_anchor_true: int = Field(ge=0)
    confirmed_commercial: int = Field(ge=0)
    likely_commercial: int = Field(ge=0)
    possible_commercial: int = Field(ge=0)
    organic: int = Field(ge=0)
    unknown: int = Field(ge=0)
    direct_hydration_attempted: int = Field(ge=0)
    direct_hydration_succeeded: int = Field(ge=0)
    public_caption_tracks_discovered: int = Field(ge=0)


class VideoRankingEntry(StrictModel):
    video_id: str
    video_url: str
    views: int | None
    local_outperformance: float | None
    system_status: CommercialStatus
    system_probability: float = Field(ge=0, le=1)
    platform_is_ad: bool | None
    has_product_anchor: bool | None


class CodexPreparationJob(StrictModel):
    schema_version: Literal["codex-native-v2"]
    job_id: str
    job_digest: str
    created_at: datetime
    creator: CreatorProfile
    product: ProductBrief | None
    assets: list[CodexVideoAsset]
    metadata_summary: CreatorMetadataSummary
    top_commercial_by_views: list[VideoRankingEntry]
    top_commercial_by_relative: list[VideoRankingEntry]
    source_report_dir: str
    review_output_path: str
    review_schema_path: str
    instructions_path: str
    limitations: list[str]


class ReviewTechnique(StrictModel):
    name: str
    description: str
    evidence: list[EvidenceText]
    confidence: Literal["high", "medium", "low"]


class ReviewTimelineBeat(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    visual: str
    speech: str
    roles: list[str]


class CodexVideoReview(StrictModel):
    video_id: str
    evidence_basis: list[EvidenceText]
    commercial_read: str
    observed_commercial_status: CommercialStatus
    commercial_confidence: float = Field(ge=0, le=1)
    commercial_type: str
    commercial_evidence: list[EvidenceText]
    content_style: str
    hook_type: str
    opening_hook: str
    hook_duration_seconds: float | None = Field(ge=0)
    product_first_seen_seconds: float | None = Field(ge=0)
    structure: list[ReviewTimelineBeat]
    shot_sequence: list[str]
    filming_techniques: list[ReviewTechnique]
    product_presentation: str
    proof_method: str
    camera_and_editing: str
    creator_delivery: str
    on_screen_text: str
    call_to_action: str
    reusable_script_beats: list[str]
    strengths: list[str]
    weaknesses: list[str]
    limitations: list[str]


class PatternVideoEvidence(StrictModel):
    video_id: str
    evidence: list[EvidenceText]


class SharedCreativePattern(StrictModel):
    name: str
    description: str
    video_ids: list[str]
    evidence: list[EvidenceText]
    evidence_by_video: list[PatternVideoEvidence]
    confidence: Literal["high", "medium", "low"]
    performance_association: str
    negotiation_use: str


class ProductUnderstanding(StrictModel):
    summary: str
    category: str | None
    apparent_selling_points: list[str]
    likely_target_customers: list[str]
    image_observations: list[str]
    unknowns: list[str]


class CreatorFitReview(StrictModel):
    verdict: Literal["strong_fit", "test_fit", "weak_fit", "not_fit", "not_assessed"]
    fit_score: float | None = Field(ge=0, le=100)
    confidence: Literal["high", "medium", "low", "not_assessed"]
    rationale: str
    matches: list[str]
    mismatches: list[str]
    supporting_video_ids: list[str]
    risks: list[str]
    recommended_collaboration_format: str
    validation_test: str
    unknowns: list[str]


class ScriptNegotiationBrief(StrictModel):
    objective: str
    recommended_concept: str
    must_keep: list[str]
    suggested_sequence: list[str]
    creator_freedom: list[str]
    avoid: list[str]
    deliverables: list[str]
    questions_for_creator: list[str]


class CodexCreatorReview(StrictModel):
    schema_version: Literal["codex-native-v2"]
    job_id: str
    job_digest: str
    creator: str
    analysis_mode: Literal["current-codex-native", "codex-cli-headless"]
    evidence_scope: str
    product_understanding: ProductUnderstanding | None
    video_reviews: list[CodexVideoReview]
    shared_patterns: list[SharedCreativePattern]
    product_fit: CreatorFitReview
    script_brief: ScriptNegotiationBrief
    limitations: list[str]


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return an OpenAI/Codex structured-output-compatible strict JSON schema."""

    schema = model.model_json_schema()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    return schema


class DataCoverage(StrictModel):
    videos_discovered: int = 0
    videos_classified: int = 0
    videos_deep_analyzed: int = 0
    videos_failed: int = 0
    commercial_classification_confidence: float | None = None
    performance_data_availability: float = 0.0
    direct_hydration_attempted: int = 0
    direct_hydration_succeeded: int = 0
    public_caption_tracks_discovered: int = 0
    known_limitations: list[str] = Field(default_factory=list)


class RunManifest(StrictModel):
    run_id: str
    creator: str
    started_at: datetime
    finished_at: datetime | None = None
    profile_provider: str
    videos_discovered: int = 0
    commercial_candidates: int = 0
    videos_deep_analyzed: int = 0
    failures: int = 0
    model: str | None = None
    prompt_version: str = "v1"
    analysis_version: str = "v1"
    status: str = "running"
    errors: list[str] = Field(default_factory=list)
