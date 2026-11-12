"""
tests/test_research_service.py
==============================
Tests for the pipeline runner extracted out of ``server.py``.

The orchestrator is replaced with a fake, so these tests exercise the real
provider-fallback loop, event sequencing, session persistence and cancellation
without any LLM calls.
"""

from __future__ import annotations

import queue
import threading

import pytest

from app.config import Settings
from app.schemas.requests import ResearchRunRequest
from app.services.research_service import ResearchService


class FakeSessionService:
    def __init__(self) -> None:
        self.saved: list[dict] = []

    def save_session(self, state, *, duration_seconds=None, notes="", user_id=None):
        self.saved.append(
            {"state": state, "user_id": user_id, "notes": notes, "duration": duration_seconds}
        )
        return "session-id"


def make_settings() -> Settings:
    return Settings.from_env(
        env={
            "APP_ENV": "test",
            "GROQ_API_KEY": "groq-key",
            "GEMINI_API_KEY": "gemini-key",
            "SHARE_SECRET": "unit-test-share-secret-0123456789abcdef",
        }
    )


class FakeOrchestrator:
    def __init__(self, provider: str) -> None:
        self.provider = provider

    def run(self, topic: str) -> dict:
        return {
            "status": "completed",
            "provider": self.provider,
            "critic_score": 9,
            "revision_count": 1,
            "saved_report_path": "/reports/r.md",
            "search_results_raw": [{}, {}],
            "logs": ["done"],
        }


def run_pipeline(
    service: ResearchService, cancel_event=None, topic: str = "Valid research topic"
) -> list:
    event_queue: queue.Queue = queue.Queue()
    service.run_blocking(
        request=ResearchRunRequest(topic=topic),
        user_id="user-1",
        event_queue=event_queue,
        research_id="run-test",
        cancel_event=cancel_event or threading.Event(),
    )
    events: list = []
    while True:
        item = event_queue.get(timeout=2)
        if item is None:
            break
        events.append(item)
    return events


class TestPipelineExecution:
    def test_completes_and_persists_session(self, monkeypatch) -> None:
        calls: list[str] = []

        def fake_build(*, provider, **_kwargs):
            calls.append(provider)
            return FakeOrchestrator(provider)

        monkeypatch.setattr("app.orchestration.pipeline.build_orchestrator", fake_build)
        sessions = FakeSessionService()
        service = ResearchService(make_settings(), sessions)  # type: ignore[arg-type]

        events = run_pipeline(service)

        assert calls == ["groq"]
        complete = [payload for kind, payload in events if kind == "complete"]
        assert len(complete) == 1
        assert complete[0]["status"] == "completed"
        assert complete[0]["critic_score"] == 9
        assert complete[0]["research_id"] == "run-test"
        assert len(sessions.saved) == 1
        assert sessions.saved[0]["user_id"] == "user-1"
        assert sessions.saved[0]["notes"] == "provider=groq"

    def test_provider_chain_dedupes_and_honours_alias(self) -> None:
        service = ResearchService(make_settings(), FakeSessionService())  # type: ignore[arg-type]
        chain = service.provider_chain("hf")
        assert chain[0] == "huggingface"
        assert len(chain) == len(set(chain))

    def test_falls_back_on_quota_error(self, monkeypatch) -> None:
        calls: list[str] = []

        def fake_build(*, provider, **_kwargs):
            calls.append(provider)
            if provider == "groq":
                raise RuntimeError("429 quota exceeded for this project")
            return FakeOrchestrator(provider)

        monkeypatch.setattr("app.orchestration.pipeline.build_orchestrator", fake_build)
        service = ResearchService(make_settings(), FakeSessionService())  # type: ignore[arg-type]

        events = run_pipeline(service)

        assert calls[0] == "groq"
        assert calls[1] == "gemini"
        notifications = [message for kind, message in events if kind == "agent_start"]
        assert any("retrying with 'gemini'" in message for message in notifications)
        complete = [payload for kind, payload in events if kind == "complete"][0]
        assert complete["provider"] == "gemini"

    def test_all_providers_failing_emits_safe_error(self, monkeypatch) -> None:
        secret = "sk-" + "a" * 24

        def fake_build(**_kwargs):
            raise RuntimeError(f"401 unauthorized: api key {secret}")

        monkeypatch.setattr("app.orchestration.pipeline.build_orchestrator", fake_build)
        sessions = FakeSessionService()
        service = ResearchService(make_settings(), sessions)  # type: ignore[arg-type]

        events = run_pipeline(service)

        errors = [payload for kind, payload in events if kind == "error"]
        assert len(errors) == 1
        assert errors[0]["error_code"] == "llm_provider_unavailable"
        assert "traceback" not in errors[0]
        assert secret not in errors[0]["message"]
        assert sessions.saved == []

    def test_cancelled_run_never_starts_a_provider(self, monkeypatch) -> None:
        calls: list[str] = []

        def fake_build(*, provider, **_kwargs):
            calls.append(provider)
            return FakeOrchestrator(provider)

        monkeypatch.setattr("app.orchestration.pipeline.build_orchestrator", fake_build)
        cancel_event = threading.Event()
        cancel_event.set()
        service = ResearchService(make_settings(), FakeSessionService())  # type: ignore[arg-type]

        events = run_pipeline(service, cancel_event=cancel_event)

        assert calls == []
        assert events == []
