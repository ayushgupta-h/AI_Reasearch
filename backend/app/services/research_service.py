"""
app/services/research_service.py
================================
Research execution and SSE streaming.

This module owns everything that used to live inside ``server.py``'s request
handlers and background thread:

* provider fallback chain and provider-error classification,
* running the LangGraph pipeline off the event loop,
* translating pipeline stdout into ``progress`` / ``agent_start`` events,
* persisting the finished session,
* client-disconnect cancellation and stream error events.

Public contract is unchanged: event names, payload keys and keepalive comments
are identical to the original implementation.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
import traceback
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.config import PROVIDER_ORDER, Settings
from app.errors import (
    AppError,
    ConfigurationError,
    LLMProviderError,
    OrchestrationError,
)
from app.infrastructure.ports import ResearchExecutionRequest, ResearchExecutor
from app.infrastructure.research_executor import LocalResearchExecutor
from app.observability import (
    get_logger,
    log_context,
    new_research_id,
    redact,
)
from app.schemas.requests import ResearchRunRequest
from app.services.agent_history_service import AgentHistoryService
from app.services.report_management_service import ReportManagementService
from app.services.session_service import SessionService
from app.utils.sse import QueueStreamWriter, build_event, detect_agent
from app.utils.stdout_proxy import capture_stdout

logger = get_logger("app.research")

# Client-safe text for domain failures raised deeper in the pipeline.
_RESEARCH_ERROR_MESSAGES = {
    "research_execution_failed": "The research run could not be completed.",
    "agent_execution_failed": "A research agent failed to complete its stage.",
    "search_provider_unavailable": "Search providers are temporarily unavailable.",
    "vector_store_failed": "The knowledge base is temporarily unavailable.",
    "citation_validation_failed": "Citations could not be validated.",
    "report_generation_failed": "The research report could not be generated.",
}

_PROVIDER_ERROR_MARKERS = (
    "429", "resource_exhausted", "quota", "rate limit", "rate_limit",
    "401", "403", "unauthorized", "api key", "api_key", "billing",
    "insufficient", "exceeded your current",
    # Network-level failures: a transient socket/proxy blip on one call
    # should let the next provider in the chain try, not kill the run.
    "winerror", "socket", "unreachable", "connection", "timed out",
    "timeout", "network", "ssl", "getaddrinfo failed", "name or service",
    "502", "503", "504", "overloaded", "temporarily unavailable",
    "max retries exceeded",
    "bad request", "invalid_request_error", "model_not_supported",
)


def is_provider_error(exc: BaseException) -> bool:
    """True when another provider could plausibly survive this failure."""
    text = str(exc).lower()
    return any(marker in text for marker in _PROVIDER_ERROR_MARKERS)


def brief_error(exc: BaseException, limit: int = 160) -> str:
    text = " ".join(redact(exc).split())
    return (text[:limit] + "…") if len(text) > limit else text


class ResearchService:
    def __init__(
        self,
        settings: Settings,
        sessions: SessionService,
        executor: Optional[ResearchExecutor] = None,
        history: Optional[AgentHistoryService] = None,
        reports: Optional[ReportManagementService] = None,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._executor: ResearchExecutor = executor or LocalResearchExecutor()
        self._history = history
        self._reports = reports

    # ── Provider chain ────────────────────────────────────────────────────

    def provider_chain(self, requested: str) -> List[str]:
        """Requested provider first, then every other configured provider."""
        first = (requested or self._settings.default_provider).strip().lower()
        if first == "hf":
            first = "huggingface"
        chain: List[str] = [first]
        for provider in (*self._settings.configured_providers(), *PROVIDER_ORDER):
            if provider not in chain:
                chain.append(provider)
        return chain

    # ── Blocking pipeline (runs in a worker thread) ───────────────────────

    def run_blocking(
        self,
        *,
        request: ResearchRunRequest,
        user_id: Optional[str],
        event_queue: "queue.Queue",
        research_id: str,
        cancel_event: threading.Event,
    ) -> None:
        """Execute the pipeline, pushing events onto *event_queue*.

        Always terminates the stream by pushing the ``None`` sentinel.
        """
        started = time.perf_counter()
        try:
            with log_context(research_id=research_id):
                self._execute(
                    request=request,
                    user_id=user_id,
                    event_queue=event_queue,
                    research_id=research_id,
                    cancel_event=cancel_event,
                    started=started,
                )
        except Exception as exc:  # defensive: _execute handles its own errors
            logger.exception("research.failed", extra={"error_code": "research_execution_failed"})
            self._mark_failed(research_id, user_id, exc)
            event_queue.put(("error", self._error_payload(exc, research_id)))
        finally:
            event_queue.put(None)

    def _execute(
        self,
        *,
        request: ResearchRunRequest,
        user_id: Optional[str],
        event_queue: "queue.Queue",
        research_id: str,
        cancel_event: threading.Event,
        started: float,
    ) -> None:
        writer = QueueStreamWriter(event_queue, passthrough=None)
        final_state: Optional[Dict[str, Any]] = None
        used_provider = request.provider
        failures: List[str] = []
        chain = self.provider_chain(request.provider)

        with capture_stdout(writer):
            for attempt, provider in enumerate(chain):
                if cancel_event.is_set():
                    logger.info("research.cancelled_before_attempt", extra={"agent": provider})
                    return
                try:
                    if attempt:
                        event_queue.put((
                            "agent_start",
                            f"Provider '{chain[attempt - 1]}' is unavailable "
                            f"(quota / rate limit) — retrying with '{provider}'...",
                        ))
                    else:
                        event_queue.put((
                            "agent_start",
                            f"Initialising Multi-Agent Orchestrator ({provider})...",
                        ))

                    event_queue.put((
                        "agent_start",
                        f"Launching research pipeline for: {request.topic}",
                    ))
                    candidate = dict(
                        self._executor.execute(
                            ResearchExecutionRequest(
                                topic=request.topic,
                                provider=provider,
                                max_revisions=request.max_revisions,
                                approval_threshold=request.approval_threshold,
                                output_dir=str(request.output_dir),
                                research_id=research_id,
                            ),
                            user_id=user_id,
                        )
                    )

                    status = candidate.get("status", "")
                    if status in ("completed", "cancelled"):
                        final_state = candidate
                        used_provider = provider
                        break

                    failures.append(f"{provider}: pipeline ended with status '{status}'")
                except Exception as exc:
                    failures.append(f"{provider}: {brief_error(exc)}")
                    logger.exception(
                        "research.provider_failed",
                        extra={"agent": provider, "error_code": "llm_provider_failed"},
                    )
                    if not is_provider_error(exc) or attempt == len(chain) - 1:
                        break
                    continue

        if final_state is None:
            raise LLMProviderError(
                "All LLM providers failed — " + " | ".join(failures)
            )

        if not cancel_event.is_set():
            self._persist(
                final_state=final_state,
                user_id=user_id,
                used_provider=used_provider,
                started=started,
                event_queue=event_queue,
            )
            self._register_report(final_state, user_id, research_id, request.topic)

        duration = round(time.perf_counter() - started, 1)
        critic_score = final_state.get("critic_score")
        revision_count = final_state.get("revision_count", 0)
        from app.telemetry import get_metrics

        get_metrics().record_research_outcome(
            duration_seconds=duration,
            sources_count=len(final_state.get("search_results_raw", [])),
            claims_count=len(final_state.get("claims", [])),
            critic_score=critic_score if isinstance(critic_score, int) else None,
            revision_count=revision_count if isinstance(revision_count, int) else 0,
        )
        logger.info(
            "research.completed",
            extra={
                "duration_ms": round(duration * 1000, 1),
                "provider": used_provider,
                "status": final_state.get("status", "completed"),
                "critic_score": critic_score,
            },
        )

        event_queue.put((
            "complete",
            {
                "status": final_state.get("status", "completed"),
                "provider": used_provider,
                "critic_score": final_state.get("critic_score"),
                "revision_count": final_state.get("revision_count", 0),
                "saved_report_path": final_state.get("saved_report_path", ""),
                "sources_analyzed": len(final_state.get("search_results_raw", [])),
                "logs": final_state.get("logs", []),
                "research_id": research_id,
            },
        ))
        self._mark_completed(
            research_id, user_id, final_state.get("status", "completed")
        )

    def _persist(
        self,
        *,
        final_state: Dict[str, Any],
        user_id: Optional[str],
        used_provider: str,
        started: float,
        event_queue: "queue.Queue",
    ) -> None:
        try:
            self._sessions.save_session(
                final_state,
                duration_seconds=time.perf_counter() - started,
                notes=f"provider={used_provider}",
                user_id=user_id,
            )
        except Exception as exc:
            # Persistence failures must not destroy a finished research run.
            logger.warning("research.session_persist_failed", extra={"error_code": "database_unavailable"})
            event_queue.put(("progress", f"Session persistence skipped: {brief_error(exc)}"))

    # ── SSE streaming ─────────────────────────────────────────────────────

    async def stream(
        self, request: ResearchRunRequest, user_id: Optional[str]
    ) -> AsyncGenerator[str, None]:
        """Stream pipeline progress for one HTTP request."""
        event_queue: "queue.Queue" = queue.Queue()
        loop = asyncio.get_running_loop()
        research_id = new_research_id()
        cancel_event = threading.Event()

        logger.info(
            "research.started",
            extra={"provider": request.provider, "agent": "pipeline"},
        )

        worker = threading.Thread(
            target=self.run_blocking,
            kwargs={
                "request": request,
                "user_id": user_id,
                "event_queue": event_queue,
                "research_id": research_id,
                "cancel_event": cancel_event,
            },
            daemon=True,
            name=f"research-{research_id[:8]}",
        )
        worker.start()

        previous_agent: Optional[str] = None
        try:
            # Inside the try so a disconnect before the first frame still
            # cancels the worker thread instead of leaving it running.
            yield build_event(
                "progress",
                "Research pipeline initialising...",
                data={"topic": request.topic, "research_id": research_id},
            )

            while True:
                try:
                    item = await loop.run_in_executor(
                        None, lambda: event_queue.get(timeout=0.05)
                    )
                except queue.Empty:
                    # Keep proxies from dropping an idle connection.
                    yield ": keepalive\n\n"
                    continue

                if item is None:
                    yield build_event("progress", "Pipeline execution complete.")
                    break

                event_type, payload = item

                if event_type == "complete":
                    yield build_event(
                        "complete",
                        "Research report successfully generated.",
                        data=payload,
                    )
                    break

                if event_type == "error":
                    if isinstance(payload, dict):
                        yield build_event(
                            "error",
                            payload.get("message", "Unknown error"),
                            data=payload,
                        )
                    else:
                        yield build_event("error", str(payload))
                    break

                if event_type == "agent_start":
                    agent = detect_agent(str(payload))
                    if agent and agent != previous_agent:
                        previous_agent = agent
                        yield build_event("agent_start", f"Agent active: {agent}", agent=agent)
                    yield build_event("progress", str(payload), agent=agent)
                    continue

                line = str(payload)
                agent = detect_agent(line)
                if agent and agent != previous_agent:
                    previous_agent = agent
                    yield build_event("agent_start", f"Agent active: {agent}", agent=agent)
                yield build_event("progress", line, agent=agent or previous_agent)

        except (GeneratorExit, asyncio.CancelledError):
            cancel_event.set()
            self._mark_cancelled(research_id, user_id)
            logger.warning("research.stream_cancelled", extra={"agent": "pipeline"})
            raise
        except Exception:
            cancel_event.set()
            logger.exception("research.stream_failed", extra={"error_code": "stream_error"})
            yield build_event(
                "error",
                "The progress stream was interrupted.",
                data={"error_code": "stream_error", "research_id": research_id},
            )
        finally:
            cancel_event.set()

    # ── Execution history ────────────────────────────────────────────────

    def _mark_completed(
        self, research_id: str, user_id: Optional[str], status: str
    ) -> None:
        if self._history is None:
            return
        try:
            self._history.complete_run(
                research_id,
                user_id,
                status="completed" if status == "completed" else "cancelled",
            )
        except Exception:  # telemetry must never affect the response
            logger.exception("research.history_complete_failed")

    def _mark_failed(
        self, research_id: str, user_id: Optional[str], exc: BaseException
    ) -> None:
        if self._history is None:
            return
        try:
            self._history.fail_run(research_id, user_id, exc)
        except Exception:  # telemetry must never affect the response
            logger.exception("research.history_failure_failed")

    def _mark_cancelled(self, research_id: str, user_id: Optional[str]) -> None:
        if self._history is None:
            return
        try:
            self._history.cancel_run(research_id, user_id)
        except Exception:
            logger.exception("research.history_cancel_failed")

    # ── Reports ──────────────────────────────────────────────────────────

    def _register_report(
        self,
        final_state: Dict[str, Any],
        user_id: Optional[str],
        research_id: str,
        topic: str,
    ) -> None:
        if self._reports is None or not user_id:
            return
        content = final_state.get("final_report") or final_state.get("report") or ""
        if not str(content).strip():
            return
        try:
            self._reports.register_from_run(
                owner_id=user_id,
                research_id=research_id,
                title=topic,
                content_markdown=str(content),
                critic_score=final_state.get("critic_score"),
                citations_verified=final_state.get("citations_verified"),
                citations_total=final_state.get("citations_total"),
            )
        except Exception:  # report metadata must never break a completed run
            logger.exception("research.report_register_failed")

    # ── Error shaping ─────────────────────────────────────────────────────

    @staticmethod
    def _error_payload(exc: BaseException, research_id: str) -> Dict[str, Any]:
        """Client-safe error payload; tracebacks stay in the server log."""
        if isinstance(exc, LLMProviderError):
            return {
                "message": brief_error(exc, limit=500),
                "error_code": "llm_provider_unavailable",
                "research_id": research_id,
            }
        if isinstance(exc, ConfigurationError):
            return {
                "message": "The research engine is not configured correctly.",
                "error_code": "configuration_error",
                "research_id": research_id,
            }
        if isinstance(exc, OrchestrationError):
            return {
                "message": "The research pipeline could not be initialised.",
                "error_code": "orchestration_failed",
                "research_id": research_id,
            }
        if isinstance(exc, AppError) and exc.code in _RESEARCH_ERROR_MESSAGES:
            return {
                "message": _RESEARCH_ERROR_MESSAGES[exc.code],
                "error_code": exc.code,
                "research_id": research_id,
            }
        logger.debug("research.error_traceback %s", traceback.format_exc())
        return {
            "message": "The research pipeline failed to complete.",
            "error_code": "research_execution_failed",
            "research_id": research_id,
        }
