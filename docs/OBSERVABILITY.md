# Production Observability Integration Guide (TASK 2.6)

Research AI Quest incorporates end-to-end production monitoring, metrics, structured logging, and distributed tracing.

---

## 1. Observability Architecture

```
                                 ┌─────────────────────────┐
                                 │      Grafana (3000)     │
                                 └────────────▲────────────┘
                                              │ (Queries)
                     ┌────────────────────────┴────────────────────────┐
                     │                                                 │
          ┌─────────────────────┐                           ┌─────────────────────┐
          │  Prometheus (9090)  │                           │    Jaeger (16686)   │
          └──────────▲──────────┘                           └──────────▲──────────┘
                     │ (Pull /metrics)                                 │ (Push OTLP 4317/4318)
  ┌──────────────────┴─────────────────────────────────────────────────┴──────────────────┐
  │                                   Research AI System                                  │
  │                                                                                       │
  │  FastAPI (HTTP) ──> Celery (Worker) ──> LangGraph ──> Agent ──> LLM ──> Search        │
  │     [Trace Context Propagation via W3C `traceparent` & `tracestate`]                  │
  │     [Correlated `research_id`, `trace_id`, and `request_id` across Logs & Spans]      │
  └───────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Metrics Dictionary

All application metrics are exposed via the Prometheus exposition format on `GET /metrics`.

### 2.1 API Metrics
| Metric Name | Type | Labels | Description |
|---|---|---|---|
| `http_requests_total` | Counter | `method`, `path`, `status_code` | Total HTTP requests received |
| `http_request_errors_total` | Counter | `method`, `path`, `error_type`, `status_code` | Total 4xx and 5xx errors |
| `http_request_duration_seconds` | Histogram | `method`, `path` | Overall request latency |
| `http_endpoint_duration_seconds` | Histogram | `endpoint` | Handler execution latency |
| `http_concurrent_connections` | Gauge | *none* | Currently active concurrent HTTP connections |

### 2.2 Celery & Background Pipeline Metrics
| Metric Name | Type | Labels | Description |
|---|---|---|---|
| `celery_queue_depth` | Gauge | `queue` | Estimated waiting tasks in Redis queue |
| `celery_active_tasks` | Gauge | `task_name`, `worker` | Number of currently executing tasks |
| `celery_failed_tasks_total` | Counter | `task_name`, `exception_type` | Total task execution failures |
| `celery_retry_count_total` | Counter | `task_name` | Total transient retries triggered |
| `celery_worker_availability` | Gauge | `worker_name` | Worker status (1=healthy, 0=offline) |
| `celery_task_duration_seconds` | Histogram | `task_name`, `status` | Celery execution latency |

### 2.3 AI & Model Usage Metrics
| Metric Name | Type | Labels | Description |
|---|---|---|---|
| `ai_llm_requests_total` | Counter | `provider`, `model`, `status` | Total LLM invocations |
| `ai_token_usage_total` | Counter | `provider`, `model`, `token_type` | Tokens (`prompt`, `completion`, `total`) |
| `ai_estimated_cost_usd_total` | Counter | `provider`, `model` | Cumulative approximate cost in USD |
| `ai_llm_duration_seconds` | Histogram | `provider`, `model` | LLM invocation duration |
| `ai_provider_failures_total` | Counter | `provider`, `error_code` | Provider API failures |
| `ai_fallback_total` | Counter | `from_provider`, `to_provider`, `reason` | Frequency of provider failover |

### 2.4 Research & Quality Metrics
| Metric Name | Type | Labels | Description |
|---|---|---|---|
| `research_duration_seconds` | Histogram | *none* | End-to-end research execution time |
| `research_sources_count` | Histogram | *none* | Count of web sources discovered and parsed |
| `research_claims_count` | Histogram | *none* | Count of claims extracted |
| `research_claims_verification_status_total` | Counter | `status` | Claims by status (`verified`, `disputed`, `unverified`) |
| `research_critic_score` | Histogram | *none* | Quality evaluation score distribution (1-10) |
| `research_revision_count` | Histogram | *none* | Number of refinement revisions per run |
| `research_report_generation_duration_seconds` | Histogram | *none* | Markdown/PDF/DOCX rendering latency |

---

## 3. Distributed Tracing Flow

OpenTelemetry traces operations end-to-end:

1. **FastAPI Request**:
   - `HttpTelemetryMiddleware` extracts incoming `traceparent` headers if present; otherwise, it creates a new root server span (`HTTP {method} {path}`).
   - Sets response headers `X-Request-ID` and `X-Trace-ID`.
2. **Celery Task Dispatch**:
   - `ResearchTaskProducer.submit` injects the W3C trace context into Celery task headers (`traceparent`, `tracestate`, `research_id`).
3. **Celery Task Execution**:
   - `research_task` in `research_tasks.py` extracts the parent trace context and starts child span `celery.task.research.run`.
4. **LangGraph Nodes**:
   - `instrument_node` starts child spans for each agent stage: `agent.intake_node`, `agent.search_node`, `agent.reader_node`, `agent.analyst_node`, `agent.writer_node`, `agent.critic_node`, etc.
5. **LLM Provider Calls**:
   - `TracedLLMCallbackHandler` starts client spans `llm.invoke {provider}/{model}`, recording token counts, model names, and latency.
6. **Search & Tool Invocations**:
   - `tools.py` records spans for `search.tavily`, `search.scrape`, and `search.multi_source`.

### Context Correlation
- Every log message automatically binds `request_id`, `research_id`, `trace_id`, and `span_id`.
- Sinks (Console or JSON) format these fields so logs and traces can be cross-referenced in Grafana / Loki / Jaeger.

---

## 4. Privacy & Sensitive Data Protection

Telemetry enforces strict privacy controls:
- **No Token Material**: Bearer tokens, Supabase JWTs, API keys (`tvly-*`, `gsk_*`, `sk-*`, etc.) are redacted via regex before reaching any log or span attribute.
- **No Document Bodies or Raw Prompts**: Span attributes record prompt and document length only (`gen_ai.prompt_char_length`, `tool.input_length`), never raw user text or confidential files.
- **Non-blocking Guarantee**: Telemetry recording functions are wrapped in fail-safe exception handlers; if Prometheus or OpenTelemetry collector is unreachable, research execution continues 100% uninterrupted.

---

## 5. Deployment with Docker Compose

To start the monitoring stack (Prometheus, Grafana, Jaeger):

```bash
docker compose -f monitoring/docker-compose.monitoring.yml up -d
```

### Endpoints
- **Grafana UI**: [http://localhost:3000](http://localhost:3000) (Default user: `admin`, password: `admin`)
  - Dashboards are pre-provisioned under the **Research AI** folder:
    - *Research AI — API Overview*
    - *Research AI — Celery & Background Pipeline*
    - *Research AI — LLM & Provider Telemetry*
    - *Research AI — Research & Quality Metrics*
- **Prometheus UI**: [http://localhost:9090](http://localhost:9090)
- **Jaeger UI**: [http://localhost:16686](http://localhost:16686)
- **FastAPI Metrics Endpoint**: [http://localhost:8000/metrics](http://localhost:8000/metrics)

---

## 6. Environment Configuration

Add the following environment variables to configure distributed trace exports:

```env
# OpenTelemetry Exporter Endpoint (gRPC or HTTP)
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_EXPORTER_OTLP_PROTOCOL=grpc

# Service metadata
OTEL_SERVICE_NAME=research-ai
API_VERSION=2.0.0
APP_ENV=production
```

