# TECHNICAL_DEBT.md
# Research AI Quest — Technical Debt Register
> **Date:** November 10, 2026  
> **Source:** BACKEND_AUDIT.md findings, categorized by debt type and payoff effort  
> **Format:** Each item includes Debt ID, severity, affected files, root cause, and resolution effort estimate

---

## DEBT SEVERITY LEGEND

| Symbol | Level | Meaning |
|---|---|---|
| 🔴 | Critical | Active security risk or guaranteed runtime failure |
| 🟠 | High | Causes bugs or data loss under real usage |
| 🟡 | Medium | Degrades quality, maintainability, or performance |
| 🟢 | Low | Cleanup, polish, or minor optimization |

---

## CATEGORY A — SECURITY DEBT

### TD-SEC-01 🔴 Hardcoded HMAC Share Secret
**Files:** `server.py:194`  
**Root Cause:** `_get_share_secret()` falls back to `"quest_report_share_secret_2026"` when `SHARE_SECRET` env var is not set. This string is now in version history.  
**Impact:** Any deployment without `SHARE_SECRET` set uses a publicly known key. Any attacker who reads this repository can forge a valid share token for any filename.  
**Resolution:** Add `SHARE_SECRET` to required env vars; raise `RuntimeError` on startup if missing; rotate the secret; never commit a default.  
**Effort:** 1 hour

---

### TD-SEC-02 🔴 Hardcoded Supabase Anon Key
**Files:** `auth_validator.py:45-54`  
**Root Cause:** A real Supabase publishable key `sb_publishable_6X9XiRuSzEe-...` is hardcoded as the fallback return value in `get_supabase_anon_key()`.  
**Impact:** Key is exposed in source control. Should be treated as compromised and rotated.  
**Resolution:** Remove hardcoded fallback. Add a startup check that requires `SUPABASE_ANON_KEY` to be set. Rotate the exposed key in Supabase dashboard immediately.  
**Effort:** 30 minutes to fix code + Supabase key rotation

---

### TD-SEC-03 🔴 Auth Bypass via Network Interference
**Files:** `auth_validator.py:168-177`  
**Root Cause:** When `requests.RequestException` is caught during Supabase Auth API call, the code falls back to trusting an **unverified** JWT payload if `aud == "authenticated"`. No signature verification is performed in this branch.  
**Impact:** An attacker who can intercept or disrupt the network path to Supabase can present a self-forged JWT with any `sub` (user ID), gaining access to any user's research sessions and reports.  
**Resolution:** Remove the network-failure trust fallback. Either fail closed (raise 503) or require `SUPABASE_JWT_SECRET` for offline verification.  
**Effort:** 2 hours

---

### TD-SEC-04 🔴 Non-Expiring HMAC Share Tokens
**Files:** `server.py:198-207`  
**Root Cause:** `generate_share_token()` creates a 24-hex-char HMAC of just the filename with no timestamp, nonce, or expiry. The token is valid permanently.  
**Impact:** Shared report links cannot be revoked. Leaked tokens provide permanent access.  
**Resolution:** Include a UTC timestamp and expiry window in the token payload. Use HMAC over `{filename}|{expiry_epoch}`. Verify expiry in `verify_share_token()`.  
**Effort:** 3 hours

---

### TD-SEC-05 🟠 `sys.stdout` Global Redirect in Multi-Thread Context
**Files:** `server.py:395-484`  
**Root Cause:** `_run_research_in_thread()` overwrites the process-global `sys.stdout` with a per-request `_SSEWriter`. Concurrent research requests overwrite each other's stdout interceptor.  
**Impact:** Under concurrent load, SSE streams from multiple users cross-contaminate. User A may see logs from User B's research run.  
**Resolution:** Replace global stdout redirect with a per-thread `logging.Handler`. Each research thread registers a handler on its own logger hierarchy, keeping streams isolated.  
**Effort:** 1 day

---

### TD-SEC-06 🟠 CORS Wildcard Combined with Credentials
**Files:** `server.py:103-116`  
**Root Cause:** `allow_origins=["*"]` and `allow_credentials=True` are both set. Modern browsers reject this combination, but the security intent is broken: any origin that bypasses CORS can send credentialed requests.  
**Resolution:** Remove `"*"` from `allow_origins`. Use an explicit allowlist from environment variables (`ALLOWED_ORIGINS`). Never combine wildcard with credentials.  
**Effort:** 1 hour

---

### TD-SEC-07 🟡 Unauthenticated Callers Can Generate Share Tokens for Any Report
**Files:** `server.py:618-645`  
**Root Cause:** The `/reports/{filename}/share` endpoint only verifies ownership when `current_user` is not `None`. Unauthenticated callers bypass the ownership check.  
**Resolution:** Either require auth for share token generation, or implement a rate-limited brute-force guard for unauthenticated access.  
**Effort:** 2 hours

---

## CATEGORY B — RUNTIME CORRECTNESS DEBT

### TD-RUNTIME-01 🔴 `validate_jwt_token` Referenced But Not Defined
**Files:** `server.py:677`, `auth_validator.py` (missing)  
**Root Cause:** `server.py`'s `get_report_file()` route does `from auth_validator import validate_jwt_token`, but this function does not exist in `auth_validator.py`. The public API is `verify_supabase_token()`.  
**Impact:** `ImportError` at runtime whenever a JWT is passed as a query parameter to `/reports/`. The report download endpoint partially breaks.  
**Resolution:** Add `validate_jwt_token = verify_supabase_token` alias in `auth_validator.py`, or fix the import in `server.py`.  
**Effort:** 15 minutes

---

### TD-RUNTIME-02 🔴 `user_owns_report()` May Not Exist on `SessionManager`
**Files:** `server.py:637, 684`, `session_manager.py` (not confirmed)  
**Root Cause:** `user_owns_report()` is called in two routes, but was not found during the review of `session_manager.py`'s SQL queries and schema definition. If this method is absent, both the share and download endpoints raise `AttributeError`.  
**Resolution:** Confirm whether `user_owns_report()` exists in `session_manager.py` lines 120–506. If missing, implement it: query `research_sessions` WHERE `user_id = ? AND (saved_report_path LIKE ? OR docx_path LIKE ? OR pdf_path LIKE ?)`.  
**Effort:** 1 hour to verify + implement if missing

---

### TD-RUNTIME-03 🔴 `tools.py` Crashes Server at Import if `TAVILY_API_KEY` Missing
**Files:** `tools.py:4, 10`  
**Root Cause:** `TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))` is called at module level. `TavilyClient` raises if `api_key` is `None`.  
**Impact:** Any deployment without `TAVILY_API_KEY` cannot import `tools.py`, which prevents `agent.py` and `orchestrator.py` from loading, which prevents the server from starting.  
**Resolution:** Wrap in lazy initialization: instantiate `TavilyClient` inside `web_search()` body, check for key presence first.  
**Effort:** 30 minutes

---

### TD-RUNTIME-04 🔴 `agent.py` Calls `get_llm()` at Module Import Time
**Files:** `agent.py:121`  
**Root Cause:** `llm = get_llm()` at module level runs before the calling process's `.env` is loaded. If no LLM keys are present in environment at import time, `get_llm()` raises `RuntimeError` and crashes the import chain.  
**Resolution:** Remove all module-level chain/LLM instantiation (`llm`, `analyst_chain`, `writer_chain`, `critic_chain`, `refiner_chain`). Make all chains lazy — build them only when `build_*_chain()` or `get_llm()` are explicitly called.  
**Effort:** 2 hours + test coverage update

---

### TD-RUNTIME-05 🔴 `writer_node()` Has No Error Handler
**Files:** `orchestrator.py:446`  
**Root Cause:** `writer_chain.invoke({...})` is called without a `try/except`. All other nodes (`reader_node`, `analyst_node`) have exception handlers that gracefully degrade.  
**Resolution:** Wrap the writer chain call in a `try/except Exception` block. On failure, set `report` to a minimal template with the available analysis text.  
**Effort:** 30 minutes

---

### TD-RUNTIME-06 🟠 RAG `_indexed_count` Bug — Retrieval Capped at k=1 for Persistent Collections
**Files:** `knowledge_base.py:174, 397`  
**Root Cause:** `self._indexed_count` is initialized to `0` in `__init__`. When `retrieve()` is called on a `ResearchKnowledgeBase` whose collection was populated in a **previous** run (loaded from disk), `_indexed_count` remains 0. The expression `min(k, max(self._indexed_count, 1))` evaluates to `min(k, 1) = 1`.  
**Impact:** Any subsequent research run that benefits from a previously populated ChromaDB collection gets only 1 RAG result instead of the requested 6.  
**Resolution:** After `_get_vectorstore()` initializes, query the collection's actual document count and set `self._indexed_count` from it.  
**Effort:** 1 hour

---

### TD-RUNTIME-07 🟠 `asyncio.run()` Inside Synchronous Node Causes `nest_asyncio` Mutation
**Files:** `orchestrator.py:198-219`  
**Root Cause:** `asyncio.run()` creates a new event loop, which fails when called from a thread that already has one (the FastAPI worker context). The fallback mutates the global event loop via `nest_asyncio.apply()`. Under concurrent requests, this mutates the shared loop, causing race conditions.  
**Resolution:** Convert `search_node()` to run all source searches using `ThreadPoolExecutor.map()` instead of relying on `asyncio`. This avoids the event loop conflict entirely.  
**Effort:** 4 hours

---

### TD-RUNTIME-08 🟠 SSE Client Disconnect Does Not Cancel Background Thread
**Files:** `server.py:490-559`  
**Root Cause:** The background research thread is a `daemon=True` thread. When the SSE client disconnects, the generator eventually gets a disconnect exception, but the daemon thread continues running until it finishes (20–120 seconds), burning LLM API tokens.  
**Resolution:** Add a `threading.Event` cancellation token. Pass it to `_run_research_in_thread()`. Add a disconnection detection hook in `_sse_generator()` to set the event. Check the event in long-running nodes.  
**Effort:** 1 day

---

## CATEGORY C — DESIGN AND ARCHITECTURE DEBT

### TD-DESIGN-01 🟠 Two `ResearchRequest` Classes with the Same Name
**Files:** `query_intake.py:49`, `server.py:125`  
**Root Cause:** Both files define `ResearchRequest` Pydantic models for different purposes (research plan vs. API request body). Same name creates import confusion and potential shadowing.  
**Resolution:** Rename `query_intake.py`'s model to `ResearchPlan` or `QueryPlan` to reflect its purpose.  
**Effort:** 2 hours + search/replace across imports

---

### TD-DESIGN-02 🟡 LLM Factory Duplicated in `agent.py` and `server.py`
**Files:** `agent.py:49-117`, `server.py:317-368`  
**Root Cause:** `server.py`'s `_chat_via_provider()` re-implements provider switching and LLM instantiation independently of `agent.py`'s `get_llm()`.  
**Resolution:** Extract `get_llm()` into its own `llm_factory.py` module and import it in both `agent.py` and `server.py`.  
**Effort:** 2 hours

---

### TD-DESIGN-03 🟡 `SessionManager` Instantiated Ad-Hoc in 6+ Locations
**Files:** `server.py:458, 601, 611, 627, 659`, `main.py:32`  
**Root Cause:** No singleton, no dependency injection. Every handler creates its own `SessionManager()` instance with its own SQLite connection.  
**Resolution:** Use FastAPI's `lifespan` context to create a single `SessionManager` on startup and store it in app state; inject via `Depends()`.  
**Effort:** 3 hours

---

### TD-DESIGN-04 🟡 Orchestrator Nodes Tightly Coupled to Rich Console
**Files:** `orchestrator.py` — all node methods  
**Root Cause:** `console.print(Panel(...))` is called directly inside every pipeline node, mixing presentation with business logic.  
**Resolution:** Replace direct Rich calls with structured log events (e.g., a `PipelineEvent` dataclass put into the SSE queue). The server or CLI consumer decides how to render.  
**Effort:** 1 day

---

### TD-DESIGN-05 🟡 `load_dotenv()` Called 5+ Times Across All Modules
**Files:** `agent.py:28`, `query_intake.py:23`, `hf_provider.py:30`, `search_sources.py:31`, `server.py:74-76`  
**Root Cause:** Each module independently calls `load_dotenv()` as a defensive measure. `server.py` calls it three times.  
**Resolution:** Call `load_dotenv()` exactly once at process startup (in `server.py`'s startup or `main.py`). Remove redundant calls from all other modules.  
**Effort:** 1 hour

---

### TD-DESIGN-06 🟡 `pipeline.py` and Dead HF Chains Are Orphaned Dead Code
**Files:** `pipeline.py`, `hf_provider.py:101-183`, `agent.py:335-361`  
**Root Cause:** `pipeline.py` was replaced by `orchestrator.py`. `hf_provider.py`'s `get_hf_writer_chain()` and `get_hf_critic_chain()` were replaced by `agent.py`'s generic builders. `build_critic_text_chain()` in `agent.py` is unused.  
**Resolution:** Delete `pipeline.py`. Delete `get_hf_writer_chain()` and `get_hf_critic_chain()` from `hf_provider.py`. Delete `build_critic_text_chain()` from `agent.py`.  
**Effort:** 30 minutes

---

### TD-DESIGN-07 🟡 `main.py` CLI Provider Choices Incomplete
**Files:** `main.py:62`  
**Root Cause:** `argparse` only accepts `["gemini", "huggingface", "hf"]`. Groq (the preferred default) and Mistral are absent.  
**Resolution:** Update choices to `["groq", "gemini", "mistral", "huggingface", "hf"]`.  
**Effort:** 15 minutes

---

## CATEGORY D — PERFORMANCE DEBT

### TD-PERF-01 🟠 Sentence-Transformer Model Loaded on Every Pipeline Run
**Files:** `knowledge_base.py:72-93, 164-172`  
**Root Cause:** `_get_embeddings()` is called in `ResearchKnowledgeBase.__init__()` for every pipeline run. `HuggingFaceEmbeddings` loads the model from disk on each call.  
**Resolution:** Cache the embeddings object as a module-level singleton using a lock:
```python
_EMBEDDING_CACHE = None
_EMBEDDING_LOCK = threading.Lock()
def _get_embeddings():
    global _EMBEDDING_CACHE
    if _EMBEDDING_CACHE is None:
        with _EMBEDDING_LOCK:
            if _EMBEDDING_CACHE is None:
                _EMBEDDING_CACHE = HuggingFaceEmbeddings(...)
    return _EMBEDDING_CACHE
```
**Effort:** 1 hour

---

### TD-PERF-02 🟠 PDF Export Blocks FastAPI Async Event Loop
**Files:** `server.py:702-762`, `report_exporter.py`  
**Root Cause:** `export_report()` is an `async def` route handler that calls synchronous PDF generation (WeasyPrint) without offloading to an executor.  
**Resolution:** Wrap the exporter call in `await asyncio.get_event_loop().run_in_executor(None, ...)`.  
**Effort:** 1 hour

---

### TD-PERF-03 🟡 Writer Chain Truncates Research Context to 3,500 Characters
**Files:** `orchestrator.py:442-443`  
**Root Cause:** `[:3500]` hardcoded slice on search results and scraped content. Modern LLMs have 32k–128k context windows.  
**Resolution:** Calculate available token budget based on the LLM's context window and dynamically allocate it.  
**Effort:** 4 hours

---

### TD-PERF-04 🟡 No Connection Pooling or Caching for SQLite
**Files:** `server.py:596-614`, `session_manager.py`  
**Root Cause:** New `SessionManager()` creates a new SQLite connection per request. Under concurrent load, this causes lock contention.  
**Resolution:** Use a single shared `SessionManager` instance (see TD-DESIGN-03). Consider WAL mode for SQLite: `PRAGMA journal_mode=WAL`.  
**Effort:** 2 hours (combined with TD-DESIGN-03)

---

## CATEGORY E — TEST COVERAGE DEBT

### TD-TEST-01 🟠 Zero Integration Tests for FastAPI Endpoints
**Files:** All `tests/`  
**Root Cause:** Existing tests cover units (agent chains, orchestrator state, search sources) but no FastAPI `TestClient` tests exist for any HTTP endpoint.  
**Coverage Gaps:**
- Auth middleware (`get_current_user`, `get_optional_user`)
- SSE streaming response format
- Report download with/without share token
- Session CRUD endpoints
- `/export` endpoint with format validation

**Resolution:** Add `tests/test_server.py` using `httpx` async TestClient.  
**Effort:** 2 days

---

### TD-TEST-02 🟠 Security Primitives Have No Tests
**Files:** `auth_validator.py`, `server.py:193-207`, `crypto_utils.py`  
**Coverage Gaps:**
- `generate_share_token()` / `verify_share_token()` correctness and forgery resistance
- `encrypt_text()` / `decrypt_text()` round-trip
- `verify_supabase_token()` with expired/tampered JWT
- Auth bypass fallback branch (network error scenario)

**Resolution:** Add `tests/test_security.py` with mocked Supabase responses.  
**Effort:** 1 day

---

### TD-TEST-03 🟡 Missing Tests for Report Exporter
**Files:** `report_exporter.py`  
**Coverage Gaps:**
- Citation style application (APA/MLA/Harvard formatting correctness)
- PDF generation (at least smoke test that it doesn't crash)
- DOCX table parsing
- Empty/minimal input handling

**Resolution:** Add `tests/test_report_exporter.py`.  
**Effort:** 4 hours

---

### TD-TEST-04 🟡 Crypto Utils Not Tested
**Files:** `crypto_utils.py`  
**Coverage Gaps:**
- Encrypt → Decrypt round-trip
- Idempotent encryption (already-encrypted text returned as-is)
- Key file generation and persistence
- Key parsing (hex, base64, raw 32-byte)

**Resolution:** Add `tests/test_crypto_utils.py`.  
**Effort:** 3 hours

---

## DEBT SUMMARY TABLE

| Debt ID | Category | Severity | Effort | Priority |
|---|---|---|---|---|
| TD-SEC-01 | Security | 🔴 | 1h | P0 |
| TD-SEC-02 | Security | 🔴 | 30m | P0 |
| TD-SEC-03 | Security | 🔴 | 2h | P0 |
| TD-SEC-04 | Security | 🟠 | 3h | P1 |
| TD-SEC-05 | Security | 🟠 | 1d | P1 |
| TD-SEC-06 | Security | 🟠 | 1h | P1 |
| TD-SEC-07 | Security | 🟡 | 2h | P2 |
| TD-RUNTIME-01 | Runtime | 🔴 | 15m | P0 |
| TD-RUNTIME-02 | Runtime | 🔴 | 1h | P0 |
| TD-RUNTIME-03 | Runtime | 🔴 | 30m | P0 |
| TD-RUNTIME-04 | Runtime | 🔴 | 2h | P0 |
| TD-RUNTIME-05 | Runtime | 🔴 | 30m | P1 |
| TD-RUNTIME-06 | Runtime | 🟠 | 1h | P1 |
| TD-RUNTIME-07 | Runtime | 🟠 | 4h | P1 |
| TD-RUNTIME-08 | Runtime | 🟠 | 1d | P2 |
| TD-DESIGN-01 | Design | 🟠 | 2h | P2 |
| TD-DESIGN-02 | Design | 🟡 | 2h | P3 |
| TD-DESIGN-03 | Design | 🟡 | 3h | P3 |
| TD-DESIGN-04 | Design | 🟡 | 1d | P3 |
| TD-DESIGN-05 | Design | 🟡 | 1h | P3 |
| TD-DESIGN-06 | Design | 🟡 | 30m | P3 |
| TD-DESIGN-07 | Design | 🟡 | 15m | P2 |
| TD-PERF-01 | Performance | 🟠 | 1h | P2 |
| TD-PERF-02 | Performance | 🟠 | 1h | P2 |
| TD-PERF-03 | Performance | 🟡 | 4h | P3 |
| TD-PERF-04 | Performance | 🟡 | 2h | P3 |
| TD-TEST-01 | Testing | 🟠 | 2d | P2 |
| TD-TEST-02 | Testing | 🟠 | 1d | P2 |
| TD-TEST-03 | Testing | 🟡 | 4h | P3 |
| TD-TEST-04 | Testing | 🟡 | 3h | P3 |

**Total estimated resolution effort: ~8–10 engineering days**

