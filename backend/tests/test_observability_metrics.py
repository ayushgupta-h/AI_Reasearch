"""
tests/test_observability_metrics.py
===================================
Comprehensive test suite for Production Observability Integration (TASK 2.6).

Covers:
1. Metrics endpoint (/metrics) returns valid Prometheus exposition format.
2. Tracking API metrics (request count, errors, latency, concurrent connections).
3. Tracking Celery metrics (queue depth, active tasks, failures, retries, availability).
4. Tracking AI metrics (token usage, estimated cost, LLM latency, fallbacks).
5. Tracking Research metrics (duration, sources, claims, critic scores, revisions).
6. OpenTelemetry distributed tracing and W3C context propagation.
7. Sensitive attribute and token redaction in spans.
8. Non-blocking error safety under failures.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from app.factory import create_app
from app.observability import current_span_id, current_trace_id, log_context
from app.telemetry import (
    MetricsRegistry,
    TracedLLMCallbackHandler,
    extract_trace_context,
    get_metrics,
    inject_trace_context,
    sanitize_span_attributes,
    trace_span,
)


@pytest.fixture
def test_client() -> TestClient:
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class TestPrometheusEndpoint:
    """Verify /metrics endpoint exposition."""

    def test_metrics_endpoint_returns_200_and_plain_text(self, test_client: TestClient) -> None:
        response = test_client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers.get("content-type", "")
        text = response.text
        # Check core Prometheus metric families exist
        assert "http_requests_total" in text
        assert "http_request_duration_seconds" in text
        assert "http_concurrent_connections" in text
        assert "ai_token_usage_total" in text
        assert "research_duration_seconds" in text

    def test_http_request_records_method_and_path(self, test_client: TestClient) -> None:
        # Perform request to /health
        res = test_client.get("/health")
        assert res.status_code == 200

        # Scrape /metrics
        metrics_text = test_client.get("/metrics").text
        assert 'http_requests_total{method="GET",path="/health",status_code="200"}' in metrics_text
        assert 'http_request_duration_seconds_count{method="GET",path="/health"}' in metrics_text

    def test_response_carries_trace_and_request_headers(self, test_client: TestClient) -> None:
        res = test_client.get("/health")
        assert res.status_code == 200
        assert "x-request-id" in res.headers
        assert "x-trace-id" in res.headers
        assert len(res.headers["x-trace-id"]) == 32


class TestMetricsRegistry:
    """Unit tests for isolated MetricsRegistry."""

    @pytest.fixture
    def metrics(self) -> MetricsRegistry:
        return MetricsRegistry(registry=CollectorRegistry(auto_describe=True))

    def test_record_http_request_and_errors(self, metrics: MetricsRegistry) -> None:
        metrics.record_http_request("GET", "/test", 200, 0.05)
        metrics.record_http_request("POST", "/test", 500, 0.12)

        data = metrics.export().decode("utf-8")
        assert 'http_requests_total{method="GET",path="/test",status_code="200"} 1.0' in data
        assert 'http_requests_total{method="POST",path="/test",status_code="500"} 1.0' in data
        assert 'http_request_errors_total{error_type="server_error",method="POST",path="/test",status_code="500"} 1.0' in data

    def test_path_normalization_prevents_high_cardinality(self, metrics: MetricsRegistry) -> None:
        metrics.record_http_request("GET", "/reports/0123456789abcdef0123456789abcdef", 200, 0.01)
        data = metrics.export().decode("utf-8")
        assert 'path="/reports/:id"' in data

    def test_celery_metrics_recording(self, metrics: MetricsRegistry) -> None:
        metrics.record_celery_task_start("research.run", "worker-1")
        metrics.record_celery_queue_depth("celery", 5)
        metrics.record_celery_worker_availability("worker-1", True)
        metrics.record_celery_retry("research.run")
        metrics.record_celery_task_finish(
            "research.run", "failed", 1.5, worker="worker-1", exception_type="RateLimitError"
        )

        data = metrics.export().decode("utf-8")
        assert 'celery_queue_depth{queue="celery"} 5.0' in data
        assert 'celery_worker_availability{worker_name="worker-1"} 1.0' in data
        assert 'celery_retry_count_total{task_name="research.run"} 1.0' in data
        assert 'celery_failed_tasks_total{exception_type="RateLimitError",task_name="research.run"} 1.0' in data

    def test_ai_llm_metrics_and_cost_estimation(self, metrics: MetricsRegistry) -> None:
        metrics.record_llm_usage(
            provider="groq",
            model="openai/gpt-oss-120b",
            duration_seconds=0.45,
            prompt_tokens=400,
            completion_tokens=200,
            status="success",
        )
        metrics.record_llm_fallback("groq", "gemini", "rate_limit")

        data = metrics.export().decode("utf-8")
        assert 'ai_llm_requests_total{model="openai/gpt-oss-120b",provider="groq",status="success"} 1.0' in data
        assert 'ai_token_usage_total{model="openai/gpt-oss-120b",provider="groq",token_type="total"} 600.0' in data
        assert 'ai_fallback_total{from_provider="groq",reason="rate_limit",to_provider="gemini"} 1.0' in data
        assert "ai_estimated_cost_usd_total" in data

    def test_research_outcomes_recording(self, metrics: MetricsRegistry) -> None:
        metrics.record_research_outcome(
            duration_seconds=12.5,
            sources_count=8,
            claims_count=15,
            verified_count=12,
            disputed_count=2,
            unverified_count=1,
            critic_score=9,
            revision_count=1,
        )
        metrics.record_report_generation(0.35)

        data = metrics.export().decode("utf-8")
        assert "research_duration_seconds_bucket" in data
        assert "research_sources_count_bucket" in data
        assert "research_critic_score_bucket" in data
        assert 'research_claims_verification_status_total{status="verified"} 12.0' in data
        assert 'research_claims_verification_status_total{status="disputed"} 2.0' in data
        assert "research_report_generation_duration_seconds_bucket" in data


class TestDistributedTracing:
    """Verify OpenTelemetry tracing and context propagation."""

    def test_trace_span_binds_ids_to_log_context(self) -> None:
        with trace_span("unit_test_span", research_id="test-run-123") as span:
            span_ctx = span.get_span_context()
            if span_ctx.is_valid:
                assert current_trace_id() == format(span_ctx.trace_id, "032x")
                assert current_span_id() == format(span_ctx.span_id, "016x")

    def test_w3c_trace_context_propagation(self) -> None:
        with trace_span("parent_span", research_id="run-456"):
            carrier = inject_trace_context()
            assert "traceparent" in carrier

            extracted = extract_trace_context(carrier)
            assert extracted is not None

    def test_sensitive_attribute_sanitization(self) -> None:
        raw_attrs = {
            "api_key": "sk-secret-1234567890",
            "access_token": "token-xyz",
            "prompt": "confidential user document query",
            "provider": "gemini",
            "critic_score": 9,
        }
        sanitized = sanitize_span_attributes(raw_attrs)
        assert "api_key" not in sanitized
        assert "access_token" not in sanitized
        assert "prompt" not in sanitized
        assert "prompt.length" in sanitized
        assert sanitized["provider"] == "gemini"
        assert sanitized["critic_score"] == 9


class TestNonBlockingSafety:
    """Verify monitoring failures never interrupt research execution."""

    def test_metrics_swallows_exceptions(self) -> None:
        metrics = get_metrics()
        # Passing invalid types must not raise
        metrics.record_http_request(None, None, 200, "invalid")  # type: ignore[arg-type]
        metrics.record_celery_task_finish(None, None, "invalid")  # type: ignore[arg-type]
        metrics.record_llm_usage(provider=None, model=None, duration_seconds="invalid")  # type: ignore[arg-type]
        metrics.record_research_outcome(duration_seconds="invalid")  # type: ignore[arg-type]

    def test_traced_callback_handler_safety(self) -> None:
        handler = TracedLLMCallbackHandler(default_provider="groq")
        # Ensure handling invalid/empty inputs doesn't throw
        handler.on_llm_start({}, [], run_id="run-1")
        handler.on_tool_start({}, "input", run_id="run-2")
        handler.on_tool_end("output", run_id="run-2")
        handler.on_llm_error(RuntimeError("simulated error"), run_id="run-1")

