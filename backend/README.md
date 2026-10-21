# Research AI — Backend

**Owner: You (Ayush)**

The backend contains the entire multi-agent research pipeline built with LangGraph, FastAPI, and ChromaDB.

## Folder Structure

```
backend/
├── agent.py              # 6 LangChain agent/chain factory functions
├── orchestrator.py       # LangGraph pipeline (9 nodes: intake → exporter)
├── search_sources.py     # 7 search source adapters (arXiv, DDG, Wikipedia…)
├── knowledge_base.py     # ChromaDB RAG vector store
├── citation_validator.py # Concurrent HTTP citation checker
├── report_exporter.py    # DOCX + PDF export (python-docx / WeasyPrint)
├── session_manager.py    # SQLite session history + PipelineTimer
├── query_intake.py       # Query validation and sub-query decomposition
├── server.py             # FastAPI REST server with SSE streaming
├── main.py               # CLI entry point
├── pipeline.py           # Thin pipeline runner
├── tools.py              # LangChain tool wrappers (web_search, scrape_url)
├── hf_provider.py        # HuggingFace LLM adapter
├── .env                  # API keys (never commit)
├── requirements.txt      # All Python dependencies
├── pytest.ini            # pytest configuration
├── sessions.db           # SQLite session history (auto-created)
├── reports/              # Generated .md / .docx / .pdf reports
├── chroma_db/            # ChromaDB persistent vector store
└── tests/
    ├── conftest.py
    ├── test_agent.py
    ├── test_orchestrator.py
    └── test_search_sources.py
```

## Quick Start

```bash
cd backend

# Install dependencies
pip install -r requirements.txt

# Run CLI research (interactive)
python main.py

# Run with a topic directly
python main.py --topic "Impact of LLMs on scientific discovery"

# View past research sessions
python main.py --history

# Start the FastAPI REST server (for frontend)
uvicorn server:app --host 0.0.0.0 --port 8000 --reload

# Run all tests
python -m pytest tests/ -v
```

## API Endpoints (for Frontend)

| Method | Path         | Description                                 |
|--------|--------------|---------------------------------------------|
| `GET`  | `/health`    | Liveness check                              |
| `GET`  | `/providers` | List supported LLM providers                |
| `POST` | `/research`  | Start research pipeline (streams SSE)       |
| `GET`  | `/docs`      | Swagger UI                                  |

### POST /research — Request Body

```json
{
  "topic": "Quantum computing breakthroughs",
  "provider": "gemini",
  "max_revisions": 2,
  "approval_threshold": 8,
  "output_dir": "reports"
}
```

### SSE Event Types

| Event        | When                                              |
|--------------|---------------------------------------------------|
| `progress`   | Each agent log line                               |
| `agent_start`| A new agent node becomes active                   |
| `complete`   | Pipeline done — contains `saved_report_path`, etc.|
| `error`      | Unrecoverable failure                             |

## Pipeline Architecture

```
Intake → Search → Reader → RAG (ChromaDB) → Analyst → Writer
                                                           ↓
                                           Exporter ← Refiner ← Critic
                                               ↓
                              .md + .docx + .pdf  +  Citation Appendix
```

## Environment Variables (.env)

```
GEMINI_API_KEY=your_key_here
TAVILY_API_KEY=your_key_here        # optional
NEWSAPI_KEY=your_key_here           # optional
SERPAPI_KEY=your_key_here           # optional
HF_API_TOKEN=your_token_here        # optional (HuggingFace)
LLM_PROVIDER=gemini                 # gemini | huggingface
```
