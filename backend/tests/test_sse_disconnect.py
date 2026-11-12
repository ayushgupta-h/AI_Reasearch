"""SSE streaming: completion, error frames and client-disconnect cancellation."""

from __future__ import annotations

import asyncio
import queue
import threading

import pytest

from app.config import Settings
from app.schemas.requests import ResearchRunRequest
from app.services.research_service import ResearchService


class _Sessions:
    def __init__(self) -> None:
        self.saved: list = []

    def save_session(self, state, **kwargs):
        self.saved.append(state)
        return "session-id"


def _settings() -> Settings:
    return Settings.from_env(env={"APP_ENV": "test", "SHARE_SECRET": "x" * 32})


@pytest.fixture
def service() -> ResearchService:
    return ResearchService(_settings(), _Sessions())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_stream_emits_initial_progress_then_complete(service) -> None:
    payload = {
        "status": "completed",
        "provider": "gemini",
        "critic_score": 9,
        "revision_count": 0,
        "saved_report_path": "/reports/r.md",
        "sources_analyzed": 2,
        "logs": ["done"],
        "research_id": "abc",
    }

    def fake_run(**kwargs):
        kwargs["event_queue"].put(("complete", payload))
        kwargs["event_queue"].put(None)

    service.run_blocking = fake_run  # type: ignore[assignment]

    frames = []
    async for frame in service.stream(ResearchRunRequest(topic="Streaming topic"), None):
        frames.append(frame)
        if "event: complete" in frame:
            break

    assert "event: progress" in frames[0]
    assert '"research_id"' in frames[0]
    assert "event: complete" in frames[-1]
    assert '"critic_score": 9' in frames[-1]


@pytest.mark.asyncio
async def test_worker_error_becomes_a_safe_error_frame(service) -> None:
    def fake_run(**kwargs):
        kwargs["event_queue"].put(
            (
                "error",
                {
                    "message": "The research pipeline failed to complete.",
                    "error_code": "research_execution_failed",
                    "research_id": "run-x",
                },
            )
        )
        kwargs["event_queue"].put(None)

    service.run_blocking = fake_run  # type: ignore[assignment]

    frames = []
    async for frame in service.stream(ResearchRunRequest(topic="Failing topic"), None):
        frames.append(frame)
        if "event: error" in frame:
            break

    error_frame = frames[-1]
    assert "event: error" in error_frame
    assert "research_execution_failed" in error_frame
    assert "Traceback" not in error_frame


@pytest.mark.asyncio
async def test_client_disconnect_sets_the_cancellation_event(service) -> None:
    captured: dict[str, threading.Event] = {}
    started = threading.Event()

    def fake_run(**kwargs):
        captured["cancel"] = kwargs["cancel_event"]
        started.set()
        # Simulate a long-running pipeline that never emits a sentinel.
        while not kwargs["cancel_event"].wait(0.05):
            kwargs["event_queue"].put(("progress", "still working"))

    service.run_blocking = fake_run  # type: ignore[assignment]

    stream = service.stream(ResearchRunRequest(topic="Disconnect topic"), None)
    first = await stream.__anext__()
    assert "event: progress" in first

    await asyncio.get_running_loop().run_in_executor(None, started.wait, 2)
    assert "cancel" in captured

    await stream.aclose()
    assert captured["cancel"].is_set()


@pytest.mark.asyncio
async def test_stream_does_not_persist_on_disconnect(service) -> None:
    captured: dict[str, threading.Event] = {}

    def fake_run(**kwargs):
        captured["cancel"] = kwargs["cancel_event"]
        while not kwargs["cancel_event"].wait(0.05):
            kwargs["event_queue"].put(("progress", "working"))

    service.run_blocking = fake_run  # type: ignore[assignment]

    stream = service.stream(ResearchRunRequest(topic="Cancel before persist"), None)
    await stream.__anext__()
    await stream.aclose()

    assert service._sessions.saved == []  # type: ignore[attr-defined]
    assert captured["cancel"].is_set()


@pytest.mark.asyncio
async def test_sentinel_terminates_the_stream(service) -> None:
    def fake_run(**kwargs):
        kwargs["event_queue"].put(("progress", "one"))
        kwargs["event_queue"].put(("progress", "two"))
        kwargs["event_queue"].put(None)

    service.run_blocking = fake_run  # type: ignore[assignment]

    frames = [
        frame
        async for frame in service.stream(ResearchRunRequest(topic="Sentinel topic"), None)
    ]

    assert any("one" in frame for frame in frames)
    assert any("two" in frame for frame in frames)
    assert "Pipeline execution complete." in frames[-1]
