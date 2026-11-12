# UPGRADE_PLAN.md
# Research AI Quest — Prioritized Implementation & Upgrade Plan
> **Date:** November 10, 2026  
> **Based on:** BACKEND_AUDIT.md, TECHNICAL_DEBT.md, EXISTING_FEATURES.md  
> **Principle:** Fix security and runtime correctness before performance, before design improvements. No new infrastructure required.

---

## HOW TO READ THIS PLAN

Each phase is sequenced so that:
- Each fix does not break any verified existing feature
- Security issues are resolved before the server is exposed to any network
- Runtime crashes are fixed before performance improvements begin
- Tests are added immediately after each fix to prevent regression

Effort estimates are for a single developer familiar with the codebase.

---

## PHASE 0 — IMMEDIATE HOTFIXES (Before Any Public Access)
**Estimated effort: ~4 hours**  
**Goal: Eliminate guaranteed runtime failures and critical secret exposure**

---

### 0.1 Fix: `validate_jwt_token` ImportError
**Debt ID:** TD-RUNTIME-01  
**File:** `auth_validator.py`  
**Problem:** `server.py` imports `validate_jwt_token` from `auth_validator.py`, but the function does not exist. The report download endpoint raises `ImportError` at runtime when a JWT query param is used.  
**Fix:** Add an alias at the bottom of `auth_validator.py`:
```python
# Alias for backward compatibility with server.py import
validate_jwt_token = verify_supabase_token
```
**Test:** Call `GET /reports/{filename}?token=<jwt>` and verify it reaches auth logic, not an `ImportError`.

---

### 0.2 Fix: Confirm and Implement `user_owns_report()`
**Debt ID:** TD-RUNTIME-02  
**File:** `session_manager.py`  
**Problem:** `user_owns_report(user_id, filename)` is called in two server routes but may be absent from `SessionManager`.  
**Fix:** Read `session_manager.py` lines 120–506 fully. If the method does not exist, add it:
```python
def user_owns_report(self, user_id: str, filename: str) -> bool:
    with self._connect() as conn:
        row = conn.execute(
            """SELECT 1 FROM research_sessions
               WHERE user_id = ?
               AND (saved_report_path LIKE ? OR docx_path LIKE ? OR pdf_path LIKE ?)
               LIMIT 1""",
            (user_id, f"%{filename}", f"%{filename}", f"%{filename}"),
        ).fetchone()
        return row is not None
```
**Test:** Verify that authenticated users can access their own reports and get 403 for others' reports.

---

### 0.3 Fix: Remove Hardcoded Supabase Anon Key
**Debt ID:** TD-SEC-02  
**File:** `auth_validator.py:39-55`  
**Problem:** `sb_publishable_6X9XiRuSzEe-...` is hardcoded in source.  
**Steps:**
1. Rotate the exposed key in the Supabase dashboard immediately.
2. Remove the hardcoded fallback string from `get_supabase_anon_key()`.
3. Log a startup warning if `SUPABASE_ANON_KEY` is not configured.
```python
def get_supabase_anon_key() -> str:
    key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY")
    if not key:
        import warnings
        warnings.warn("SUPABASE_ANON_KEY is not set. Auth API calls will fail.", RuntimeWarning)
    return key or ""
```
**Test:** Verify server still starts with a warning when the key is not set.

---

### 0.4 Fix: Remove Hardcoded HMAC Share Secret
**Debt ID:** TD-SEC-01, TD-SEC-04  
**File:** `server.py:193-195`  
**Problem:** `"quest_report_share_secret_2026"` default is a known value in source.  
**Fix:**
```python
def _get_share_secret() -> bytes:
    secret = os.getenv("SHARE_SECRET") or os.getenv("CHROMA_ENCRYPTION_KEY")
    if not secret:
        raise RuntimeError(
            "SHARE_SECRET environment variable is required. "
            "Set a random 32+ character secret in backend/.env"
        )
    return secret.encode("utf-8")
```
Add `SHARE_SECRET` to the `.env.example` with instructions.  
**Test:** Verify server raises on startup if `SHARE_SECRET` is unset; verify token generates correctly when set.

---

### 0.5 Fix: `tools.py` Crashes If `TAVILY_API_KEY` Is Missing
**Debt ID:** TD-RUNTIME-03  
**File:** `tools.py:4, 10`  
**Problem:** `TavilyClient` instantiated at module level raises if key is `None`.  
**Fix:** Move instantiation inside the tool function and guard it:
```python
@tool
def web_search(query: str) -> str:
    """Search the web for recent and reliable information on a topic."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "Tavily search unavailable: TAVILY_API_KEY not configured."
    from tavily import TavilyClient
    tavily = TavilyClient(api_key=api_key)
    result = tavily.search(query=query, max_results=5)
    ...
```
**Test:** Verify server starts and pipeline runs without `TAVILY_API_KEY`; `web_search` returns the fallback string; arXiv and DuckDuckGo still work.

---

### 0.6 Fix: `agent.py` Module-Level `get_llm()` Call
**Debt ID:** TD-RUNTIME-04  
**File:** `agent.py:121, 224, 287, 332, 400`  
**Problem:** `llm = get_llm()` and all chain builds at module level crash on import if no keys.  
**Fix:** Remove all 5 module-level instantiations. Add a module-level `None` placeholder with a lazy getter:
```python
_default_llm = None

def get_default_llm():
    global _default_llm
    if _default_llm is None:
        _default_llm = get_llm()
    return _default_llm
```
Update any code in `orchestrator.py` that uses `agent.llm` or `agent.analyst_chain` directly to call the builder function instead.  
**Test:** Import `agent.py` with no API keys set and verify no exception is raised.

---

## PHASE 1 — SECURITY HARDENING
**Estimated effort: ~2 days**  
**Goal: Close all remaining security vulnerabilities**

---

### 1.1 Fix: CORS Configuration
**Debt ID:** TD-SEC-06  
**File:** `server.py:103-116`  
**Fix:** Remove `"*"` from `allow_origins`. Read allowed origins from env:
```python
_allowed_origins = [o.strip() for o in os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5500,http://127.0.0.1:5500,http://localhost:8123,http://localhost:3000"
).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
```

---

### 1.2 Fix: Add Token Expiry to HMAC Share Links
**Debt ID:** TD-SEC-04  
**File:** `server.py:198-207`  
**Fix:** Include expiry timestamp in token HMAC. Default TTL: 7 days.
```python
def generate_share_token(filename: str, ttl_hours: int = 168) -> str:
    clean = Path(filename).name
    expiry = int(time.time()) + ttl_hours * 3600
    payload = f"{clean}|{expiry}".encode("utf-8")
    sig = hmac.new(_get_share_secret(), payload, hashlib.sha256).hexdigest()[:24]
    return f"{expiry}:{sig}"

def verify_share_token(filename: str, token: str) -> bool:
    if not token or ":" not in token:
        return False
    try:
        expiry_str, sig = token.split(":", 1)
        expiry = int(expiry_str)
    except ValueError:
        return False
    if time.time() > expiry:
        return False  # Expired
    clean = Path(filename).name
    payload = f"{clean}|{expiry}".encode("utf-8")
    expected_sig = hmac.new(_get_share_secret(), payload, hashlib.sha256).hexdigest()[:24]
    return hmac.compare_digest(expected_sig, sig)
```

---

### 1.3 Fix: Remove Auth Bypass on Network Failure
**Debt ID:** TD-SEC-03  
**File:** `auth_validator.py:168-177`  
**Fix:** Replace the insecure fallback with a proper 503 response:
```python
except requests.RequestException as req_err:
    raise HTTPException(
        status_code=503,
        detail=f"Authentication service temporarily unavailable. Please retry: {req_err}",
    )
```
**Note:** If offline token verification is needed, set `SUPABASE_JWT_SECRET` and rely on the local PyJWT verification path (branch at line 105), which already works without network access.

---

### 1.4 Fix: Require Auth for Share Token Generation
**Debt ID:** TD-SEC-07  
**File:** `server.py:618-645`  
**Fix:** Always verify file ownership regardless of auth state. For unauthenticated callers, check if the report exists and was created in an anonymous session (no `user_id`):
```python
# In get_report_share_link():
if current_user:
    if not sm.user_owns_report(current_user["user_id"], clean_filename):
        raise HTTPException(status_code=403, detail="Access denied.")
else:
    # Only allow anonymous share if the session was also created anonymously
    if not sm.report_is_anonymous(clean_filename):
        raise HTTPException(status_code=401, detail="Authentication required to share this report.")
```

---

### 1.5 Fix: Validate `output_dir` for Path Traversal
**Debt ID:** VAL-01  
**File:** `server.py:151`  
**Fix:** Add a validator to `ResearchRequest`:
```python
from pydantic import field_validator

@field_validator("output_dir")
@classmethod
def validate_output_dir(cls, v: str) -> str:
    resolved = Path(v).resolve()
    allowed_base = Path(__file__).resolve().parent / "reports"
    if not str(resolved).startswith(str(allowed_base)):
        raise ValueError("output_dir must be within the reports directory.")
    return str(resolved)
```

---

### 1.6 Fix: Validate `provider` Against Known Values
**Debt ID:** VAL-03  
**File:** `server.py:134`  
**Fix:** Use `Literal` type:
```python
from typing import Literal
provider: Literal["groq", "gemini", "mistral", "huggingface"] = Field(
    default_factory=lambda: os.getenv("LLM_PROVIDER", "groq"),
    description="LLM provider.",
)
```

---

## PHASE 2 — RUNTIME CORRECTNESS FIXES
**Estimated effort: ~3 days**  
**Goal: Eliminate all classes of runtime failures under normal load**

---

### 2.1 Fix: Add Error Handler to `writer_node()`
**Debt ID:** TD-RUNTIME-05  
**File:** `orchestrator.py` — `writer_node()`  
**Fix:**
```python
try:
    drafted_report = self.writer_chain.invoke({...})
except Exception as e:
    console.print(f"  [dim red]⚠ Writer Chain fallback triggered: {e}[/dim red]")
    drafted_report = (
        f"# Research Report: {refined_query}\n\n"
        f"## Analysis Summary\n\n{state.get('analysis', 'Analysis unavailable.')}"
    )
```

---

### 2.2 Fix: `query_intake.py` Chain Calls Need Error Handling
**Debt ID:** ERR-04  
**File:** `query_intake.py` — `validate_query()`, `decompose_query()`  
**Fix:** Wrap each chain call in try/except. On `validate_query()` failure, return a default `QueryValidation(is_valid=True, clarity_score=7, issues=[], suggestions=[])`. On `decompose_query()` failure, return a minimal `ResearchRequest` with the original topic as the single sub-query.

---

### 2.3 Fix: RAG `_indexed_count` Bug
**Debt ID:** TD-RUNTIME-06  
**File:** `knowledge_base.py` — `_get_vectorstore()` and `retrieve()`  
**Fix:** After vector store is initialized, fetch the actual document count:
```python
# At end of _get_vectorstore():
try:
    actual_count = self._vectorstore._collection.count()
    if actual_count > self._indexed_count:
        self._indexed_count = actual_count
except Exception:
    pass  # Non-critical: retrieval k will just be conservative
```

---

### 2.4 Fix: Resolve `asyncio.run()` / `nest_asyncio` Conflict in Search Node
**Debt ID:** TD-RUNTIME-07  
**File:** `orchestrator.py` — `search_node()`  
**Fix:** Replace async parallel search with `concurrent.futures.ThreadPoolExecutor`:
```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def _run_one_query(orchestrator, query, source_types, max_results):
    return orchestrator.search(query=query, source_types=source_types, max_results_per_source=max_results)

with ThreadPoolExecutor(max_workers=min(len(sub_queries), 5)) as executor:
    futures = {
        executor.submit(_run_one_query, orchestrator, q, suggested_sources, 3): q
        for q in sub_queries
    }
    all_results = []
    for future in as_completed(futures):
        try:
            all_results.extend(future.result())
        except Exception as e:
            console.print(f"  [yellow]⚠ Search failed for query: {e}[/yellow]")
```

---

### 2.5 Fix: Isolate stdout Capture Per Research Thread
**Debt ID:** TD-SEC-05, TD-RUNTIME*  
**File:** `server.py` — `_run_research_in_thread()`  
**Fix:** Instead of overwriting `sys.stdout`, use Python's `logging` framework with a custom `QueueHandler`:
```python
import logging

class _SSELogHandler(logging.Handler):
    def __init__(self, event_queue: queue.Queue):
        super().__init__()
        self._q = event_queue
    
    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        clean = _strip_ansi(msg)
        if clean:
            self._q.put(("progress", clean))

# In _run_research_in_thread():
handler = _SSELogHandler(event_queue)
root_logger = logging.getLogger()
root_logger.addHandler(handler)
try:
    ...  # run pipeline
finally:
    root_logger.removeHandler(handler)
    event_queue.put(None)
```
Then update all orchestrator nodes to use `logging.getLogger(__name__).info(...)` instead of `console.print()`.

---

### 2.6 Fix: Add SSE Disconnection Cancellation
**Debt ID:** TD-RUNTIME-08  
**File:** `server.py` — `_sse_generator()`, `_run_research_in_thread()`  
**Fix:** Add a `threading.Event` cancel token:
```python
cancel_event = threading.Event()
thread = threading.Thread(
    target=_run_research_in_thread,
    args=(request, event_queue, cancel_event),
    daemon=True,
)

# In _sse_generator() disconnect detection:
try:
    while True:
        item = ...
        yield ...
except (GeneratorExit, Exception):
    cancel_event.set()  # Signal thread to stop
    raise
```
Nodes check `cancel_event.is_set()` at the start of each node.

---

## PHASE 3 — PERFORMANCE IMPROVEMENTS
**Estimated effort: ~2 days**  
**Goal: Eliminate major bottlenecks without changing API contracts**

---

### 3.1 Fix: Cache Sentence-Transformer Model as Singleton
**Debt ID:** TD-PERF-01  
**File:** `knowledge_base.py` — `_get_embeddings()`  
```python
import threading
_EMBEDDING_SINGLETON = None
_EMBEDDING_LOCK = threading.Lock()

def _get_embeddings():
    global _EMBEDDING_SINGLETON
    if _EMBEDDING_SINGLETON is None:
        with _EMBEDDING_LOCK:
            if _EMBEDDING_SINGLETON is None:
                try:
                    from langchain_huggingface import HuggingFaceEmbeddings
                    _EMBEDDING_SINGLETON = HuggingFaceEmbeddings(
                        model_name="sentence-transformers/all-MiniLM-L6-v2",
                        model_kwargs={"device": "cpu"},
                        encode_kwargs={"normalize_embeddings": True},
                    )
                except Exception:
                    from langchain_core.embeddings import FakeEmbeddings
                    _EMBEDDING_SINGLETON = FakeEmbeddings(size=384)
    return _EMBEDDING_SINGLETON
```
**Expected improvement:** Saves 2–8 seconds per pipeline run after first run.

---

### 3.2 Fix: Offload PDF Export to Thread Executor
**Debt ID:** TD-PERF-02  
**File:** `server.py` — `export_report()` route  
```python
@app.post("/export", tags=["Reports"])
async def export_report(req: ExportRequest, ...):
    ...
    loop = asyncio.get_event_loop()
    saved_path = await loop.run_in_executor(
        None,
        lambda: exporter.export_single(
            markdown_text=raw_content,
            fmt=fmt,
            output_path=target_path,
            citation_style=req.citation_style or "apa",
        )
    )
```

---

### 3.3 Fix: Use `SessionManager` Singleton via FastAPI Lifespan
**Debt IDs:** TD-DESIGN-03, TD-PERF-04  
**File:** `server.py`  
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.session_manager = SessionManager()
    yield
    # cleanup if needed

app = FastAPI(lifespan=lifespan, ...)

# In route handlers:
async def list_sessions(request: Request, ...):
    sm = request.app.state.session_manager
```

---

### 3.4 Fix: Dynamic Context Window Allocation for Writer
**Debt ID:** TD-PERF-03  
**File:** `orchestrator.py` — `writer_node()`  
Replace hardcoded `[:3500]` with a configurable limit that can be set per provider based on their context window. Start with a safe 8,000 characters per input slot.

---

## PHASE 4 — DESIGN CLEANUP
**Estimated effort: ~1.5 days**  
**Goal: Eliminate technical debt that impacts maintainability**

---

### 4.1 Rename `query_intake.ResearchRequest` → `QueryPlan`
**Debt ID:** TD-DESIGN-01  
Rename `class ResearchRequest` in `query_intake.py` to `class QueryPlan`. Update all usages in `orchestrator.py`.

---

### 4.2 Extract LLM Factory to `llm_factory.py`
**Debt ID:** TD-DESIGN-02  
Create `backend/llm_factory.py` with `get_llm()`, `_build_llm_for_provider()`, `_provider_has_key()`, `_is_retryable()`, `PROVIDER_ORDER`. Import in `agent.py` and `server.py` from the shared module.

---

### 4.3 Add Missing CLI Providers
**Debt ID:** TD-DESIGN-07  
**File:** `main.py:62`  
Change to: `choices=["groq", "gemini", "mistral", "huggingface", "hf"]`

---

### 4.4 Delete Dead Code
**Debt ID:** TD-DESIGN-06  
- Delete `pipeline.py`
- Delete `get_hf_writer_chain()` and `get_hf_critic_chain()` from `hf_provider.py`
- Delete `build_critic_text_chain()` from `agent.py`

---

### 4.5 Remove Redundant `load_dotenv()` Calls
**Debt ID:** TD-DESIGN-05  
Keep `load_dotenv()` only in `server.py` (the entry point). Remove from all other modules.

---

## PHASE 5 — TEST COVERAGE
**Estimated effort: ~3 days**  
**Goal: Ensure all fixed functionality is regression-proof**

---

### 5.1 `tests/test_server.py` — API Endpoint Integration Tests
Using `httpx.AsyncClient` with FastAPI's `ASGITransport`:
- `test_health()` — GET /health returns 200
- `test_providers()` — GET /providers returns expected list
- `test_research_no_topic()` — POST /research with empty topic returns 422
- `test_research_streams_sse()` — POST /research returns `text/event-stream` with `data:` prefix
- `test_report_download_requires_auth()` — GET /reports/x without token returns 401
- `test_report_download_with_share_token()` — Valid token allows access; expired token returns 403
- `test_export_pdf()` — POST /export with format=pdf returns PDF bytes
- `test_delete_session_requires_auth()` — DELETE /sessions/{id} without JWT returns 401

---

### 5.2 `tests/test_security.py` — Security Primitive Tests
- `test_share_token_generates_deterministically()` — Same inputs → same token
- `test_share_token_invalid_for_different_filename()` — Token for file A does not verify for file B
- `test_share_token_expires()` — Token with past expiry fails `verify_share_token()`
- `test_encrypt_decrypt_roundtrip()` — `decrypt_text(encrypt_text(x)) == x`
- `test_encrypt_idempotent()` — `encrypt_text(encrypt_text(x)) == encrypt_text(x)`
- `test_supabase_expired_token_rejected()` — Token with past `exp` raises HTTP 401
- `test_auth_bypass_blocked_on_network_failure()` — Network error no longer grants access

---

### 5.3 `tests/test_crypto_utils.py` — Encryption Unit Tests
- Key parsing: hex / base64 / raw 32-byte
- `is_encrypted()` with encrypted and plain strings
- `decrypt_text()` passes through unencrypted strings unchanged
- Key file generation and read-back

---

### 5.4 `tests/test_report_exporter.py` — Export Unit Tests
- APA/MLA/Harvard style application on a known report fixture
- Empty input does not crash exporter
- Markdown table parsing with headers and divider row
- Citation style auto-generation when no References section exists

---

## PRIORITIZED EXECUTION ORDER

| Phase | Focus | Effort | Do Before Shipping? |
|---|---|---|---|
| **Phase 0** | Critical hotfixes (crashes + secrets) | 4h | ✅ Mandatory |
| **Phase 1** | Security hardening | 2d | ✅ Mandatory |
| **Phase 2** | Runtime correctness | 3d | ✅ Strongly recommended |
| **Phase 3** | Performance | 2d | 🟡 Recommended |
| **Phase 4** | Design cleanup | 1.5d | 🟡 Recommended |
| **Phase 5** | Test coverage | 3d | 🟡 Recommended |

**Total: ~12 engineering days** to bring the backend to a production-safe, well-tested state.

---

## WHAT NOT TO CHANGE

The following verified, working features should **not** be modified during this upgrade cycle:
- LangGraph graph structure and node order in `orchestrator.py`
- ChromaDB encryption scheme (`ENC:GCM:` prefix, AES-256-GCM, key file format)
- SQLite `research_sessions` schema (additive columns only)
- SSE event type names (`progress`, `agent_start`, `complete`, `error`) — frontend depends on these
- Report filename format and `REPORTS_DIR` location
- `CriticEvaluation` Pydantic model schema — orchestrator and critic chain depend on it
- Provider fallback order: `groq → gemini → mistral → huggingface`

