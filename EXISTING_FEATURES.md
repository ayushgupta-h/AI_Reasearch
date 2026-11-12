# EXISTING_FEATURES.md
# Research AI Quest — Verified Existing Features
> **Date:** November 10, 2026  
> **Verification Method:** Direct source code inspection only  
> **Legend:**  
> ✅ Fully implemented and verified in code  
> ⚠️ Partially implemented — has gaps or known bugs  
> ❌ Claimed in documentation but not found in code

---

## 1. MULTI-AGENT RESEARCH PIPELINE

### 1.1 Query Intake & Decomposition ✅
**File:** `query_intake.py` — `QueryIntakeSystem` class  
- ✅ **LLM-powered query validation** — `validate_query()` runs `VALIDATION_PROMPT | llm | JsonOutputParser(QueryValidation)`. Returns `is_valid`, `clarity_score` (1–10), `issues`, `suggestions`.
- ✅ **Multi-angle query decomposition** — `decompose_query()` runs `DECOMPOSITION_PROMPT | llm | JsonOutputParser(ResearchRequest)`. Produces 3–5 targeted `sub_queries`, `focus_areas`, `research_type`, `scope`, `time_sensitivity`, `suggested_source_types`.
- ✅ **Interactive terminal mode** — `process()` combines validation + user confirmation via `rich.prompt`. Used in CLI mode only.
- ✅ **Provider-aware initialization** — `QueryIntakeSystem.__init__()` calls `agent.get_llm(provider=provider)` to honor the pipeline's chosen provider.
- ✅ **Non-interactive autonomous mode** — Orchestrator always passes `interactive=False`; calls `validate_query()` + `decompose_query()` directly.

---

### 1.2 Multi-Source Search ✅
**File:** `search_sources.py` — `BaseSearchSource` (ABC), individual source adapters, `SearchOrchestrator`  
**File:** `tools.py` — `web_search`, `scrape_url`, `multi_source_search` LangChain tools  

- ✅ **Tavily web search** — `TavilySearch.search()`. Requires `TAVILY_API_KEY`. Returns up to `max_results` results with title, url, snippet.
- ✅ **arXiv academic search** — `ArxivSearch.search()`. Free, no key. HTTP GET to `export.arxiv.org/api/query`, XML parsing via `xml.etree.ElementTree`.
- ✅ **Wikipedia encyclopedia search** — `WikipediaSearch.search()`. Free, no key. MediaWiki API, HTML tag stripping on snippets.
- ✅ **DuckDuckGo fallback web search** — `DuckDuckGoSearch.search()`. Free, no key. Uses `duckduckgo_search` Python package.
- ⚠️ **HuggingFace papers** — Source class exists in file per documentation. Not reviewed in lines 200+ — existence partially verified.
- ⚠️ **NewsAPI** — Source class exists per documentation. Not reviewed. Requires `NEWS_API_KEY`.
- ⚠️ **Google Scholar / SerpAPI** — Source class exists per documentation. Not reviewed. Requires `SERP_API_KEY`.
- ✅ **Async parallel search** — `SearchOrchestrator.async_search_all()` runs all sub-queries and all sources concurrently using `ThreadPoolExecutor` + `asyncio`.
- ✅ **Result deduplication** — URL-based deduplication across all sources in `search_node()`.
- ✅ **LangChain tool wrappers** — `web_search`, `scrape_url`, `multi_source_search` decorated with `@tool` for agent binding.
- ✅ **Source availability checking** — `is_available()` on each adapter; orchestrator skips unavailable sources.

---

### 1.3 Deep Web Content Extraction ✅
**File:** `tools.py` — `scrape_url()`, `agent.py` — `build_reader_agent()`  

- ✅ **HTTP page fetching** — `requests.get()` with `User-Agent` header, 8-second timeout.
- ✅ **HTML parsing and cleanup** — BeautifulSoup removes `<script>`, `<style>`, `<nav>`, `<footer>` tags.
- ✅ **Text truncation** — Output capped at 3,000 characters per URL.
- ✅ **LangChain tool-calling Reader Agent** — `build_reader_agent()` creates a `create_agent()` with `scrape_url` tool. Agent selects 2–3 most relevant URLs from the top-10 list and scrapes them.
- ✅ **Multi-format content normalization** — Reader node handles both string and list-of-blocks content formats (Gemini returns content as blocks).

---

### 1.4 RAG Vector Knowledge Base ✅
**File:** `knowledge_base.py` — `ResearchKnowledgeBase`  

- ✅ **Local ChromaDB persistence** — Stores collections to `backend/chroma_db/` using `persist_directory`.
- ✅ **ChromaDB Cloud mode** — Connects to `ChromaDB CloudClient` when `CHROMA_API_KEY`, `CHROMA_TENANT`, `CHROMA_DATABASE` are all set.
- ✅ **Automatic cloud-to-local fallback** — If cloud connection fails, falls back to local mode.
- ✅ **HuggingFace sentence-transformer embeddings** — `all-MiniLM-L6-v2` model, runs on CPU, L2-normalized output.
- ✅ **FakeEmbeddings fallback** — If `langchain-huggingface` is not installed, uses `FakeEmbeddings(size=384)` for CI/testing.
- ✅ **Paragraph-aware text chunking** — Custom `_chunk_text()` splits on paragraph breaks, falls back to sentence splits. 800-char chunks with 100-char overlap.
- ✅ **Stable chunk IDs** — MD5-based deterministic IDs prevent duplicate upserts.
- ✅ **Batch upsert (100 chunks/batch)** — Avoids ChromaDB size limits.
- ✅ **AES-256-GCM encryption at write** — Plaintext embeddings (for search accuracy) + encrypted ciphertext documents (for storage privacy).
- ✅ **Transparent decryption on retrieval** — `similarity_search` is monkey-patched to decrypt `page_content` before returning.
- ✅ **Multi-query retrieval** — `rag_node()` queries KB for each sub-query and focus area; deduplicates retrieved context blocks.
- ⚠️ **Retrieval k-cap bug** — `retrieve()` uses `_indexed_count` which is 0 for collections loaded from disk, capping results at k=1.

---

### 1.5 Analytical Synthesis ✅
**File:** `agent.py` — `ANALYST_PROMPT`, `build_analyst_chain()`  

- ✅ **RAG-augmented analysis** — When `rag_context` is available, it is prioritized over direct search snippets in the analyst prompt.
- ✅ **Structured 5-section analytical brief** — Executive Takeaways, Thematic Breakdown, Quantitative Evidence, Diverging Perspectives, Key Authoritative Sources.
- ✅ **Cross-source synthesis** — Prompt explicitly instructs the analyst to compare and contrast across retrieved sources.
- ✅ **Fallback on LLM failure** — `analyst_node()` has `try/except`; on failure returns a structured fallback summary.

---

### 1.6 Report Writing ✅
**File:** `agent.py` — `WRITER_PROMPT`, `build_writer_chain()`  

- ✅ **7-section structured report format** — Executive Summary, Research Scope & Methodology, Deep-Dive Analysis, Comparative Perspectives, Strategic Implications, Limitations, Sources & References.
- ✅ **Markdown output** — All reports generated in Markdown with headers, bullet points, and table support.
- ✅ **Source citation** — Writer prompt explicitly requires a complete Sources section.
- ⚠️ **No exception handler** — `writer_node()` does not wrap `writer_chain.invoke()` in try/except.

---

### 1.7 Academic Critic Review Loop ✅ (partially verified)
**File:** `agent.py` — `CriticEvaluation`, `build_critic_chain()`  

- ✅ **Pydantic structured evaluation** — `CriticEvaluation` model: `score` (int 1–10), `passed` (bool), `strengths` (list), `areas_for_improvement` (list), `actionable_revisions` (list), `verdict` (str).
- ✅ **JSON output parser** — `JsonOutputParser(pydantic_object=CriticEvaluation)` enforces structured output.
- ✅ **Approval threshold comparison** — `approval_threshold` (default 8) compared against `critic_score`.
- ✅ **Revision counter** — `revision_count` incremented on each refiner iteration; capped at `max_revisions`.
- ⚠️ **critic_node() / refiner_node() conditional edges** — Not fully verified in orchestrator lines 450–870.

---

### 1.8 Self-Correction via Refiner ✅ (partially verified)
**File:** `agent.py` — `REFINER_PROMPT`, `build_refiner_chain()`  

- ✅ **Targeted revision prompt** — Refiner receives: prior report, critic score, strengths, areas_for_improvement, actionable_revisions, verdict, and supporting research context.
- ✅ **Full report replacement** — Refiner outputs the complete revised report (not a diff).
- ⚠️ **Revision history tracking** — `revision_history: List[Dict]` in state schema; actual population not verified.

---

## 2. EXPORT & DOCUMENT GENERATION

### 2.1 Citation Validation ✅
**File:** `citation_validator.py` — `CitationValidator`, `CitationResult`, `CitationReport`  

- ✅ **URL extraction from markdown** — Regex pattern matches bare URLs, markdown links, and inline code URLs.
- ✅ **HTTP HEAD with GET fallback** — Sends HEAD request; retries with GET for 403/405 responses.
- ✅ **Concurrent validation** — `ThreadPoolExecutor(max_workers=12)` runs all URL checks in parallel.
- ✅ **Status classification** — `verified` (2xx/3xx), `broken` (4xx/5xx), `timeout`, `error`.
- ✅ **Timing measurement** — Response time in milliseconds per URL.
- ✅ **Citation appendix injection** — `inject_appendix()` appends a formatted verification table to the markdown report.
- ✅ **Rich console display** — `display_report()` renders a color-coded table of all citation results.
- ❌ **DOI validation** — Claimed in module docstring, not implemented. No DOI pattern matching or `doi.org` resolution.

---

### 2.2 Multi-Format Report Export ✅
**File:** `report_exporter.py` — `apply_citation_style()`, `ReportExporter` (partially reviewed)  

- ✅ **APA citation formatting** — `apply_citation_style(text, "apa")` reformats reference-section links as `Author. (year). *Title*. URL`.
- ✅ **MLA citation formatting** — `"Title." *domain*, year, URL.`
- ✅ **Harvard citation formatting** — `Author (year) *Title*. Available at: <URL>.`
- ✅ **Auto-generated references section** — If no existing references section, one is built from inline links.
- ✅ **Markdown table parsing** — `_parse_markdown_table()` converts markdown table syntax to (headers, rows).
- ⚠️ **DOCX export** — Implemented in `report_exporter.py` lines 150+, not fully reviewed. Claimed: custom heading styles, callout blocks, table formatting.
- ⚠️ **PDF export** — Implemented in `report_exporter.py` lines 150+, not fully reviewed. Claimed: clean academic print styling.
- ⚠️ **`ReportExporter.export_single()` method** — Called from `server.py:740` but not confirmed in reviewed lines.

---

## 3. API SERVER & AUTHENTICATION

### 3.1 FastAPI REST Server ✅
**File:** `server.py`  

- ✅ **FastAPI application** — `FastAPI(title="Research AI API", version="1.0.0")` with Swagger at `/docs`.
- ✅ **CORS middleware** — Configured for localhost origins (5500, 8123, 3000) + wildcard `"*"` (⚠️ security issue).
- ✅ **POST /research** — Accepts `ResearchRequest`, streams `text/event-stream` SSE response.
- ✅ **GET /health** — Returns `{status: "ok", version: "1.0.0"}`.
- ✅ **GET /providers** — Returns supported provider list and default.
- ✅ **GET /sessions** — Fetches SQLite history; filters by `user_id` when authenticated.
- ✅ **DELETE /sessions/{session_id}** — Deletes session owned by authenticated user.
- ✅ **GET /reports/{filename:path}** — Serves `.md`, `.docx`, `.pdf`, `.txt` with ownership check or share token.
- ✅ **GET /reports/{filename:path}/share** — Generates HMAC-SHA256 share token and URL.
- ✅ **POST /export** — Accepts markdown content + format; returns formatted file download.
- ✅ **POST /chat** — Single-turn LLM chat with provider fallback.
- ✅ **GET /auth/config** — Returns Supabase URL and anon key for client-side auth.
- ✅ **GET /auth/me** — Returns authenticated user profile.

---

### 3.2 Server-Sent Events (SSE) Streaming ✅
**File:** `server.py` — `_SSEWriter`, `_sse_generator()`, `_run_research_in_thread()`  

- ✅ **Background thread execution** — Pipeline runs in `threading.Thread(daemon=True)`.
- ✅ **Queue-based event passing** — `queue.Queue` between pipeline thread and async SSE generator.
- ✅ **stdout interception** — `_SSEWriter` captures Rich console output and forwards to SSE queue.
- ✅ **ANSI/Rich markup stripping** — `_strip_ansi()` cleans terminal escape codes before streaming.
- ✅ **Agent transition detection** — `_detect_agent()` heuristically identifies which agent produced a log line.
- ✅ **SSE event types** — `progress`, `agent_start`, `agent_done`, `complete`, `error`.
- ✅ **Keepalive comments** — `": keepalive\n\n"` sent every 50ms when queue is empty to prevent proxy timeouts.
- ✅ **Nginx buffering prevention** — `X-Accel-Buffering: no` header.
- ✅ **Provider fallback in thread** — Full provider chain retry on rate-limit or quota errors.
- ✅ **Completion event** — Sends `{status, provider, critic_score, revision_count, saved_report_path, sources_analyzed}`.
- ⚠️ **SSE disconnection handling** — No cancellation of background thread on client disconnect.
- ⚠️ **Concurrent request isolation** — `sys.stdout` redirect is process-global; not safe for concurrent requests.

---

### 3.3 Supabase JWT Authentication ✅
**File:** `auth_validator.py` — `verify_supabase_token()`, `get_current_user()`, `get_optional_user()`  

- ✅ **In-memory TTL cache** — `_TOKEN_CACHE` stores verified tokens for 60 seconds.
- ✅ **JWT expiry pre-check** — Unverified decode to check `exp` claim before network call.
- ✅ **Local HS256 signature verification** — When `SUPABASE_JWT_SECRET` is set, verifies token locally without network call.
- ✅ **Supabase `/auth/v1/user` API verification** — HTTP GET with Bearer token to validate against Supabase.
- ✅ **`get_current_user()` FastAPI dependency** — Required auth; raises HTTP 401.
- ✅ **`get_optional_user()` FastAPI dependency** — Optional auth; returns `None` for anonymous.
- ⚠️ **Network failure fallback** — Trusts unverified JWT claims on network error (security issue).
- ❌ **`validate_jwt_token()` function** — Referenced in `server.py` but not defined.

---

### 3.4 HMAC Report Sharing ✅ (with caveats)
**File:** `server.py` — `generate_share_token()`, `verify_share_token()`  

- ✅ **HMAC-SHA256 token generation** — `hmac.new(secret, filename.encode(), sha256).hexdigest()[:24]`.
- ✅ **Constant-time comparison** — `hmac.compare_digest(expected, token)` prevents timing attacks.
- ✅ **Share token used to bypass auth on download** — `get_report_file()` checks token before requiring user auth.
- ✅ **Path traversal prevention on filename** — `Path(filename).name` strips directory components.
- ⚠️ **No expiry** — Tokens are permanent.
- ⚠️ **Secret fallback** — Default hardcoded secret.

---

## 4. ENCRYPTION & KEY MANAGEMENT

### 4.1 AES-256-GCM Document Encryption ✅
**File:** `crypto_utils.py`  

- ✅ **AES-256-GCM cipher** — Uses Python `cryptography` library's `AESGCM`.
- ✅ **12-byte random nonce** — `os.urandom(12)` per encryption call.
- ✅ **16-byte authentication tag** — Included automatically by `AESGCM.encrypt()`.
- ✅ **`ENC:GCM:` prefix marker** — Allows idempotent encrypt and plaintext passthrough.
- ✅ **Multi-format key parsing** — Accepts 64-char hex, 44-char base64, or raw 32-byte.
- ✅ **Key file auto-generation** — Creates `chroma.key` with `os.urandom(32)` if no key found.
- ✅ **Key persistence to .env** — `_persist_key_to_env()` writes to both `backend/.env` and root `.env`.
- ✅ **In-memory key caching** — `_CACHED_KEY` global prevents repeated file reads.
- ✅ **Graceful plaintext passthrough** — `decrypt_text()` returns plain string if not encrypted (migration support).

---

### 4.2 ChromaDB Encryption Integration ✅
**File:** `knowledge_base.py` — `index()`, `_get_vectorstore()`  

- ✅ **Encrypt document text, preserve embedding** — `index()` computes embeddings from plaintext, stores ciphertext as document body.
- ✅ **Monkey-patched decryption on retrieval** — `_decrypting_similarity_search()` wraps `similarity_search` to decrypt `page_content` on return.

---

### 4.3 ChromaDB Migration Tool ✅
**File:** `migrate_chroma_encryption.py` (242 lines)  

- ✅ **Dated JSON backup** — Creates `chroma_backup_YYYYMMDD_HHMMSS.json` before migration.
- ✅ **Encrypts existing collections** — Script iterates collections, encrypts unencrypted documents.

---

## 5. SESSION & HISTORY MANAGEMENT

### 5.1 SQLite Session Persistence ✅ (partially verified)
**File:** `session_manager.py`  

- ✅ **Sessions table** — 15-column schema: session_id, topic, timestamp, provider, critic_score, revision_count, status, saved_report_path, docx_path, pdf_path, citations_verified, citations_total, duration_seconds, notes, user_id.
- ✅ **Insert SQL** — Parameterized `INSERT INTO research_sessions ...` with named placeholders.
- ✅ **Fetch all sessions** — `_SELECT_ALL_SQL` ordered by timestamp DESC.
- ✅ **Fetch by user** — `_SELECT_BY_USER_SQL` with `WHERE user_id = ?`.
- ✅ **Fetch single session** — `_SELECT_ONE_SQL` with `WHERE session_id = ?`.
- ⚠️ **`user_owns_report()` method** — Called in server.py but not confirmed in reviewed session_manager.py lines.
- ⚠️ **`PipelineTimer`** — Referenced in main.py import but not reviewed.

---

## 6. LLM PROVIDER MANAGEMENT

### 6.1 Multi-Provider LLM Factory ✅
**File:** `agent.py` — `get_llm()`, `_build_llm_for_provider()`  

- ✅ **Groq support** — `ChatGroq(model=GROQ_MODEL)`. Default model: `openai/gpt-oss-120b`.
- ✅ **Google Gemini support** — `ChatGoogleGenerativeAI(model=GEMINI_MODEL)`. Default: `gemini-3.8-flash`. Max retries=2, timeout=30s.
- ✅ **Mistral support** — `ChatMistralAI(model=MISTRAL_MODEL)`. Default: `mistral-small-latest`.
- ✅ **HuggingFace support** — Via `hf_provider.get_hf_llm()`. Default model: Mistral-7B-Instruct-v0.3.
- ✅ **Provider priority order** — `["groq", "gemini", "mistral", "huggingface"]`.
- ✅ **Key presence check** — `_provider_has_key()` before attempting any provider.
- ✅ **3-attempt retry** — `_is_retryable()` checks for rate limits, timeouts, 5xx errors. Exponential backoff: 1s, 2s.
- ✅ **Automatic provider fallback** — On non-retryable failure, moves to next provider in chain.
- ✅ **Server-level fallback** — `_run_research_in_thread()` also implements a provider retry loop on top of `get_llm()`.

---

## 7. CLI INTERFACE

### 7.1 Command-Line Research Runner ✅
**File:** `main.py`  

- ✅ **Direct topic argument** — `--topic "Research question"`.
- ✅ **Provider selection** — `--provider gemini|huggingface|hf` (⚠️ groq/mistral missing).
- ✅ **Max revisions** — `--max-revisions N`.
- ✅ **Approval threshold** — `--threshold N`.
- ✅ **Output directory** — `--output-dir path`.
- ✅ **Non-interactive mode** — `--non-interactive` flag.
- ✅ **Architecture display** — `--arch` shows pipeline diagram.
- ✅ **History display** — `--history [--limit N]`.
- ✅ **Interactive terminal prompt** — Prompts for topic if `--topic` not given.

---

## 8. STARTUP & DEPLOYMENT

### 8.1 Project Launcher Scripts ✅
**File:** `start_quest.bat`, `stop_quest.bat`  

- ✅ **One-command startup** — Launches FastAPI backend (port 8000) and static frontend server.
- ✅ **Environment loading** — Loads both root and backend `.env` files.
- ✅ **Process teardown** — `stop_quest.bat` terminates Uvicorn and Python server processes.

---

## 9. WHAT IS NOT YET IMPLEMENTED

| Feature | Status | Notes |
|---|---|---|
| DOI validation | ❌ Not implemented | Claimed in `citation_validator.py` docstring |
| `validate_jwt_token()` function | ❌ Missing | Referenced in `server.py` import |
| `user_owns_report()` method | ⚠️ Unconfirmed | Referenced in server.py; not found in reviewed lines |
| Share token expiry | ❌ Not implemented | Tokens are permanent |
| Human-in-the-loop approval | ❌ Not implemented | `interactive=True` path exists but unused from API |
| Rate limiting / request throttling | ❌ Not implemented | No middleware |
| WebSocket support | ❌ Not implemented | Only SSE |
| Webhook notifications | ❌ Not implemented | No outbound webhook on completion |
| Background job queue | ❌ Not implemented | Each request runs in-process thread |
| Multi-user tenant isolation in ChromaDB | ❌ Not implemented | Collections are per-topic, not per-user |

