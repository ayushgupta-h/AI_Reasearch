# CURRENT_ARCHITECTURE.md
# Research AI Quest — Verified Current Architecture
> **Last Updated:** November 10, 2026  
> **Source of Truth:** Direct code inspection of all 16 backend source files  
> **Legend:** ✅ Verified in code | ⚠️ Partially implemented | ❌ Claimed but not found

---

## 1. System Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        CLIENT LAYER                                  │
│   frontend/index.html   frontend/new.html   frontend/report.html     │
│   frontend/history.html  frontend/dashboard.html  frontend/auth.js   │
└──────────────────┬───────────────────────────────────────────────────┘
                   │ HTTP + SSE (port 8000)
┌──────────────────▼───────────────────────────────────────────────────┐
│                     FASTAPI SERVER (server.py)                       │
│  POST /research → SSE stream     GET /health                         │
│  GET /sessions                   DELETE /sessions/{id}               │
│  GET /reports/{filename}         POST /reports/{filename}/share      │
│  GET /reports/{filename:path}    POST /export                        │
│  POST /chat                      GET /auth/config                    │
│  GET /auth/me                    GET /providers                      │
└───────────┬────────────────────────────────────────────────────────-─┘
            │ threading.Thread (blocking pipeline)
┌───────────▼──────────────────────────────────────────────────────────┐
│              MULTI-AGENT ORCHESTRATOR (orchestrator.py)              │
│           LangGraph StateGraph — 9 Nodes, Cyclic                     │
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │
│  │  Intake  │→ │  Search  │→ │  Reader  │→ │  RAG KB  │            │
│  │ (Node 1) │  │ (Node 2) │  │ (Node 3) │  │(Node 3.5)│            │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘            │
│        ↓                                           ↓                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │
│  │ Exporter │← │ Refiner  │← │  Critic  │← │ Analyst  │            │
│  │ (Node 8) │  │ (Node 7) │  │ (Node 6) │  │ (Node 4) │            │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘            │
│                      ↑ loop if score < threshold                     │
│                              ┌──────────┐                            │
│                              │  Writer  │                            │
│                              │ (Node 5) │                            │
│                              └──────────┘                            │
└──────────────────────────────────────────────────────────────────────┘
            │                       │                      │
   ┌────────▼──────┐      ┌────────▼──────┐    ┌─────────▼──────┐
   │ LLM Providers │      │  Search Layer  │    │  Storage Layer │
   │  (agent.py)   │      │(search_sources)│    │                │
   │  - Groq       │      │  - arXiv       │    │  ChromaDB      │
   │  - Gemini     │      │  - Wikipedia   │    │  (AES-256-GCM) │
   │  - Mistral    │      │  - DuckDuckGo  │    │                │
   │  - HuggingFace│      │  - Tavily      │    │  SQLite        │
   └───────────────┘      │  - HuggingFace │    │  sessions.db   │
                          │  - NewsAPI     │    │                │
                          │  - Google Schol│    │  File System   │
                          └────────────────┘    │  reports/      │
                                                └────────────────┘
```

---

## 2. File-by-File Component Map

### `server.py` (836 lines) ✅ Verified
**Role:** FastAPI REST server, SSE streaming, auth wiring, file serving  
**Key Classes / Functions:**
- `ResearchRequest` — Pydantic API request model (topic, provider, max_revisions, approval_threshold, output_dir, user_id)
- `HealthResponse`, `ProvidersResponse`, `SessionsResponse`, `ChatRequest`, `ChatResponse`, `ExportRequest` — Pydantic response/request models
- `_SSEWriter` — File-like object redirecting `sys.stdout` writes to a `queue.Queue` as SSE events
- `_sse_format()` — Formats dict as SSE `event: ... \ndata: ...\n\n` string
- `_build_event()` — Constructs SSE event payloads with optional agent/data fields
- `_strip_ansi()` — Strips ANSI escape codes and Rich markup from terminal output
- `_detect_agent()` — Heuristic pattern-matching to label which agent produced a log line
- `_is_provider_error()` — Classifies exceptions as provider-switching candidates (rate limit, quota, auth)
- `_chat_provider_chain()` — Returns ordered list of providers with configured keys for chat
- `_chat_via_provider()` — Single-turn LLM call for the `/chat` endpoint
- `_run_research_in_thread()` — Blocking function run in `threading.Thread`, drives `MultiAgentOrchestrator.run()`
- `_sse_generator()` — Async generator that starts the thread and polls queue for SSE events
- `generate_share_token()` / `verify_share_token()` — HMAC-SHA256 token generation and verification
- `_get_share_secret()` — Reads `SHARE_SECRET` env var (falls back to hardcoded string ⚠️)

**Endpoints:**
| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/health` | None | Returns `{status, version}` |
| GET | `/auth/config` | None | Returns Supabase URL and anon key |
| GET | `/auth/me` | Required | Returns current user profile |
| GET | `/providers` | None | Lists supported LLM providers |
| GET | `/sessions` | Optional | Filters by user_id if authenticated |
| DELETE | `/sessions/{session_id}` | Required | Verifies ownership |
| GET | `/reports/{filename:path}/share` | Optional | Generates HMAC share token |
| GET | `/reports/{filename:path}` | Optional+Token | Verifies ownership or share token |
| POST | `/export` | Optional | Exports markdown to PDF/DOCX/MD/TXT |
| POST | `/chat` | None | Single-turn chat with provider fallback |
| POST | `/research` | Optional | Streams SSE pipeline execution |

---

### `orchestrator.py` (870 lines) ✅ Partially Reviewed (lines 1–450)
**Role:** LangGraph stateful multi-agent workflow  
**Key Classes:**
- `ResearchState` (TypedDict) — Global state schema:
  - Input: `topic`, `provider`, `interactive`, `max_revisions`, `approval_threshold`, `output_dir`
  - Strategist output: `research_request`
  - Search output: `search_results_raw`, `search_results`, `top_urls`, `source_summary`
  - Reader output: `scraped_content`
  - RAG output: `rag_context`
  - Analyst output: `analysis`
  - Writer output: `report`
  - Critic output: `critic_score`, `critic_evaluation`, `critic_feedback`
  - Refiner control: `revision_count`, `revision_history`
  - Final output: `final_report`, `saved_report_path`, `status`, `logs`
- `MultiAgentOrchestrator` — Main class; `__init__` builds all chains; `_build_graph()` compiles graph; `run()` executes
- **Verified nodes:** `intake_node()`, `search_node()`, `reader_node()`, `rag_node()`, `analyst_node()`, `writer_node()`
- **Not fully reviewed:** `critic_node()`, `refiner_node()`, `exporter_node()`, `_build_graph()`, conditional edges

**Async search strategy in `search_node()`:**
1. Try `asyncio.run()` → raises if loop already running
2. Fallback: `nest_asyncio.apply()` + `loop.run_until_complete()`
3. Final fallback: Sequential per-sub-query search

---

### `agent.py` (400 lines) ✅ Fully Verified
**Role:** LLM factory and 6 specialized agent/chain builders  
**Key Constants:**
- `PROVIDER_ORDER = ["groq", "gemini", "mistral", "huggingface"]`

**Key Functions:**
- `_provider_has_key(provider)` — Checks env vars for a given provider
- `_build_llm_for_provider(provider, temperature)` — Builds LLM object for a provider
- `_is_retryable(exc)` — Classifies exception as transient (rate limit, timeout, etc.)
- `get_llm(provider, temperature)` — Multi-provider LLM factory with 3-attempt retry and fallback chain

**Key Models:**
- `CriticEvaluation` (Pydantic) — Structured critic output: `score`, `passed`, `strengths`, `areas_for_improvement`, `actionable_revisions`, `verdict`

**Builder Functions:**
- `build_search_agent(model)` — LangChain tool-calling agent with `multi_source_search` and `web_search`
- `build_reader_agent(model)` — LangChain tool-calling agent with `scrape_url`
- `build_analyst_chain(model)` — `ANALYST_PROMPT | llm | StrOutputParser()`
- `build_writer_chain(model)` — `WRITER_PROMPT | llm | StrOutputParser()`
- `build_critic_chain(model)` — `CRITIC_PROMPT | llm | JsonOutputParser(CriticEvaluation)` (structured JSON)
- `build_critic_text_chain(model)` — ⚠️ Dead code — plain text critic not used by orchestrator
- `build_refiner_chain(model)` — `REFINER_PROMPT | llm | StrOutputParser()`

**Module-Level Side Effects:** ⚠️
- `llm = get_llm()` — Called at import time, may fail if no keys configured
- `analyst_chain = build_analyst_chain()`, `writer_chain = build_writer_chain()`, `critic_chain = build_critic_chain()`, `refiner_chain = build_refiner_chain()` — All built at import time using the module-level `llm`

---

### `query_intake.py` (311 lines) ✅ Fully Verified
**Role:** Research query validation and decomposition  
**Key Classes:**
- `QueryValidation` (Pydantic) — `is_valid`, `clarity_score`, `issues`, `suggestions`
- `ResearchRequest` (Pydantic) ⚠️ **Same name as server.py model** — `original_query`, `refined_query`, `research_type`, `sub_queries`, `focus_areas`, `scope`, `time_sensitivity`, `suggested_source_types`
- `QueryIntakeSystem` — Main class:
  - `validate_query(query)` — LLM validation chain, returns `QueryValidation`
  - `decompose_query(query)` — LLM decomposition chain, returns `ResearchRequest`
  - `process(query)` — Full interactive pipeline with user confirmation (CLI mode only)

---

### `search_sources.py` (643 lines) ✅ Partially Verified (lines 1–200)
**Role:** Multi-source search aggregation  
**Base Class:** `BaseSearchSource` (ABC) — `search()`, `is_available()`  
**Data Model:** `SearchResult` (Pydantic) — `title`, `url`, `snippet`, `source_name`, `source_type`

**Verified Source Implementations:**
| Class | Source | Key Required | Type |
|---|---|---|---|
| `TavilySearch` | Tavily API | `TAVILY_API_KEY` | web |
| `ArxivSearch` | arXiv Atom API | None (free) | academic |
| `WikipediaSearch` | MediaWiki API | None (free) | encyclopedia |
| `DuckDuckGoSearch` | duckduckgo_search pkg | None | web |

**Not fully reviewed (lines 200–643):** HuggingFace, NewsAPI, Google Scholar adapters; `SearchOrchestrator` class (async search, deduplication, formatting)

---

### `knowledge_base.py` (508 lines) ✅ Fully Verified
**Role:** ChromaDB RAG vector store with AES-256-GCM encryption  
**Key Constants:** `_CHUNK_SIZE=800`, `_CHUNK_OVERLAP=100`  
**Key Functions:**
- `_get_embeddings()` — HuggingFaceEmbeddings (all-MiniLM-L6-v2) or FakeEmbeddings fallback
- `_chunk_text(text, chunk_size, overlap)` — Paragraph-aware recursive text splitter
- `_make_doc_id(text, prefix)` — MD5-based stable chunk IDs

**`ResearchKnowledgeBase` class:**
- `__init__(topic, persist_dir, embeddings)` — Lazy-initializes vectorstore
- `_make_collection_name(topic)` — Slugifies topic → ChromaDB collection name: `rai-{slug}`
- `_get_vectorstore()` — Lazy init: ChromaDB Cloud (if keys set) or local `./chroma_db/`; wraps `similarity_search` to transparently decrypt page_content
- `index(texts, metadatas, source_label)` — Chunks → AES-encrypts → upserts to ChromaDB with precomputed plaintext embeddings
- `index_search_results(search_results_raw)` — Formats and indexes list of search result dicts
- `index_scraped_content(scraped_text, url)` — Indexes raw scraped string
- `retrieve(query, k, filter_meta)` — Semantic similarity search; decrypts on return ⚠️ k-cap bug
- `retrieve_for_analysis(query, k, max_chars)` — Formatted retrieval for analyst prompt

---

### `crypto_utils.py` (215 lines) ✅ Fully Verified
**Role:** AES-256-GCM symmetric encryption for ChromaDB document text  
**Constants:** `_CIPHERTEXT_PREFIX = "ENC:GCM:"`, `_DEFAULT_KEY_PATH = backend/chroma.key`  
**Key Management Priority:**
1. `CHROMA_ENCRYPTION_KEY` env var (hex or base64)
2. `backend/chroma.key` file (hex string)
3. Auto-generate 32 random bytes → save to `chroma.key` + `.env` files

**Functions:**
- `get_or_create_chroma_key()` → `bytes` — Retrieves or generates 32-byte AES key
- `_parse_key_string(val)` — Parses hex (64 chars), base64 (44 chars), or raw 32-byte string
- `_persist_key_to_env(hex_key)` — Appends/updates `CHROMA_ENCRYPTION_KEY` in both `.env` files
- `is_encrypted(text)` → `bool` — Checks for `"ENC:GCM:"` prefix
- `encrypt_text(text, key)` → `str` — AES-256-GCM with 12-byte random nonce; returns `ENC:GCM:{base64(nonce+ciphertext+tag)}`
- `decrypt_text(ciphertext, key)` → `str` — Decrypts; passes through unencrypted text gracefully

---

### `auth_validator.py` (234 lines) ✅ Fully Verified
**Role:** Supabase JWT authentication middleware  
**Token Validation Strategy (in order):**
1. In-memory TTL cache check (60-second TTL)
2. Unverified JWT decode for expiry pre-check
3. If `SUPABASE_JWT_SECRET` set → PyJWT HS256 local verification
4. HTTP call to `{SUPABASE_URL}/auth/v1/user`
5. Network failure fallback → trust unverified claims if `aud == "authenticated"` ⚠️

**FastAPI Dependencies:**
- `get_current_user()` — Requires valid JWT; raises HTTP 401 on failure
- `get_optional_user()` — Returns user dict or `None` for anonymous access
- `get_supabase_url()` / `get_supabase_anon_key()` — Config accessors (with hardcoded fallbacks ⚠️)

**Missing:** `validate_jwt_token()` — imported in `server.py` but not defined here ❌

---

### `citation_validator.py` (383 lines) ✅ Verified
**Role:** Concurrent HTTP URL validation for research report citations  
**Models:** `CitationResult` (dataclass), `CitationReport` (dataclass)  
**`CitationValidator` class:**
- `extract_urls(text)` → `List[str]` — Regex-based URL extraction with deduplication
- `extract_domain(url)` — urllib.parse netloc extraction
- `_check_url(url)` — HEAD request → GET fallback for 403/405; returns `CitationResult`
- `validate_report(markdown_text)` — Runs all URLs concurrently via `ThreadPoolExecutor(max_workers=12)`
- `display_report(report)` — Rich table display of validation results
- `inject_appendix(markdown_text, report)` — Appends citation status table to markdown

**Missing:** ❌ DOI validation (claimed in docstring, not implemented)

---

### `report_exporter.py` (787 lines) ✅ Partially Verified (lines 1–150)
**Role:** Multi-format research report export  
**Verified functions:**
- `apply_citation_style(markdown_text, style)` — Formats references section in APA/MLA/Harvard style; also auto-generates references section from inline links if none exists
- `_parse_markdown_table(lines)` — Parses markdown table syntax into (headers, rows)

**Not fully reviewed:** `ReportExporter` class, PDF generation, DOCX generation, `export_single()` method signature — ⚠️ method existence cannot be fully confirmed from lines 1–150

---

### `session_manager.py` (506 lines) ✅ Partially Verified (lines 1–120)
**Role:** SQLite session persistence  
**Database:** `backend/sessions.db`  
**Table schema:** `research_sessions` with columns: `session_id` (UUID PK), `topic`, `timestamp`, `provider`, `critic_score`, `revision_count`, `status`, `saved_report_path`, `docx_path`, `pdf_path`, `citations_verified`, `citations_total`, `duration_seconds`, `notes`, `user_id`  
**SQL queries reviewed:** `_CREATE_TABLE_SQL`, `_INSERT_SESSION_SQL`, `_SELECT_ALL_SQL`, `_SELECT_BY_USER_SQL`, `_SELECT_ONE_SQL`  
**Not fully reviewed:** `SessionManager` class methods, `PipelineTimer`, `user_owns_report()` existence ⚠️

---

### `tools.py` (58 lines) ✅ Fully Verified
**Role:** LangChain `@tool`-decorated wrappers for search and scraping  
**Tools:**
- `web_search(query)` — Calls `TavilyClient.search()`, returns formatted title/url/snippet strings ⚠️ crashes if no `TAVILY_API_KEY`
- `scrape_url(url)` — `requests.get` + BeautifulSoup text extraction, 3,000-char truncation
- `multi_source_search(query)` — Wraps `SearchOrchestrator.search()`, returns formatted string

---

### `hf_provider.py` (183 lines) ✅ Fully Verified
**Role:** HuggingFace Inference API LLM adapter  
**Available Models:** zephyr (HuggingFaceH4/zephyr-7b-beta), mistral (Mistral-7B-Instruct-v0.3), phi3 (Phi-3-mini-4k-instruct), qwen (Qwen2.5-7B-Instruct)  
**Functions:**
- `get_hf_llm(model_key, max_new_tokens)` — Builds `ChatHuggingFace` via `HuggingFaceEndpoint`
- `is_hf_available()` — Checks `HF_TOKEN`
- `list_available_models()` — Returns model dict
- `get_hf_writer_chain()` ⚠️ Dead code — not used by orchestrator
- `get_hf_critic_chain()` ⚠️ Dead code — not used by orchestrator

---

### `main.py` (162 lines) ✅ Partially Verified (lines 1–100)
**Role:** CLI entry point  
**Args:** `--topic`, `--provider` (choices: `gemini`, `huggingface`, `hf` ⚠️ missing groq/mistral), `--max-revisions`, `--threshold`, `--output-dir`, `--non-interactive`, `--arch`, `--history`, `--limit`

---

### `migrate_chroma_encryption.py` (242 lines) — Not Reviewed in Detail
**Role:** One-time migration script to encrypt existing unencrypted ChromaDB collections  
**Outputs:** `chroma_backup_*.json` dated backup before migration  
**Status:** ✅ File exists; execution-path correctness not audited

---

### `pipeline.py` (1,249 bytes) ⚠️ Dead Code
**Status:** File exists but is not imported by any active module. Superseded by `orchestrator.py`.

---

## 3. Data Flow Diagram

```
API Request: POST /research { topic, provider, max_revisions, ... }
     │
     ▼
ResearchRequest (server.py Pydantic model) → validated
     │
     ▼
_sse_generator() starts threading.Thread(_run_research_in_thread)
     │
     ├─ sys.stdout → _SSEWriter → queue.Queue → SSE yield loop
     │
     ▼
MultiAgentOrchestrator.run(topic) → LangGraph graph.stream()
     │
     ├─ Node 1: intake_node()
     │    └─ QueryIntakeSystem.validate_query() + decompose_query()
     │    └─ Returns: research_request (sub_queries, focus_areas, etc.)
     │
     ├─ Node 2: search_node()
     │    └─ SearchOrchestrator.async_search_all(sub_queries)
     │    └─ Returns: search_results_raw, top_urls, search_results, source_summary
     │
     ├─ Node 3: reader_node()
     │    └─ reader_agent.invoke(top_urls) → scrapes 2-3 pages
     │    └─ Returns: scraped_content
     │
     ├─ Node 3.5: rag_node()
     │    └─ ResearchKnowledgeBase.index_search_results() + index_scraped_content()
     │    └─ ResearchKnowledgeBase.retrieve_for_analysis() × N queries
     │    └─ Returns: rag_context
     │
     ├─ Node 4: analyst_node()
     │    └─ analyst_chain.invoke(topic, rag_context, search_results, scraped_content)
     │    └─ Returns: analysis
     │
     ├─ Node 5: writer_node()
     │    └─ writer_chain.invoke(topic, analysis, research)
     │    └─ Returns: report (draft Markdown)
     │
     ├─ Node 6: critic_node() [not fully reviewed]
     │    └─ critic_chain.invoke(topic, report) → CriticEvaluation JSON
     │    └─ Returns: critic_score, critic_feedback, critic_evaluation
     │
     ├─ Node 7: refiner_node() [conditional] [not fully reviewed]
     │    └─ if critic_score < approval_threshold AND revision_count < max_revisions
     │    └─ refiner_chain.invoke(report, critic_feedback) → revised report
     │    └─ Returns: report (revised), revision_count++
     │    └─ → back to Node 6
     │
     └─ Node 8: exporter_node() [not fully reviewed]
          └─ CitationValidator.validate_report() + inject_appendix()
          └─ ReportExporter → save .md, .docx, .pdf
          └─ Returns: final_report, saved_report_path, status="completed"
     │
     ▼
SessionManager().save_session(final_state, duration, user_id)
     │
     ▼
SSE event: "complete" { status, critic_score, revision_count, saved_report_path }
```

---

## 4. Environment Variable Reference

| Variable | Used In | Required | Default |
|---|---|---|---|
| `GROQ_API_KEY` | `agent.py` | No | None |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | `agent.py` | No | None |
| `MISTRAL_API_KEY` | `agent.py` | No | None |
| `HF_TOKEN` / `HUGGINGFACEHUB_API_TOKEN` | `agent.py`, `hf_provider.py` | No | None |
| `LLM_PROVIDER` | `agent.py`, `server.py` | No | `"groq"` |
| `GROQ_MODEL` | `agent.py` | No | `"openai/gpt-oss-120b"` |
| `GEMINI_MODEL` | `agent.py` | No | `"gemini-3.8-flash"` |
| `MISTRAL_MODEL` | `agent.py` | No | `"mistral-small-latest"` |
| `HF_MODEL` | `agent.py` | No | `"mistral"` |
| `TAVILY_API_KEY` | `tools.py`, `search_sources.py` | ⚠️ Required at import | None (crashes) |
| `SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_URL` | `auth_validator.py` | No | Hardcoded fallback |
| `SUPABASE_ANON_KEY` / `SUPABASE_PUBLISHABLE_KEY` | `auth_validator.py` | No | ⚠️ Hardcoded fallback |
| `SUPABASE_JWT_SECRET` | `auth_validator.py` | No | None (skips local verify) |
| `SHARE_SECRET` | `server.py` | No | ⚠️ `"quest_report_share_secret_2026"` |
| `CHROMA_ENCRYPTION_KEY` | `crypto_utils.py` | No | Auto-generated → `chroma.key` |
| `CHROMA_KEY_PATH` | `crypto_utils.py` | No | `backend/chroma.key` |
| `CHROMA_API_KEY` | `knowledge_base.py` | No | None (local fallback) |
| `CHROMA_TENANT` | `knowledge_base.py` | No | None |
| `CHROMA_DATABASE` | `knowledge_base.py` | No | None |
| `NEWS_API_KEY` | `search_sources.py` | No | None (source disabled) |
| `SERP_API_KEY` | `search_sources.py` | No | None (source disabled) |

