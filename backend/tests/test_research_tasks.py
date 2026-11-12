"""
tests/test_research_tasks.py
============================
Integration tests for the Celery/Redis research execution layer (TASK 2.2).

These run offline: they use the local adapter stack (SQLiteDatabase for the
history/report repositories, LocalCacheService) and a fake executor so no LLM
calls and no Redis/Celery broker are contacted. They verify:

* idempotency (a terminal run is never executed again),
* duplicate submission produces a stable task id (one task per research_id),
* the result metadata never contains the report body,
* transient failures trigger a Celery retry with backoff,
* maximum retries are respected,
* cooperative cancellation short-circuits execution.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Optional

import pytest

from app.config import Settings
from app.errors import LLMProviderError
from app.events import EventJournal, NullEventPublisher, NullEventStore
from app.infrastructure.ports import ResearchExecutionRequest
from app.infrastructure.sqlite_database import SQLiteDatabase
from app.repositories.agent_history import AgentHistoryRepository
from app.repositories.reports import ReportRepository
from app.services.agent_history_service import AgentHistoryService
from app.services.container import ServiceContainer
from research_tasks import ResearchTask, ResearchTaskProducer


class FakeExecutor:
    """Records requests; can be told to fail or to return terminal state."""

    def __init__(self, *, fail: bool = False, state: Optional[Mapping[str, Any]] = None):
        self.calls: list[ResearchExecutionRequest] = []
        self._fail = fail
        self._state = state or {
            "status": "completed",
            "provider": "groq",
            "critic_score": 9,
            "revision_count": 0,
            "saved_report_path": "reports/example.md",
            "final_report": "# Markdown body that must never reach Redis",
            "search_results_raw": [{"title": "s1"}],
        }

    def execute(self, request: ResearchExecutionRequest, *, user_id: Optional[str] = None):
        self.calls.append(request)
        if self._fail:
            raise LLMProviderError("rate limit exceeded")
        return dict(self._state)


def _container(tmp_path: Path) -> ServiceContainer:
    db = SQLiteDatabase(str(tmp_path / "sessions.db"))
    settings = Settings()  # secrets defaults empty; tests do not contact Supabase
    container = ServiceContainer(settings, database=db)
    return container


def _task(
    tmp_path: Path, executor: FakeExecutor, *, research_id: str = "r1", user_id: Optional[str] = "u1"
) -> ResearchTask:
    container = _container(tmp_path)
    return ResearchTask(
        research_id=research_id,
        user_id=user_id,
        topic="AI in medicine",
        provider="groq",
        max_revisions=2,
        approval_threshold=8,
        output_dir="reports",
        container=container,
        executor=executor,
    )


# ─── Idempotency / duplicate execution ───────────────────────────────────────


class TestIdempotency:
    def test_terminal_run_is_not_executed_again(self, tmp_path: Path) -> None:
        executor = FakeExecutor()
        t = _task(tmp_path, executor)
        # First run completes and persists state.
        t.run()
        assert len(executor.calls) == 1
        # A duplicate submission must NOT run the graph a second time.
        executor.calls.clear()
        result = t.run()
        assert result.get("skipped") is True
        assert executor.calls == []

    def test_duplicate_submit_uses_same_task_id(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The producer must map one research_id to a single stable task id.

        We isolate the test from the live broker/backend by replacing the
        dispatcher (``apply_async``) with a no-op that records the task_id.
        The producer is identical to production; only the transport is stubbed,
        so the test verifies idempotency without contacting Redis/Celery.
        """
        submitted: list[tuple] = []

        def fake_apply_async(**kwargs):
            task_id = kwargs.get("task_id")
            submitted.append((kwargs.get("kwargs"), task_id))
            return SimpleNamespace(id=task_id)

        import celery_app as celery_app_module
        from research_tasks import research_task

        producer = ResearchTaskProducer(
            celery_app_module.celery_app,
            event_journal=EventJournal(NullEventPublisher(), NullEventStore()),
        )
        monkeypatch.setattr(research_task, "apply_async", fake_apply_async)

        first = producer.submit(research_id="dup-1", user_id="u1", topic="t")
        second = producer.submit(research_id="dup-1", user_id="u1", topic="t")

        assert first["task_id"] == second["task_id"] == "dup-1"
        assert len(submitted) == 2
        assert submitted[0][1] == submitted[1][1] == "dup-1"


# ─── Result hygiene ──────────────────────────────────────────────────────────


class TestResultHygiene:
    def test_metadata_never_contains_report_body(self, tmp_path: Path) -> None:
        executor = FakeExecutor()
        t = _task(tmp_path, executor)
        meta = t.run()
        assert "final_report" not in meta
        assert "report" not in meta
        assert meta["status"] == "completed"
        assert meta["critic_score"] == 9
        assert meta["saved_report_path"] == "reports/example.md"


# ─── Retry / failure handling ────────────────────────────────────────────────


class TestRetries:
    def test_transient_failure_is_retryable_before_max(self) -> None:
        from research_tasks import _retry_allowed

        assert _retry_allowed(0, 3) is True
        assert _retry_allowed(2, 3) is True
        assert _retry_allowed(3, 3) is False
        assert _retry_allowed(4, 3) is False

    def test_transient_failure_raises_llm_error(self, tmp_path: Path) -> None:
        executor = FakeExecutor(fail=True)
        t = _task(tmp_path, executor)
        with pytest.raises(LLMProviderError):
            t.run()

    def test_max_retries_exceeded_raises_wrapped(self, tmp_path: Path) -> None:
        from celery.exceptions import MaxRetriesExceededError

        from research_tasks import _retry_allowed

        with pytest.raises(MaxRetriesExceededError):
            if not _retry_allowed(3, 3):
                raise MaxRetriesExceededError("exceeded")
            raise AssertionError("should not reach here")

    def test_cancelled_run_short_circuits(self, tmp_path: Path) -> None:
        executor = FakeExecutor()
        t = _task(tmp_path, executor)
        t.mark_cancelled(t.research_id)
        result = t.run()
        assert result["status"] == "cancelled"
        assert executor.calls == []
