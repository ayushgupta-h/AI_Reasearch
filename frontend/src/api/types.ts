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
  chunk_count?: number;
  error_message?: string | null;
  created_at: UtcDatetime;
  updated_at: UtcDatetime;
}

export interface DocumentCitation {
  index: number;
  document_id: string;
  filename: string;
  page_number?: number | string;
  section?: string;
  text?: string;
  snippet?: string;
}

export interface DocumentChatMessage {
  message_id: string;
  conversation_id?: string;
  role: 'user' | 'assistant';
  content: string;
  citations?: DocumentCitation[];
  created_at: UtcDatetime;
}

export interface DocumentConversation {
  conversation_id: string;
  document_id: string;
  title: string;
  created_at: UtcDatetime;
  updated_at: UtcDatetime;
}

export interface DocumentPreview {
  document_id: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  chunk_count: number;
  processing_status: string;
  total_pages: number;
  pages: Array<{ page_number: number; text: string; char_count: number }>;
  preview_text: string;
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

export interface KnowledgeStats {
  total_documents: number;
  total_chunks: number;
  total_memories: number;
  contributing_sessions: number;
}

export interface MemoryRecord {
  memory_id?: string;
  id?: string;
  content?: string;
  content_text?: string;
  content_type?: string;
  source_type?: 'document' | 'research';
  verification_status?: string;
  confidence?: number;
  document_id?: string;
  filename?: string;
  topic?: string;
  query?: string;
  summary?: string;
  session_id?: string;
  project_id?: string;
  source_id?: string;
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

export interface ComparisonEvidence {
  passage: string;
  section: string;
  page?: number | null;
}

export interface ComparisonDimension {
  dimension_id: string;
  title: string;
  report_a_content: string;
  report_b_content: string;
  comparison_verdict: string;
  status: 'agreement' | 'contradiction' | 'partial_contradiction' | 'methodological_difference' | 'dataset_difference' | 'missing_information';
  explanation?: string;
  evidence_a?: ComparisonEvidence[];
  evidence_b?: ComparisonEvidence[];
}

export interface ComparisonDifference {
  category: string;
  severity: 'high' | 'medium' | 'low';
  description: string;
  report_a_claim: string;
  report_b_claim: string;
  explanation: string;
  resolution_notes: string;
}

export interface ComparisonOverviewStats {
  sections_compared: number;
  findings_compared: number;
  agreements_count: number;
  contradictions_count: number;
  methodological_diff_count: number;
  unresolved_issues_count: number;
}

export interface ReportComparisonRecord {
  comparison_id: string;
  owner_id: string;
  title: string;
  report_a_id: string;
  report_a_title: string;
  report_a_type: 'uploaded' | 'generated';
  report_b_id: string;
  report_b_title: string;
  report_b_type: 'uploaded' | 'generated';
  status: 'completed' | 'analyzing' | 'failed';
  summary_json?: ComparisonOverviewStats;
  analysis_json?: {
    executive_summary?: string;
    overview_stats?: ComparisonOverviewStats;
    dimensions?: ComparisonDimension[];
    key_differences?: ComparisonDifference[];
    methodology_comparison?: { report_a_method: string; report_b_method: string; comparison: string };
    evidence_strength?: { report_a_assessment: string; report_b_assessment: string; synthesis: string };
    unresolved_questions?: string[];
  };
  sections_json?: ComparisonDimension[];
  differences_json?: ComparisonDifference[];
  created_at: string;
  updated_at: string;
  report_a_meta?: {
    id: string;
    title: string;
    type: string;
    created_at?: string;
    page_count?: number;
  };
  report_b_meta?: {
    id: string;
    title: string;
    type: string;
    created_at?: string;
    page_count?: number;
  };
}

export interface ComparisonCitation {
  source: 'Report A' | 'Report B';
  title: string;
  section: string;
  page?: number;
  snippet: string;
}

export interface ComparisonChatMessage {
  message_id: string;
  comparison_id: string;
  owner_id: string;
  role: 'user' | 'assistant';
  content: string;
  citations?: ComparisonCitation[];
  created_at: string;
}

/* ============================================================
 * Production Observability & Infrastructure Monitoring Types
 * ============================================================ */

export interface SystemOverviewData {
  status: 'operational' | 'degraded' | 'partial_outage' | 'unavailable' | 'unknown';
  version: string;
  environment: string;
  uptime_seconds: number;
  uptime_human: string;
  process_id: number;
  python_version: string;
  platform: string;
  timestamp: string;
}

export interface InfrastructureCardData {
  id: string;
  name: string;
  status: 'operational' | 'degraded' | 'unavailable' | 'not_configured' | 'unknown';
  latency_ms?: number | null;
  last_checked: string;
  details: Record<string, any>;
  error_message?: string | null;
}

export interface AIProviderMonitoringData {
  provider: string;
  name: string;
  configured: boolean;
  state: string;
  reachability: 'available' | 'reachable' | 'unavailable' | 'not_probed' | 'missing_credentials';
  default_model: string;
  supported_models: string[];
  fallback_rank: number;
  latency_ms?: number | null;
  last_check?: string | null;
  last_error?: string | null;
}

export interface SearchSourceMonitoringData {
  id: string;
  name: string;
  category: string;
  configured: boolean;
  access_type: string;
  reachability: string;
  rate_limit_info?: string | null;
  last_checked: string;
}

export interface ResearchExecutionMonitoringData {
  total_sessions: number;
  active_sessions: number;
  completed_sessions: number;
  failed_sessions: number;
  cancelled_sessions: number;
  average_duration_seconds?: number | null;
  total_agent_retries: number;
  celery_configured: boolean;
  queue_depth?: number | null;
  active_workers?: number | null;
  active_tasks?: number | null;
  failed_tasks?: number | null;
}

export interface AgentStageMetricData {
  stage_id: string;
  name: string;
  category: string;
  total_executions: number;
  successful_executions: number;
  failed_executions: number;
  success_rate: number;
  average_duration_ms?: number | null;
  last_executed_at?: string | null;
  current_status: string;
}

export interface ApiPerformanceMetricsData {
  total_requests: number;
  requests_per_minute: number;
  average_latency_ms: number;
  p95_latency_ms: number;
  p99_latency_ms: number;
  http_2xx: number;
  http_4xx: number;
  http_5xx: number;
  error_rate_percent: number;
  concurrent_connections: number;
  active_window: string;
}

export interface LLMUsageMonitoringData {
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  total_calls: number;
  total_estimated_cost_usd: number;
  avg_cost_per_session_usd: number;
  failed_calls: number;
  providers: Array<{ provider: string; calls: number; tokens: number; cost_usd: number }>;
  models: Array<{ model: string; provider: string; calls: number; tokens: number; cost_usd: number }>;
  tracked: boolean;
}

export interface StorageMonitoringData {
  postgres: {
    status?: string;
    connected?: boolean;
    tables?: Record<string, number>;
    error?: string;
  };
  chromadb: {
    status?: string;
    collection_count?: number;
    total_vectors?: number;
    collections?: string[];
    path?: string;
    error?: string;
  };
  local_storage: {
    read_write_verified?: boolean;
    reports_count?: number;
    uploads_count?: number;
    exports_count?: number;
    app_storage_used_mb?: number;
    disk_total_gb?: number;
    disk_used_gb?: number;
    disk_free_gb?: number;
    disk_used_percent?: number;
    persistent_mount?: boolean;
    volume_label?: string;
    error?: string;
  };
}

export interface RecentSystemEventData {
  id: string;
  timestamp: string;
  category: string;
  severity: 'info' | 'warning' | 'error' | 'critical';
  component: string;
  message: string;
}

export interface DashboardOverviewData {
  overview: SystemOverviewData;
  infrastructure: InfrastructureCardData[];
  providers: AIProviderMonitoringData[];
  search_sources: SearchSourceMonitoringData[];
  research_execution: ResearchExecutionMonitoringData;
  agent_stages: AgentStageMetricData[];
  api_performance: ApiPerformanceMetricsData;
  llm_usage: LLMUsageMonitoringData;
  storage: StorageMonitoringData;
  recent_events: RecentSystemEventData[];
  timestamp: string;
}



