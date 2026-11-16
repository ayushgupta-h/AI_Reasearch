"""
app/api/usage.py
================
Read-only model usage and cost endpoints.

All endpoints require a verified JWT. Aggregations are scoped to the calling
user; research-scoped endpoints also verify ownership of the research session.
Cost values are estimates derived from a pricing config (never invoices).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from app.errors import AuthenticationError
from app.schemas.errors import ApiError
from app.schemas.usage import (
    ModelsUsageResponse,
    ProvidersUsageResponse,
    ResearchUsageResponse,
    UsageSummaryResponse,
)
from app.security.dependencies import get_current_user
from app.services.container import get_usage_service
from app.services.usage_service import UsageService

router = APIRouter(tags=["Usage"])

ResearchIdPath = Path(
    ...,
    pattern=r"^[a-f0-9]{32}$",
    description="32-character lowercase hex research id.",
)

_ERRORS = {
    401: {"model": ApiError, "description": "Authentication required."},
    404: {"model": ApiError, "description": "Research session not found."},
}


def _owner(current_user: dict) -> str:
    owner = str(current_user.get("user_id") or "").strip()
    if not owner:
        raise AuthenticationError("Authentication required.")
    return owner


@router.get(
    "/usage/summary",
    response_model=UsageSummaryResponse,
    responses=_ERRORS,
    summary="Aggregated usage & cost for the current user",
)
async def get_usage_summary(
    current_user: dict = Depends(get_current_user),
    service: UsageService = Depends(get_usage_service),
) -> UsageSummaryResponse:
    payload = service.summary(_owner(current_user))
    return UsageSummaryResponse.model_validate(payload)


@router.get(
    "/usage/research/{research_id}",
    response_model=ResearchUsageResponse,
    responses=_ERRORS,
    summary="Usage & cost for one research session",
)
async def get_research_usage(
    research_id: str = ResearchIdPath,
    current_user: dict = Depends(get_current_user),
    service: UsageService = Depends(get_usage_service),
) -> ResearchUsageResponse:
    payload = service.research_usage(research_id, _owner(current_user))
    return ResearchUsageResponse.model_validate(payload)


@router.get(
    "/usage/providers",
    response_model=ProvidersUsageResponse,
    responses=_ERRORS,
    summary="Usage & cost aggregated by provider",
)
async def get_providers_usage(
    current_user: dict = Depends(get_current_user),
    service: UsageService = Depends(get_usage_service),
) -> ProvidersUsageResponse:
    payload = service.providers(_owner(current_user))
    return ProvidersUsageResponse.model_validate(payload)


@router.get(
    "/usage/models",
    response_model=ModelsUsageResponse,
    responses=_ERRORS,
    summary="Usage & cost aggregated by model",
)
async def get_models_usage(
    current_user: dict = Depends(get_current_user),
    service: UsageService = Depends(get_usage_service),
) -> ModelsUsageResponse:
    payload = service.models(_owner(current_user))
    return ModelsUsageResponse.model_validate(payload)


__all__ = ["router"]
