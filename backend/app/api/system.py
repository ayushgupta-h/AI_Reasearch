"""System endpoints: liveness/health and provider discovery."""

from __future__ import annotations

import time

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST

from app.config import PROVIDER_ORDER, Settings, get_settings
from app.observability import current_request_id
from app.schemas.envelopes import HealthEnvelope, ProvidersEnvelope
from app.schemas.enums import HealthState, LLMProvider, ProviderState
from app.schemas.health import HealthStatus
from app.schemas.provider import ProviderStatus
from app.schemas.responses import ProvidersResponse
from app.telemetry.metrics import get_metrics

router = APIRouter()

_ENVELOPE_PARAM = Query(
    False,
    description="Wrap the payload in the standardized ApiResponse envelope.",
)


def _one_of(*refs: str) -> dict:
    return {
        "content": {
            "application/json": {
                "schema": {
                    "oneOf": [
                        {"$ref": f"#/components/schemas/{name}"} for name in refs
                    ]
                }
            }
        }
    }


_ENVELOPE_NOTE = (
    "Flat payload by default (backward compatible); with `envelope=true` the "
    "response is wrapped in the standardized ApiResponse envelope."
)


def _provider_status(settings: Settings, provider: str, rank: int) -> ProviderStatus:
    configured = bool(settings.provider_api_key(provider))
    return ProviderStatus(
        provider=LLMProvider(provider),
        model=settings.model_for(provider) or "unspecified",
        state=ProviderState.READY if configured else ProviderState.MISSING_CREDENTIALS,
        fallback_rank=rank,
    )


@router.get(
    "/health",
    tags=["System"],
    summary="Liveness and health status",
    responses={
        200: {
            "description": f"Service health. {_ENVELOPE_NOTE}",
            **_one_of("HealthStatus", "HealthEnvelope"),
        }
    },
)
async def health_check(
    request: Request, envelope: bool = _ENVELOPE_PARAM
) -> JSONResponse:
    """Liveness check endpoint."""
    settings = get_settings()
    started_at = getattr(request.app.state, "started_at", None)
    uptime = round(time.time() - started_at, 1) if started_at else None

    status = HealthStatus(
        status=HealthState.OK,
        version=settings.api_version,
        environment=settings.app_env,
        uptime_seconds=uptime,
    )
    if envelope:
        body = HealthEnvelope(
            data=status,
            message="Service healthy",
            request_id=current_request_id(),
        ).to_payload(exclude_none=True)
    else:
        body = status.to_payload()
    return JSONResponse(body)


@router.get(
    "/providers",
    tags=["System"],
    summary="Supported LLM providers and their status",
    responses={
        200: {
            "description": f"Provider list. {_ENVELOPE_NOTE}",
            **_one_of("ProvidersResponse", "ProvidersEnvelope"),
        }
    },
)
async def list_providers(envelope: bool = _ENVELOPE_PARAM) -> JSONResponse:
    """Return the supported LLM providers, the default, and non-sensitive status."""
    settings = get_settings()
    statuses = [
        _provider_status(settings, provider, rank)
        for rank, provider in enumerate(PROVIDER_ORDER, start=1)
    ]
    payload = ProvidersResponse(
        providers=list(PROVIDER_ORDER),
        default=settings.default_provider,
        statuses=statuses,
    )
    if envelope:
        body = ProvidersEnvelope(
            data=payload,
            message="Providers retrieved",
            request_id=current_request_id(),
        ).to_payload(exclude_none=True)
    else:
        body = payload.model_dump(mode="json")
    return JSONResponse(body)


@router.get(
    "/metrics",
    tags=["System"],
    summary="Prometheus metrics exposition",
    response_class=PlainTextResponse,
)
def prometheus_metrics() -> PlainTextResponse:
    """Expose Prometheus formatted metrics for scrapers."""
    return PlainTextResponse(
        content=get_metrics().export(),
        media_type=CONTENT_TYPE_LATEST,
    )
