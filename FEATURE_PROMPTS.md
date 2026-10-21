# Research AI - Feature Extension Prompts

This file contains copy-paste prompts designed to implement future enhancements to the **Research AI** multi-agent pipeline step-by-step.

---

### Prompt 1 — Async Parallel Search
```text
Add async parallel execution to the search_node in orchestrator.py so all sub-queries run simultaneously using asyncio.gather() instead of sequentially. Update requirements.txt if needed.
```

---

### Prompt 2 — Citation Validator
```text
Add a citation_validator.py module that checks every URL in the final report for HTTP status (live/dead link), extracts the domain name, and tags each source as verified or broken. Integrate it into the exporter_node in orchestrator.py.
```

---

### Prompt 3 — PDF & DOCX Export
```text
Add export support in the exporter_node of orchestrator.py to save the final report as both a .pdf and a .docx file in addition to the existing .md format. Add the required dependencies to requirements.txt.
```

---

### Prompt 4 — FastAPI REST Server
```text
Create a server.py using FastAPI that exposes a POST /research endpoint accepting a JSON body with topic, provider, max_revisions and approval_threshold. It should run the MultiAgentOrchestrator and stream progress back as server-sent events (SSE). Add uvicorn and fastapi to requirements.txt.
```

---

### Prompt 5 — Streamlit Web Dashboard
```text
Create an app.py using Streamlit that provides a beautiful UI for the Research AI system. It should have: a text input for the research topic, sidebar controls for provider, max_revisions, and threshold, a live progress log panel, and display the final report in markdown with download buttons for .md, .pdf, and .docx. Add streamlit to requirements.txt.
```

---

### Prompt 6 — Vector Knowledge Base (RAG)
```text
Add a knowledge_base.py module using ChromaDB and LangChain embeddings that: indexes all scraped content and search results into a persistent vector store, exposes a retrieve(query) method for semantic similarity search, and integrate it between the reader_node and analyst_node in orchestrator.py so the analyst uses RAG over the indexed documents. Add chromadb and langchain-chroma to requirements.txt.
```

---

### Prompt 7 — Session Memory & History
```text
Add a session_manager.py that persists research sessions to a local SQLite database using LangGraph's built-in checkpointer. Each session should store: topic, timestamp, critic score, revision count, and the saved report path. Add a --history CLI flag to main.py that lists all past research sessions in a rich table.
```

---

### Prompt 8 — Unit Tests
```text
Create a tests/ directory with pytest test files: test_agent.py (tests that all 6 chains/agents initialize correctly), test_orchestrator.py (tests the LangGraph graph compiles and runs on a mock topic without API calls using unittest.mock), and test_search_sources.py (tests each search source's is_available() method and result schema). Add pytest and pytest-mock to requirements.txt.
```
