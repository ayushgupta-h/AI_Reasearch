# Research AI Quest — API Request/Response Examples

Realistic, copy-pasteable examples for every major workflow. All examples use `http://127.0.0.1:8000` as base URL. Replace `{JWT}` with a valid Supabase access token.

> **Auth header:** `Authorization: Bearer {JWT}` on every 🔒 route.

---

## 0. Authentication

### Get Supabase client config
```bash
curl -s http://127.0.0.1:8000/auth/config
```
```json
{
  "supabaseUrl": "https://vxqalflbzpjkvmtbbqrv.supabase.co",
  "supabaseAnonKey": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

### Get current user profile (🔒)
```bash
curl -s -H "Authorization: Bearer $JWT" http://127.0.0.1:8000/auth/me
```
```json
{
  "status": "authenticated",
  "user": {
    "user_id": "u_12345",
    "sub": "01234567-...",
    "email": "ada@example.com",
    "role": "authenticated",
    "user_metadata": { "full_name": "Ada Lovelace", "avatar_url": "https://..." }
  }
}
```

### 401 when missing token
```bash
curl -s -w "\nHTTP %{http_code}\n" http://127.0.0.1:8000/auth/me
```
```json
{
  "detail": "Authentication required. Please sign in with Google.",
  "error_code": "authentication_required",
  "request_id": "req_...",
  "details": null
}
```
`HTTP 401` + header `WWW-Authenticate: Bearer`.

---

## 1. Create a project

```bash
curl -s -X POST http://127.0.0.1:8000/projects \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"name":"Climate Policy","research_domain":"climate","description":"Cross-source review"}'
```
`HTTP 201`
```json
{
  "project_id": "550e8400-e29b-41d4-a716-446655440000",
  "owner_id": "u_12345",
  "name": "Climate Policy",
  "description": "Cross-source review",
  "research_domain": "climate",
  "status": "active",
  "created_at": "2025-01-01T09:00:00Z",
  "updated_at": "2025-01-01T09:00:00Z"
}
```

---

## 2. Create a research session inside a project

```bash
curl -s -X POST http://127.0.0.1:8000/projects/550e8400-e29b-41d4-a716-446655440000/research \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"topic":"How do carbon taxes affect emissions?","provider":"groq","priority":"high"}'
```
`HTTP 201`
```json
{
  "research_id": "4f2a1c9b3e8d4a6f0c1b2a3d4e5f607",
  "project_id": "550e8400-e29b-41d4-a716-446655440000",
  "owner_id": "u_12345",
  "topic": "How do carbon taxes affect emissions?",
  "provider": "groq",
  "status": "pending",
  "priority": "high",
  "notes": null,
  "created_at": "2025-01-01T09:05:00Z",
  "updated_at": "2025-01-01T09:05:00Z"
}
```

---

## 3. Start a research run (SSE)

```bash
curl -N -X POST http://127.0.0.1:8000/research \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"topic":"How do carbon taxes affect emissions?","provider":"groq","max_revisions":2,"approval_threshold":8}'
```
`HTTP 200` → `Content-Type: text/event-stream`. See `SSE_EVENTS.md` for frame anatomy. First frames:

```
retry: 3000

id: 1
event: progress
data: {"event":"progress","message":"Research queued.","data":{"event_type":"research.queued","research_id":"4f2a1c...","sequence_number":1,"timestamp":"2025-01-01T09:06:00Z"}}

id: 2
event: agent_start
data: {"event":"agent_start","message":"Agent active: strategist","agent":"strategist","data":{"agent":"strategist","event_type":"agent.started","research_id":"4f2a1c...","sequence_number":2,"timestamp":"2025-01-01T09:06:01Z"}}
```

---

## 4. List agent executions

```bash
curl -s "http://127.0.0.1:8000/research/4f2a1c9b3e8d4a6f0c1b2a3d4e5f607/agents?limit=5&status=completed" \
  -H "Authorization: Bearer $JWT"
```
```json
{
  "research_id": "4f2a1c9b3e8d4a6f0c1b2a3d4e5f607",
  "count": 2,
  "pagination": { "limit": 5, "offset": 0, "total": 2, "returned": 2, "has_more": false },
  "executions": [
    {
      "execution_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5",
      "research_id": "4f2a1c9b3e8d4a6f0c1b2a3d4e5f607",
      "agent_name": "strategist",
      "status": "completed",
      "started_at": "2025-01-01T09:06:01Z",
      "finished_at": "2025-01-01T09:06:03Z",
      "duration_ms": 2000.0,
      "retry_count": 0,
      "error_summary": null,
      "output_reference": "reports/4f2a1c/strategist.json"
    },
    {
      "execution_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c6",
      "research_id": "4f2a1c9b3e8d4a6f0c1b2a3d4e5f607",
      "agent_name": "search",
      "status": "completed",
      "started_at": "2025-01-01T09:06:04Z",
      "finished_at": "2025-01-01T09:08:00Z",
      "duration_ms": 116000.0,
      "retry_count": 0,
      "error_summary": null,
      "output_reference": "reports/4f2a1c/search.json"
    }
  ]
}
```

---

## 5. Get progress

```bash
curl -s http://127.0.0.1:8000/research/4f2a1c9b3e8d4a6f0c1b2a3d4e5f607/progress \
  -H "Authorization: Bearer $JWT"
```
```json
{
  "research_id": "4f2a1c9b3e8d4a6f0c1b2a3d4e5f607",
  "status": "running",
  "total_executions": 8,
  "completed_executions": 3,
  "failed_executions": 0,
  "running_executions": 1,
  "pending_executions": 4,
  "percent_complete": 43.75,
  "current_agent": "reader",
  "started_at": "2025-01-01T09:06:01Z",
  "updated_at": "2025-01-01T09:09:00Z",
  "latest_event": "Agent active: reader"
}
```

---

## 6. Timeline

```bash
curl -s "http://127.0.0.1:8000/research/4f2a1c9b3e8d4a6f0c1b2a3d4e5f607/timeline?limit=10" \
  -H "Authorization: Bearer $JWT"
```
```json
{
  "research_id": "4f2a1c9b3e8d4a6f0c1b2a3d4e5f607",
  "count": 3,
  "pagination": { "limit": 10, "offset": 0, "total": 3, "returned": 3, "has_more": false },
  "events": [
    { "event_id": "b1b2c3d4e5f6a7b8c9d0e1f2a3b4c0",
      "research_id": "4f2a1c9b3e8d4a6f0c1b2a3d4e5f607",
      "event_type": "research_created", "agent_name": null,
      "message": "Research created.", "execution_id": null,
      "occurred_at": "2025-01-01T09:05:00Z" },
    { "event_id": "b1b2c3d4e5f6a7b8c9d0e1f2a3b4c1",
      "research_id": "4f2a1c9b3e8d4a6f0c1b2a3d4e5f607",
      "event_type": "agent_started", "agent_name": "strategist",
      "message": "Agent started: strategist", "execution_id": "a1b2c3...",
      "occurred_at": "2025-01-01T09:06:01Z" }
  ]
}
```

---

## 7. Claims & evidence

```bash
curl -s "http://127.0.0.1:8000/research/4f2a1c9b3e8d4a6f0c1b2a3d4e5f607/claims?status=supported" \
  -H "Authorization: Bearer $JWT"
```
```json
{
  "research_id": "4f2a1c9b3e8d4a6f0c1b2a3d4e5f607",
  "total": 1,
  "claims": [
    {
      "claim_id": "550e8400-e29b-41d4-a716-446655440001",
      "session_id": "4f2a1c9b3e8d4a6f0c1b2a3d4e5f607",
      "text": "Carbon taxes reduce emissions in the transport sector.",
      "claim_type": "causal",
      "status": "supported",
      "confidence": 0.85,
      "supporting_evidence": [
        {
          "evidence_id": "550e8400-e29b-41d4-a716-446655440002",
          "source_id": "550e8400-e29b-41d4-a716-446655440010",
          "quote": "Emissions fell 12% after the tax was introduced.",
          "url": "https://example.org/paper",
          "stance": "supports",
          "strength": 0.9,
          "retrieved_at": "2025-01-01T09:07:00Z"
        }
      ],
      "contradicting_evidence": [],
      "evidence_ids": ["550e8400-e29b-41d4-a716-446655440002"],
      "citation_ids": [],
      "created_at": "2025-01-01T09:07:30Z"
    }
  ]
}
```

---

## 8. Sources

```bash
curl -s "http://127.0.0.1:8000/research/4f2a1c9b3e8d4a6f0c1b2a3d4e5f607/sources?source_type=academic&limit=5" \
  -H "Authorization: Bearer $JWT"
```
```json
{
  "research_id": "4f2a1c9b3e8d4a6f0c1b2a3d4e5f607",
  "total": 1,
  "limit": 5,
  "offset": 0,
  "sources": [
    {
      "source_id": "550e8400-e29b-41d4-a716-446655440010",
      "session_id": "4f2a1c9b3e8d4a6f0c1b2a3d4e5f607",
      "title": "Carbon Taxes and Emissions",
      "url": "https://doi.org/10.1000/xyz123",
      "doi": "10.1000/xyz123",
      "source_name": "arXiv",
      "source_type": "academic",
      "publisher": "Elsevier",
      "author": "J. Smith",
      "peer_reviewed": true,
      "credibility_score": 0.95,
      "credibility_confidence": 0.9,
      "credibility_label": "high",
      "language": "en",
      "retrieved_at": "2025-01-01T09:06:30Z",
      "metadata": {}
    }
  ]
}
```

---

## 9. Reports

### List reports for a research run
```bash
curl -s "http://127.0.0.1:8000/research/4f2a1c9b3e8d4a6f0c1b2a3d4e5f607/reports" \
  -H "Authorization: Bearer $JWT"
```
```json
{
  "research_id": "4f2a1c9b3e8d4a6f0c1b2a3d4e5f607",
  "count": 1,
  "pagination": { "limit": 20, "offset": 0, "total": 1, "returned": 1, "has_more": false },
  "reports": [
    {
      "report_id": "c1b2c3d4e5f6a7b8c9d0e1f2a3b4c1",
      "research_id": "4f2a1c9b3e8d4a6f0c1b2a3d4e5f607",
      "owner_id": "u_12345",
      "title": "Carbon Taxes and Emissions",
      "format": "md",
      "version": 1,
      "status": "approved",
      "critic_score": 9,
      "verification_status": "verified",
      "storage_reference": "reports/4f2a1c/carbon-taxes.md",
      "created_at": "2025-01-01T09:30:00Z",
      "updated_at": "2025-01-01T09:45:00Z"
    }
  ]
}
```

### Get report versions
```bash
curl -s http://127.0.0.1:8000/reports/c1b2c3d4e5f6a7b8c9d0e1f2a3b4c1/versions \
  -H "Authorization: Bearer $JWT"
```
```json
{
  "report_id": "c1b2c3d4e5f6a7b8c9d0e1f2a3b4c1",
  "count": 2,
  "versions": [
    {
      "version_id": "d1b2c3d4e5f6a7b8c9d0e1f2a3b4c1",
      "report_id": "c1b2c3d4e5f6a7b8c9d0e1f2a3b4c1",
      "version": 1,
      "status": "approved",
      "critic_score": 9,
      "verification_status": "verified",
      "storage_reference": "reports/4f2a1c/carbon-taxes.md",
      "change_summary": null,
      "created_at": "2025-01-01T09:30:00Z"
    },
    {
      "version_id": "d1b2c3d4e5f6a7b8c9d0e1f2a3b4c2",
      "report_id": "c1b2c3d4e5f6a7b8c9d0e1f2a3b4c1",
      "version": 2,
      "status": "draft",
      "critic_score": null,
      "verification_status": "unverified",
      "storage_reference": "reports/4f2a1c/carbon-taxes.v2.md",
      "change_summary": "Added policy comparison section.",
      "created_at": "2025-01-01T10:00:00Z"
    }
  ]
}
```

### Get a version with its Markdown body
```bash
curl -s http://127.0.0.1:8000/reports/c1b2c3d4e5f6a7b8c9d0e1f2a3b4c1/versions/d1b2c3d4e5f6a7b8c9d0e1f2a3b4c1 \
  -H "Authorization: Bearer $JWT"
```
```json
{
  "version_id": "d1b2c3d4e5f6a7b8c9d0e1f2a3b4c1",
  "report_id": "c1b2c3d4e5f6a7b8c9d0e1f2a3b4c1",
  "version": 1,
  "status": "approved",
  "critic_score": 9,
  "verification_status": "verified",
  "storage_reference": "reports/4f2a1c/carbon-taxes.md",
  "change_summary": null,
  "created_at": "2025-01-01T09:30:00Z",
  "content_markdown": "# Carbon Taxes and Emissions\n\n## Abstract\n..."
}
```

### Create a new version (approved version is never overwritten)
```bash
curl -s -X POST http://127.0.0.1:8000/reports/c1b2c3d4e5f6a7b8c9d0e1f2a3b4c1/versions \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"content_markdown":"# v2 ...","status":"draft","change_summary":"Added comparison"}'
```
`HTTP 201` → full `ReportVersionDetail`.

### Export a version as PDF (binary download)
```bash
curl -s -o report.pdf -w "%{http_code} %{content_type} %{size_download}\n" \
  -X POST http://127.0.0.1:8000/reports/c1b2c3d4e5f6a7b8c9d0e1f2a3b4c1/export \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"format":"pdf","version_id":"d1b2c3d4e5f6a7b8c9d0e1f2a3b4c1","citation_style":"apa"}'
```
```
200 application/pdf <bytes>
```
Header: `Content-Disposition: attachment; filename="<name>.pdf"`.

### Delete a report and its versions
```bash
curl -s -X DELETE http://127.0.0.1:8000/reports/c1b2c3d4e5f6a7b8c9d0e1f2a3b4c1 \
  -H "Authorization: Bearer $JWT"
```
```json
{ "status": "deleted", "report_id": "c1b2c3d4e5f6a7b8c9d0e1f2a3b4c1" }
```

---

## 10. Report review workflow

### Submit report for review
```bash
curl -s -X POST http://127.0.0.1:8000/reports/c1b2c3d4e5f6a7b8c9d0e1f2a3b4c1/reviews/submit \
  -H "Authorization: Bearer $JWT"
```
```json
{
  "report": {
    "report_id": "c1b2c3d4e5f6a7b8c9d0e1f2a3b4c1",
    "session_id": "4f2a1c9b3e8d4a6f0c1b2a3d4e5f607",
    "title": "Carbon Taxes and Emissions",
    "status": "reviewing",
    "critic_score": 9,
    "latest_decision": null,
    "latest_comment": null,
    "reviewer_id": null,
    "decided_at": null,
    "created_at": "2025-01-01T09:30:00Z",
    "updated_at": "2025-01-01T09:46:00Z"
  },
  "audit": null,
  "workflow_resumed": false
}
```

### Approve the report
```bash
curl -s -X POST http://127.0.0.1:8000/reports/c1b2c3d4e5f6a7b8c9d0e1f2a3b4c1/reviews/decision \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"decision":"approved","comment":"Looks good."}'
```

### Request changes on a plan
```bash
curl -s -X POST http://127.0.0.1:8000/research/4f2a1c9b3e8d4a6f0c1b2a3d4e5f607/plan/request-changes \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"comment":"Add a time horizon.","query":"..."}'
```

---

## 11. Documents

### Upload
```bash
curl -s -X POST http://127.0.0.1:8000/documents/upload \
  -H "Authorization: Bearer $JWT" \
  -F "file=@paper.pdf;type=application/pdf" \
  -F "research_id=4f2a1c9b3e8d4a6f0c1b2a3d4e5f607"
```
`HTTP 201` → `DocumentRecord` (see API_ENDPOINTS.md) with `processing_status: "uploaded"`.

### Check processing status
```bash
curl -s http://127.0.0.1:8000/documents/a1b2c3d4e5f6a7b8c9d0e1f2a3b4c8/status \
  -H "Authorization: Bearer $JWT"
```
```json
{
  "document_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c8",
  "processing_status": "indexed",
  "error_code": null,
  "error_message": null,
  "updated_at": "2025-01-01T09:12:00Z"
}
```

### Delete
```bash
curl -s -X DELETE http://127.0.0.1:8000/documents/a1b2c3d4e5f6a7b8c9d0e1f2a3b4c8 \
  -H "Authorization: Bearer $JWT"
```
```json
{ "status": "deleted", "document_id": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c8" }
```

---

## 12. Ownership & authorization (all 🔒 routes)

### Cross-user access is denied with 404 (not 403)
```bash
curl -s -w "\nHTTP %{http_code}\n" \
  http://127.0.0.1:8000/research/4f2a1c9b3e8d4a6f0c1b2a3d4e5f607 \
  -H "Authorization: Bearer $OTHER_USER_JWT"
```
```json
{
  "detail": "Research session not found",
  "error_code": "not_found",
  "request_id": "req_...",
  "details": null
}
```
`HTTP 404` — ownership failures intentionally return `404` (not `403`) to avoid leaking resource existence.

---

## 13. Pagination (shared)

Every list endpoint accepts `limit` (max 100) and `offset`. Response `pagination` object:

| Field | Meaning |
|---|---|
| `limit` | page size requested |
| `offset` | items skipped |
| `total` | total matching items |
| `returned` | items in this page |
| `has_more` | true iff `offset + returned < total` |
