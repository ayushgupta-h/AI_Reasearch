"""
app/factory.py
==============
FastAPI application factory.

Wiring only — every responsibility lives in a dedicated module:

    config      → app.config
    errors      → app.errors
    logging     → app.observability
    HTTP        → app.api.*
    business    → app.services.*
    pipeline    → app.orchestration.*
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.config import Settings, get_settings
from app.errors import ConfigurationError, install_exception_handlers
from app.middleware import RequestContextMiddleware
from app.observability import configure_logging, get_logger
from app.openapi import install_contract_documentation
from app.services.container import ServiceContainer
from app.telemetry import HttpTelemetryMiddleware, setup_tracing
from app.utils.stdout_proxy import install_stdout_proxy


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Build and configure the Research AI API application."""
    settings = settings or get_settings()

    configure_logging(
        level=settings.log_level,
        fmt=settings.log_format,
        log_file=settings.log_file,
    )
    logger = get_logger("app.startup")

    # Raises ConfigurationError in production when required settings are absent.
    try:
        problems = settings.validate()
    except ConfigurationError as exc:
        logger.critical("config.invalid %s", exc.message)
        raise
    for problem in problems:
        logger.warning("config.warning %s", problem)

    settings.ensure_directories()
    install_stdout_proxy()
    setup_tracing(service_name="research-ai")

    container = ServiceContainer(settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.container = container
        logger.info(
            "server.started env=%s providers=%s",
            settings.app_env,
            ",".join(settings.configured_providers()) or "none",
        )
        try:
            yield
        finally:
            # Join local job threads and release adapter resources.
            container.shutdown()
            logger.info("server.stopped")

    application = FastAPI(
        title="Research AI API",
        description=(
            "Multi-Agent Research Orchestrator powered by LangGraph. "
            "Streams live agent progress via Server-Sent Events (SSE)."
        ),
        version=settings.api_version,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    application.state.container = container
    application.state.settings = settings
    application.state.started_at = time.time()

    # Added first → innermost. CORS is added last so preflight requests are
    # answered before request-context work happens.
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(HttpTelemetryMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        # Auth uses bearer tokens, never cookies; wildcard + credentials is
        # invalid per the CORS spec, so credentials stay off.
        allow_credentials=(
            settings.cors_allow_credentials and "*" not in settings.allowed_origins
        ),
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Accept",
            "X-Request-ID",
            "traceparent",
            "tracestate",
        ],
        expose_headers=[
            "X-Request-ID",
            "X-Trace-ID",
            "Content-Disposition",
            "X-Read-Only",
        ],
    )

    install_exception_handlers(application)
    application.include_router(api_router)
    # Publish the full schema registry (including models without endpoints yet).
    install_contract_documentation(application)
    return application
