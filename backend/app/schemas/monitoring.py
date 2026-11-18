"""
app/schemas/monitoring.py
=========================
Pydantic schemas for the Production Infrastructure Monitoring & Observability Dashboard.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import Field

from app.schemas.base import OutputSchema, UtcDatetime, utc_now


class SystemOverview(OutputSchema):
    status: str = Field(..., description="Overall state: operational, degraded, partial_outage, unavailable, unknown")
    version: str = Field(..., description="Application semantic version")
    environment: str = Field(..., description="Deployment environment: production, development, etc.")
    uptime_seconds: float = Field(..., description="Process uptime in seconds")
    uptime_human: str = Field(..., description="Human-readable duration (e.g. 2h 15m 30s)")
    process_id: int = Field(..., description="Operating system PID")
    python_version: str = Field(..., description="Active Python runtime version")
    platform: str = Field(..., description="Host OS platform and architecture")
    timestamp: UtcDatetime = Field(default_factory=utc_now)


class InfrastructureCard(OutputSchema):
    id: str = Field(..., description="Unique identifier (e.g. fastapi, postgres, redis, celery, chromadb, storage)")
    name: str = Field(..., description="Display service name")
    status: str = Field(..., description="operational, degraded, unavailable, not_configured, unknown")
    latency_ms: Optional[float] = Field(None, description="Measured response latency in milliseconds")
    last_checked: str = Field(..., description="ISO 8601 UTC timestamp")
    details: Dict[str, Any] = Field(default_factory=dict, description="Safe operational metadata")
    error_message: Optional[str] = Field(None, description="Sanitized failure description if unavailable")


class AIProviderMonitoring(OutputSchema):
    provider: str = Field(..., description="Identifier: gemini, groq, mistral, huggingface")
    name: str = Field(..., description="Full provider title")
    configured: bool = Field(..., description="Whether API key or token is configured in settings")
    state: str = Field(..., description="ready, missing_credentials, rate_limited, unavailable")
    reachability: str = Field(..., description="available, reachable, unavailable, not_probed, missing_credentials")
    default_model: str = Field(..., description="Default model configured for this provider")
    supported_models: List[str] = Field(default_factory=list, description="Available models under this provider")
    fallback_rank: int = Field(..., description="Fallback routing priority (1=highest)")
    latency_ms: Optional[float] = Field(None, description="Measured probe response latency in ms")
    last_check: Optional[str] = Field(None, description="ISO 8601 UTC timestamp of last probe")
    last_error: Optional[str] = Field(None, description="Sanitized recent error if any")


class SearchSourceMonitoring(OutputSchema):
    id: str = Field(..., description="Source identifier (arxiv, wikipedia, duckduckgo, tavily, etc.)")
    name: str = Field(..., description="Display title")
    category: str = Field(..., description="Academic Literature, Encyclopedia, General Web, News, AI Research")
    configured: bool = Field(..., description="Whether required credentials or public adapter is ready")
    access_type: str = Field(..., description="Public / Free, API Key Required, Not Configured")
    reachability: str = Field(..., description="operational, missing_key, not_implemented, rate_limited")
    rate_limit_info: Optional[str] = Field(None, description="Observed rate limit guidelines")
    last_checked: str = Field(..., description="ISO 8601 timestamp")


class ResearchExecutionMonitoring(OutputSchema):
    total_sessions: int = Field(default=0, description="Total recorded research runs")
    active_sessions: int = Field(default=0, description="Currently running investigations")
    completed_sessions: int = Field(default=0, description="Successfully finalized reports")
    failed_sessions: int = Field(default=0, description="Failed research sessions")
    cancelled_sessions: int = Field(default=0, description="User-cancelled sessions")
    average_duration_seconds: Optional[float] = Field(None, description="Average duration of completed investigations")
    total_agent_retries: int = Field(default=0, description="Total node retries across runs")
    celery_configured: bool = Field(default=False, description="Whether Celery worker queue is active")
    queue_depth: Optional[int] = Field(None, description="Pending tasks in Celery queue")
    active_workers: Optional[int] = Field(None, description="Live online Celery worker processes")
    active_tasks: Optional[int] = Field(None, description="Currently processing Celery tasks")
    failed_tasks: Optional[int] = Field(None, description="Total failed Celery jobs")


class AgentStageMetric(OutputSchema):
    stage_id: str = Field(..., description="LangGraph node identifier (e.g. intake_node, search_node)")
    name: str = Field(..., description="Display title for agent or chain")
    category: str = Field(..., description="intake, retrieval, analysis, generation, validation, export, reliability")
    total_executions: int = Field(default=0, description="Total execution count")
    successful_executions: int = Field(default=0, description="Completed executions")
    failed_executions: int = Field(default=0, description="Failed executions")
    success_rate: float = Field(default=100.0, description="Percentage of successful runs (0-100)")
    average_duration_ms: Optional[float] = Field(None, description="Mean stage run duration in milliseconds")
    last_executed_at: Optional[str] = Field(None, description="Timestamp of most recent run")
    current_status: str = Field(default="idle", description="idle, active, degraded")


class ApiPerformanceMetrics(OutputSchema):
    total_requests: int = Field(default=0, description="Total HTTP requests processed")
    requests_per_minute: float = Field(default=0.0, description="Estimated current request throughput")
    average_latency_ms: float = Field(default=0.0, description="Mean request duration in ms")
    p95_latency_ms: float = Field(default=0.0, description="95th percentile latency in ms")
    p99_latency_ms: float = Field(default=0.0, description="99th percentile latency in ms")
    http_2xx: int = Field(default=0, description="Successful 2xx HTTP responses")
    http_4xx: int = Field(default=0, description="Client error 4xx HTTP responses")
    http_5xx: int = Field(default=0, description="Server error 5xx HTTP responses")
    error_rate_percent: float = Field(default=0.0, description="Percentage of 4xx and 5xx errors")
    concurrent_connections: int = Field(default=0, description="Active concurrent inbound HTTP requests")
    active_window: str = Field(default="Process Lifetime", description="Time window of measurement")


class LLMUsageMonitoring(OutputSchema):
    total_input_tokens: int = Field(default=0)
    total_output_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    total_calls: int = Field(default=0)
    total_estimated_cost_usd: float = Field(default=0.0)
    avg_cost_per_session_usd: float = Field(default=0.0)
    failed_calls: int = Field(default=0)
    providers: List[Dict[str, Any]] = Field(default_factory=list, description="Usage breakdown by LLM provider")
    models: List[Dict[str, Any]] = Field(default_factory=list, description="Usage breakdown by specific model")
    tracked: bool = Field(default=True, description="Whether usage telemetry is actively recorded")


class StorageMonitoring(OutputSchema):
    postgres: Dict[str, Any] = Field(default_factory=dict, description="PostgreSQL status, tables, and counts")
    chromadb: Dict[str, Any] = Field(default_factory=dict, description="ChromaDB status, collections, vector counts")
    local_storage: Dict[str, Any] = Field(default_factory=dict, description="File counts, disk usage, persistent volume")


class RecentSystemEvent(OutputSchema):
    id: str = Field(..., description="Unique event identifier")
    timestamp: str = Field(..., description="ISO 8601 UTC timestamp")
    category: str = Field(..., description="System, Database, Provider, Pipeline, Storage, Security")
    severity: str = Field(..., description="info, warning, error, critical")
    component: str = Field(..., description="Subsystem emitting the event")
    message: str = Field(..., description="Redacted human-readable event log message")


class DashboardOverviewResponse(OutputSchema):
    overview: SystemOverview
    infrastructure: List[InfrastructureCard]
    providers: List[AIProviderMonitoring]
    search_sources: List[SearchSourceMonitoring]
    research_execution: ResearchExecutionMonitoring
    agent_stages: List[AgentStageMetric]
    api_performance: ApiPerformanceMetrics
    llm_usage: LLMUsageMonitoring
    storage: StorageMonitoring
    recent_events: List[RecentSystemEvent]
    timestamp: UtcDatetime = Field(default_factory=utc_now)
