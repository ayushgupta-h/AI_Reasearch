"""
app/errors.py
=============
Typed application errors with a single, consistent HTTP representation.

Design notes
------------
* ``AppError`` is the only exception type services should raise for expected
  failures. Routes never build ad-hoc ``HTTPException`` payloads for domain
  errors; they let the handlers in this module translate.
* The public payload keeps the ``detail`` key used by the existing frontend
  and by FastAPI's own error responses, so clients need no changes.
* Internal exception messages (LLM provider traces, SQLite paths, file system
  errors) are logged but never returned verbatim to anonymous callers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.errors")


# ─── Error hierarchy ─────────────────────────────────────────────────────────


class AppError(Exception):
    """Base class for expected application failures."""

    status_code: int = 500
    code: str = "internal_error"
    default_message: str = "An unexpected application error occurred."

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        details: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details or {}
        self.headers = headers or {}
        super().__init__(self.message)

    def to_payload(self, request_id: str = "-") -> Dict[str, Any]:
        # Mask anything that looks like a credential before it reaches a client.
        from app.observability import redact

        payload: Dict[str, Any] = {
            "detail": redact(self.message),
            "error_code": self.code,
            "request_id": request_id,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(AppError):
    """Invalid request payload or parameters."""

    status_code = 422
    code = "validation_error"
    default_message = "The request is invalid."


class AuthenticationError(AppError):
    """Missing or invalid credentials."""

    status_code = 401
    code = "authentication_required"
    default_message = "Authentication required."


class AuthorizationError(AppError):
    """Authenticated, but not permitted to access the resource."""

    status_code = 403
    code = "forbidden"
    default_message = "You do not have access to this resource."


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    default_message = "The requested resource was not found."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    default_message = "The request conflicts with the current state."


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"
    default_message = "The request could not be processed."


class DatabaseError(AppError):
    """SQLite / persistence failure."""

    status_code = 503
    code = "database_unavailable"
    default_message = "The session database is temporarily unavailable."


class LLMProviderError(AppError):
    """Every configured LLM provider failed."""

    status_code = 503
    code = "llm_provider_unavailable"
    default_message = "All language model providers are temporarily unavailable."


class OrchestrationError(AppError):
    """LangGraph pipeline failure that is not provider-specific."""

    status_code = 500
    code = "orchestration_failed"
    default_message = "The research pipeline failed to complete."


class RAGError(AppError):
    """Vector store / retrieval failure."""

    status_code = 500
    code = "rag_failed"
    default_message = "Knowledge base retrieval failed."


class FileProcessingError(AppError):
    """Report export or file read/write failure."""

    status_code = 500
    code = "file_processing_failed"
    default_message = "The file could not be processed."


class StreamingError(AppError):
    """SSE streaming failure."""

    status_code = 500
    code = "stream_error"
    default_message = "The progress stream was interrupted."


class ConfigurationError(AppError):
    """Fatal misconfiguration detected at startup."""

    status_code = 500
    code = "configuration_error"
    default_message = "The server is misconfigured."


class EventStreamUnavailableError(AppError):
    """The distributed event transport is not configured or reachable."""

    status_code = 503
    code = "service_unavailable"
    default_message = "The research event stream is temporarily unavailable."


class ResearchExecutionError(AppError):
    """A research run failed for a reason that is not provider-specific."""

    status_code = 500
    code = "research_execution_failed"
    default_message = "The research run could not be completed."


class AgentExecutionError(ResearchExecutionError):
    """A single agent stage failed inside the research pipeline."""

    code = "agent_execution_failed"
    default_message = "A research agent failed to complete its stage."


class SearchProviderError(AppError):
    """Every configured search provider failed or returned nothing usable."""

    status_code = 503
    code = "search_provider_unavailable"
    default_message = "Search providers are temporarily unavailable."


class VectorStoreError(AppError):
    """Vector store read/write/search failure (ChromaDB)."""

    status_code = 500
    code = "vector_store_failed"
    default_message = "The knowledge base is temporarily unavailable."


class CitationValidationError(AppError):
    """Citation extraction or link verification failed."""

    status_code = 500
    code = "citation_validation_failed"
    default_message = "Citations could not be validated."


class ReportGenerationError(AppError):
    """Report drafting, refinement or artifact writing failed."""

    status_code = 500
    code = "report_generation_failed"
    default_message = "The research report could not be generated."


class ResourceNotFoundError(NotFoundError):
    """Explicit alias for a missing resource (same 404 contract)."""


# ─── Handlers ────────────────────────────────────────────────────────────────


def _request_id(request: Optional[Request]) -> str:
    if request is not None:
        rid = getattr(request.state, "request_id", None)
        if rid:
            return str(rid)
    try:  # pragma: no cover - defensive, contextvar may be unset
        from app.observability import current_request_id

        return current_request_id()
    except Exception:  # pragma: no cover
        return "-"


def _sanitize_validation_errors(errors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop echoed input values and internal context from validation errors."""
    clean: List[Dict[str, Any]] = []
    for err in errors:
        clean.append(
            {
                "loc": list(err.get("loc", ())),
                "msg": err.get("msg", "Invalid value."),
                "type": err.get("type", "value_error"),
            }
        )
    return clean


def install_exception_handlers(app: FastAPI) -> None:
    """Register the application-wide exception handlers exactly once."""

    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        rid = _request_id(request)
        log = logger.warning if exc.status_code < 500 else logger.error
        log(
            "request.failed",
            extra={
                "error_code": exc.code,
                "status_code": exc.status_code,
                "path": request.url.path,
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_payload(rid),
            headers=exc.headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        rid = _request_id(request)
        logger.info(
            "request.validation_failed",
            extra={"path": request.url.path, "status_code": 422},
        )
        return JSONResponse(
            status_code=422,
            content={
                "detail": _sanitize_validation_errors(exc.errors()),
                "error_code": "validation_error",
                "request_id": rid,
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        rid = _request_id(request)
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": detail,
                "error_code": _code_for_status(exc.status_code),
                "request_id": rid,
            },
            headers=getattr(exc, "headers", None) or None,
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        rid = _request_id(request)
        logger.exception(
            "request.unhandled_exception",
            extra={"path": request.url.path, "status_code": 500},
        )
        # Never leak the exception text or traceback to the client.
        return JSONResponse(
            status_code=500,
            content={
                "detail": "An unexpected server error occurred. Please try again.",
                "error_code": "internal_error",
                "request_id": rid,
            },
        )


def _code_for_status(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "authentication_required",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        422: "validation_error",
        429: "rate_limited",
        503: "service_unavailable",
    }.get(status_code, "http_error")
