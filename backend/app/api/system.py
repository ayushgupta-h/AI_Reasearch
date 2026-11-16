"""System endpoints: liveness/health and provider discovery."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST

from app.config import PROVIDER_ORDER, get_settings
from app.observability import current_request_id
from app.schemas.envelopes import HealthEnvelope, ProvidersEnvelope
from app.schemas.enums import HealthState
from app.schemas.responses import ProvidersResponse
from app.services.container import get_health_service
from app.services.health_service import HealthService
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
    request: Request,
    envelope: bool = _ENVELOPE_PARAM,
    health: HealthService = Depends(get_health_service),
) -> JSONResponse:
    """Liveness check endpoint."""
    status = health.liveness()
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
    "/health/live",
    tags=["System"],
    summary="Liveness check",
    responses={200: {"description": "Process is alive (never depends on optional deps)."}},
)
async def health_live(
    health: HealthService = Depends(get_health_service),
) -> JSONResponse:
    return JSONResponse(health.liveness().to_payload())


@router.get(
    "/health/ready",
    tags=["System"],
    summary="Readiness check",
    responses={
        200: {"description": "Application can accept workload."},
        503: {"description": "Core dependency unavailable; app not ready."},
    },
)
async def health_ready(
    health: HealthService = Depends(get_health_service),
) -> JSONResponse:
    status = health.readiness()
    code = 200 if status.status == HealthState.OK else 503
    return JSONResponse(status.to_payload(), status_code=code)


@router.get(
    "/health/dependencies",
    tags=["System"],
    summary="Full dependency report",
    responses={200: {"description": "Configured dependencies and provider statuses."}},
)
async def health_dependencies(
    health: HealthService = Depends(get_health_service),
) -> JSONResponse:
    return JSONResponse(health.dependencies().to_payload())


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
async def list_providers(
    envelope: bool = _ENVELOPE_PARAM,
    health: HealthService = Depends(get_health_service),
) -> JSONResponse:
    """Return the supported LLM providers, the default, and non-sensitive status."""
    settings = get_settings()
    payload = ProvidersResponse(
        providers=list(PROVIDER_ORDER),
        default=settings.default_provider,
        statuses=health.provider_statuses(probe=False),
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
