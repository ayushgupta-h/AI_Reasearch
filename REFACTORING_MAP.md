# Refactoring Map — Research AI Quest Backend

**Task 1.2 deliverable.** Shows every old file → new location, what was moved/extracted, and what P0 bugs were fixed.

> Original flat `backend/*.py` files are **NOT deleted**. They are either:
> - **Shims** — thin wrappers that re-export from `app/`
> - **Unchanged** — still imported directly by the pipeline (LangGraph nodes)
> - **Dead code** — marked for safe deletion after full verification

---

## Architecture Before → After

```
BEFORE (flat)                    AFTER (layered)
─────────────────────────────    ────────────────────────────────────────
backend/
  server.py          ─────────► app/factory.py        (app factory, CORS, middleware)
  server.py          ─────────► app/api/system.py     (GET /health, /providers)
  server.py          ─────────► app/api/auth.py       (GET /auth/config, /auth/me)
  server.py          ─────────► app/api/sessions.py   (GET /sessions, DELETE /sessions/{id})
  server.py          ─────────► app/api/reports.py    (GET /reports, POST /export, /share)
  server.py          ─────────► app/api/research.py   (POST /research SSE, POST /chat)
  server.py          ─────────► app/services/research_service.py
  server.py          ─────────► app/services/chat_service.py
  server.py          ─────────► app/services/report_service.py
  server.py          ─────────► app/services/session_service.py
  server.py          ─────────► app/security/share_tokens.py
  server.py          ─────────► app/schemas/requests.py
  server.py          ─────────► app/schemas/responses.py

  auth_validator.py  ─────────► app/security/dependencies.py  (re-export shim)
  session_manager.py ─────────► app/services/session_service.py (facade)
  crypto_utils.py    unchanged  (imported by knowledge_base.py directly)
  knowledge_base.py  unchanged  (imported by orchestrator.py directly)
  orchestrator.py    unchanged  (LangGraph graph — not restructured)
  agent.py           ─ fixed ─► module-level llm removed (lazy init)
  tools.py           ─ fixed ─► Tavily lazy-init (no crash on missing key)
  query_intake.py    unchanged
  search_sources.py  unchanged
  citation_validator.py unchanged
  report_exporter.py unchanged
  hf_provider.py     unchanged
  pipeline.py        DEAD CODE (superseded by orchestrator.py)
  main.py            unchanged  (CLI entry point)
```

---

## File-by-File Migration

### New `app/` Files Created

| New File | Responsibility | Source |
|---|---|---|
| `app/config.py` | Centralised env config, `Settings` dataclass, backwards-compat constants | Extracted from all flat files |
| `app/errors.py` | Typed `AppError` hierarchy + FastAPI exception handlers | New |
| `app/factory.py` | `create_app()` — wires middleware, routers, lifespan | Extracted from `server.py` |
| `app/middleware.py` | Raw-ASGI request-ID middleware, access logging | Extracted from `server.py` |
| `app/observability.py` | Structured logging, correlation IDs, secret redaction | New |
| `app/main.py` | ASGI entry point: `from app.factory import create_app; app = create_app()` | New |
| `app/api/__init__.py` | Aggregates all sub-routers into `api_router` | New |
| `app/api/system.py` | `GET /health`, `GET /providers` | Extracted from `server.py` |
| `app/api/auth.py` | `GET /auth/config`, `GET /auth/me` | Extracted from `server.py` |
| `app/api/sessions.py` | `GET /sessions`, `DELETE /sessions/{id}` | Extracted from `server.py` |
| `app/api/reports.py` | `GET /reports/{file}`, `/share`, `POST /export` | Extracted from `server.py` |
| `app/api/research.py` | `POST /research` (SSE), `POST /chat` | Extracted from `server.py` |
| `app/schemas/requests.py` | `ResearchRunRequest`, `ChatRequest`, `ExportRequest` | Extracted from `server.py` |
| `app/schemas/responses.py` | All response models | Extracted from `server.py` |
| `app/schemas/pipeline.py` | Lazy aliases: `QueryPlan`, `QueryValidation`, `CriticEvaluation` | New |
| `app/schemas/enums.py` | All status/state enums | New |
| `app/schemas/base.py` | `BaseSchema`, `OutputSchema`, constrained text types | New |
| `app/schemas/health.py` | `HealthStatus`, `ComponentCheck` | New |
| `app/schemas/provider.py` | `ProviderStatus` | New |
| `app/schemas/common.py` | `PaginationMeta`, `ApiResponse`, `Page` | New |
| `app/schemas/envelopes.py` | `HealthEnvelope`, `ProvidersEnvelope` | New |
| `app/security/dependencies.py` | Re-exports `auth_validator` functions; adds `user_from_query_token` | Thin shim |
| `app/security/share_tokens.py` | `generate_share_token()`, `verify_share_token()` — key from config | Extracted + fixed |
| `app/services/container.py` | `ServiceContainer` — lazy singleton DI | Extracted from `server.py` |
| `app/services/session_service.py` | `SessionService` facade over `SessionManager` | Extracted from `server.py` |
| `app/services/research_service.py` | Pipeline runner, provider chain, SSE streaming | Extracted from `server.py` |
| `app/services/chat_service.py` | Single-turn chat with provider fallback | Extracted from `server.py` |
| `app/services/report_service.py` | File resolution, auth, download, export | Extracted from `server.py` |
| `app/orchestration/pipeline.py` | `build_orchestrator()` factory — isolates HTTP layer from LangGraph | New |
| `app/orchestration/instrumentation.py` | `instrument_node()` — stage logging wrapper for graph nodes | New |
| `app/utils/sse.py` | SSE frame builder, `QueueStreamWriter`, agent detection | Extracted from `server.py` |
| `app/utils/stdout_proxy.py` | `ThreadLocalStream` — per-thread stdout capture (concurrent runs safe) | Extracted from `server.py` |

---

### Original Files — Status

| File | Status | Notes |
|---|---|---|
| `server.py` | ✅ Shim | Now calls `app.factory.create_app()` — `uvicorn server:app` still works |
| `agent.py` | ✅ Fixed | Removed module-level `llm = get_llm()` and all chain builds at import time |
| `tools.py` | ✅ Fixed | `TavilyClient` now lazy-initialised inside `_get_tavily()` |
| `orchestrator.py` | ✅ Fixed | Added try/except to `writer_node()` |
| `knowledge_base.py` | ✅ Fixed | `_indexed_count` seeded from actual collection count after init |
| `auth_validator.py` | ✅ Unchanged | Still the JWT implementation; `app/security/dependencies.py` re-exports it |
| `session_manager.py` | ✅ Unchanged | Still the SQLite implementation; `app/services/session_service.py` wraps it |
| `query_intake.py` | ✅ Unchanged | Used directly by `orchestrator.py` |
| `search_sources.py` | ✅ Unchanged | Used directly by `orchestrator.py` |
| `citation_validator.py` | ✅ Unchanged | Used directly by `orchestrator.py` |
| `report_exporter.py` | ✅ Unchanged | Used by `orchestrator.py` and `app/services/report_service.py` |
| `crypto_utils.py` | ✅ Unchanged | Used directly by `knowledge_base.py` |
| `hf_provider.py` | ✅ Unchanged | Used by `agent.py` |
| `main.py` | ✅ Unchanged | CLI entry point — still works |
| `pipeline.py` | ⚠️ Dead code | Superseded by `orchestrator.py`; safe to delete |

---

## P0 Bug Fixes Applied During Refactor

| ID | File | Bug | Fix |
|---|---|---|---|
| TD-RUNTIME-01 | `auth_validator.py` | `validate_jwt_token` name missing | Re-exported as alias in `app/security/dependencies.py` |
| TD-RUNTIME-03 | `tools.py` | `TavilyClient()` at module level crashes import without key | Lazy `_get_tavily()` with `EnvironmentError` guard |
| TD-RUNTIME-04 | `agent.py` | `llm = get_llm()` + 3 chain builds at module level | Replaced with `_get_default_llm()` lazy singleton |
| TD-RUNTIME-05 | `orchestrator.py` | `writer_node()` had no exception handler | Added try/except that logs then re-raises |
| TD-RUNTIME-06 | `knowledge_base.py` | `_indexed_count=0` after loading existing collection → k=1 cap | Seeds from `_collection.count()` after vectorstore init |
| TD-SEC-02 | `server.py` | `CORS allow_credentials=True` + wildcard origin | Fixed in `factory.py`: `cors_allow_credentials=False` |
| TD-SEC-04 | `server.py` | HMAC secret hardcoded fallback | Now from `config.share_secret`; ephemeral random secret in dev with warning |
| TD-SEC-03 | `auth_validator.py` | Supabase anon key hardcoded | Removed — config reads from `SUPABASE_ANON_KEY` env var only |
| SCHEMA-FIX | `app/schemas/source.py` | `LanguageCode` imported from wrong module (`enums` vs `base`) | Fixed import to use `app.schemas.base.LanguageCode` |

---

## Entry Points

| Command | Works? | Notes |
|---|---|---|
| `uvicorn server:app --port 8000` | ✅ | Original entry point, now delegates to `app.factory` |
| `uvicorn app.main:app --port 8000` | ✅ | New canonical entry point |
| `python main.py --topic "AI"` | ✅ | CLI mode unchanged |
| `uvicorn backend.server:app` | ✅ | From repo root |

---

## Future Integration Stubs

The following extension points are ready in `app/config.py` (commented):

```python
# DATABASE_URL = os.getenv("DATABASE_URL", "")   # → async SQLAlchemy / PostgreSQL
# REDIS_URL    = os.getenv("REDIS_URL", "")       # → Celery broker / cache
```

`SessionService` and `ServiceContainer` are designed so `SessionManager` (SQLite) can be swapped for a PostgreSQL repository without changing any API or service code.

