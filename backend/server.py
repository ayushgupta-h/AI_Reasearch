"""
Research AI — FastAPI REST Server
===================================
Exposes the Multi-Agent Research Orchestrator over HTTP with Server-Sent Events
(SSE) streaming so clients can observe each agent's progress in real time.

This module is intentionally a thin entrypoint: the application is assembled by
``app.factory.create_app`` from the layered modules in ``backend/app/``.

Endpoints
---------
POST /research
    Start a research job and stream progress as SSE events.
POST /chat
    Single-turn assistant reply with provider fallback.
GET  /sessions, DELETE /sessions/{session_id}
    Research history for the authenticated user.
GET  /reports/{filename}, GET /reports/{filename}/share, POST /export
    Report download, sharing and multi-format export.
GET  /health, GET /providers, GET /auth/config, GET /auth/me
    System and authentication helpers.

Usage
-----
    uvicorn server:app --host 0.0.0.0 --port 8000 --reload

SSE Event Schema
----------------
Each event is a JSON object with the shape::

    {
        "event":   "progress" | "agent_start" | "agent_done" | "complete" | "error",
        "agent":   "Strategist" | "Search" | ... | "Exporter" (optional),
        "message": "Human-readable status string",
        "data":    { ... }   (optional — only on "complete" and "error" events)
    }
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the backend directory is importable when uvicorn is launched from the
# repository root (``uvicorn backend.server:app``).
_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.config import get_settings  # noqa: E402  (path bootstrap must run first)
from app.factory import create_app  # noqa: E402

app = create_app()


if __name__ == "__main__":
    import uvicorn

    _settings = get_settings()
    uvicorn.run(
        "server:app",
        host=_settings.server_host,
        port=_settings.server_port,
        reload=True,
        log_level="info",
    )
