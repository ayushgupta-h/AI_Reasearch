# Research AI Quest — Backend Context

> **Generated on:** November 10, 2026  
> **Repository:** `Research_AI`  
> **Component:** Backend Engine & APIs  

---

## 1. Executive Summary

The **Research AI Quest Backend** is a production-grade, multi-agent automated research and report-generation system. Given any research query or open-ended topic, it automatically decomposes the query, retrieves information across both academic and general web search sources, persists vectorized context in an encrypted ChromaDB database, performs deep cross-source synthesis, drafts an academic-grade report, conducts an iterative review & refinement loop via an LLM critic, verifies citations and links asynchronously, and exports the final output to Markdown, formatted DOCX, and PDF.

The entire workflow is orchestrated via **LangGraph**, wrapped in a **FastAPI** server that streams real-time execution progress to the frontend using **Server-Sent Events (SSE)**.

---

## 2. Directory Structure

```
backend/
├── agent.py                   # 6 specialized LangChain agent & chain factory functions
├── orchestrator.py            # LangGraph multi-agent cyclic state machine (9 nodes)
├── query_intake.py            # Query decomposition, intent detection, and scoping
├── search_sources.py          # 7+ search adapters (arXiv, PubMed, DDG, Semantic Scholar, etc.)
├── tools.py                   # Web search & deep page scraper tool wrappers
├── knowledge_base.py          # ChromaDB RAG vector store abstraction
├── crypto_utils.py            # AES-256-GCM at-rest encryption utilities for vectors
├── migrate_chroma_encryption.py # Utility script to migrate/encrypt legacy vector collections
├── citation_validator.py      # Async HTTP link & DOI checker with appendix builder
├── report_exporter.py         # Multi-format report exporter (MD, styled DOCX, PDF)
├── session_manager.py         # SQLite session persistence & step-level execution timer
├── auth_validator.py          # Supabase JWT token validation & user extraction
├── server.py                  # FastAPI REST API with real-time SSE streaming & follow-up chat
├── main.py                    # CLI entry point for interactive and headless runs
├── pipeline.py                # Lightweight pipeline runner
├── hf_provider.py             # Hugging Face LLM provider adapter
├── .env                       # API keys and environment variables (ignored by git)
├── chroma.key                 # Master symmetric encryption key for ChromaDB
├── sessions.db                # SQLite database recording history and metrics
├── reports/                   # Output folder for generated .md, .docx, and .pdf documents
├── chroma_db/                 # Encrypted ChromaDB persistent directory
└── tests/                     # Pytest suite for agents, orchestrator, and sources
    ├── conftest.py
    ├── test_agent.py
    ├── test_knowledge_base.py
    ├── test_orchestrator.py
    └── test_search_sources.py
```

---

## 3. Pipeline Architecture & Execution Flow

The workflow is modeled as a stateful cyclic directed graph using **LangGraph**:

```
                       [ User Query / API Request ]
                                    │
                                    ▼
                         [ 1. Query Intake ]
                    (Decompose into Sub-queries)
                                    │
                                    ▼
                      [ 2. Multi-Source Search ]
               (arXiv, PubMed, DDG, Semantic Scholar, Wiki)
                                    │
                                    ▼
                          [ 3. Reader Agent ]
                     (Headless Scrape & Clean Text)
                                    │
                                    ▼
                       [ 4. Encrypted ChromaDB ]
                      (Vector Chunking & Storage)
                                    │
                                    ▼
                          [ 5. Analyst Chain ]
                      (Cross-Source RAG Synthesis)
                                    │
                                    ▼
                          [ 6. Writer Chain ]
                      (Academic Draft Generation)
                                    │
                                    ▼
                          [ 7. Critic Chain ]
                       (Rubric Scoring 0 to 10)
                                    │
            ┌───────────────────────┴───────────────────────┐
   Score < Threshold &                             Score >= Threshold or
    Revisions Remaining                             Max Revisions Reached
            │                                               │
            ▼                                               ▼
   [ 8. Refiner Chain ]                          [ 9. Citation Validator ]
 (Targeted Self-Correction)                       (Async HTTP & DOI Check)
            │                                               │
            └───────────────► (Re-eval)                     ▼
                                                 [ 10. Report Exporter ]
                                                   (.md, .docx, .pdf)
                                                            │
                                                            ▼
                                                 [ 11. Session Manager ]
                                                (Saved to SQLite / SSE Done)
```

---

## 4. Key Components & Implementation Details

### 4.1 Multi-Agent Engine (`agent.py`)
Configures 6 distinct agent roles using LangChain and Pydantic structured output parsers:
1. **Search Agent**: Coordinates query expansion and search source calls.
2. **Reader Agent**: Performs deep scraping of high-relevance URLs.
3. **Analyst Chain**: Identifies consensus, contradictions, evidence gaps, and synthesizes key insights.
4. **Writer Chain**: Generates a publication-grade academic report with executive summary, methodology, body sections, and bibliography.
5. **Critic Chain**: Evaluates drafts across clarity, depth, accuracy, and structure, returning structured JSON containing feedback and a numerical score (0.0 to 10.0).
6. **Refiner Chain**: Modifies and improves drafts specifically addressing the Critic's critiques.

**Multi-Provider Fallback**:
Provider fallback chain: `Groq` &rarr; `Gemini` &rarr; `Mistral` &rarr; `Hugging Face`. If rate limits (429), quota exhaustion, or missing keys occur, the engine fails over transparently to the next available provider.

### 4.2 Multi-Source Retrieval (`search_sources.py` & `tools.py`)
Integrated adapters:
- **arXiv**: Academic pre-prints in physics, CS, math, and AI.
- **PubMed**: Biomedical and life sciences literature.
- **Semantic Scholar**: Citation graphs and academic papers.
- **DuckDuckGo**: General web search, news, and technical blogs.
- **Wikipedia**: Domain definitions and conceptual overviews.
- **SerpAPI / Google Scholar**: Fallback for scholarly queries.

### 4.3 Encrypted Vector Store (`knowledge_base.py` & `crypto_utils.py`)
- **Engine**: ChromaDB with `sentence-transformers` embeddings.
- **Chunking**: Recursive text splitting (1,000 characters, 200 overlap).
- **At-Rest Encryption**: Symmetrically encrypts document text and metadata with **AES-256-GCM** using a local `chroma.key` before persisting to disk; decrypts on RAG retrieval.
- **Migration**: Includes `migrate_chroma_encryption.py` with automated JSON backup creation.

### 4.4 Citation Validation (`citation_validator.py`)
- Concurrently pings referenced URLs using asynchronous HTTP requests (`httpx`/`aiohttp`).
- Resolves and verifies DOI links.
- Flags live, redirected, and broken sources.
- Appends a structured **Citation & Verification Appendix** to the final report.

### 4.5 Multi-Format Document Exporter (`report_exporter.py`)
- **Markdown (`.md`)**: Full structured text.
- **Microsoft Word (`.docx`)**: Custom styled document via `python-docx` with colored headers, custom callout quote blocks, shaded table rows, and page breaks.
- **PDF (`.pdf`)**: Formatted document with clean academic print styling and typography.

### 4.6 REST API & Real-Time Streaming (`server.py`)
- Built on **FastAPI** with CORS enabled for frontend clients (`localhost:5500`, `localhost:8123`, `localhost:3000`).
- **Endpoints**:
  - `POST /research`: Spawns research pipeline in a background thread and streams progress events (`agent_start`, `progress`, `agent_done`, `complete`, `error`) via **Server-Sent Events (SSE)**.
  - `POST /chat`: Context-aware Q&A allowing users to converse with completed research reports.
  - `GET /health` & `GET /providers`: System status and list of active LLM backends.
  - `GET /sessions` & `DELETE /sessions/{id}`: View and manage research history.
  - `GET /reports/{filename}`: Safe download for generated reports.
  - `POST /reports/{filename}/share`: Generates HMAC-SHA256 signed public download tokens.
- **Authentication (`auth_validator.py`)**:
  - Integrates with **Supabase Auth** (JWT verification via JWKS / secret).
  - Supports both authenticated and optional/guest modes.

### 4.7 Session & History Tracking (`session_manager.py`)
- Backed by SQLite ([`sessions.db`](file:///c:/Users/aaayu/OneDrive/Desktop/Reasearch_AI/backend/sessions.db)).
- Records session ID, user ID, topic, provider, duration, final critic score, revision count, and file paths.
- Step-level latency timing via `PipelineTimer`.

---

## 5. Environment Variables & Configuration (`.env`)

```ini
# LLM Provider Keys
GROQ_API_KEY=your_groq_api_key
GEMINI_API_KEY=your_gemini_api_key
GOOGLE_API_KEY=your_google_api_key
MISTRAL_API_KEY=your_mistral_api_key
HF_TOKEN=your_huggingface_token

# Default LLM Provider (groq | gemini | mistral | huggingface)
LLM_PROVIDER=groq

# Search Engine APIs (optional fallbacks)
SERPAPI_API_KEY=your_serpapi_key

# Supabase Auth Configuration
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_supabase_anon_key
SUPABASE_JWT_SECRET=your_jwt_secret

# Security & Secrets
SHARE_SECRET_KEY=your_random_hmac_secret_key
CHROMA_ENCRYPTION_KEY=your_aes_key_or_reads_from_chroma.key
```

---

## 6. How to Run & Operate

### Launch via Quick Batch Script (Root)
```cmd
start_quest.bat
```
*(Starts the FastAPI backend on port 8000 and the frontend on port 5500/8123).*

### Run the FastAPI Server Directly
```bash
cd backend
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

### Run via Command-Line Interface (Interactive or Headless)
```bash
# Interactive topic prompt
python backend/main.py

# Direct topic execution
python backend/main.py --topic "Impact of LLMs on scientific discovery"

# Review past session history
python backend/main.py --history
```

### Run Tests
```bash
pytest backend/tests/ -v
```

