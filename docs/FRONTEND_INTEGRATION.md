# Research AI Quest — Frontend Integration Guide

How the React frontend should talk to the FastAPI backend without guessing.

---

## 1. Base URL & environment

The frontend reads the API base from `localStorage["questApi"]` (default `http://127.0.0.1:8000`). Never hardcode the base URL in components — read it from a shared config module:

```ts
const API_BASE = (localStorage.getItem('questApi') || 'http://127.0.0.1:8000').replace(/\/$/, '');
```

---

## 2. Authentication

Authentication is **Supabase-managed** and bootstrapped client-side. The backend only *verifies* issued JWTs and never issues tokens itself.

### Bootstrapping
1. Fetch `GET /auth/config` → `{ supabaseUrl, supabaseAnonKey }` (public, no auth).
2. Create the Supabase client with those values (or the verified defaults).
3. Trigger Google OAuth via the Supabase SDK (`signInWithOAuth({ provider: 'google', options: { redirectTo } })`).
4. Handle the redirect callback (access token in URL hash, or PKCE `code` exchange) and store the session.

### Token expectations
- The backend expects a **Supabase JWT access token**.
- Send it as an **`Authorization: Bearer <token>`** header on every protected request.
- Identity is derived **only** from verified JWT claims — the backend never trusts `owner_id`/`user_id` from request bodies or query params.
- The `sub` claim is the user identity; the backend exposes it as `user_id` in `GET /auth/me`.

### Recommended API client helper
```ts
async function api(path: string, init: RequestInit = {}) {
  const token = getAccessToken();               // from your auth store
  const headers = new Headers(init.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    // err = { detail, error_code, request_id, details, timestamp }
    throw new ApiError(err, res.status);
  }
  return res;
}
```

### Unauthorized handling
Backend returns `401` with body:
```json
{ "detail": "Authentication required. Please sign in with Google.",
  "error_code": "authentication_required", "request_id": "...", "details": null }
```
and header `WWW-Authenticate: Bearer`. On `401` → redirect to your sign-in flow. Token refresh should be handled by the Supabase SDK (`autoRefreshToken`), not by the backend.

---

## 3. User identity

After login, call `GET /auth/me` to get the current user. The `user` object is the verified JWT payload; typical fields: `sub`, `email`, `role`, `user_metadata`, `app_metadata`. Use `user_id`/`sub` as the display/user key in the UI.

---

## 4. Response envelope & errors

- Most read endpoints return **flat payloads** (not wrapped). Do not unwrap a `data` key unless you opted into `?envelope=true`.
- **Errors** are always `ApiError` (see above) → `{ detail, error_code, request_id, details, timestamp }`.
- On any non-2xx, surface `detail` to the user and log `request_id` (useful for cross-referencing backend logs).
- **Ownership:** a resource you don't own returns `404` (not `403`). Treat `404` as "not found or not yours".

---

## 5. Research workflow (recommended sequence)

1. **Create a project** → `POST /projects` (returns `ProjectRecord` with `project_id`).
2. **Create a research session** → `POST /projects/{project_id}/research` (returns `ResearchRecord` with `research_id`).
3. **Start the run** → `POST /research` (SSE stream). Subscribe to the stream to drive a live progress UI.
4. **Poll/watch progress** → `GET /research/{research_id}/progress`, `GET /research/{research_id}/timeline`, `GET /research/{research_id}/agents`.
5. **Show evidence** → `GET /research/{research_id}/claims`, `GET /research/{research_id}/sources`.
6. **Show reports** → `GET /research/{research_id}/reports`, `GET /reports/{report_id}/versions`.
7. **Publish/approve** → report review endpoints (see API_ENDPOINTS.md §9).

Use the SSE stream for live updates; use the REST endpoints for the authoritative current state (they are the source of truth for the DB).

---

## 6. SSE integration (live progress)

Use the browser-native `EventSource` (or a wrapper). Full contract in `SSE_EVENTS.md`.

Key points:
- Stream URL: `GET /research/{research_id}/events` (requires `Authorization: Bearer <token>`).
- `EventSource` cannot set headers → pass the JWT via:
  - `?token=<jwt>` query param (backend accepts it), **and**
  - send `Last-Event-ID` with the sequence number to resume (or `?last_event_id=<n>`).
- Frame shape (each `data:` is JSON):
  ```json
  { "event": "progress|agent_start|agent_done|complete|error",
    "message": "...", "agent": "optional",
    "data": { "event_type": "...", "research_id": "...", "sequence_number": 3, "timestamp": "...", ... } }
  ```
- Track `data.sequence_number` from the last received event; send it as `Last-Event-ID` on reconnect to avoid gaps.
- Handle `retry: 3000` (3s reconnect) and `: keepalive` comment frames.
- Terminal frames: `event: complete` or `event: error`.

---

## 7. Pagination pattern (all list endpoints)

```ts
interface PaginationMeta { limit: number; offset: number; total: number; returned: number; has_more: boolean }
```
Request `?limit=&offset=`. Render `has_more` for an infinite-scroll/"Load more" control.

---

## 8. File uploads (documents)

`POST /documents/upload` is `multipart/form-data`:
```
FormData:
  file: File            // required, PDF/DOCX/TXT/MD
  project_id?: string    // UUID
  research_id?: string   // 32-char hex
```
Use `fetch` with `FormData` (do **not** set `Content-Type` manually — the browser sets the boundary). On `201` you get a `DocumentRecord`; poll `GET /documents/{id}/status` for processing progress.

---

## 9. File downloads (reports)

These endpoints return **binary** file responses (`FileResponse`):
- `GET /reports/{filename:path}` — download a generated report.
- `GET /reports/{report_id}/export` (POST) — export a version as MD/DOCX/PDF.
- `POST /export` — legacy content export.

Consume as a blob:
```ts
const res = await api(`/reports/${encodeURIComponent(filename)}`);
const blob = await res.blob();
const disposition = res.headers.get('Content-Disposition') || '';
const match = disposition.match(/filename="?([^"]+)"?/);
const name = match ? match[1] : 'report';
const url = URL.createObjectURL(blob);
// trigger download: <a href={url} download={name} />
```

---

## 10. Ownership & multi-user isolation

- Every protected route derives the owner from the JWT. Never send `owner_id`/`user_id` in bodies.
- List endpoints (projects, documents, sessions, usage) are automatically scoped to the caller.
- To avoid confusing states, always fetch lists that are scoped to the authenticated user; a project/research you did not create will `404`.

---

## 11. Concurrency & timing notes

- SSE stream and `GET /research/{id}/progress` may be slightly out of sync; the REST endpoints are authoritative.
- Report version creation (`POST /reports/{report_id}/versions`) is **immutable** — a new version never mutates an older one. If the user edits an approved report, you should **create a new version** (status `draft`), not attempt to overwrite the approved one.
- Document processing is async; `processing_status` moves `uploaded → pending → extracting → chunking → embedding → indexed` (or `failed`). Poll `/status`.

---

## 12. Not-yet-implemented features

These have **no live backend routes** today — guard your UI accordingly (disable buttons / show "coming soon"):
- ❌ Cancel research (`POST /research/{id}/cancel`) — planned.
- ❌ Resume research (`POST /research/{id}/resume`) — planned.
- ❌ Notifications (`GET/POST/DELETE /notifications`) — planned.
- ⚠️ Usage endpoints (`GET /usage/*`) — implemented but **not yet mounted**; do not ship usage UI until confirmed live.
- ⚠️ Claim/source endpoints require PostgreSQL persistence; when not configured they return empty lists or `404`.
