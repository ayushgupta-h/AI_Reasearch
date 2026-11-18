"""HTTP API layer: one router module per bounded context."""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    agent_history,
    auth,
    claims,
    completeness,
    documents,
    memory,
    monitoring,
    notifications,
    projects,
    report_comparison,
    report_generation,
    report_management,
    reports,
    research,
    research_sessions,
    reviews,
    sessions,
    sources,
    system,
    usage,
)

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(research.router)
api_router.include_router(projects.router)
api_router.include_router(research_sessions.router)
api_router.include_router(claims.router)
api_router.include_router(report_comparison.router)
api_router.include_router(memory.router)
api_router.include_router(report_generation.router)
api_router.include_router(agent_history.router)
api_router.include_router(documents.router)
api_router.include_router(report_management.router)
api_router.include_router(reviews.router)
api_router.include_router(sessions.router)
api_router.include_router(reports.router)
api_router.include_router(sources.router)
api_router.include_router(completeness.router)
api_router.include_router(notifications.router)
api_router.include_router(usage.router)
api_router.include_router(monitoring.router)

__all__ = ["api_router"]
