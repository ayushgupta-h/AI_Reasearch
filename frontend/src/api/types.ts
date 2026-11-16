/**
 * Research AI Quest — TypeScript API Types
 * Derived from backend OpenAPI specifications & schemas.
 */

export type UtcDatetime = string;
export type ResearchId = string;
export type ExecutionId = string;
export type DocumentId = string;
export type ReportId = string;
export type VersionId = string;
export type UUID = string;

export interface PaginationMeta {
  limit: number;
  offset: number;
  total: number;
  returned: number;
  has_more: boolean;
}

export interface ApiErrorPayload {
  detail: string;
  error_code?: string;
  request_id?: string;
  details?: Record<string, unknown> | null;
  timestamp?: UtcDatetime | null;
}

export interface ResearchSession {
  id: string;
  session_id?: string;
  research_id?: string;
  title?: string;
  topic?: string;
  query?: string;
  status?: string;
  provider?: string;
  critic_score?: number;
  revision_count?: number;
  duration_ms?: number;
  duration_seconds?: number;
  sources_count?: number;
  claims_count?: number;
  summary?: string;
  report_content?: string;
  sources?: any[];
  claims?: any[];
  citations_verified?: number;
  citations_total?: number;
  timestamp?: string;
  created_at?: string;
  updated_at?: string;
}

export interface Project {
  project_id: string;
  id?: string;
  owner_id: string;
  name: string;
  description?: string | null;
  research_domain?: string | null;
  status: 'active' | 'paused' | 'completed' | 'archived';
  created_at: UtcDatetime;
  updated_at: UtcDatetime;
}

export interface SourceRef {
  id: string;
  source_id?: string;
  title?: string;
  url?: string | null;
  doi?: string | null;
  publisher?: string | null;
  source_type?: string | null;
  published_at?: string | null;
  citation_status?: string | null;
  credibility_score?: number | null;
  credibility_label?: string | null;
}

export interface ClaimRecord {
  id: string;
  claim_id?: string;
  text?: string;
  verification_status?: string;
  status?: string;
  confidence?: number;
  claim_type?: string;
  supporting_evidence?: Array<{ quote: string; url?: string; stance: string }>;
  contradicting_evidence?: Array<{ quote: string; url?: string; stance: string }>;
}

export interface ResearchReport {
  id: string | number;
  report_id?: string;
  research_id?: string;
  title?: string;
  content?: string;
  markdown?: string;
  version?: number;
  status?: string;
  critic_score?: number;
  verification_status?: string;
  created_at?: string;
}

export interface DocumentRecord {
  document_id: string;
  id?: string;
  owner_id: string;
  project_id?: string | null;
  research_id?: string | null;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  processing_status: 'uploaded' | 'pending' | 'extracting' | 'chunking' | 'embedding' | 'indexed' | 'failed';
  error_message?: string | null;
  created_at: UtcDatetime;
  updated_at: UtcDatetime;
}

export interface ContradictionRecord {
  contradiction_id: string;
  session_id: string;
  claim_a_id: string;
  claim_b_id: string;
  category: string;
  severity: 'low' | 'medium' | 'high';
  status: string;
  description?: string;
  resolution_notes?: string;
  evidence?: Array<{ evidence_id: string; side: string; weight?: number }>;
}

export interface MemoryRecord {
  memory_id?: string;
  id?: string;
  content?: string;
  topic?: string;
  query?: string;
  summary?: string;
  tags?: string[];
  created_at?: string;
  [key: string]: unknown;
}

export interface HealthCheckComponent {
  name: string;
  state: 'ok' | 'degraded' | 'error';
  detail?: string | null;
  duration_ms?: number | null;
}

export interface HealthStatus {
  status: 'ok' | 'degraded' | 'error';
  version: string;
  environment?: string | null;
  uptime_seconds?: number | null;
  timestamp: UtcDatetime;
  checks: HealthCheckComponent[];
}

export interface ProviderStatus {
  provider: string;
  model: string;
  state: 'ready' | 'missing_credentials' | 'degraded' | 'unavailable';
  capabilities: string[];
  fallback_rank: number;
}

export interface ProvidersResponse {
  providers: string[];
  default: string;
  statuses?: ProviderStatus[] | null;
}

export interface NotificationRecord {
  notification_id: string;
  title: string;
  message: string;
  severity?: 'info' | 'warning' | 'error' | 'success';
  read: boolean;
  created_at: UtcDatetime;
}

export interface ReviewRecord {
  plan_id?: string;
  report_id?: string;
  session_id: string;
  title: string;
  status: string;
  query?: string;
  objective?: string;
  critic_score?: number;
  review_note?: string;
  reviewed_by?: string;
  reviewed_at?: string;
  details?: Record<string, unknown>;
  created_at: UtcDatetime;
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
  per_research?: Array<{ research_id: string; requests: number; total_tokens: number; estimated_cost_usd: number }>;
  per_provider?: Array<{ provider: string; requests: number; total_tokens: number; estimated_cost_usd: number; failures: number }>;
}

