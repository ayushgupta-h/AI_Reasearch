"""HTTP API layer: one router module per bounded context."""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    agent_history,
    auth,
    documents,
    projects,
    report_management,
    reports,
    research,
    research_sessions,
    sessions,
    system,
)

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(research.router)
api_router.include_router(projects.router)
api_router.include_router(research_sessions.router)
api_router.include_router(agent_history.router)
api_router.include_router(documents.router)
api_router.include_router(report_management.router)
api_router.include_router(sessions.router)
api_router.include_router(reports.router)

__all__ = ["api_router"]
