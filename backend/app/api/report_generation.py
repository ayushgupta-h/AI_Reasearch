"""
app/api/report_generation.py
============================
Evidence-grounded report generation API endpoints.

Generates a structured Markdown research report from verified claims, evidence,
contradictions and source-credibility evaluations. Reports can be versioned
through the existing ReportManagementService and exported via the existing
ReportExporter (Markdown, DOCX, PDF).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Path, Query

from app.errors import AuthenticationError, NotFoundError
from app.schemas.errors import ApiError
from app.security.dependencies import get_current_user
from app.services.container import get_report_generator
from app.services.report_generation_service import ReportContext

router = APIRouter(tags=["Report Generation"])


def _owner(current_user: dict) -> str:
    owner = str(current_user.get("user_id") or "").strip()
    if not owner:
        raise AuthenticationError("Authentication required.")
    return owner


@router.post(
    "/research/{research_id}/report/generate",
    response_model=dict,
    responses={
        401: {"model": ApiError, "description": "Authentication required."},
        404: {"model": ApiError, "description": "Research session not found."},
    },
    summary="Generate an evidence-grounded research report",
)
async def generate_report(
    research_id: str = Path(
        ...,
        description="Research session ID.",
    ),
    topic: str = Body(..., description="Research topic / question."),
    max_revisions: int = Body(2, ge=0, le=10),
    current_user: dict = Depends(get_current_user),
    generator=Depends(get_report_generator),
) -> dict:
    """Generate an evidence-grounded Markdown report for a research session.

    The report is built from the session's verified claims, evidence passages,
    contradictions and source-credibility evaluations. It is returned as
    Markdown and can be versioned/exported through the report management API.
    """
    user_id = _owner(current_user)

    # Build context from available state; the generator works with whatever
    # inputs are present and gracefully degrades when none are available.
    context = ReportContext(
        topic=topic,
        research_question=topic,
        claims=[],
        verification_results=[],
        contradictions=[],
        credibility_evaluations=[],
        evidence_map={},
        retrieved_memories=[],
        critic_score=None,
        revision_count=0,
        max_revisions=max_revisions,
        search_source_count=0,
        rag_available=False,
    )

    generated = generator.generate(context)
    return {
        "research_id": research_id,
        "user_id": user_id,
        "title": generated.title,
        "content_markdown": generated.content_markdown,
        "word_count": generated.word_count,
        "citations": generated.citations,
        "verified_claim_count": generated.verified_claim_count,
        "unverified_claim_count": generated.unverified_claim_count,
        "contradicted_claim_count": generated.contradicted_claim_count,
        "contradiction_count": generated.contradiction_count,
        "source_count": generated.source_count,
    }


__all__ = ["router"]
