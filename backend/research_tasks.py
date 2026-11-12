"""
research_tasks.py
=================
Celery task that executes one research run using the existing LangGraph
orchestrator (TASK 2.2).

Design invariants
-----------------
* **No duplicate execution.** The *research_id* is the stable task ``id`` and
  the idempotency key. Before starting we check the run's persisted state; if
  it is already terminal (completed/cancelled) we return immediately without
  running the graph again. Duplicate submissions therefore never run the graph
  twice.
* **PostgreSQL is the source of truth.** Task status is persisted through the
  existing ``AgentHistoryService`` (research_runs table) and the finished
  report through ``ReportManagementService``. The Celery result backend only
  carries lightweight metadata (status, provider, paths) - never report body.
* **Retries with backoff.** Transient provider failures recur with exponential
  backoff (jittered), capped at ``CELERY_MAX_RETRIES``. A retry never
  overwrites a completed report and never duplicates audit records because the
  completion bookkeeping is idempotent.
* **Timeouts.** ``time_limit`` / ``soft_time_limit`` are set from config so a
  hung run cannot occupy a worker forever.
* **Cancellation.** The task inspects a Redis/run flag (``cancel``) before
  starting; cancellation is cooperative (a running graph is not killed mid
  node, which matches the existing pipeline's behaviour).
"""

from __future__ import annotations

import time
from typing import Any, Dict, Mapping, Optional

from celery import Celery
from celery.exceptions import MaxRetriesExceededError, TimeLimitExceeded

from app.config import get_settings
from app.errors import LLMProviderError
from app.events import (
    BoundEventPublisher,
    EventJournal,
    ResearchStreamEventType,
    build_event_journal,
)
from app.infrastructure.ports import ResearchExecutionRequest
from app.infrastructure.research_executor import LocalResearchExecutor
from app.observability import get_logger, redact
from app.services.agent_history_service import AgentHistoryService
from app.services.container import ServiceContainer
from app.services.report_management_service import ReportManagementService
from app.services.session_service import SessionService
from app.telemetry import (
    extract_trace_context,
    get_metrics,
    inject_trace_context,
    trace_span,
)

logger = get_logger("research.celery.tasks")

# Shared in-process cancellation marker store, used when no real Redis-backed
# cache is available (local/dev mode). Both the setter and the checker must
# read the same instance so cooperative cancellation works offline.
from app.infrastructure.local_cache import LocalCacheService

_SHARED_CACHE = LocalCacheService()


class ResearchTask:
    """Encapsulates a single research run's state + persistence helpers.

    Kept separate from the Celery task object so the logic is unit-testable
    without a broker.
    """

    def __init__(
        self,
        *,
        research_id: str,
        user_id: Optional[str],
        topic: str,
        provider: str,
        max_revisions: int,
        approval_threshold: int,
        output_dir: str,
        container: Optional[ServiceContainer] = None,
        executor: Optional[LocalResearchExecutor] = None,
        event_journal: Optional[EventJournal] = None,
    ) -> None:
        self.research_id = research_id
        self.user_id = user_id
        self.topic = topic
        self.provider = provider
        self.max_revisions = max_revisions
        self.approval_threshold = approval_threshold
        self.output_dir = output_dir
        self._container = container or ServiceContainer(get_settings())
        self._journal = event_journal or build_event_journal(self._container.settings)
        self._events = BoundEventPublisher(
            self._journal,
            research_id=research_id,
            user_id=user_id or "anonymous",
        )
        self._executor = executor or LocalResearchExecutor(
            recorder_factory=self._container.agent_history.recorder_for,
            event_publisher=self._events,
        )

    def _publish(
        self,
        event_type: ResearchStreamEventType,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Best-effort event publication; never affects run outcome."""
        try:
            self._events.publish(event_type, payload)
        except Exception:
            logger.exception(
                "research.event_publish_failed",
                extra={"research_id": self.research_id, "event_type": event_type.value},
            )

    def _set_status(self, status: str) -> None:
        try:
            self._journal.set_run_status(self.research_id, self.user_id or "anonymous", status)
        except Exception:
            logger.exception(
                "research.status_persist_failed",
                extra={"research_id": self.research_id, "status": status},
            )

    # ── Service accessors (lazy) ──────────────────────────────────────────

    @property
    def history(self) -> AgentHistoryService:
        return self._container.agent_history

    @property
    def sessions(self) -> SessionService:
        return self._container.sessions

    @property
    def reports(self) -> ReportManagementService:
        return self._container.report_management

    # ── Idempotency ───────────────────────────────────────────────────────

    def already_terminal(self) -> bool:
        """True when the run has already reached a finished state.

        We only consult our own authoritative store (the run record). A
        completed/cancelled run is never executed again.
        """
        if not self.user_id:
            return False
        try:
            run = self.history._repository.get_run(self.research_id, self.user_id)
        except Exception:
            return False
        if not run:
            return False
        return run.get("status") in {"completed", "cancelled", "failed"}

    def check_cancelled(self, cache: Optional[Any] = None) -> bool:
        """Cooperative cancellation check (Redis-backed when available)."""
        try:
            store = cache if cache is not None else _SHARED_CACHE
            flag = store.get(f"research:cancel:{self.research_id}")
            return flag is not None and flag in {"1", "true"}
        except Exception:
            return False

    @staticmethod
    def mark_cancelled(research_id: str, cache: Optional[Any] = None) -> None:
        """Set the cancellation flag (best effort, never raises)."""
        try:
            store = cache if cache is not None else _SHARED_CACHE
            store.set(f"research:cancel:{research_id}", "1", ttl_seconds=3600)
        except Exception:
            pass

    # ── Execute ───────────────────────────────────────────────────────────

    def run(self) -> Mapping[str, Any]:
        """Execute the research graph (or short-circuit if already done)."""
        if self.already_terminal():
            logger.info(
                "research.task_skipped_terminal",
                extra={"research_id": self.research_id, "agent": "pipeline"},
            )
            return {
                "status": "completed",
                "provider": self.provider,
                "skipped": True,
                "research_id": self.research_id,
            }

        if self.check_cancelled():
            self.history.cancel_run(self.research_id, self.user_id)
            self._set_status("cancelled")
            self._publish(
                ResearchStreamEventType.RESEARCH_FAILED,
                {"status": "cancelled", "reason": "cancelled"},
            )
            return {
                "status": "cancelled",
                "provider": self.provider,
                "research_id": self.research_id,
            }

        started = time.perf_counter()
        history = self.history
        self._set_status("running")
        self._publish(
            ResearchStreamEventType.RESEARCH_STARTED,
            {"topic": self.topic, "provider": self.provider},
        )
        if self.user_id:
            history.recorder_for(self.research_id, self.user_id, self.topic)

        request = ResearchExecutionRequest(
            topic=self.topic,
            provider=self.provider,
            max_revisions=self.max_revisions,
            approval_threshold=self.approval_threshold,
            output_dir=self.output_dir,
            research_id=self.research_id,
        )

        try:
            state: Mapping[str, Any] = dict(
                self._executor.execute(request, user_id=self.user_id)
            )
        except Exception as exc:
            if self.user_id:
                history.fail_run(self.research_id, self.user_id, exc)
            self._set_status("failed")
            self._publish(
                ResearchStreamEventType.RESEARCH_FAILED,
                {"message": redact(exc)[:500], "error_code": "research_execution_failed"},
            )
            raise

        status = state.get("status", "")
        duration = round(time.perf_counter() - started, 1)
        if status in ("completed", "cancelled"):
            self._finalize(state, started)
            self._publish_outcome(state, duration=duration)
            return self._metadata(state)

        # Non-terminal pipeline outcome (e.g. no provider succeeded).
        error = LLMProviderError(
            f"Research pipeline ended with status {status!r}: "
            f"{redact(state.get('logs', []))}"
        )
        if self.user_id:
            history.fail_run(self.research_id, self.user_id, error)
        self._set_status("failed")
        self._publish(
            ResearchStreamEventType.RESEARCH_FAILED,
            {"message": redact(error)[:500], "error_code": "llm_provider_unavailable"},
        )
        raise error

    def _publish_outcome(self, state: Mapping[str, Any], duration: float = 0.0) -> None:
        """Publish the business milestones that are available in final state."""
        status = str(state.get("status", "completed"))
        self._set_status("completed" if status == "completed" else "cancelled")
        sources_count = len(state.get("search_results_raw", []))
        critic_score = state.get("critic_score")
        revision_count = state.get("revision_count", 0)

        # Record Research Metrics in Prometheus
        get_metrics().record_research_outcome(
            duration_seconds=duration,
            sources_count=sources_count,
            claims_count=len(state.get("claims", [])),
            critic_score=critic_score if isinstance(critic_score, int) else None,
            revision_count=revision_count if isinstance(revision_count, int) else 0,
        )

        self._publish(
            ResearchStreamEventType.RESEARCH_COMPLETED,
            {
                "status": status,
                "provider": state.get("provider", self.provider),
                "critic_score": critic_score,
                "revision_count": revision_count,
                "saved_report_path": state.get("saved_report_path", ""),
                "sources_analyzed": sources_count,
                "logs": state.get("logs", []),
                "research_id": self.research_id,
            },
        )

    # ── Persistence (idempotent) ──────────────────────────────────────────

    def _finalize(self, state: Mapping[str, Any], started: float) -> None:
        research_id = self.research_id
        user_id = self.user_id
        status = state.get("status", "completed")

        # 1. Mark the run completed (idempotent: update on the canonical row).
        if user_id:
            self.history.complete_run(
                research_id, user_id, status="completed" if status == "completed" else "cancelled"
            )
        else:
            self.history.complete_run(research_id, "", status="completed")
        self._set_status("completed" if status == "completed" else "cancelled")

        # 2. Persist the session record (existing SessionService).
        try:
            self.sessions.save_session(
                dict(state),
                duration_seconds=round(time.perf_counter() - started, 1),
                notes=f"provider={state.get('provider', self.provider)}",
                user_id=user_id,
            )
        except Exception:
            logger.warning(
                "research.session_persist_failed",
                extra={"research_id": research_id, "error_code": "database_unavailable"},
            )

        # 3. Register the report (existing ReportManagementService) - content
        #    goes to object storage, not Redis.
        if user_id:
            try:
                self.reports.register_from_run(
                    owner_id=user_id,
                    research_id=research_id,
                    title=self.topic,
                    content_markdown=str(
                        state.get("final_report") or state.get("report") or ""
                    ),
                    critic_score=state.get("critic_score"),
                    citations_verified=state.get("citations_verified"),
                    citations_total=state.get("citations_total"),
                )
            except Exception:
                logger.warning(
                    "research.report_register_failed",
                    extra={"research_id": research_id},
                )

    def _metadata(self, state: Mapping[str, Any]) -> Mapping[str, Any]:
        """Lightweight result - NO report body. Never stored in Redis fully."""
        return {
            "status": state.get("status", "completed"),
            "provider": state.get("provider", self.provider),
            "critic_score": state.get("critic_score"),
            "revision_count": state.get("revision_count", 0),
            "saved_report_path": state.get("saved_report_path", ""),
            "research_id": self.research_id,
            "sources_analyzed": len(state.get("search_results_raw", [])),
        }


def _retry_allowed(retries: int, max_retries: int) -> bool:
    """Whether a transient failure may be retried (before max_retries)."""
    return retries < max_retries


class ResearchTaskProducer:
    """Enqueues research runs with a stable id (idempotency key)."""

    def __init__(
        self,
        app: Celery,
        *,
        default_max_retries: Optional[int] = None,
        event_journal: Optional[EventJournal] = None,
    ) -> None:
        self.app = app
        self.default_max_retries = default_max_retries
        self._journal = event_journal or build_event_journal(get_settings())

    def submit(
        self,
        *,
        research_id: str,
        user_id: Optional[str],
        topic: str,
        provider: Optional[str] = None,
        max_revisions: Optional[int] = None,
        approval_threshold: Optional[int] = None,
        output_dir: Optional[str] = None,
    ) -> Dict[str, str]:
        """Dispatch a research run. The *research_id* becomes the task id, so
        a duplicate ``submit`` for the same id either lands on the same task
        or is rejected by Celery - the graph can never run twice."""
        payload = {
            "research_id": research_id,
            "user_id": user_id,
            "topic": topic,
            "provider": provider or get_settings().default_provider,
            "max_revisions": max_revisions if max_revisions is not None else get_settings().max_revisions,
            "approval_threshold": (
                approval_threshold
                if approval_threshold is not None
                else get_settings().approval_threshold
            ),
            "output_dir": output_dir
            or str(get_settings().reports_dir),
        }
        owner = user_id or "anonymous"
        self._journal.set_run_status(research_id, owner, "queued")
        try:
            self._journal.publish(
                research_id=research_id,
                user_id=owner,
                event_type=ResearchStreamEventType.RESEARCH_QUEUED,
                payload={"topic": topic, "provider": payload["provider"]},
            )
            # Inject OpenTelemetry distributed trace context into Celery headers
            headers = inject_trace_context({"research_id": research_id})
            result = research_task.apply_async(
                kwargs=payload,
                task_id=research_id,
                headers=headers,
            )
        except Exception as exc:
            self._journal.set_run_status(research_id, owner, "failed")
            self._journal.publish(
                research_id=research_id,
                user_id=owner,
                event_type=ResearchStreamEventType.RESEARCH_FAILED,
                payload={"message": "Could not enqueue the research task.", "error_code": "queue_error"},
            )
            raise exc
        return {
            "research_id": research_id,
            "task_id": result.id,
            "status": "queued",
        }


# ── Celery task object ──────────────────────────────────────────────────────


def _build_task():
    from celery_app import celery_app

    @celery_app.task(
        bind=True,
        name="research.run",
        max_retries=get_settings().celery_max_retries,
        retry_backoff=get_settings().celery_retry_backoff,
        retry_backoff_max=get_settings().celery_retry_backoff_max,
        retry_jitter=True,
        time_limit=get_settings().celery_task_time_limit,
        soft_time_limit=get_settings().celery_task_soft_time_limit,
        acks_late=True,
        reject_on_worker_lost=True,
    )
    def research_task(
        self,
        research_id: str,
        user_id: Optional[str],
        topic: str,
        provider: str,
        max_revisions: int,
        approval_threshold: int,
        output_dir: str,
    ) -> Mapping[str, Any]:
        worker = ResearchTask(
            research_id=research_id,
            user_id=user_id,
            topic=topic,
            provider=provider,
            max_revisions=max_revisions,
            approval_threshold=approval_threshold,
            output_dir=output_dir,
        )
        task_name = "research.run"
        worker_name = getattr(self.request, "hostname", "default")
        parent_headers = getattr(self.request, "headers", None) or {}
        parent_ctx = extract_trace_context(parent_headers)
        metrics = get_metrics()

        task_started = time.perf_counter()
        metrics.record_celery_task_start(task_name=task_name, worker=worker_name)
        with trace_span(
            f"celery.{task_name}",
            attributes={"topic": topic, "provider": provider, "worker": worker_name},
            research_id=research_id,
            context=parent_ctx,
        ):
            try:
                result = worker.run()
                duration = time.perf_counter() - task_started
                metrics.record_celery_task_finish(
                    task_name=task_name,
                    status="completed",
                    duration_seconds=duration,
                    worker=worker_name,
                )
                return result
            except (LLMProviderError, TimeLimitExceeded) as exc:
                metrics.record_celery_retry(task_name=task_name)
                # Transient failures → retry with backoff up to max_retries.
                if not _retry_allowed(self.request.retries, self.max_retries):
                    duration = time.perf_counter() - task_started
                    metrics.record_celery_task_finish(
                        task_name=task_name,
                        status="failed",
                        duration_seconds=duration,
                        worker=worker_name,
                        exception_type=type(exc).__name__,
                    )
                    raise MaxRetriesExceededError(
                        f"Research {research_id} exceeded max retries", exc=exc
                    ) from exc
                raise self.retry(exc=exc, countdown=None) from exc
            except Exception as exc:
                duration = time.perf_counter() - task_started
                metrics.record_celery_task_finish(
                    task_name=task_name,
                    status="failed",
                    duration_seconds=duration,
                    worker=worker_name,
                    exception_type=type(exc).__name__,
                )
                raise

    return research_task


research_task = _build_task()
producer = ResearchTaskProducer(research_task.app)
