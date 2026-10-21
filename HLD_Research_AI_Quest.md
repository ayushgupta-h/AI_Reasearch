# 🧠 High-Level Design (HLD) — Research AI: Quest
### Autonomous Multi-Agent Research Orchestrator

> **Version:** 1.0 | **Date:** October 2026 | **Status:** Active

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture Diagram](#2-system-architecture-diagram)
3. [Component Table](#3-component-table)
4. [8-Agent Pipeline Flow](#4-8-agent-pipeline-flow)
5. [Data Flow Diagram](#5-data-flow-diagram)
6. [API Contract Summary](#6-api-contract-summary)
7. [LLM Provider Fallback Chain](#7-llm-provider-fallback-chain)
8. [Storage Layer](#8-storage-layer)
9. [Non-Functional Requirements](#9-non-functional-requirements)
10. [Folder Structure](#10-folder-structure)

---

## 1. Project Overview

**Research AI — Quest** is a fully autonomous, web-based research platform. A user types a research topic, and 8 specialized AI agents work together — searching the web, reading articles, analyzing facts, writing a structured report, and peer-reviewing it — before delivering a publication-grade document.

### Core Idea in One Sentence
> *"You ask a question. Eight AI agents go research it and hand you back a polished report."*

### Key Capabilities

| Capability | Description |
|---|---|
| Multi-Agent Orchestration | 8 agents with distinct roles, coordinated by LangGraph |
| Live Progress Streaming | Real-time agent updates via Server-Sent Events (SSE) |
| Multi-Source Search | Tavily, arXiv, Wikipedia, DuckDuckGo searched simultaneously |
| Self-Correcting Reports | Critic → Refiner loop until quality score ≥ 8/10 |
| Multi-Format Export | Reports saved as `.md`, `.docx`, and `.pdf` |
| Session History | Every research run stored in SQLite, viewable in UI |
| LLM Fallback Chain | Gemini → Groq → Mistral → HuggingFace (automatic failover) |

---

## 2. System Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                         USER'S BROWSER                               │
│                                                                      │
│  ┌───────────┐  ┌──────────┐  ┌─────────────┐  ┌────────────────┐  │
│  │index.html │  │ new.html │  │ report.html │  │ history.html   │  │
│  │ (Landing) │  │ (Submit) │  │ (View rpt.) │  │ (Past Runs)    │  │
│  └───────────┘  └────┬─────┘  └──────┬──────┘  └───────┬────────┘  │
│                       │               │                  │           │
└───────────────────────┼───────────────┼──────────────────┼───────────┘
                        │ POST+SSE      │ GET .md file     │ GET /sessions
                        ▼              ▼                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  BACKEND — FastAPI  (port 8000)                      │
│                                                                      │
│  ┌───────────────┐    ┌───────────────────────────────────────────┐ │
│  │   server.py   │    │            orchestrator.py                │ │
│  │               │    │          (LangGraph State Graph)          │ │
│  │ POST /research├───►│                                           │ │
│  │ POST /chat    │    │  Agent1 → Agent2 → Agent3 → Agent4        │ │
│  │ GET  /sessions│    │       → Agent5 → [Critic ↔ Refiner]      │ │
│  │ GET  /health  │    │                         → Agent8          │ │
│  └───────────────┘    └───────────────────┬───────────────────────┘ │
│                                           │                          │
│       ┌───────────────────────────────────┼──────────────────┐      │
│       ▼               ▼                   ▼                  ▼       │
│  ┌──────────┐   ┌──────────┐   ┌──────────────┐   ┌──────────────┐ │
│  │ ChromaDB │   │  SQLite  │   │  reports/    │   │  LLM APIs    │ │
│  │(Vector KB│   │(Sessions)│   │(.md/.docx/   │   │(Gemini/Groq/ │ │
│  └──────────┘   └──────────┘   │  .pdf)       │   │ Mistral/HF)  │ │
│                                 └──────────────┘   └──────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
                        │
          ┌─────────────┼────────────────┐
          ▼             ▼                ▼
   ┌──────────┐  ┌─────────────┐  ┌──────────────────┐
   │  Tavily  │  │  arXiv API  │  │ Wikipedia /      │
   │  Search  │  │ (Academic)  │  │ DuckDuckGo       │
   └──────────┘  └─────────────┘  └──────────────────┘
```

---

## 3. Component Table

### Frontend Components

| File | Role | Technology |
|---|---|---|
| `index.html` | Landing page — intro, features, call-to-action | HTML, CSS, JS |
| `new.html` | Research submission form + live SSE progress feed | HTML, JS (EventSource API) |
| `report.html` | Renders the final Markdown report in browser | HTML, JS (Markdown parser) |
| `history.html` | Lists all past research sessions with scores | HTML, JS (fetch /sessions) |
| `dashboard.html` | Analytics: avg score, total runs, provider stats | HTML, JS |
| `login.html` | Auth/sign-in page | HTML, CSS, JS |
| `desk.html` | Redirect shim to `new.html` | HTML |

### Backend Components

| File | Role | Technology |
|---|---|---|
| `server.py` | Main API gateway — all HTTP endpoints + SSE streaming | FastAPI, uvicorn |
| `orchestrator.py` | Builds and runs the 8-agent LangGraph state machine | LangGraph, Python |
| `agent.py` | Defines LLM chains: Analyst, Writer, Critic, Refiner | LangChain, Gemini/Groq |
| `query_intake.py` | Agent 1 logic: validates and decomposes user query | LangChain, Pydantic |
| `search_sources.py` | Agent 2 logic: multi-source parallel search | Tavily, arXiv, DuckDuckGo |
| `citation_validator.py` | Verifies URLs are real and reachable | requests, BeautifulSoup |
| `report_exporter.py` | Converts Markdown report to DOCX and PDF | python-docx, WeasyPrint |
| `knowledge_base.py` | Vector store for RAG (store and retrieve past research) | ChromaDB, sentence-transformers |
| `session_manager.py` | Save/load research sessions from SQLite DB | sqlite3, Python |
| `hf_provider.py` | HuggingFace Inference API LLM integration | langchain-huggingface |
| `tools.py` | Shared tool definitions (web_search, scrape_url) | LangChain Tools |
| `pipeline.py` | Programmatic Python API wrapper for the orchestrator | Python |
| `main.py` | CLI entry point for running research from terminal | Python, Rich, argparse |

---

## 4. 8-Agent Pipeline Flow

```
User Topic (string)
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│  AGENT 1 — Strategist  (query_intake.py)                │
│  INPUT : Raw topic string                               │
│  DOES  : Validates clarity, classifies scope,           │
│           breaks into 3–5 targeted sub-queries          │
│  OUTPUT: List of sub-queries + source type hints        │
└─────────────────────────┬────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────┐
│  AGENT 2 — Search Engine  (search_sources.py)           │
│  INPUT : Sub-queries from Agent 1                       │
│  DOES  : Parallel search: Tavily + arXiv +              │
│           Wikipedia + DuckDuckGo. Deduplicates by URL.  │
│  OUTPUT: List of SearchResult objects                   │
│           (title, url, snippet, source_type)            │
└─────────────────────────┬────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────┐
│  AGENT 3 — Deep Reader  (agent.py)                      │
│  INPUT : Top URLs from Agent 2                          │
│  DOES  : Full-page scrape via BeautifulSoup,            │
│           extracts text, quotes, and statistics         │
│  OUTPUT: Raw full-text content per URL                  │
└─────────────────────────┬────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────┐
│  AGENT 4 — Analyst  (agent.py)                          │
│  INPUT : Full-text content from Agent 3                 │
│  DOES  : Cross-source synthesis, fact-checking,         │
│           identifies consensus vs. controversy          │
│  OUTPUT: Structured thematic briefing (key findings)    │
└─────────────────────────┬────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────┐
│  AGENT 5 — Report Writer  (agent.py)                    │
│  INPUT : Analyst briefing + all source data             │
│  DOES  : Writes full Markdown report:                   │
│           Executive Summary, Findings,                  │
│           Conclusion, Citations                         │
│  OUTPUT: Full Markdown report string                    │
└─────────────────────────┬────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────┐
│  AGENT 6 — Critic  (agent.py)                           │
│  INPUT : Markdown report from Agent 5 (or Refiner)     │
│  DOES  : Scores report 1–10 on:                         │
│           factual grounding, citation completeness,     │
│           evidence depth, clarity                       │
│  OUTPUT: CriticEvaluation { score, feedback }           │
└─────────────────────────┬────────────────────────────────┘
                          │
             ┌────────────┴──────────────────┐
             │   Score ≥ 8 OR max cycles?    │
             │                               │
          YES (approved)                 NO (needs work)
             │                               │
             │                               ▼
             │               ┌───────────────────────────────┐
             │               │  AGENT 7 — Refiner  (agent.py)│
             │               │  INPUT : Report + feedback    │
             │               │  DOES  : Rewrites weak        │
             │               │   sections, adds evidence,    │
             │               │   fixes citation gaps         │
             │               │  OUTPUT: Improved report      │
             │               └──────────────┬────────────────┘
             │                              │
             │                   (loops back to Agent 6)
             │
             ▼
┌──────────────────────────────────────────────────────────┐
│  AGENT 8 — Exporter  (report_exporter.py)               │
│  INPUT : Approved Markdown report + run metadata        │
│  DOES  : Adds YAML frontmatter (score, topic, date),    │
│           saves as .md, converts to .docx and .pdf      │
│  OUTPUT: Files saved to backend/reports/                │
│           Path returned to frontend via SSE             │
└──────────────────────────────────────────────────────────┘
```

---

## 5. Data Flow Diagram

```
 USER              FRONTEND (8123)       BACKEND (8000)        EXTERNAL APIs
  │                     │                     │                      │
  ├─ types topic ───────►│                     │                      │
  │                     ├─ POST /research ────►│                      │
  │                     │                     ├─ spawn 8-agent ──────┤
  │                     │◄── SSE: agent_start ─┤  pipeline            │
  │◄─ sees "Strategist" ─┤                     │                      │
  │                     │                     ├─── search Tavily ────►│
  │                     │◄── SSE: progress ───┤◄── results ───────────┤
  │◄─ sees "Searching…" ─┤                     ├─── scrape URLs ──────►│
  │                     │                     │◄── page HTML ─────────┤
  │                     │                     ├─── call Gemini LLM ──►│
  │                     │◄── SSE: progress ───┤◄── analysis/report ───┤
  │◄─ sees "Writing…"   ─┤                     │                      │
  │                     │◄── SSE: complete ───┤                      │
  │                     │    { report_path,   │─── save .md/.pdf ────┐│
  │                     │      critic_score } │─── save to SQLite ───┘│
  │                     ├─ GET /reports/*.md ►│                      │
  │                     │◄── Markdown file ───┤                      │
  │◄── sees report ──────┤                     │                      │
  │                     │                     │                      │
  ├─ visits history ────►│                     │                      │
  │                     ├─ GET /sessions ─────►│                      │
  │◄── sees past runs ───┤◄── JSON list ───────┤                      │
```

---

## 6. API Contract Summary

| Method | Endpoint | Purpose | Response Type |
|---|---|---|---|
| `POST` | `/research` | Start pipeline, stream live agent progress | `text/event-stream` (SSE) |
| `POST` | `/chat` | Single-turn LLM chat (desk assistant) | `application/json` |
| `GET` | `/sessions` | Fetch all past research sessions | `application/json` |
| `GET` | `/health` | Server liveness check | `application/json` |
| `GET` | `/reports/{filename}` | Download a generated report file | File (MD/DOCX/PDF) |
| `GET` | `/providers` | List supported LLM providers | `application/json` |
| `GET` | `/docs` | Interactive Swagger API documentation | HTML |

### SSE Event Types (POST /research stream)

| Event | When It Fires | Key Payload Fields |
|---|---|---|
| `progress` | Each pipeline log line | `message` |
| `agent_start` | An agent becomes active | `agent`, `message` |
| `complete` | Pipeline finished successfully | `critic_score`, `saved_report_path`, `sources_analyzed` |
| `error` | Unrecoverable failure | `message`, `traceback` |

### POST /research — Request Body

```json
{
  "topic": "Impact of LLMs on scientific discovery",
  "provider": "gemini",
  "max_revisions": 2,
  "approval_threshold": 8
}
```

---

## 7. LLM Provider Fallback Chain

The system automatically tries the next provider on quota/auth/rate-limit errors:

```
Request arrives
      │
      ▼
┌──────────────────────────────────────────┐
│  1. Google Gemini  (PRIMARY)             │
│     Model : gemini-2.5-flash             │
│     Key   : GEMINI_API_KEY               │
│     Why   : Best quality, huge context   │
└─────────────────┬────────────────────────┘
                  │  429 / quota / auth error?
                  ▼
┌──────────────────────────────────────────┐
│  2. Groq  (FALLBACK 1)                   │
│     Model : openai/gpt-oss-120b          │
│     Key   : GROQ_API_KEY                 │
│     Why   : Extremely fast inference     │
└─────────────────┬────────────────────────┘
                  │  Fails?
                  ▼
┌──────────────────────────────────────────┐
│  3. Mistral AI  (FALLBACK 2)             │
│     Model : mistral-small-latest         │
│     Key   : MISTRAL_API_KEY              │
│     Why   : GDPR-friendly, EU-hosted     │
└─────────────────┬────────────────────────┘
                  │  Fails?
                  ▼
┌──────────────────────────────────────────┐
│  4. HuggingFace  (LAST RESORT)           │
│     Model : Mistral-7B / Zephyr-7B       │
│     Key   : HF_TOKEN                     │
│     Why   : Free tier, always available  │
└─────────────────┬────────────────────────┘
                  │  All fail?
                  ▼
         HTTP 503 returned with
         per-provider error breakdown
```

> **Note:** Fallback applies to both `/research` pipeline runs and `/chat` calls.

---

## 8. Storage Layer

Three separate storage mechanisms, each serving a distinct purpose:

```
┌───────────────────────────────────────────────────────────────────┐
│                         STORAGE LAYER                             │
│                                                                   │
│  ┌─────────────────────┐  ┌──────────────────┐  ┌─────────────┐ │
│  │      ChromaDB        │  │      SQLite       │  │  /reports   │ │
│  │   (Vector Store)     │  │   (Session DB)    │  │  (Files)    │ │
│  │                      │  │                   │  │             │ │
│  │  Stores embeddings   │  │  Stores metadata  │  │  Stores     │ │
│  │  of past research    │  │  per pipeline run:│  │  final      │ │
│  │  for semantic RAG    │  │  topic, score,    │  │  reports:   │ │
│  │  retrieval           │  │  duration, status │  │  .md        │ │
│  │                      │  │                   │  │  .docx      │ │
│  │  File: chroma_db/    │  │  File: sessions.db│  │  .pdf       │ │
│  │  Model: all-MiniLM   │  │                   │  │             │ │
│  │  Used by: Agents 3,4 │  │  Used by:         │  │  Served via │ │
│  │  (knowledge_base.py) │  │  history.html     │  │  GET        │ │
│  │                      │  │  dashboard.html   │  │  /reports/  │ │
│  └─────────────────────┘  └──────────────────┘  └─────────────┘ │
└───────────────────────────────────────────────────────────────────┘
```

| Store | Technology | Purpose | Persists? |
|---|---|---|---|
| ChromaDB | `chromadb` + `sentence-transformers` | Semantic search / RAG over past research | ✅ Yes |
| SQLite | `sqlite3` (Python built-in) | Session metadata per run (score, topic, time) | ✅ Yes |
| `/reports` folder | Filesystem | Final report files (.md, .docx, .pdf) | ✅ Yes |

---

## 9. Non-Functional Requirements

### 9.1 Real-Time Streaming (SSE)
- Frontend uses the browser-native `EventSource` API to connect to `/research`
- Each agent's log line is pushed immediately as it is printed
- Keepalive comment sent every 50 ms to prevent proxy timeouts
- Zero page reloads — user sees the full pipeline live

### 9.2 Self-Correction Loop
- Critic and Refiner cycle automatically until quality is acceptable
- Default quality threshold: **8 / 10** (set via `approval_threshold`)
- Default max revision cycles: **2** (set via `max_revisions`)
- Loop exits on: score ≥ threshold **OR** max cycles reached

### 9.3 Multi-Format Report Export

| Format | Library | Notes |
|---|---|---|
| Markdown `.md` | Built-in file write | Primary; human-readable |
| Word `.docx` | `python-docx` | For office users |
| PDF `.pdf` | `WeasyPrint` → `reportlab` fallback | For sharing and printing |

### 9.4 Security and Configuration
- All API keys live in `.env` — never hardcoded in source
- CORS set to `*` for local dev — restrict origins in production
- Backend authentication: none — login.html is a frontend UI stub only

### 9.5 Performance Characteristics

| Operation | Typical Duration |
|---|---|
| Full 8-agent pipeline | 3–8 minutes |
| `/chat` single response | 3–10 seconds |
| `/sessions` list fetch | < 100 ms (SQLite) |
| Report file download | < 1 second (static file) |

### 9.6 Error Handling Strategy

| Failure Type | How It Is Handled |
|---|---|
| LLM provider fails (quota/auth) | Auto-fallback to next provider in chain |
| Individual search source fails | That source is skipped; others continue |
| Pipeline crash (uncaught exception) | SSE `error` event sent to frontend with traceback |
| Session save fails | Logged and skipped — result still returned to UI |
| All providers fail | HTTP 503 with per-provider breakdown returned |

---

## 10. Folder Structure

```
Reasearch_AI/
│
├── frontend/                    ← Static web UI (served on :8123)
│   ├── index.html               ← Landing page
│   ├── new.html                 ← Submit topic + live SSE progress
│   ├── report.html              ← Report viewer (renders .md)
│   ├── history.html             ← Past sessions list
│   ├── dashboard.html           ← Analytics and stats
│   ├── login.html               ← Auth page (UI only, no backend)
│   └── desk.html                ← Redirect shim → new.html
│
├── backend/                     ← Python API (served on :8000)
│   ├── server.py                ← FastAPI app + all route handlers
│   ├── orchestrator.py          ← LangGraph 8-agent state machine
│   ├── agent.py                 ← LLM chain definitions
│   ├── query_intake.py          ← Agent 1: Strategist
│   ├── search_sources.py        ← Agent 2: Multi-source search
│   ├── citation_validator.py    ← URL verification module
│   ├── report_exporter.py       ← MD → DOCX/PDF export
│   ├── knowledge_base.py        ← ChromaDB RAG vector store
│   ├── session_manager.py       ← SQLite session CRUD
│   ├── hf_provider.py           ← HuggingFace LLM provider
│   ├── tools.py                 ← Shared LangChain tool definitions
│   ├── pipeline.py              ← Python API wrapper
│   ├── main.py                  ← CLI entry point
│   ├── sessions.db              ← SQLite database file
│   ├── chroma_db/               ← ChromaDB persistent vector files
│   ├── reports/                 ← Generated report output files
│   └── requirements.txt         ← Python dependencies
│
├── .env                         ← API keys (never commit to Git)
├── .gitignore                   ← Excludes .env, .venv, reports/
├── start_quest.bat              ← One-click Windows launcher
├── stop_quest.bat               ← Graceful shutdown script
├── pyproject.toml               ← Python project metadata
└── README.md                    ← Project documentation

---
Document End
For questions refer to README.md or the inline docstrings in each backend module.
```
