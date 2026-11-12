"""Authenticated, replayable SSE service for distributed research runs."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Optional

from fastapi import Request

from app.config import Settings, get_settings
from app.errors import AuthenticationError, EventStreamUnavailableError, NotFoundError
from app.events.journal import EventJournal
from app.events.redis_streams import EventReader, EventTransportError
from app.events.schemas import ResearchStreamEvent
from app.events.sse import event_to_sse, terminal_event
from app.observability import get_logger
from app.services.agent_history_service import AgentHistoryService

logger = get_logger("app.events.service")


class ResearchStreamService:
    """Authorises a run and streams Redis events with replay semantics."""

    def __init__(
        self,
        reader: Optional[EventReader],
        history: AgentHistoryService,
        journal: Optional[EventJournal] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._reader = reader
        self._history = history
        self._journal = journal
        self._settings = settings or get_settings()

    def ensure_available(self) -> None:
        """Fail before opening a stream when Redis is not configured."""
        if self._reader is None:
            raise EventStreamUnavailableError(
                "The distributed research event stream is not configured."
            )

    def authorize(self, research_id: str, user_id: str) -> None:
        """Verify run ownership before a StreamingResponse is started."""
        owner = (user_id or "").strip()
        if not owner:
            raise AuthenticationError("Authentication required.")

        # The SQLite/SQLAlchemy execution history is the normal ownership
        # source. Workers that only write the PostgreSQL journal are covered by
        # the second check. Never trust a user_id carried in an event payload.
        try:
            self._history.progress(research_id, owner)
            return
        except NotFoundError:
            pass

        if self._journal is not None:
            status = self._journal.get_run_status(research_id)
            if status and status.get("owner_id") == owner:
                return

        raise NotFoundError("Research run not found.")

    async def stream(
        self,
        *,
        research_id: str,
        user_id: str,
        last_sequence: int,
        request: Request,
    ) -> AsyncGenerator[str, None]:
        """Yield SSE frames from replay through live completion."""
        self.ensure_available()
        self.authorize(research_id, user_id)
        reader = self._reader
        last_sequence = max(0, int(last_sequence))
        cursor = "0-0"

        # Tell EventSource how quickly to reconnect after a dropped connection.
        yield "retry: 3000\n\n"

        try:
            replayed, cursor = await asyncio.to_thread(
                reader.replay,
                research_id,
                after_sequence=last_sequence,
                limit=self._settings.event_stream_replay_limit,
            )
            if not replayed and self._journal is not None:
                # Redis retention may have expired a completed stream. The
                # PostgreSQL journal is the durable fallback for replay.
                replayed = self._journal.list_persisted(
                    research_id,
                    after_sequence=last_sequence,
                    limit=self._settings.event_stream_replay_limit,
                )
            if replayed and last_sequence and replayed[0].sequence_number > last_sequence + 1:
                # The stream's MAXLEN retention has already discarded some
                # events. Keep the client usable and make the gap explicit.
                yield (
                    "event: progress\n"
                    'data: {"event": "progress", "message": '
                    '"Some earlier progress events have expired.", '
                    '"data": {"replay_truncated": true}}\n\n'
                )
            for event in replayed:
                if event.sequence_number <= last_sequence:
                    continue
                last_sequence = max(last_sequence, event.sequence_number)
                yield event_to_sse(event)
                if terminal_event(event):
                    return

            while True:
                if await request.is_disconnected():
                    return
                events, cursor = await asyncio.to_thread(
                    reader.read_batch,
                    research_id,
                    after_sequence=last_sequence,
                    cursor=cursor,
                    block_ms=self._settings.event_stream_block_ms,
                    count=self._settings.event_stream_read_count,
                )
                if not events:
                    yield ": keepalive\n\n"
                    continue
                for event in events:
                    if event.sequence_number <= last_sequence:
                        continue
                    last_sequence = max(last_sequence, event.sequence_number)
                    yield event_to_sse(event)
                    if terminal_event(event):
                        return

                if cursor == "0-0" and self._journal is not None:
                    # A completed run whose Redis stream already expired must
                    # not leave a reconnecting client waiting forever.
                    status = self._journal.get_run_status(research_id)
                    if status and status.get("status") in {"completed", "failed", "cancelled"}:
                        return
        except asyncio.CancelledError:
            raise
        except EventTransportError:
            logger.exception("event.stream_transport_failed", extra={"research_id": research_id})
            yield (
                "event: error\n"
                'data: {"event": "error", "message": '
                '"The research event stream is temporarily unavailable.", '
                '"data": {"error_code": "stream_error"}}\n\n'
            )
        except Exception:
            logger.exception("event.stream_failed", extra={"research_id": research_id})
            yield (
                "event: error\n"
                'data: {"event": "error", "message": '
                '"The research progress stream was interrupted.", '
                '"data": {"error_code": "stream_error"}}\n\n'
            )


__all__ = ["ResearchStreamService"]
