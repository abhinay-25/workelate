"""Pydantic v2 request and response contracts aligned with BuildSprint R3 core modules."""

from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class AvailableDateSchema(BaseModel):
    day: int
    date: str


class HealthResponse(BaseModel):
    status: Literal["ok", "error"]
    db_connected: bool
    current_day: int
    ingested_days: list[int]
    total_records: int
    indexed_vectors: int
    default_date: str
    available_dates: list[AvailableDateSchema]
    version: str = "1.0.0"


class PersonSchema(BaseModel):
    person_id: str
    org_id: str
    org_name: str
    name: str
    first_name: str
    last_name: str
    role: str
    team: Optional[str] = None
    zone: str


class PeopleResponse(BaseModel):
    people: list[PersonSchema]
    orgs: list[dict[str, str]]


class ClientSchema(BaseModel):
    client_id: str
    org_id: str
    name: str


class UsageMetricsSchema(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    ms: int = 0
    mode: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None


# --- Overview ---


class OverviewMetricsSchema(BaseModel):
    active_tasks: int
    blocked_tasks: int
    pending_commitments: int
    tracked_claims: int
    meetings_on_date: int
    visible_records: int


class ActivityItemSchema(BaseModel):
    record_id: str
    type: str
    at_utc: str
    local_date: str
    author_id: str
    author_name: str
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    visibility: str
    channel: Optional[str] = None
    title_or_subject: Optional[str] = None
    preview: str
    has_injection_span: bool = False


class DeadlineItemSchema(BaseModel):
    kind: Literal["task", "promise"]
    item_id: str
    title: str
    owner_id: str
    owner_name: str
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    due_date: str
    status: str
    blocker: Optional[str] = None
    sources: list[str]


class MeetingSummarySchema(BaseModel):
    meeting_id: str
    title: str
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    start_utc: str
    attendees: list[str]
    has_cached_brief: bool = False


class OverviewResponse(BaseModel):
    person: PersonSchema
    current_day: int
    selected_date: str
    available_dates: list[AvailableDateSchema]
    metrics: OverviewMetricsSchema
    clients: list[ClientSchema]
    recent_activity: list[ActivityItemSchema]
    upcoming_deadlines: list[DeadlineItemSchema]
    meetings_on_date: list[MeetingSummarySchema]


# --- Briefs ---


class BriefRequest(BaseModel):
    person_id: str = Field(..., min_length=1)
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    client_id: Optional[str] = None
    regenerate: bool = False


class BriefChangeItem(BaseModel):
    text: str
    sources: list[str]


class BriefPromiseItem(BaseModel):
    owner: str
    owner_name: Optional[str] = None
    what: str
    due: str
    sources: list[str]


class BriefOpenRecordItem(BaseModel):
    record: str
    why: str


class BriefQuestionItem(BaseModel):
    text: str
    sources: list[str]


class EnrichedBriefItem(BaseModel):
    meeting_id: str
    meeting_title: Optional[str] = None
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    start_utc: Optional[str] = None
    attendees: list[str] = Field(default_factory=list)
    status: Literal["brief", "nothing_new"]
    changed: list[BriefChangeItem] = Field(default_factory=list)
    promises: list[BriefPromiseItem] = Field(default_factory=list)
    open_these: list[BriefOpenRecordItem] = Field(default_factory=list)
    question_to_ask: Optional[BriefQuestionItem] = None
    usage: UsageMetricsSchema


class BriefResponse(BaseModel):
    person_id: str
    date: str
    client_id: Optional[str] = None
    briefs: list[EnrichedBriefItem]
    total_usage: UsageMetricsSchema


# --- Ask Assistant ---


class AskRequest(BaseModel):
    person_id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)
    at: Optional[str] = None


class AskAnswerBlock(BaseModel):
    text: str
    sources: list[str]


class AskMatchedItem(BaseModel):
    phrase: str
    id: str
    label: Optional[str] = None
    sources: list[str] = Field(default_factory=list)


class AskClarifyBlock(BaseModel):
    question: str
    options: list[str]


class AskRefusedBlock(BaseModel):
    reason: str


class AskResponse(BaseModel):
    person_id: str
    at_utc: str
    status: Literal["answer", "clarify", "refused", "nothing"]
    answer: Optional[AskAnswerBlock] = None
    matched: list[AskMatchedItem] = Field(default_factory=list)
    clarify: Optional[AskClarifyBlock] = None
    refused: Optional[AskRefusedBlock] = None
    usage: UsageMetricsSchema


# --- Authorized Record Detail ---


class RecordDetailResponse(BaseModel):
    id: str
    day: int
    org: str
    type: str
    at_utc: str
    local_date: str
    author_id: str
    author_name: str
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    visibility: str
    channel: Optional[str] = None
    subject: Optional[str] = None
    title: Optional[str] = None
    sanitized_body: str
    has_injection_span: bool = False
    is_boilerplate: bool = False
    is_newsletter: bool = False


# --- Memory & Feedback ---


class PreferenceRowSchema(BaseModel):
    pref_id: Optional[int] = None
    day: int
    pref_key: str
    pref_val: str
    source_words: str


class MemoryClaimSchema(BaseModel):
    subject_key: str
    predicate: str
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    value: str
    old_value: Optional[str] = None
    source_kind: str
    effective_at_utc: str
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    is_expired: bool = False
    sources: list[str] = Field(default_factory=list)


class MemoryPromiseSchema(BaseModel):
    promise_id: Optional[int] = None
    owner_id: str
    owner_name: str
    what: str
    due: str
    status: str = "open"
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    sources: list[str] = Field(default_factory=list)


class MemoryTaskSchema(BaseModel):
    task_id: str
    title: str
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    owner_id: str
    owner_name: str
    due: str
    status: str
    blocked_reason: Optional[str] = None
    sources: list[str] = Field(default_factory=list)


class EntityAliasSchema(BaseModel):
    alias: str
    entity_type: str
    entity_id: str
    entity_label: str
    evidence_kind: str
    confidence: float
    source_record_id: Optional[str] = None


class PersonalNoteSchema(BaseModel):
    text: str
    visibility: str
    at_utc: str
    sources: list[str]


class ForgottenTopicSchema(BaseModel):
    forget_id: int
    day: int
    raw_words: str
    keywords: list[str]
    affected_record_ids: list[str]


class MemoryResponse(BaseModel):
    person: PersonSchema
    current_day: int
    reference_date: str
    active_preferences_summary: dict[str, Any]
    preference_history: list[PreferenceRowSchema]
    claims: list[MemoryClaimSchema]
    tasks: list[MemoryTaskSchema]
    promises: list[MemoryPromiseSchema]
    entity_aliases: list[EntityAliasSchema]
    personal_notes: list[PersonalNoteSchema]
    forgotten_topics: list[ForgottenTopicSchema]


class MemoryCorrectRequest(BaseModel):
    person_id: str = Field(..., min_length=1)
    words: str = Field(..., min_length=2)
    meeting_id: Optional[str] = None


class MemoryCorrectResponse(BaseModel):
    ok: bool
    applied: bool
    message: str
    person_id: str
    updated_claims: list[MemoryClaimSchema] = Field(default_factory=list)


class MemoryForgetRequest(BaseModel):
    person_id: str = Field(..., min_length=1)
    what: str = Field(..., min_length=2)


class MemoryForgetResponse(BaseModel):
    ok: bool
    person_id: str
    what: str
    forgotten_records: int
    keywords: list[str]


# --- Audit ---


class AuditEventSchema(BaseModel):
    event_uid: str
    day: int
    at_utc: Optional[str] = None
    category: Literal[
        "correction",
        "preference",
        "forget",
        "security_filter",
        "claim_update",
        "record",
    ]
    title: str
    summary: str
    actor_id: Optional[str] = None
    actor_name: Optional[str] = None
    visibility: str
    client_id: Optional[str] = None
    client_name: Optional[str] = None
    sources: list[str] = Field(default_factory=list)
    status: str = "verified"


class IngestedDaySummarySchema(BaseModel):
    day: int
    date: str
    visible_records: int


class AuditResponse(BaseModel):
    person: PersonSchema
    current_day: int
    ingested_days: list[IngestedDaySummarySchema]
    visibility_counts: dict[str, int]
    category_counts: dict[str, int]
    events: list[AuditEventSchema]


# --- System Usage ---


class ProviderStatusSchema(BaseModel):
    tier: str
    provider: str
    model: str
    configured: bool
    role: str


class EmbeddingStatusSchema(BaseModel):
    provider: str
    model: str
    indexed_records: int
    indexed_chunks: int
    vector_dim: int
    backend: str


class UsageTotalsSchema(BaseModel):
    total_requests: int
    deterministic_requests: int
    llm_assisted_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    avg_latency_ms: float


class OperationBreakdownSchema(BaseModel):
    operation: str
    count: int
    deterministic_count: int
    llm_count: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    avg_latency_ms: float


class UsageLogEntrySchema(BaseModel):
    id: int
    created_at_utc: str
    operation: str
    person_id: Optional[str] = None
    org_id: Optional[str] = None
    mode: str
    provider: Optional[str] = None
    model: Optional[str] = None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    status: str
    detail: Optional[str] = None


class UsageResponse(BaseModel):
    providers: list[ProviderStatusSchema]
    embedding: EmbeddingStatusSchema
    guardrails: dict[str, Any]
    totals: UsageTotalsSchema
    by_operation: list[OperationBreakdownSchema]
    recent_calls: list[UsageLogEntrySchema]


# --- Ingestion ---


class IngestRequest(BaseModel):
    day: int = Field(..., ge=1, le=30)


class IngestResponse(BaseModel):
    ok: bool
    day: int
    skipped: Optional[str] = None
    records: int = 0
    feedback: int = 0
    vectors: dict[str, Any] = Field(default_factory=dict)
