export interface AvailableDate {
  day: number;
  date: string;
}

export interface HealthResponse {
  status: 'ok' | 'error';
  db_connected: boolean;
  current_day: number;
  ingested_days: number[];
  total_records: number;
  indexed_vectors: number;
  default_date: string;
  available_dates: AvailableDate[];
  version: string;
}

export interface Person {
  person_id: string;
  org_id: string;
  org_name: string;
  name: string;
  first_name: string;
  last_name: string;
  role: string;
  team?: string | null;
  zone: string;
}

export interface OrgSummary {
  org_id: string;
  name: string;
}

export interface PeopleResponse {
  people: Person[];
  orgs: OrgSummary[];
}

export interface ClientInfo {
  client_id: string;
  org_id: string;
  name: string;
}

export interface UsageMetrics {
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  ms: number;
  mode?: string | null;
  provider?: string | null;
  model?: string | null;
}

// Overview
export interface OverviewMetrics {
  active_tasks: number;
  blocked_tasks: number;
  pending_commitments: number;
  tracked_claims: number;
  meetings_on_date: number;
  visible_records: number;
}

export interface ActivityItem {
  record_id: string;
  type: string;
  at_utc: string;
  local_date: string;
  author_id: string;
  author_name: string;
  client_id?: string | null;
  client_name?: string | null;
  visibility: string;
  channel?: string | null;
  title_or_subject?: string | null;
  preview: string;
  has_injection_span: boolean;
}

export interface DeadlineItem {
  kind: 'task' | 'promise';
  item_id: string;
  title: string;
  owner_id: string;
  owner_name: string;
  client_id?: string | null;
  client_name?: string | null;
  due_date: string;
  status: string;
  blocker?: string | null;
  sources: string[];
}

export interface MeetingSummary {
  meeting_id: string;
  title: string;
  client_id?: string | null;
  client_name?: string | null;
  start_utc: string;
  attendees: string[];
  has_cached_brief: boolean;
}

export interface OverviewResponse {
  person: Person;
  current_day: number;
  selected_date: string;
  available_dates: AvailableDate[];
  metrics: OverviewMetrics;
  clients: ClientInfo[];
  recent_activity: ActivityItem[];
  upcoming_deadlines: DeadlineItem[];
  meetings_on_date: MeetingSummary[];
}

// Briefs
export interface BriefRequest {
  person_id: string;
  date: string;
  client_id?: string | null;
  regenerate?: boolean;
}

export interface BriefChangeItem {
  text: string;
  sources: string[];
}

export interface BriefPromiseItem {
  owner: string;
  owner_name?: string | null;
  what: string;
  due: string;
  sources: string[];
}

export interface BriefOpenRecordItem {
  record: string;
  why: string;
}

export interface BriefQuestionItem {
  text: string;
  sources: string[];
}

export interface EnrichedBriefItem {
  meeting_id: string;
  meeting_title?: string | null;
  client_id?: string | null;
  client_name?: string | null;
  start_utc?: string | null;
  attendees: string[];
  status: 'brief' | 'nothing_new';
  changed: BriefChangeItem[];
  promises: BriefPromiseItem[];
  open_these: BriefOpenRecordItem[];
  question_to_ask?: BriefQuestionItem | null;
  usage: UsageMetrics;
}

export interface BriefResponse {
  person_id: string;
  date: string;
  client_id?: string | null;
  briefs: EnrichedBriefItem[];
  total_usage: UsageMetrics;
}

// Ask Assistant
export interface AskRequest {
  person_id: string;
  question: string;
  at?: string | null;
}

export interface AskMatchedItem {
  phrase: string;
  id: string;
  label?: string | null;
  sources: string[];
}

export interface AskResponse {
  person_id: string;
  at_utc: string;
  status: 'answer' | 'clarify' | 'refused' | 'nothing';
  answer?: { text: string; sources: string[] } | null;
  matched: AskMatchedItem[];
  clarify?: { question: string; options: string[] } | null;
  refused?: { reason: string } | null;
  usage: UsageMetrics;
}

// Authorized Record Detail
export interface RecordDetailResponse {
  id: string;
  day: number;
  org: string;
  type: string;
  at_utc: string;
  local_date: string;
  author_id: string;
  author_name: string;
  client_id?: string | null;
  client_name?: string | null;
  visibility: string;
  channel?: string | null;
  subject?: string | null;
  title?: string | null;
  sanitized_body: string;
  has_injection_span: boolean;
  is_boilerplate: boolean;
  is_newsletter: boolean;
}

// Memory
export interface PreferenceRow {
  pref_id?: number | null;
  day: number;
  pref_key: string;
  pref_val: string;
  source_words: string;
}

export interface MemoryClaim {
  subject_key: string;
  predicate: string;
  client_id?: string | null;
  client_name?: string | null;
  value: string;
  old_value?: string | null;
  source_kind: string;
  effective_at_utc: string;
  valid_from?: string | null;
  valid_until?: string | null;
  is_expired: boolean;
  sources: string[];
}

export interface MemoryPromise {
  promise_id?: number | null;
  owner_id: string;
  owner_name: string;
  what: string;
  due: string;
  status: string;
  client_id?: string | null;
  client_name?: string | null;
  sources: string[];
}

export interface MemoryTask {
  task_id: string;
  title: string;
  client_id?: string | null;
  client_name?: string | null;
  owner_id: string;
  owner_name: string;
  due: string;
  status: string;
  blocked_reason?: string | null;
  sources: string[];
}

export interface EntityAlias {
  alias: string;
  entity_type: string;
  entity_id: string;
  entity_label: string;
  evidence_kind: string;
  confidence: number;
  source_record_id?: string | null;
}

export interface PersonalNote {
  text: string;
  visibility: string;
  at_utc: string;
  sources: string[];
}

export interface ForgottenTopic {
  forget_id: number;
  day: number;
  raw_words: string;
  keywords: string[];
  affected_record_ids: string[];
}

export interface MemoryResponse {
  person: Person;
  current_day: number;
  reference_date: string;
  active_preferences_summary: {
    skip_newsletters?: boolean;
    max_changed?: number | null;
    most_important_first?: boolean;
    blockers_first?: boolean;
    ignore_topics?: string[];
    keep_topics?: string[];
  };
  preference_history: PreferenceRow[];
  claims: MemoryClaim[];
  tasks: MemoryTask[];
  promises: MemoryPromise[];
  entity_aliases: EntityAlias[];
  personal_notes: PersonalNote[];
  forgotten_topics: ForgottenTopic[];
}

export interface MemoryCorrectResponse {
  ok: boolean;
  applied: boolean;
  message: string;
  person_id: string;
  updated_claims: MemoryClaim[];
}

export interface MemoryForgetResponse {
  ok: boolean;
  person_id: string;
  what: string;
  forgotten_records: number;
  keywords: string[];
}

// Audit
export type AuditCategory =
  | 'correction'
  | 'preference'
  | 'forget'
  | 'security_filter'
  | 'claim_update'
  | 'record';

export interface AuditEvent {
  event_uid: string;
  day: number;
  at_utc?: string | null;
  category: AuditCategory;
  title: string;
  summary: string;
  actor_id?: string | null;
  actor_name?: string | null;
  visibility: string;
  client_id?: string | null;
  client_name?: string | null;
  sources: string[];
  status: string;
}

export interface IngestedDaySummary {
  day: number;
  date: string;
  visible_records: number;
}

export interface AuditResponse {
  person: Person;
  current_day: number;
  ingested_days: IngestedDaySummary[];
  visibility_counts: Record<string, number>;
  category_counts: Record<string, number>;
  events: AuditEvent[];
}

// System Usage
export interface ProviderStatus {
  tier: string;
  provider: string;
  model: string;
  configured: boolean;
  role: string;
}

export interface EmbeddingStatus {
  provider: string;
  model: string;
  indexed_records: number;
  indexed_chunks: number;
  vector_dim: number;
  backend: string;
}

export interface UsageTotals {
  total_requests: number;
  deterministic_requests: number;
  llm_assisted_requests: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  avg_latency_ms: number;
}

export interface OperationBreakdown {
  operation: string;
  count: number;
  deterministic_count: number;
  llm_count: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  avg_latency_ms: number;
}

export interface UsageLogEntry {
  id: number;
  created_at_utc: string;
  operation: string;
  person_id?: string | null;
  org_id?: string | null;
  mode: string;
  provider?: string | null;
  model?: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_ms: number;
  status: string;
  detail?: string | null;
}

export interface UsageResponse {
  providers: ProviderStatus[];
  embedding: EmbeddingStatus;
  guardrails: {
    brief_token_limit: number;
    brief_safe_budget: number;
    ask_token_limit: number;
    ask_safe_budget: number;
    temperature: number;
    xml_prompt_sandboxing: boolean;
  };
  totals: UsageTotals;
  by_operation: OperationBreakdown[];
  recent_calls: UsageLogEntry[];
}
