"""
app/telemetry/metrics.py
========================
Prometheus metrics registry and telemetry collectors (TASK 2.6).

Tracks:
1. API metrics: request count, error count, response latency, endpoint latency, concurrent connections.
2. Celery metrics: queue depth, active tasks, failed tasks, retry count, worker availability, execution duration.
3. AI metrics: provider, model, token usage, estimated cost, LLM latency, provider failures, fallback frequency.
4. Research metrics: duration, number of sources, number of claims, verification status, critic scores, revision count, report duration.

Fail-safe design:
All recording helpers swallow exceptions and log warnings so telemetry never interrupts research execution.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Mapping, Optional

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger("app.telemetry.metrics")

# Estimated cost table in USD per 1,000 tokens (prompt / completion averages)
_MODEL_COST_PER_1K_TOKENS: Dict[str, float] = {
    # Groq (Llama 3 / Mixtral)
    "groq/llama-3.3-70b-versatile": 0.00059,
    "groq/llama-3.1-8b-instant": 0.00008,
    "groq/mixtral-8x7b-32768": 0.00027,
    "groq": 0.0005,
    # Google Gemini
    "gemini/gemini-2.0-flash": 0.00015,
    "gemini/gemini-1.5-flash": 0.00015,
    "gemini/gemini-1.5-pro": 0.0020,
    "gemini": 0.0002,
    # Mistral
    "mistral/mistral-small-latest": 0.0002,
    "mistral/mistral-large-latest": 0.0020,
    "mistral": 0.0003,
    # HuggingFace (serverless inference API)
    "huggingface": 0.0,
    # Default fallback
    "default": 0.0002,
}


def estimate_cost_usd(provider: str, model: Optional[str], total_tokens: int) -> float:
    """Calculate approximate LLM invocation cost in USD."""
    if total_tokens <= 0:
        return 0.0
    key = f"{provider}/{model}".lower() if model else provider.lower()
    cost_per_1k = _MODEL_COST_PER_1K_TOKENS.get(
        key,
        _MODEL_COST_PER_1K_TOKENS.get(provider.lower(), _MODEL_COST_PER_1K_TOKENS["default"]),
    )
    return round((total_tokens / 1000.0) * cost_per_1k, 6)


class MetricsRegistry:
    """Thread-safe Prometheus metrics registry for the application."""

    def __init__(self, registry: Optional[CollectorRegistry] = None) -> None:
        self.registry = registry or CollectorRegistry(auto_describe=True)

        # ── 1. API Metrics ──────────────────────────────────────────────────
        self.http_requests_total = Counter(
            "http_requests_total",
            "Total HTTP requests received",
            ["method", "path", "status_code"],
            registry=self.registry,
        )
        self.http_request_errors_total = Counter(
            "http_request_errors_total",
            "Total HTTP request errors",
            ["method", "path", "error_type", "status_code"],
            registry=self.registry,
        )
        self.http_request_duration_seconds = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency in seconds",
            ["method", "path"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
            registry=self.registry,
        )
        self.http_endpoint_duration_seconds = Histogram(
            "http_endpoint_duration_seconds",
            "HTTP endpoint execution latency in seconds",
            ["endpoint"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
            registry=self.registry,
        )
        self.http_concurrent_connections = Gauge(
            "http_concurrent_connections",
            "Number of active concurrent HTTP connections",
            registry=self.registry,
        )

        # ── 2. Celery Metrics ───────────────────────────────────────────────
        self.celery_queue_depth = Gauge(
            "celery_queue_depth",
            "Estimated number of tasks waiting in queue",
            ["queue"],
            registry=self.registry,
        )
        self.celery_active_tasks = Gauge(
            "celery_active_tasks",
            "Number of currently active Celery tasks",
            ["task_name", "worker"],
            registry=self.registry,
        )
        self.celery_failed_tasks_total = Counter(
            "celery_failed_tasks_total",
            "Total number of failed Celery tasks",
            ["task_name", "exception_type"],
            registry=self.registry,
        )
        self.celery_retry_count_total = Counter(
            "celery_retry_count_total",
            "Total number of Celery task retries",
            ["task_name"],
            registry=self.registry,
        )
        self.celery_worker_availability = Gauge(
            "celery_worker_availability",
            "Status of Celery worker (1=healthy/online, 0=offline)",
            ["worker_name"],
            registry=self.registry,
        )
        self.celery_task_duration_seconds = Histogram(
            "celery_task_duration_seconds",
            "Celery task execution duration in seconds",
            ["task_name", "status"],
            buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0),
            registry=self.registry,
        )

        # ── 3. AI / LLM Metrics ─────────────────────────────────────────────
        self.ai_llm_requests_total = Counter(
            "ai_llm_requests_total",
            "Total LLM invocations by provider and model",
            ["provider", "model", "status"],
            registry=self.registry,
        )
        self.ai_token_usage_total = Counter(
            "ai_token_usage_total",
            "Total tokens consumed by LLM invocations",
            ["provider", "model", "token_type"],
            registry=self.registry,
        )
        self.ai_estimated_cost_usd_total = Counter(
            "ai_estimated_cost_usd_total",
            "Estimated total cost of LLM invocations in USD",
            ["provider", "model"],
            registry=self.registry,
        )
        self.ai_llm_duration_seconds = Histogram(
            "ai_llm_duration_seconds",
            "LLM invocation latency in seconds",
            ["provider", "model"],
            buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 40.0),
            registry=self.registry,
        )
        self.ai_provider_failures_total = Counter(
            "ai_provider_failures_total",
            "Total LLM provider invocation failures",
            ["provider", "error_code"],
            registry=self.registry,
        )
        self.ai_fallback_total = Counter(
            "ai_fallback_total",
            "Frequency of LLM provider fallback switches",
            ["from_provider", "to_provider", "reason"],
            registry=self.registry,
        )

        # ── 4. Research Pipeline Metrics ────────────────────────────────────
        self.research_duration_seconds = Histogram(
            "research_duration_seconds",
            "Total research pipeline duration in seconds",
            buckets=(1.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0, 300.0),
            registry=self.registry,
        )
        self.research_sources_count = Histogram(
            "research_sources_count",
            "Number of web sources discovered and analyzed",
            buckets=(0, 1, 3, 5, 10, 15, 20, 30, 50),
            registry=self.registry,
        )
        self.research_claims_count = Histogram(
            "research_claims_count",
            "Number of claims extracted in research",
            buckets=(0, 1, 3, 5, 10, 20, 40),
            registry=self.registry,
        )
        self.research_claims_verification_status_total = Counter(
            "research_claims_verification_status_total",
            "Count of research claims by verification status",
            ["status"],
            registry=self.registry,
        )
        self.research_critic_score = Histogram(
            "research_critic_score",
            "Distribution of research evaluation critic scores",
            buckets=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10),
            registry=self.registry,
        )
        self.research_revision_count = Histogram(
            "research_revision_count",
            "Number of revisions performed by refiner agent",
            buckets=(0, 1, 2, 3, 4, 5),
            registry=self.registry,
        )
        self.research_report_generation_duration_seconds = Histogram(
            "research_report_generation_duration_seconds",
            "Report rendering and export duration in seconds",
            buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
            registry=self.registry,
        )

    # ── Safe Recording Helpers (Non-blocking) ───────────────────────────────

    def record_http_request(
        self, method: str, path: str, status_code: int, duration_seconds: float
    ) -> None:
        try:
            clean_path = self._normalize_path(path)
            self.http_requests_total.labels(
                method=method.upper(), path=clean_path, status_code=str(status_code)
            ).inc()
            self.http_request_duration_seconds.labels(
                method=method.upper(), path=clean_path
            ).observe(duration_seconds)
            if status_code >= 400:
                error_type = "client_error" if status_code < 500 else "server_error"
                self.http_request_errors_total.labels(
                    method=method.upper(),
                    path=clean_path,
                    error_type=error_type,
                    status_code=str(status_code),
                ).inc()
        except Exception as exc:
            logger.debug("Failed to record http metric: %s", exc)

    def record_endpoint_latency(self, endpoint: str, duration_seconds: float) -> None:
        try:
            self.http_endpoint_duration_seconds.labels(endpoint=endpoint).observe(
                duration_seconds
            )
        except Exception as exc:
            logger.debug("Failed to record endpoint latency: %s", exc)

    def record_celery_task_start(self, task_name: str, worker: str = "default") -> None:
        try:
            self.celery_active_tasks.labels(task_name=task_name, worker=worker).inc()
        except Exception as exc:
            logger.debug("Failed to increment active celery tasks: %s", exc)

    def record_celery_task_finish(
        self,
        task_name: str,
        status: str,
        duration_seconds: float,
        worker: str = "default",
        exception_type: Optional[str] = None,
    ) -> None:
        try:
            self.celery_active_tasks.labels(task_name=task_name, worker=worker).dec()
            self.celery_task_duration_seconds.labels(
                task_name=task_name, status=status
            ).observe(duration_seconds)
            if status == "failed" and exception_type:
                self.celery_failed_tasks_total.labels(
                    task_name=task_name, exception_type=exception_type
                ).inc()
        except Exception as exc:
            logger.debug("Failed to record celery task finish: %s", exc)

    def record_celery_retry(self, task_name: str) -> None:
        try:
            self.celery_retry_count_total.labels(task_name=task_name).inc()
        except Exception as exc:
            logger.debug("Failed to record celery retry: %s", exc)

    def record_celery_queue_depth(self, queue: str, depth: int) -> None:
        try:
            self.celery_queue_depth.labels(queue=queue).set(max(0, depth))
        except Exception as exc:
            logger.debug("Failed to set queue depth: %s", exc)

    def record_celery_worker_availability(self, worker_name: str, is_available: bool) -> None:
        try:
            self.celery_worker_availability.labels(worker_name=worker_name).set(
                1.0 if is_available else 0.0
            )
        except Exception as exc:
            logger.debug("Failed to record worker availability: %s", exc)

    def record_llm_usage(
        self,
        *,
        provider: str,
        model: Optional[str],
        duration_seconds: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        status: str = "success",
        error_code: Optional[str] = None,
    ) -> None:
        try:
            prov = provider.lower()
            mod = (model or "default").lower()
            self.ai_llm_requests_total.labels(provider=prov, model=mod, status=status).inc()
            self.ai_llm_duration_seconds.labels(provider=prov, model=mod).observe(
                duration_seconds
            )
            total = prompt_tokens + completion_tokens
            if prompt_tokens > 0:
                self.ai_token_usage_total.labels(
                    provider=prov, model=mod, token_type="prompt"
                ).inc(prompt_tokens)
            if completion_tokens > 0:
                self.ai_token_usage_total.labels(
                    provider=prov, model=mod, token_type="completion"
                ).inc(completion_tokens)
            if total > 0:
                self.ai_token_usage_total.labels(
                    provider=prov, model=mod, token_type="total"
                ).inc(total)
                cost = estimate_cost_usd(prov, mod, total)
                if cost > 0:
                    self.ai_estimated_cost_usd_total.labels(
                        provider=prov, model=mod
                    ).inc(cost)
            if status != "success" and error_code:
                self.ai_provider_failures_total.labels(
                    provider=prov, error_code=error_code
                ).inc()
        except Exception as exc:
            logger.debug("Failed to record LLM usage metric: %s", exc)

    def record_llm_fallback(
        self, from_provider: str, to_provider: str, reason: str = "rate_limit"
    ) -> None:
        try:
            self.ai_fallback_total.labels(
                from_provider=from_provider.lower(),
                to_provider=to_provider.lower(),
                reason=reason[:60],
            ).inc()
        except Exception as exc:
            logger.debug("Failed to record LLM fallback: %s", exc)

    def record_research_outcome(
        self,
        *,
        duration_seconds: float,
        sources_count: int = 0,
        claims_count: int = 0,
        verified_count: int = 0,
        disputed_count: int = 0,
        unverified_count: int = 0,
        critic_score: Optional[int] = None,
        revision_count: int = 0,
    ) -> None:
        try:
            self.research_duration_seconds.observe(duration_seconds)
            self.research_sources_count.observe(sources_count)
            self.research_claims_count.observe(claims_count)
            if verified_count > 0:
                self.research_claims_verification_status_total.labels(
                    status="verified"
                ).inc(verified_count)
            if disputed_count > 0:
                self.research_claims_verification_status_total.labels(
                    status="disputed"
                ).inc(disputed_count)
            if unverified_count > 0:
                self.research_claims_verification_status_total.labels(
                    status="unverified"
                ).inc(unverified_count)
            if critic_score is not None and 1 <= critic_score <= 10:
                self.research_critic_score.observe(critic_score)
            self.research_revision_count.observe(revision_count)
        except Exception as exc:
            logger.debug("Failed to record research outcome: %s", exc)

    def record_report_generation(self, duration_seconds: float) -> None:
        try:
            self.research_report_generation_duration_seconds.observe(duration_seconds)
        except Exception as exc:
            logger.debug("Failed to record report generation duration: %s", exc)

    def export(self) -> bytes:
        """Export current metrics in Prometheus text format."""
        return generate_latest(self.registry)

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Normalize URL path so dynamic IDs don't cause label cardinality explosions."""
        if not path:
            return "/"
        import re

        # Mask 32-hex IDs and UUIDs
        clean = re.sub(r"/[a-f0-9]{32}(?=/|$)", "/:id", path)
        clean = re.sub(
            r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?=/|$)",
            "/:uuid",
            clean,
        )
        return clean


# Global default instance
DEFAULT_METRICS = MetricsRegistry()


def get_metrics() -> MetricsRegistry:
    """Return the global MetricsRegistry singleton."""
    return DEFAULT_METRICS

