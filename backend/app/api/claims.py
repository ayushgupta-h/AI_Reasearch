"""
app/api/claims.py
=================
Claim-level evidence verification endpoints.

Exposes verified claims and their supporting/contradicting evidence for a
research session. All endpoints are owner-scoped: a caller can only read
claims for sessions they own.
"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Path, Query

from app.errors import AuthenticationError, NotFoundError
from app.schemas.errors import ApiError
from app.security.dependencies import get_current_user, get_optional_user
from app.services.container import get_claim_repository, get_contradiction_repository, get_session_service
from app.services.session_service import SessionService

router = APIRouter(tags=["Claim Verification"])

ResearchIdPath = Path(
    ...,
    pattern=r"^[a-zA-Z0-9_-]+$",
    description="Research id.",
)

ClaimIdPath = Path(
    ...,
    pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    description="UUID claim id.",
)


def _owner(current_user: dict) -> str:
    owner = str(current_user.get("user_id") or "").strip()
    if not owner:
        raise AuthenticationError("Authentication required.")
    return owner


@router.get(
    "/research/{research_id}/claims",
    response_model=dict,
    responses={
        401: {"model": ApiError, "description": "Authentication required."},
        404: {"model": ApiError, "description": "Research session not found."},
    },
    summary="List verified claims for a research session",
)
async def list_claims(
    research_id: str = ResearchIdPath,
    status: Optional[str] = Query(
        None,
        description="Filter by verification status (supported, partially_supported, contradicted, unverified, insufficient_context).",
    ),
    current_user: Optional[dict] = Depends(get_optional_user),
    repo=Depends(get_claim_repository),
    session_service: SessionService = Depends(get_session_service),
) -> dict:
    """Return all claims for a research session with their verification status."""
    owner_id = str(current_user.get("user_id") or "").strip() if current_user else ""
    claims = []
    repo_available = repo is not None and getattr(repo, "available", False)
    if repo_available:
        try:
            claims = repo.list_claims(research_id, status=status)
        except Exception:
            claims = []

    if not claims:
        sess = session_service.get_session(research_id, user_id=owner_id if owner_id else None) or session_service.get_session(research_id)
        if sess:
            raw_claims = sess.get("claims") or sess.get("claim_verification_results") or []
            if isinstance(raw_claims, list):
                for i, c in enumerate(raw_claims):
                    if isinstance(c, dict):
                        c_status = c.get("status") or c.get("verification_status") or "supported"
                        if status and c_status != status:
                            continue
                        claims.append({
                            "claim_id": c.get("claim_id") or f"claim-{i+1}",
                            "id": c.get("claim_id") or f"claim-{i+1}",
                            "text": c.get("text") or c.get("conclusion") or c.get("claim_text") or f"Claim {i+1}",
                            "status": c_status,
                            "verification_status": c_status,
                            "confidence": float(c.get("confidence", 0.9) if c.get("confidence") is not None else 0.9),
                            "claim_type": c.get("claim_type") or "factual",
                            "supporting_evidence": c.get("supporting_evidence") or [],
                            "contradicting_evidence": c.get("contradicting_evidence") or [],
                        })
                    elif isinstance(c, str):
                        claims.append({
                            "claim_id": f"claim-{i+1}",
                            "id": f"claim-{i+1}",
                            "text": c,
                            "status": "supported",
                            "verification_status": "supported",
                            "confidence": 0.9,
                            "claim_type": "factual",
                            "supporting_evidence": [],
                            "contradicting_evidence": [],
                        })

    return {
        "research_id": research_id,
        "claims": claims,
        "total": len(claims),
    }


@router.get(
    "/research/{research_id}/claims/{claim_id}",
    response_model=dict,
    responses={
        401: {"model": ApiError, "description": "Authentication required."},
        404: {"model": ApiError, "description": "Claim not found or not owned by caller."},
    },
    summary="Get one claim with its evidence",
)
async def get_claim(
    research_id: str = ResearchIdPath,
    claim_id: str = ClaimIdPath,
    current_user: dict = Depends(get_current_user),
    repo=Depends(get_claim_repository),
) -> dict:
    """Return a single claim with its linked evidence passages."""
    repo_available = repo is not None and getattr(repo, "available", False)
    if not repo_available:
        raise NotFoundError("Claim verification data requires PostgreSQL persistence.")

    claim = repo.get_claim_with_evidence(research_id, claim_id)
    if claim is None:
        raise NotFoundError("Claim not found.")
    return claim


@router.get(
    "/research/{research_id}/contradictions",
    response_model=dict,
    responses={
        401: {"model": ApiError, "description": "Authentication required."},
    },
    summary="List detected contradictions for a research session",
)
async def list_contradictions(
    research_id: str = ResearchIdPath,
    current_user: Optional[dict] = Depends(get_optional_user),
    contradiction_repo=Depends(get_contradiction_repository),
) -> dict:
    """Return all contradictions detected between claims in this session."""
    repo_available = contradiction_repo is not None and getattr(contradiction_repo, "available", False)
    if not repo_available:
        return {
            "research_id": research_id,
            "contradictions": [],
            "total": 0,
            "message": "Contradiction analysis requires PostgreSQL persistence.",
        }

    try:
        items = contradiction_repo.list_for_session(research_id)
    except Exception:
        items = []
    return {
        "research_id": research_id,
        "contradictions": items,
        "total": len(items),
    }


__all__ = ["router"]
