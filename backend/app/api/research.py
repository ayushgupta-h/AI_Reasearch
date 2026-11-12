"""Research endpoints: the multi-agent pipeline (SSE) and follow-up chat."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.errors import BadRequestError
from app.events import ResearchStreamService
from app.schemas.errors import ApiError
from app.schemas.requests import ChatRequest, ResearchRunRequest
from app.schemas.responses import ChatResponse
from app.security.dependencies import get_current_user, get_optional_user
from app.services.chat_service import ChatService
from app.services.container import (
    get_chat_service,
    get_research_service,
    get_research_stream_service,
)
from app.services.research_service import ResearchService

router = APIRouter()


@router.post(
    "/research",
    tags=["Research"],
    summary="Start a research job and stream live progress via SSE",
    response_description=(
        "A text/event-stream of SSE events. Each event is a JSON object with "
        "keys: event, message, agent (optional), data (optional). The `data` "
        "payload of the `complete` event matches ResearchResponse."
    ),
    responses={
        200: {
            "description": "Server-Sent Events stream (`text/event-stream`).",
            "content": {
                "text/event-stream": {
                    "schema": {"$ref": "#/components/schemas/ResearchEvent"}
                }
            },
        },
        503: {
            "model": ApiError,
            "description": "Every configured LLM provider failed.",
        },
    },
)
async def run_research(
    request: ResearchRunRequest,
    current_user: Optional[dict] = Depends(get_optional_user),
    service: ResearchService = Depends(get_research_service),
) -> StreamingResponse:
    """Start a multi-agent research pipeline and stream progress as SSE."""
    user_id = current_user.get("user_id") if current_user else None
    return StreamingResponse(
        service.stream(request, user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering
            "Connection": "keep-alive",
        },
    )


@router.get(
    "/research/{research_id}/events",
    tags=["Research"],
    summary="Reconnectable SSE stream for a distributed research run",
    response_description=(
        "A text/event-stream of research events. Frames carry an SSE `id` equal to the "
        "event sequence number; reconnect with Last-Event-ID to replay missed events."
    ),
    responses={
        200: {"description": "Server-Sent Events stream (`text/event-stream`)."},
        401: {"model": ApiError, "description": "Authentication required."},
        404: {"model": ApiError, "description": "Research run not found."},
        503: {"model": ApiError, "description": "Event transport is unavailable."},
    },
)
async def stream_research_events(
    request: Request,
    research_id: str = Path(
        ..., pattern=r"^[a-f0-9]{32}$", description="32-character lowercase hex research id."
    ),
    last_event_id: Optional[str] = Query(
        None, description="Sequence number to replay after (query fallback for EventSource)."
    ),
    last_event_id_header: Optional[str] = Header(None, alias="Last-Event-ID"),
    current_user: dict = Depends(get_current_user),
    service: ResearchStreamService = Depends(get_research_stream_service),
) -> StreamingResponse:
    """Stream a queued/running research run with replay after reconnect.

    The authenticated identity is always derived from the JWT; a research id
    cannot be used to subscribe to another user's private event stream.
    """
    raw_sequence = (last_event_id_header or last_event_id or "0").strip()
    try:
        last_sequence = int(raw_sequence)
    except (TypeError, ValueError) as exc:
        raise BadRequestError("Last-Event-ID must be a non-negative sequence number.") from exc
    if last_sequence < 0:
        raise BadRequestError("Last-Event-ID must be a non-negative sequence number.")

    user_id = str(current_user.get("user_id") or "")
    # Validate configuration and ownership before Starlette starts the
    # streaming response; otherwise failures would be delivered as a 200
    # stream that closes early.
    service.ensure_available()
    service.authorize(research_id, user_id)
    return StreamingResponse(
        service.stream(
            research_id=research_id,
            user_id=user_id,
            last_sequence=last_sequence,
            request=request,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post(
    "/chat",
    response_model=ChatResponse,
    tags=["Research"],
    responses={
        503: {
            "model": ApiError,
            "description": "Every configured LLM provider failed.",
        }
    },
)
async def chat(
    request: ChatRequest,
    service: ChatService = Depends(get_chat_service),
) -> ChatResponse:
    """Single-turn chat answer from the first available LLM provider.

    Returns HTTP 503 with a sanitised explanation when every provider fails.
    """
    reply, provider = await run_in_threadpool(service.complete, request.message)
    return ChatResponse(reply=reply, provider=provider)
