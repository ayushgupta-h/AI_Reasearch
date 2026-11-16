# Research AI Quest — API Endpoints

Complete integration contract for the FastAPI backend.

> **Base URL:** `http://127.0.0.1:8000` (dev) · live OpenAPI: `http://127.0.0.1:8000/openapi.json`
>
> **Authentication:** All routes below marked 🔒 require a valid Supabase JWT (see `FRONTEND_INTEGRATION.md`).
> **Status conventions:** 🔒 = authenticated · 🟢 = public/optional · ⚠️ = requires PostgreSQL persistence · 🚧 = implemented but not yet mounted · ⏳ = planned, not implemented.

---

## Conventions

### Response shapes
- **Flat payload** by default. `{ "success": true, "data": ..., "message": "OK", "request_id": ... }` envelope is **opt-in** via `?envelope=true` on legacy read endpoints only.
- **Pagination** responses use `{ "items": [...], "pagination": { "limit", "offset", "total", "returned", "has_more" } }`.
- **Errors** always use the `ApiError` shape:

```json
{
  "detail": "Research session not found",
  "error_code": "not_found",
  "request_id": "req_abc123",
  "details": null,
  "timestamp": "2025-01-01T00:00:00Z"
}
```

### Path parameter formats
| Param | Format | Example |
|---|---|---|
| `research_id` | 32-char lowercase hex | `4f2a1c9b3e8d4a6f0c1b2a3d4e5f607` |
| `execution_id`, `document_id`, `report_id`, `version_id` | 32-char lowercase hex | `a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5` |
| `project_id`, `claim_id`, `source_id` | UUID (with dashes) | `550e8400-e29b-41d4-a716-446655440000` |
| `session_id` (review routes) | 32-36 chars, hex + optional dashes | `4f2a1c9b3e8d4a6f0c1b2a3d4e5f607` |

---

## 1. Authentication & System

### `GET /auth/config` — 🟢 public
Return the Supabase client configuration for client-side auth.

```json
{ "supabaseUrl": "https://vxqalflbzpjkvmtbbqrv.supabase.co", "supabaseAnonKey": "eyJhbGci..." }
```

### `GET /auth/me` — 🔒
Return the profile of the currently authenticated user.

```json
{ "status": "authenticated", "user": { "user_id": "u_123", "sub": "...", "email": "a@b.com", "role": "authenticated", "user_metadata": {} } }
```

### `GET /health` — 🟢 public
Liveness. `?envelope=true` wraps in `ApiResponse`.

```json
{
  "status": "ok",
  "version": "0.1.0",
  "environment": "development",
  "uptime_seconds": 42.0,
  "timestamp": "2025-01-01T00:00:00Z",
  "checks": [ { "name": "database", "state": "ok", "detail": null, "duration_ms": 1.2 } ]
}
```

### `GET /providers` — 🟢 public
Supported LLM providers and their status.

```json
{
  "providers": ["groq", "gemini", "mistral", "huggingface"],
  "default": "groq",
  "statuses": [
    { "provider": "groq", "model": "llama-3.3-70b", "state": "ready",
      "capabilities": ["chat", "research"], "fallback_rank": 1 }
  ]
}
```

### `GET /metrics` — 🟢 public
Prometheus metrics in `text/plain` exposition format.

### `DELETE /sessions/{session_id}` — 🔒
Delete a research session record owned by the user.

```json
{ "status": "deleted", "session_id": "..." }
```

---

## 2. Research

### `POST /research` — 🟢 optional auth
Start a multi-agent research pipeline. Returns a **Server-Sent Events stream** (see `SSE_EVENTS.md`).

Request body:
```json
{
  "topic": "Impact of large language models on scientific discovery",
  "provider": "groq",
  "max_revisions": 2,
  "approval_threshold": 8,
  "output_dir": "reports",
  "require_plan_approval": false
}
```
| Field | Type | Default | Constraints |
|---|---|---|---|
| `topic` | string | — | required, 3–500 chars |
| `provider` | `"groq"\|"gemini"\|"mistral"\|"huggingface"` | configured default | |
| `max_revisions` | int | config | 0–5 |
| `approval_threshold` | int | config | 1–10 (critic score out of 10) |
| `output_dir` | string | reports dir | must resolve inside reports dir |
| `require_plan_approval` | bool | `false` | pauses after planning for human decision |

### `GET /research/{research_id}/events` — 🔒
Reconnectable SSE stream (see `SSE_EVENTS.md`). Supports `Last-Event-ID` header or `?last_event_id=` query.

### `GET /research/{research_id}` — 🔒
Get one research session (a project member).

```json
{
  "research_id": "4f2a1c...", "project_id": "550e8400-e29b-...",
  "owner_id": "u_123", "topic": "Impact of LLMs",
  "provider": "groq", "status": "completed", "priority": "normal",
  "notes": null, "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:05:00Z"
}
```

### `PATCH /research/{research_id}` — 🔒
Update a research session. All fields optional; `null` clears an optional field. Always returns the full `ResearchRecord`.

```json
{ "topic": "...", "provider": "gemini", "status": "paused", "priority": "high", "notes": "..." }
```

### `DELETE /research/{research_id}` — 🔒
Delete a research session.

```json
{ "status": "deleted", "research_id": "4f2a1c..." }
```

### `POST /research/{research_id}/completeness/evaluate` — 🔒 ⚠️
Evaluate research completeness. Body: `topic` (required), `max_additional_iterations` (default 2, 0–10), `persist` (default true). Returns `session_id`, `overall_status`, `completeness_score`, `objectives[]`, `gaps[]`, `recommendations[]`, `additional_search_tasks[]`, `iterations_performed`, `max_iterations`.

### `GET /research/{research_id}/completeness/objectives` — 🔒 ⚠️
List objectives. Query: `status` ∈ `complete|partially_complete|insufficient_evidence|not_investigated|blocked`.

### `GET /research/{research_id}/completeness/evaluations` — 🔒 ⚠️
List evaluations. Query: `limit` (10), `offset` (0).

### `GET /research/{research_id}/completeness/evaluations/latest` — 🔒 ⚠️
Most recent evaluation. `404` if none.

### `GET /research/{research_id}/plan` — 🔒
Get proposed research plan for review (see Human Review).

### `POST /chat` — 🟢 optional auth
Single-turn chat answer from the first available provider.

```json
{ "message": "Summarize current evidence on X." }
```
→ `200` → `{ "reply": "...", "provider": "groq" }` · `503` if every provider fails.

---

## 3. Cancel & Resume

| Intent | Status | Notes |
|---|---|---|
| **Cancel** a running research | 🚧/⏳ | No HTTP endpoint exists. `cancel_run()` exists only in the service layer (`AgentHistoryService`). Until mounted, use `PATCH /research/{research_id}` to set `status: "cancelled"`. |
| **Resume** a research run | ⏳ | Not implemented. `PATCH /research/{research_id}` can move status back to `pending`/`running`, but there is no dedicated resume orchestration endpoint. |

> ⚠️ These are documented as **planned** — do not ship frontend code that depends on them.

---

## 4. Projects

### `POST /projects` — 🔒 → `201`
```json
{ "name": "Healthcare Research", "description": "...", "research_domain": "healthcare", "status": "active" }
```
Returns `ProjectRecord` (status defaults to `active`).

### `GET /projects` — 🔒
List the caller's projects.

| Query | Type | Default |
|---|---|---|
| `limit` | int | 20 |
| `offset` | int | 0 |
| `status` | ProjectStatus | — |
| `research_domain` | string | — |
| `q` | string (name/description search) | — |
| `created_after` / `created_before` | ISO-8601 | — |
| `sort` | `created_at\|updated_at\|name\|status` | `created_at` |
| `order` | `asc\|desc` | `desc` |

```json
{ "projects": [ { "project_id": "...", "owner_id": "u_123", "name": "Healthcare",
    "description": null, "research_domain": "healthcare", "status": "active",
    "created_at": "...", "updated_at": "..." } ],
  "count": 1, "pagination": { "limit": 20, "offset": 0, "total": 1, "returned": 1, "has_more": false } }
```

### `GET /projects/{project_id}` — 🔒
Get one project. `404` if not owned.

### `PATCH /projects/{project_id}` — 🔒
Update. Body = any subset of `name`, `description`, `research_domain`, `status`.

### `DELETE /projects/{project_id}` — 🔒
Delete project **and its research sessions**.

```json
{ "status": "deleted", "project_id": "..." }
```

### `POST /projects/{project_id}/research` — 🔒 → `201`
Create a research session under a project.

```json
{ "topic": "...", "provider": "groq", "priority": "normal", "notes": null }
```

### `GET /projects/{project_id}/research` — 🔒
List research sessions in a project. Query mirrors project listing plus `provider` filter and `sort` ∈ `created_at|updated_at|topic|status|priority`. Returns `ResearchListResponse { research_sessions[], count, pagination }`.

---

## 5. Agent Execution History

All four endpoints are 🔒 and owner-scoped. `research_id` is the 32-char hex id.

### `GET /research/{research_id}/agents` — 🔒
List agent executions.

| Query | Type | Default |
|---|---|---|
| `limit` | int | 20 |
| `offset` | int | 0 |
| `status` | AgentExecutionStatus | — |
| `agent_name` | string pattern `[a-z][a-z0-9_]{0,63}` | — |
| `started_after` / `started_before` | ISO-8601 | — |
| `sort` | `started_at\|agent_name\|status\|duration_ms` | `started_at` |
| `order` | `asc\|desc` | `asc` |

```json
{
  "research_id": "4f2a1c...",
  "count": 1,
  "pagination": { "limit": 20, "offset": 0, "total": 1, "returned": 1, "has_more": false },
  "executions": [
    {
      "execution_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5",
      "research_id": "4f2a1c...",
      "agent_name": "search",
      "status": "completed",
      "started_at": "2025-01-01T00:00:00Z",
      "finished_at": "2025-01-01T00:02:00Z",
      "duration_ms": 120000.0,
      "retry_count": 0,
      "error_summary": null,
      "output_reference": "reports/.../search.json"
    }
  ]
}
```

### `GET /research/{research_id}/agents/{execution_id}` — 🔒
Single execution. `404` if not found/owned.

### `GET /research/{research_id}/timeline` — 🔒
Chronological timeline. Query: `limit` (50), `offset` (0), `event_type` ∈ TimelineEventType, `agent_name`, `occurred_after`/`occurred_before`.

```json
{
  "research_id": "4f2a1c...", "count": 1,
  "pagination": { "limit": 50, "offset": 0, "total": 1, "returned": 1, "has_more": false },
  "events": [
    { "event_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c6",
      "research_id": "4f2a1c...",
      "event_type": "agent_completed", "agent_name": "search",
      "message": "Search completed.", "execution_id": "a1b2c3...",
      "occurred_at": "2025-01-01T00:02:00Z" }
  ]
}
```

### `GET /research/{research_id}/progress` — 🔒
Aggregate progress.

```json
{
  "research_id": "4f2a1c...", "status": "completed",
  "total_executions": 8, "completed_executions": 8, "failed_executions": 0,
  "running_executions": 0, "pending_executions": 0,
  "percent_complete": 100.0, "current_agent": null,
  "started_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:10:00Z",
  "latest_event": "Research report successfully generated."
}
```

---

## 6. Evidence

### Claims (🔒 ⚠️ — requires PostgreSQL)
- `GET /research/{research_id}/claims` — list claims. Query `status` ∈ `supported|partially_supported|contradicted|unverified|insufficient_context`. Returns `{ research_id, claims[], total }`.
- `GET /research/{research_id}/claims/{claim_id}` — one claim with linked evidence. `404` if not found/owned.

Claim object (from `Claim` schema):
```json
{
  "claim_id": "550e8400-...", "session_id": "4f2a1c...",
  "text": "The claim text", "subject": null, "research_question": null,
  "conclusion": null, "methodology": null, "population": null, "time_period": null,
  "evidence_ids": [], "claim_type": "factual", "status": "unverified",
  "confidence": 0.5, "supporting_evidence": [], "contradicting_evidence": [],
  "citation_ids": [], "created_at": "2025-01-01T00:00:00Z"
}
```
Evidence object:
```json
{
  "evidence_id": "550e8400-...", "claim_id": null, "source_id": null,
  "quote": "quoted passage", "url": "https://...", "stance": "supports",
  "strength": 0.8, "retrieved_at": "2025-01-01T00:00:00Z"
}
```

### Sources (🔒 ⚠️)
- `GET /research/{research_id}/sources` — list. Query: `source_type`, `publisher`, `citation_status` ∈ CitationStatus, `created_after`/`created_before` (ISO-8601 strings), `sort` ∈ `created_at|title|source_type|citation_status`, `order`, `limit`, `offset`. Returns `{ research_id, sources[], total, limit, offset }`.
- `GET /sources/{source_id}` — one source.
- `GET /research/{research_id}/sources/statistics` — counts grouped by type/publisher/citation status.
- `PATCH /sources/{source_id}/status?citation_status=verified` — update citation validation status.
- `DELETE /sources/{source_id}` — soft-delete → `{ "status": "deleted", "source_id": "..." }`.

Source object (from `ResearchSource` schema):
```json
{
  "source_id": "550e8400-...", "session_id": "4f2a1c...",
  "title": "Title", "url": "https://...", "doi": null, "source_name": "arXiv",
  "source_type": "academic", "snippet": "...", "publisher": null, "author": null,
  "venue": null, "methodology": null, "peer_reviewed": null, "references_available": null,
  "source_nature": null, "published_at": null, "retrieved_at": "...",
  "credibility_score": 0.9, "credibility_confidence": 0.8, "credibility_label": "high",
  "language": "en", "metadata": {}
}
```

### Contradictions & Verification results (🔒 ⚠️)
Verification results and contradictions are returned **nested inside claim responses** (`supporting_evidence`, `contradicting_evidence`, `contradicted` status). There are **no standalone contradiction endpoints** in the current API — contradictions are surfaced via claims. Contradiction categories: `direct_contradiction | partial_contradiction | contextual_difference | methodological_difference | temporal_difference | no_contradiction | insufficient_information`.

---

## 7. Reports

### Legacy report artifact routes
- `GET /reports/{filename:path}` — 🔒/🟢 download a generated report (`FileResponse`). Also accepts `?share=<token>` or `?token=<jwt>` for read-only share links. Response headers include `X-Read-Only: true` for share links.
- `GET /reports/{filename:path}/share` — 🔒 generate share token → `{ filename, share_token, share_url }`.
- `POST /export` — 🟢 export draft content. Body: `{ content, topic, format, citation_style }` where `format` ∈ `pdf|docx|md|txt`, `citation_style` ∈ `apa|mla|harvard`. Returns a binary file download (`FileResponse`).

### Report management (versioned) — all 🔒
- `GET /research/{research_id}/reports` — list reports for a run. Query: `limit`, `offset`, `status` ∈ ReportVersionStatus, `sort` ∈ `created_at|updated_at|title|status|version`, `order`.

```json
{
  "research_id": "4f2a1c...", "count": 1,
  "pagination": { "limit": 20, "offset": 0, "total": 1, "returned": 1, "has_more": false },
  "reports": [
    { "report_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c7",
      "research_id": "4f2a1c...", "owner_id": "u_123",
      "title": "Impact of LLMs", "format": "md", "version": 1,
      "status": "draft", "critic_score": 8, "verification_status": "unverified",
      "storage_reference": "reports/impact-llms.md",
      "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:10:00Z" }
  ]
}
```

- `GET /reports/{report_id}` — report metadata (`ReportRecord`).
- `GET /reports/{report_id}/versions` — list immutable versions → `{ report_id, versions[], count }`.
- `GET /reports/{report_id}/versions/{version_id}` — one version **including Markdown body** (`ReportVersionDetail` — adds `content_markdown`).
- `POST /reports/{report_id}/export` — export a version. Body:
  ```json
  { "format": "pdf", "version_id": null, "citation_style": "apa" }
  ```
  Returns a binary file download (`FileResponse`, `Content-Disposition: attachment`).
- `POST /reports/{report_id}/versions` — **`201`** create a new immutable version:
  ```json
  { "content_markdown": "...", "status": "draft", "critic_score": 8,
    "verification_status": "unverified", "change_summary": "..." }
  ```
  An approved version is never overwritten — a new version is always a new row.
- `DELETE /reports/{report_id}` — delete a report and all its versions → `{ "status": "deleted", "report_id": "..." }`.

### Report version statuses
`draft | reviewing | revision_requested | approved | published`

### Human review of reports (🔒)
See the Human Review section below.

---

## 8. Documents

All 🔒 and owner-scoped.

- `POST /documents/upload` — **`201`** multipart/form-data. Fields: `file` (PDF/DOCX/TXT/MD), optional `project_id` (UUID), optional `research_id` (32-char hex). Returns `DocumentRecord` with `processing_status: "uploaded"`.
- `GET /documents` — list. Query: `limit`, `offset`, `project_id`, `research_id`, `status` ∈ DocumentProcessingStatus, `q` (filename search), `created_after`/`created_before`, `sort` ∈ `created_at|original_filename|size_bytes|processing_status`, `order`.
- `GET /documents/{document_id}` — metadata.
- `GET /documents/{document_id}/status` — processing status → `{ document_id, processing_status, error_code, error_message, updated_at }`.
- `DELETE /documents/{document_id}` — delete metadata + stored bytes → `{ "status": "deleted", "document_id": "..." }`.

Document processing statuses: `uploaded | pending | extracting | chunking | embedding | indexed | failed`

DocumentRecord:
```json
{
  "document_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c8",
  "owner_id": "u_123", "project_id": null, "research_id": null,
  "original_filename": "paper.pdf", "mime_type": "application/pdf",
  "size_bytes": 204800, "sha256": "<64 hex>", "storage_key": "docs/paper.pdf",
  "processing_status": "uploaded", "error_code": null, "error_message": null,
  "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z"
}
```

---

## 9. Human Review (plans & reports)

All 🔒 and owner-scoped.

### Research plan review
- `GET /research/{research_id}/plan` → `ResearchPlanReviewRecord`
- `POST /research/{research_id}/plan/decision` — body `{ "decision": "approved|changes_requested|rejected", "comment": "..." }` (comment required on reject/changes).
- `POST /research/{research_id}/plan/approve` — body optional `{ "comment": "..." }`
- `POST /research/{research_id}/plan/reject` — body optional `{ "comment": "..." }` (comment required)
- `POST /research/{research_id}/plan/request-changes` — body `{ "comment": "...", "title": null, "objective": null, "query": null }`
- `POST /research/{research_id}/plan/comments` — body `{ "comment": "...", "kind": "general", "claim_ids": [] }` → `ReviewAuditRecord`
- `GET /research/{research_id}/reviews` → `ReviewHistoryResponse { research_id, entries[], count }`

`ResearchPlanReviewRecord`:
```json
{
  "plan_id": "550e8400-...", "session_id": "4f2a1c...", "title": "Plan",
  "objective": null, "query": "research question", "token_budget": 0,
  "agent_count": 1, "status": "pending", "review_note": null, "reviewed_by": null,
  "reviewed_at": null, "details": {}, "created_at": "...", "updated_at": "..."
}
```

### Report review
- `GET /reports/{report_id}/reviews` → `ReportReviewRecord`
- `POST /reports/{report_id}/reviews/submit` — submit report for human review
- `POST /reports/{report_id}/reviews` — body `{ "decision": "approved|changes_requested|rejected", "comment": "..." }`
- `POST /reports/{report_id}/reviews/decision` — same body
- `POST /reports/{report_id}/reviews/comments` — body `{ "comment": "...", "kind": "general|correction|unsupported_claim|additional_research", "claim_ids": [] }`
- `POST /reports/{report_id}/reviews/revise` — body optional `{ "comment": "..." }`
- `GET /reports/{report_id}/reviews/history` → `ReviewHistoryResponse`

`ReportReviewRecord`:
```json
{
  "report_id": "550e8400-...", "session_id": "4f2a1c...", "title": "Report",
  "status": "reviewing", "critic_score": 8, "latest_decision": null,
  "latest_comment": null, "reviewer_id": null, "decided_at": null,
  "created_at": "...", "updated_at": "..."
}
```

---

## 10. Memory

All 🔒 and owner-scoped. Requires PostgreSQL + ChromaDB for semantic features.

- `GET /memory/search?q=...&k=6&project_id=&session_id=&content_type=&verified_only=false` — semantic search.
- `GET /memory` — list. Query: `project_id`, `session_id`, `verification_status`, `content_type`, `limit` (100), `offset` (0).
- `POST /memory` — create. Query/form params: `content` (required), `content_type` (default `finding`), `verification_status` (default `unverified`), `confidence`, `project_id`, `session_id`, `source_id`.
- `GET /memory/{memory_id}` — one record (`memory_id` is UUID).
- `PUT /memory/{memory_id}` — update (`content`, `verification_status`, `confidence`; verified findings cannot be overwritten).
- `DELETE /memory/{memory_id}` — soft-delete.

---

## 11. Usage & Cost

> 🚧 **Implemented but not yet mounted.** `backend/app/api/usage.py` defines all four routes and `get_usage_service` exists in the container, but `usage.router` is **not registered** in `backend/app/api/__init__.py`. These endpoints are not reachable until wiring is added.

All 🔒. **Cost fields are estimates** — each response carries `estimated: true`.

- `GET /usage/summary` — dashboard for the current user:

```json
{
  "user_id": "u_123", "total_requests": 42, "total_input_tokens": 120000,
  "total_output_tokens": 8000, "total_tokens": 128000,
  "total_estimated_cost_usd": 0.042, "provider_failure_count": 1,
  "fallback_count": 0,
  "per_research": [ { "research_id": "...", "requests": 42, "total_tokens": 128000, "estimated_cost_usd": 0.042 } ],
  "per_provider": [ { "provider": "groq", "requests": 42, "total_tokens": 128000, "estimated_cost_usd": 0.042, "failures": 1 } ],
  "per_agent": [ { "agent_name": "search", "requests": 6, "total_tokens": 20000, "estimated_cost_usd": 0.006 } ],
  "estimated": true
}
```

- `GET /usage/research/{research_id}` — per-session usage → `ResearchUsageResponse` (includes `usage[]` records, `per_provider`, `per_agent`, `failures`, `fallbacks`).
- `GET /usage/providers` — → `ProvidersUsageResponse { user_id, providers[], estimated }` where each `ProviderAggregate` = `{ provider, requests, input_tokens, output_tokens, total_tokens, estimated_cost_usd, failures }`.
- `GET /usage/models` — → `ModelsUsageResponse { user_id, models[], estimated }` where each `ModelAggregate` adds `model`.

Usage record (`UsageRecord`):
```json
{
  "usage_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c9",
  "research_id": "4f2a1c...", "user_id": "u_123", "agent_name": "search",
  "provider": "groq", "model": "llama-3.3-70b",
  "input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500,
  "duration_ms": 4500.0, "estimated_cost_usd": 0.0009, "request_status": "success",
  "occurred_at": "2025-01-01T00:00:00Z"
}
```

---

## 12. Notifications

⏳ **Planned — not implemented.** There are **no notification endpoints** in the current API (searched `backend/app/api/` — no `notif` matches). Recommend one of:
- `GET /notifications` — list (with `limit`/`offset`, `read` filter)
- `POST /notifications/{id}/read` (or `PATCH /notifications/{id}`) — mark as read
- `DELETE /notifications/{id}` — delete

These are **proposed** contracts below — do not implement frontend against them until the backend routes exist.

---

## 13. Planned / Not-implemented endpoints (summary)

| Feature | Status |
|---|---|
| `GET /usage/*` | 🚧 implemented, not mounted |
| Cancel research (`POST /research/{id}/cancel`) | ⏳ planned |
| Resume research (`POST /research/{id}/resume`) | ⏳ planned |
| Notifications (`GET/POST/DELETE /notifications`) | ⏳ planned |
| Standalone contradiction endpoints | ⏳ planned (currently nested in claims) |
| Document raw-bytes download | ⏳ planned (only metadata + blob via report routes today) |
