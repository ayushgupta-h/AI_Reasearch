# Research AI Quest — TypeScript API Types

TypeScript interfaces mirroring the backend Pydantic schemas. These types are auto-derivable from the backend `openapi.json`; this file is a hand-maintained snapshot. Use them to type API responses.

> Status legend per type: ✅ implemented · ⚠️ requires PostgreSQL · 🚧 not mounted · ⏳ planned.

---

## 0. Primitives & shared

```ts
export type UtcDatetime = string;               // ISO-8601 UTC, ends with "Z"
export type ResearchId = string;                // 32-char lowercase hex
export type ExecutionId = string;               // 32-char lowercase hex
export type DocumentId = string;                // 32-char lowercase hex
export type ReportId = string;                  // 32-char lowercase hex
export type VersionId = string;                 // 32-char lowercase hex
export type UUID = string;                      // canonical UUID with dashes

export interface PaginationMeta {
  limit: number;
  offset: number;
  total: number;
  returned: number;
  has_more: boolean;
}

export interface ApiError {
  detail: string;
  error_code: ErrorCode;
  request_id: string;
  details?: Record<string, unknown> | null;
  timestamp?: UtcDatetime | null;
}

export interface ApiResponse<T> {
  success: true;
  data: T;
  message: string;
  request_id?: string | null;
}
```

---

## 1. Enums

```ts
export type LLMProvider = 'groq' | 'gemini' | 'mistral' | 'huggingface';
export type ResearchPriority = 'low' | 'normal' | 'high' | 'critical';
export type SessionStatus = 'pending' | 'running' | 'completed' | 'cancelled' | 'failed';
export type ProjectStatus = 'active' | 'paused' | 'completed' | 'archived';
export type AgentName =
  | 'strategist' | 'search' | 'reader' | 'rag' | 'analyst'
  | 'writer' | 'critic' | 'refiner' | 'exporter' | 'citation_validator';
export type AgentExecutionStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
export type TimelineEventType =
  | 'research_created' | 'research_started' | 'agent_started' | 'agent_completed'
  | 'agent_failed' | 'research_completed' | 'research_failed' | 'research_cancelled';
export type SourceType = 'academic' | 'web' | 'news' | 'encyclopedia' | 'technical' | 'other';
export type CitationStatus = 'unverified' | 'verified' | 'broken' | 'timeout' | 'error';
export type ClaimType =
  | 'factual' | 'statistical' | 'causal' | 'comparative'
  | 'predictive' | 'definitional';
export type ClaimStatus =
  | 'unverified' | 'supported' | 'partially_supported' | 'contradicted'
  | 'insufficient_evidence' | 'insufficient_context';
export type EvidenceStance = 'supports' | 'contradicts' | 'neutral' | 'contextual';
export type ContradictionSeverity = 'low' | 'medium' | 'high';
export type ContradictionStatus = 'unresolved' | 'resolved' | 'accepted_ambiguity';
export type ContradictionCategory =
  | 'direct_contradiction' | 'partial_contradiction' | 'contextual_difference'
  | 'methodological_difference' | 'temporal_difference' | 'no_contradiction'
  | 'insufficient_information';
export type ReportStatus = 'draft' | 'under_review' | 'approved' | 'revised' | 'published' | 'archived';
export type ApprovalDecision = 'approved' | 'changes_requested' | 'rejected';
export type ReviewStatus =
  | 'draft' | 'pending' | 'pending_review' | 'reviewing' | 'approved'
  | 'changes_requested' | 'revision_requested' | 'rejected' | 'revised' | 'published';
export type ReviewCommentKind = 'general' | 'correction' | 'unsupported_claim' | 'additional_research';
export type DocumentProcessingStatus =
  | 'uploaded' | 'pending' | 'extracting' | 'chunking' | 'embedding' | 'indexed' | 'failed';
export type ReportFormat = 'md' | 'docx' | 'pdf';
export type ReportVersionStatus = 'draft' | 'reviewing' | 'revision_requested' | 'approved' | 'published';
export type ReportVerificationStatus = 'unverified' | 'partially_verified' | 'verified' | 'failed';
export type ProviderState = 'ready' | 'missing_credentials' | 'degraded' | 'unavailable';
export type ProviderCapability = 'chat' | 'research' | 'tool_calling';
export type HealthState = 'ok' | 'degraded' | 'error';

export type ErrorCode =
  | 'bad_request' | 'validation_error' | 'authentication_required' | 'forbidden'
  | 'not_found' | 'conflict' | 'method_not_allowed' | 'rate_limited'
  | 'database_unavailable' | 'llm_provider_unavailable' | 'orchestration_failed'
  | 'research_execution_failed' | 'agent_execution_failed' | 'search_provider_unavailable'
  | 'vector_store_failed' | 'citation_validation_failed' | 'report_generation_failed'
  | 'rag_failed' | 'file_processing_failed' | 'stream_error' | 'configuration_error'
  | 'service_unavailable' | 'http_error' | 'internal_error';
```

---

## 2. Auth & System

```ts
export interface AuthConfigResponse {
  supabaseUrl: string;
  supabaseAnonKey: string;
}

export interface AuthMeResponse {
  status: string;                       // "authenticated"
  user: Record<string, unknown>;        // verified JWT claims incl. sub/email/role/user_metadata
}

export interface ComponentCheck {
  name: string;
  state: HealthState;
  detail?: string | null;
  duration_ms?: number | null;
}

export interface HealthStatus {
  status: HealthState;
  version: string;
  environment?: string | null;
  uptime_seconds?: number | null;
  timestamp: UtcDatetime;
  checks: ComponentCheck[];
}

export interface ProviderStatus {
  provider: LLMProvider;
  model: string;
  state: ProviderState;
  capabilities: ProviderCapability[];
  fallback_rank: number;
}

export interface ProvidersResponse {
  providers: string[];
  default: string;
  statuses?: ProviderStatus[] | null;
}

export interface SessionRecord {
  session_id: string;
  topic: string;
  timestamp: string;
  provider?: string | null;
  critic_score?: number | null;
  revision_count?: number | null;
  status?: string | null;
  saved_report_path?: string | null;
  citations_verified?: number | null;
  citations_total?: number | null;
  duration_seconds?: number | null;
  user_id?: string | null;
}

export interface SessionsResponse {
  sessions: SessionRecord[];
  count: number;
  pagination?: PaginationMeta | null;
}

export interface DeletedSessionResponse {
  status: string;                        // "deleted"
  session_id: string;
}
```

---

## 3. Projects

```ts
export interface ProjectRecord {
  project_id: string;
  owner_id: string;
  name: string;
  description?: string | null;
  research_domain?: string | null;
  status: ProjectStatus;
  created_at: UtcDatetime;
  updated_at: UtcDatetime;
}

export interface ProjectsResponse {
  projects: ProjectRecord[];
  count: number;
  pagination: PaginationMeta;
}

export interface ProjectCreateRequest {
  name: string;                          // 1–120 chars
  description?: string | null;
  research_domain?: string | null;
  status?: ProjectStatus;                // default "active"
}

export interface ProjectUpdateRequest {
  name?: string | null;
  description?: string | null;
  research_domain?: string | null;
  status?: ProjectStatus | null;
}

export interface DeletedProjectResponse {
  status: string;
  project_id: string;
}
```

---

## 4. Research

```ts
export interface ResearchRecord {
  research_id: string;
  project_id: string;
  owner_id: string;
  topic: string;
  provider?: LLMProvider | null;
  status: SessionStatus;
  priority: ResearchPriority;
  notes?: string | null;
  created_at: UtcDatetime;
  updated_at: UtcDatetime;
}

export interface ResearchListResponse {
  research_sessions: ResearchRecord[];
  count: number;
  pagination: PaginationMeta;
}

export interface ResearchCreateRequest {
  topic: string;                          // 3–500 chars
  provider?: LLMProvider;
  priority?: ResearchPriority;            // default "normal"
  notes?: string | null;
}

export interface ResearchUpdateRequest {
  topic?: string | null;
  provider?: LLMProvider | null;
  status?: SessionStatus | null;
  priority?: ResearchPriority | null;
  notes?: string | null;
}

export interface ResearchRunRequest {
  topic: string;
  provider?: LLMProvider;
  max_revisions?: number;                 // 0–5
  approval_threshold?: number;            // 1–10
  output_dir?: string;
  require_plan_approval?: boolean;
}

export interface DeletedResearchResponse {
  status: string;
  research_id: string;
}

export interface ChatRequest {
  message: string;                        // 1–2000 chars
}

export interface ChatResponse {
  reply: string;
  provider: string;
}
```

---

## 5. Agent Execution History

```ts
export interface AgentExecutionRecord {
  execution_id: ExecutionId;
  research_id: ResearchId;
  agent_name: string;                     // pattern [a-z][a-z0-9_]{0,63}
  status: AgentExecutionStatus;
  started_at: UtcDatetime;
  finished_at?: UtcDatetime | null;
  duration_ms?: number | null;            // >0, ≤86,400,000
  retry_count: number;
  error_summary?: string | null;          // ≤300 chars, redacted
  output_reference?: string | null;       // ≤500 chars
}

export interface AgentExecutionsResponse {
  research_id: ResearchId;
  executions: AgentExecutionRecord[];
  count: number;
  pagination: PaginationMeta;
}

export interface TimelineEventRecord {
  event_id: ExecutionId;
  research_id: ResearchId;
  event_type: TimelineEventType;
  agent_name?: string | null;
  message: string;                        // 1–600 chars
  execution_id?: ExecutionId | null;
  occurred_at: UtcDatetime;
}

export interface ResearchTimelineResponse {
  research_id: ResearchId;
  events: TimelineEventRecord[];
  count: number;
  pagination: PaginationMeta;
}

export interface ResearchProgressResponse {
  research_id: ResearchId;
  status: SessionStatus;
  total_executions: number;
  completed_executions: number;
  failed_executions: number;
  running_executions: number;
  pending_executions: number;
  percent_complete: number;               // 0–100
  current_agent?: string | null;
  started_at?: UtcDatetime | null;
  updated_at?: UtcDatetime | null;
  latest_event?: string | null;           // ≤600 chars
}
```

---

## 6. Evidence (Claims / Sources)

```ts
export interface Evidence {
  evidence_id: UUID;
  claim_id?: UUID | null;
  source_id?: UUID | null;
  quote: string;
  url?: string | null;                    // AnyHttpUrl
  stance: EvidenceStance;
  strength: number;                       // 0–1
  retrieved_at: UtcDatetime;
}

export interface Claim {
  claim_id: UUID;
  session_id?: UUID | null;
  text: string;
  subject?: string | null;
  research_question?: string | null;
  conclusion?: string | null;
  methodology?: string | null;
  population?: string | null;
  time_period?: string | null;
  evidence_ids: UUID[];
  claim_type: ClaimType;
  status: ClaimStatus;
  confidence: number;                     // 0–1
  supporting_evidence: Evidence[];
  contradicting_evidence: Evidence[];
  citation_ids: UUID[];
  created_at: UtcDatetime;
}

export interface ClaimsResponse {          // backend: GET /research/{id}/claims
  research_id: ResearchId;
  claims: Claim[];
  total: number;
  message?: string;                       // present when PostgreSQL unavailable
}

export interface ResearchSource {
  source_id: UUID;
  session_id?: UUID | null;
  title: string;
  url?: string | null;
  doi?: string | null;
  source_name?: string | null;
  source_type: SourceType;
  snippet?: string | null;
  publisher?: string | null;
  author?: string | null;
  venue?: string | null;
  methodology?: string | null;
  peer_reviewed?: boolean | null;
  references_available?: boolean | null;
  source_nature?: string | null;
  published_at?: UtcDatetime | null;
  retrieved_at: UtcDatetime;
  credibility_score?: number | null;      // 0–1
  credibility_confidence?: number | null; // 0–1
  credibility_label?: string | null;
  language: string;                       // BCP-47, default "en"
  metadata: Record<string, string>;
}

export interface SourcesResponse {         // backend: GET /research/{id}/sources
  research_id: ResearchId;
  sources: ResearchSource[];
  total: number;
  limit: number;
  offset: number;
}

export interface SourceStatisticsResponse { // backend: GET /research/{id}/sources/statistics
  [key: string]: unknown;                 // aggregated counts by type/publisher/citation status
}
```

---

## 7. Human Review

```ts
export interface ResearchPlanReviewRecord {
  plan_id: UUID;
  session_id: UUID;
  title: string;
  objective?: string | null;
  query: string;
  token_budget: number;
  agent_count: number;
  status: ReviewStatus;
  review_note?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: UtcDatetime | null;
  details: Record<string, unknown>;
  created_at: UtcDatetime;
  updated_at: UtcDatetime;
}

export interface PlanReviewDecisionRequest {
  decision: ApprovalDecision;
  comment?: string | null;                 // required for changes_requested/rejected
}

export interface PlanModificationRequest {
  comment?: string | null;
  title?: string | null;
  objective?: string | null;
  query?: string | null;
}

export interface ReportReviewRecord {
  report_id: UUID;
  session_id: UUID;
  title: string;
  status: ReviewStatus;
  critic_score?: number | null;
  latest_decision?: ApprovalDecision | null;
  latest_comment?: string | null;
  reviewer_id?: string | null;
  decided_at?: UtcDatetime | null;
  created_at: UtcDatetime;
  updated_at: UtcDatetime;
}

export interface ReportReviewDecisionRequest {
  decision: ApprovalDecision;
  comment?: string | null;
}

export interface ReportRevisionRequest {
  comment?: string | null;
}

export interface ReviewCommentRequest {
  comment: string;
  kind: ReviewCommentKind;
  claim_ids: UUID[];
}

export interface ReviewCommentRecord {
  comment_id: UUID;
  review_request_id: UUID;
  author_id?: string | null;
  body: string;
  kind: ReviewCommentKind;
  claim_ids: UUID[];
  is_resolved: boolean;
  created_at: UtcDatetime;
}

export interface ReviewAuditRecord {
  audit_id: UUID;
  session_id: UUID;
  plan_id?: UUID | null;
  report_id?: UUID | null;
  reviewer_id: string;
  action: string;                          // ReviewAction value
  previous_status?: string | null;
  new_status: string;
  comment?: string | null;
  created_at: UtcDatetime;
}

export interface ReviewHistoryResponse {
  research_id: UUID;
  entries: ReviewAuditRecord[];
  count: number;
}

export interface PlanReviewResponse {
  plan: ResearchPlanReviewRecord;
  audit?: ReviewAuditRecord | null;
  workflow_resumed: boolean;
}

export interface ReportReviewResponse {
  report: ReportReviewRecord;
  audit?: ReviewAuditRecord | null;
  workflow_resumed: boolean;
}
```

---

## 8. Reports (management)

```ts
export interface ReportRecord {
  report_id: ReportId;
  research_id: ReportId;
  owner_id: string;
  title: string;                          // 3–200 chars
  format: ReportFormat;
  version: number;                        // ≥1
  status: ReportVersionStatus;
  critic_score?: number | null;           // 0–10
  verification_status: ReportVerificationStatus;
  storage_reference?: string | null;
  created_at: UtcDatetime;
  updated_at: UtcDatetime;
}

export interface ResearchReportsResponse {
  research_id: ResearchId;
  reports: ReportRecord[];
  count: number;
  pagination: PaginationMeta;
}

export interface ReportVersionSummary {
  version_id: VersionId;
  report_id: ReportId;
  version: number;
  status: ReportVersionStatus;
  critic_score?: number | null;
  verification_status: ReportVerificationStatus;
  storage_reference?: string | null;
  change_summary?: string | null;
  created_at: UtcDatetime;
}

export interface ReportVersionDetail extends ReportVersionSummary {
  content_markdown: string;
}

export interface ReportVersionsResponse {
  report_id: ReportId;
  versions: ReportVersionSummary[];
  count: number;
}

export interface ReportVersionCreateRequest {
  content_markdown: string;
  status?: ReportVersionStatus;            // default "draft"
  critic_score?: number | null;
  verification_status?: ReportVerificationStatus; // default "unverified"
  change_summary?: string | null;
}

export interface ReportExportRequest {
  format?: ReportFormat;                   // default "pdf"
  version_id?: VersionId | null;
  citation_style?: 'apa' | 'mla' | 'harvard';  // default "apa"
}

export interface DeletedReportResponse {
  status: string;
  report_id: ReportId;
}

export interface ShareTokenResponse {
  filename: string;
  share_token: string;
  share_url: string;
}

export interface ExportRequest {
  content: string;                         // ≤200,000
  topic?: string;
  format?: 'pdf' | 'docx' | 'md' | 'txt';  // default "pdf"
  citation_style?: 'apa' | 'mla' | 'harvard'; // default "apa"
}
```

---

## 9. Documents

```ts
export interface DocumentRecord {
  document_id: DocumentId;
  owner_id: string;
  project_id?: string | null;
  research_id?: string | null;
  original_filename: string;
  mime_type: string;
  size_bytes: number;                      // ≤104,857,600
  sha256: string;                          // 64 hex chars
  storage_key: string;
  processing_status: DocumentProcessingStatus;
  error_code?: string | null;
  error_message?: string | null;
  created_at: UtcDatetime;
  updated_at: UtcDatetime;
}

export interface DocumentListResponse {
  documents: DocumentRecord[];
  count: number;
  pagination: PaginationMeta;
}

export interface DocumentStatusResponse {
  document_id: DocumentId;
  processing_status: DocumentProcessingStatus;
  error_code?: string | null;
  error_message?: string | null;
  updated_at: UtcDatetime;
}

export interface DeletedDocumentResponse {
  status: string;
  document_id: string;
}
```

---

## 10. Usage & Cost (🚧 not mounted)

```ts
export interface UsageRecord {
  usage_id: string;                        // 32-char hex
  research_id: ResearchId;
  user_id: string;
  agent_name?: string | null;
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  duration_ms?: number | null;
  estimated_cost_usd: number;
  request_status: string;                  // e.g. "success" | "failure"
  occurred_at: UtcDatetime;
}

export interface ResearchCostBreakdown {
  research_id: ResearchId;
  requests: number;
  total_tokens: number;
  estimated_cost_usd: number;
}

export interface AgentCostBreakdown {
  agent_name: string;
  requests: number;
  total_tokens: number;
  estimated_cost_usd: number;
}

export interface ResearchProviderBreakdown {
  provider: string;
  requests: number;
  total_tokens: number;
  estimated_cost_usd: number;
  failures: number;
}

export interface UsageSummaryResponse {
  user_id: string;
  total_requests: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  total_estimated_cost_usd: number;
  provider_failure_count: number;
  fallback_count: number;
  per_research: ResearchCostBreakdown[];
  per_provider: ResearchProviderBreakdown[];
  per_agent: AgentCostBreakdown[];
  estimated: true;                          // all cost fields are estimates
}

export interface ResearchUsageResponse {
  research_id: ResearchId;
  user_id: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  failures: number;
  fallbacks: number;
  usage: UsageRecord[];
  per_provider: ResearchProviderBreakdown[];
  per_agent: AgentCostBreakdown[];
  estimated: true;
}

export interface ProviderAggregate {
  provider: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  failures: number;
}

export interface ModelAggregate extends ProviderAggregate {
  model: string;
}

export interface ProvidersUsageResponse {
  user_id: string;
  providers: ProviderAggregate[];
  estimated: true;
}

export interface ModelsUsageResponse {
  user_id: string;
  models: ModelAggregate[];
  estimated: true;
}
```

---

## 11. Memory (⚠️ requires PostgreSQL)

```ts
export interface MemoryRecord {
  [key: string]: unknown;                  // record shape returned as `record.__dict__`
}

export interface MemorySearchResponse {
  query: string;
  user_id: string;
  results: MemoryRecord[];
  total: number;
}

export interface MemoryListResponse {
  user_id: string;
  results: MemoryRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface MemoryCreateResponse {
  user_id: string;
  created: boolean;
  reason?: string;                         // when created === false
  record?: MemoryRecord;                   // when created === true
}

export interface MemoryGetResponse {
  record: MemoryRecord;
}

export interface MemoryDeleteResponse {
  deleted: boolean;
  memory_id: string;
}
```

---

## 12. Completeness (⚠️ requires PostgreSQL)

```ts
export interface CompletenessObjective {
  objective_id: string;
  description: string;
  required_evidence: unknown;
  status: string;                          // ObjectiveStatus value
  related_claim_ids: string[];
  related_source_ids: string[];
  evidence_quote_count: number;
  supported_claim_count: number;
  contradicted_claim_count: number;
  unverified_claim_count: number;
}

export interface CompletenessEvaluateResponse {
  session_id: string;
  overall_status: string;
  completeness_score: number;
  objectives: CompletenessObjective[];
  gaps: unknown[];
  recommendations: unknown[];
  additional_search_tasks: unknown[];
  iterations_performed: number;
  max_iterations: number;
}

export interface ObjectivesResponse {
  session_id: string;
  objectives: unknown[];
  total: number;
}
```

---

## 13. SSE frames

```ts
export type LegacyStreamEvent = 'progress' | 'agent_start' | 'agent_done' | 'complete' | 'error';

export interface StreamFrameData {
  event_id: string;
  research_id: string;
  event_type: StreamCanonicalType;
  sequence_number: number;
  timestamp: UtcDatetime;
  [key: string]: unknown;                  // per-event extras (agent, progress, count, ...)
}

export interface StreamFrame {
  event: LegacyStreamEvent;
  message: string;
  agent?: string;
  data: StreamFrameData;
}

export type StreamCanonicalType =
  | 'research.queued' | 'research.started' | 'agent.started' | 'agent.progress'
  | 'agent.completed' | 'agent.failed' | 'source.discovered' | 'claim.extracted'
  | 'claim.verified' | 'contradiction.detected' | 'report.generated'
  | 'review.required' | 'research.completed' | 'research.failed';
```
