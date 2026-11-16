# Research AI Quest — SSE Event Contract

Server-Sent Events (SSE) protocol for research progress and event streaming.

---

## 1. Connection URLs

| Purpose | Endpoint | Auth |
|---|---|---|
| Start a run (server emits stream) | `POST /research` | 🟢 optional |
| Reconnectable run stream | `GET /research/{research_id}/events` | 🔒 required |

Both respond `200` with `Content-Type: text/event-stream`.

---

## 2. Authentication approach

The stream endpoint (`GET /research/{research_id}/events`) requires a valid Supabase JWT. Because the browser-native `EventSource` API cannot set request headers, the token is passed as a query parameter:

- `GET /research/{research_id}/events?token=<jwt>` — primary.
- The JWT is also accepted via the standard `Authorization: Bearer <jwt>` header for non-EventSource clients (e.g. `fetch` with a `ReadableStream`).
- `GET /research/{research_id}/events` uses the same dependency as other protected routes; identity always comes from the verified token, never from a research id.

Auth failures return `401` (`ApiError`) **before** the stream opens. Ownership failures return `404`.

---

## 3. Frame format

Each frame is standard SSE:

```
retry: 3000
id: <sequence_number>
event: <legacy_event_name>
data: <json>
```

- `retry: 3000` is sent once at connection start → client should reconnect after ~3s on drop.
- `id:` carries the event **sequence number**; clients must echo it back via `Last-Event-ID` (header) or `?last_event_id=` (query) on reconnect.
- `event:` and the `data.event` field both carry the *legacy* event name so existing clients keep working (see table below).
- `data:` is a single JSON object. Non-data frames (`: keepalive`) should be ignored.

---

## 4. Event names

### Legacy event name (the `event:` field and `data.event`)
| Legacy name | Meaning |
|---|---|
| `progress` | Generic progress / lifecycle update |
| `agent_start` | An agent just started |
| `agent_done` | An agent completed |
| `complete` | Research finished successfully (terminal) |
| `error` | Failure (terminal if it's a research/agent failure) |

### Canonical event type (`data.event_type`)
The canonical type is always present in `data.event_type`:

| Canonical type | Legacy map | Terminal? |
|---|---|---|
| `research.queued` | `progress` | no |
| `research.started` | `progress` | no |
| `agent.started` | `agent_start` | no |
| `agent.progress` | `progress` | no |
| `agent.completed` | `agent_done` | no |
| `agent.failed` | `error` | no |
| `source.discovered` | `progress` | no |
| `claim.extracted` | `progress` | no |
| `claim.verified` | `progress` | no |
| `contradiction.detected` | `progress` | no |
| `report.generated` | `progress` | no |
| `review.required` | `progress` | no |
| `research.completed` | `complete` | **yes** |
| `research.failed` | `error` | **yes** |

Terminal events close the stream from the server side.

---

## 5. Event payload

Every `data:` payload has this envelope:

```json
{
  "event": "agent_start",
  "message": "Agent active: search",
  "agent": "search",
  "data": {
    "agent": "search",
    "event_id": "3f9a...",
    "research_id": "4f2a1c9b3e8d4a6f0c1b2a3d4e5f607",
    "event_type": "agent.started",
    "sequence_number": 3,
    "timestamp": "2025-01-01T09:06:01Z"
  }
}
```

| Envelope field | Type | Notes |
|---|---|---|
| `event` | string | legacy event name (matches the `event:` field) |
| `message` | string | human-readable, redacted, ≤2000 chars |
| `agent` | string \| absent | present when the event is agent-scoped |
| `data` | object | canonical fields + provider-specific extras |

Stable `data` fields:

| `data` field | Type | Notes |
|---|---|---|
| `event_id` | string | stable UUID-ish id |
| `research_id` | string | 32-char hex run id |
| `event_type` | string | canonical type (table above) |
| `sequence_number` | int | monotonically increasing per run; use for replay |
| `timestamp` | string | ISO-8601 UTC (`2025-01-01T00:00:00Z`) |

Extra `data` fields vary by event type (e.g. `agent`, `progress`, `count`, `message`). `user_id` is deliberately **stripped** from streamed payloads. Treat unknown `data` keys as opaque/optional.

### Example: `complete` (terminal)
```json
{
  "event": "complete",
  "message": "Research report successfully generated.",
  "data": {
    "event_type": "research.completed",
    "research_id": "4f2a1c...",
    "sequence_number": 12,
    "timestamp": "2025-01-01T09:40:00Z",
    "report_id": "c1b2c3...",
    "saved_report_path": "reports/4f2a1c/report.md"
  }
}
```

### Example: `error` (terminal for agent/research failure)
```json
{
  "event": "error",
  "message": "Agent failed: search",
  "agent": "search",
  "data": {
    "event_type": "agent.failed",
    "research_id": "4f2a1c...",
    "sequence_number": 5,
    "timestamp": "2025-01-01T09:10:00Z",
    "error_code": "search_provider_unavailable"
  }
}
```

> ⚠️ The stream also sends two **special** non-canonical frames:
> - `event: progress` with `data.replay_truncated: true` — some earlier events expired from the store.
> - `event: error` with `data.error_code: "stream_error"` — transport/stream interruption (not a research failure).

---

## 6. Reconnection & Last-Event-ID

- Every event carries a `sequence_number`.
- On reconnect, send the last received sequence number:
  - Header: `Last-Event-ID: <n>` (preferred), or
  - Query: `?last_event_id=<n>`.
- The server replays events with sequence > n, then streams live. Gaps due to retention are flagged with `replay_truncated: true`.
- Use `retry: 3000` for reconnect timing. If the stream is idle, the server sends `: keepalive` comments to keep the connection alive.

**Client reconnection algorithm:**
```
lastSequence = 0
while (streamActive) {
  try {
    es = new EventSource(`/research/{id}/events?token=...&last_event_id=${lastSequence}`)
    es.onmessage / addEventListener('progress'|'agent_start'|..., e => {
      const frame = JSON.parse(e.data)
      lastSequence = Math.max(lastSequence, frame.data.sequence_number)
      if (frame.event === 'complete' || (frame.event === 'error' && terminal)) { es.close(); return }
    })
  } catch { await sleep(3000) }   // honor retry
}
```

---

## 7. Error handling

| Situation | Server behavior | Client guidance |
|---|---|---|
| No/invalid token | `401` ApiError before stream opens | redirect to sign-in |
| Unauthorized research | `404` ApiError before stream opens | treat as not-found |
| Stream transport unavailable | `503` before stream opens | show "stream unavailable" state |
| Mid-stream transport failure | `event: error`, `data.error_code: "stream_error"` | reconnect after 3s |
| Research/agent failure | `event: error`, canonical `event_type: ...failed` | mark run failed; stop reconnecting |
| Expired earlier events | `data.replay_truncated: true` | inform user some progress is missing |

Rule: **only reconnect on transport-level errors and non-terminal frames.** Stop on `complete` or terminal `error`.

---

## 8. REST fallback (source of truth)

The SSE stream is a live projection; the REST endpoints are authoritative and always usable:
- `GET /research/{research_id}/progress` — current aggregate state.
- `GET /research/{research_id}/timeline` — full chronological history.
- `GET /research/{research_id}/agents` — execution records with status/timing.
- `GET /research/{research_id}/reports` — generated reports.

Use REST for page loads and after a reconnect to reconcile any missed frames.
