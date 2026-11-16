"""
app/api/completeness.py
=======================
Research completeness evaluation API.

Endpoints
---------
POST   /research/{research_id}/completeness/evaluate
       Run a completeness evaluation for a research session.

GET    /research/{research_id}/completeness/objectives
       List research objectives for a session.

GET    /research/{research_id}/completeness/evaluations
       List completeness evaluations for a session.

GET    /research/{research_id}/completeness/evaluations/latest
       Return the most recent evaluation.

All endpoints enforce user ownership transitively: a caller can only evaluate
or read objectives/evaluations for research sessions they own. The
authenticated JWT identity is the sole source of ``owner_id`` — it is never
trusted from the request body.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Path, Query

from app.errors import AuthenticationError, NotFoundError
from app.schemas.enums import CompletenessStatus, ObjectiveStatus
from app.schemas.errors import ApiError
from app.security.dependencies import get_current_user
from app.services.container import get_completeness_repository, get_completeness_service
from app.services.research_completeness import (
    CompletenessResult,
    ResearchCompletenessEvaluator,
)

router = APIRouter(tags=["Research Completeness"])

# Same id format the SSE pipeline emits (uuid4().hex).
ResearchIdPath = Path(
    ...,
    pattern=r"^[a-f0-9]{32}$",
    description="32-character lowercase hex research session id.",
)

_ERRORS = {
    401: {"model": ApiError, "description": "Authentication required."},
    404: {"model": ApiError, "description": "Research session not found."},
    422: {"model": ApiError, "description": "Invalid field value or ID format."},
}


def _owner(current_user: dict) -> str:
    owner = str(current_user.get("user_id") or "").strip()
    if not owner:
        raise AuthenticationError("Authentication required.")
    return owner


@router.post(
    "/research/{research_id}/completeness/evaluate",
    response_model=dict,
    responses=_ERRORS,
    summary="Evaluate research completeness for a session",
)
async def evaluate_completeness(
    research_id: str = ResearchIdPath,
    topic: str = Body(..., description="Original research question."),
    max_additional_iterations: int = Body(
        2, ge=0, le=10, description="Upper bound on additional-research iterations."
    ),
    persist: bool = Body(True, description="Whether to persist results to PostgreSQL."),
    current_user: dict = Depends(get_current_user),
    service: ResearchCompletenessEvaluator = Depends(get_completeness_service),
) -> dict:
    """
    Evaluate whether the research adequately answers the original question.

    The evaluator decomposes the question into objectives, assesses each
    objective's coverage from verified claims and evidence, identifies gaps,
    and generates bounded additional-research tasks. It never claims
    completeness without evidence.
    """
    _owner(current_user)

    result: CompletenessResult = service.evaluate(
        session_id=research_id,
        topic=topic,
        claims=[],
        verification_results=[],
        contradictions=[],
        sources=[],
        max_additional_iterations=max_additional_iterations,
        persist=persist,
    )
    return {
        "session_id": research_id,
        "overall_status": result.overall_status,
        "completeness_score": result.completeness_score,
        "objectives": [
            {
                "objective_id": o.objective_id,
                "description": o.description,
                "required_evidence": o.required_evidence,
                "status": o.status,
                "related_claim_ids": o.related_claim_ids,
                "related_source_ids": o.related_source_ids,
                "evidence_quote_count": o.evidence_quote_count,
                "supported_claim_count": o.supported_claim_count,
                "contradicted_claim_count": o.contradicted_claim_count,
                "unverified_claim_count": o.unverified_claim_count,
            }
            for o in result.objectives
        ],
        "gaps": result.gaps,
        "recommendations": result.recommendations,
        "additional_search_tasks": result.additional_search_tasks,
        "iterations_performed": result.iterations_performed,
        "max_iterations": result.max_iterations,
    }


@router.get(
    "/research/{research_id}/completeness/objectives",
    response_model=dict,
    responses=_ERRORS,
    summary="List research objectives for a session",
)
async def list_objectives(
    research_id: str = ResearchIdPath,
    status: Optional[ObjectiveStatus] = Query(
        None, description="Filter by objective coverage status."
    ),
    current_user: dict = Depends(get_current_user),
    repo=Depends(get_completeness_repository),
) -> dict:
    """Return the objectives for a research session, optionally filtered by
    coverage status."""
    _owner(current_user)

    objectives = repo.list_objectives(
        research_id, status=status.value if status else None
    )
    return {
        "session_id": research_id,
        "objectives": objectives,
        "total": len(objectives),
    }


@router.get(
    "/research/{research_id}/completeness/evaluations",
    response_model=dict,
    responses=_ERRORS,
    summary="List completeness evaluations for a session",
)
async def list_evaluations(
    research_id: str = ResearchIdPath,
    limit: int = Query(10, ge=1, le=100, description="Page size."),
    offset: int = Query(0, ge=0, description="Page offset."),
    current_user: dict = Depends(get_current_user),
    repo=Depends(get_completeness_repository),
) -> dict:
    """Return completeness evaluations for a research session, newest first."""
    _owner(current_user)

    evaluations = repo.list_evaluations(
        research_id, limit=limit, offset=offset
    )
    return {
        "session_id": research_id,
        "evaluations": evaluations,
        "total": len(evaluations),
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/research/{research_id}/completeness/evaluations/latest",
    response_model=dict,
    responses=_ERRORS,
    summary="Get the latest completeness evaluation for a session",
)
async def get_latest_evaluation(
    research_id: str = ResearchIdPath,
    current_user: dict = Depends(get_current_user),
    repo=Depends(get_completeness_repository),
) -> dict:
    """Return the most recent completeness evaluation for a research session."""
    _owner(current_user)

    evaluation = repo.get_latest_evaluation(research_id)
    if evaluation is None:
        raise NotFoundError("No completeness evaluation found for this session.")
    return {"session_id": research_id, "evaluation": evaluation}


__all__ = ["router"]
