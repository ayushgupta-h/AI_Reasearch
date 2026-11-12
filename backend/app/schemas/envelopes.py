"""
app/schemas/envelopes.py
========================
Concrete envelope models for endpoints that support ``?envelope=true``.

Concrete (non-generic) subclasses are used so FastAPI/OpenAPI can name each
schema unambiguously (``HealthEnvelope``, ``ProvidersEnvelope``, ...).
"""

from __future__ import annotations

from app.schemas.common import ApiResponse
from app.schemas.health import HealthStatus
from app.schemas.responses import ProvidersResponse, SessionsResponse


class HealthEnvelope(ApiResponse[HealthStatus]):
    """Envelope for ``GET /health?envelope=true``."""


class ProvidersEnvelope(ApiResponse[ProvidersResponse]):
    """Envelope for ``GET /providers?envelope=true``."""


class SessionsEnvelope(ApiResponse[SessionsResponse]):
    """Envelope for ``GET /sessions?envelope=true``."""
