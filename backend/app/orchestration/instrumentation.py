"""
app/orchestration/instrumentation.py
===================================
Agent-stage instrumentation for LangGraph nodes.

Nodes are wrapped at graph-build time instead of being edited individually, so
the pipeline's behaviour is untouched while every stage gets:

* ``agent.stage.started`` / ``agent.stage.finished`` / ``agent.stage.failed``
  log records carrying ``agent``, ``stage``, ``research_id`` and ``duration_ms``;
* exception logging with the original exception re-raised unchanged;
* optional execution-history recording through a recorder object supplied by
  the service layer (the pipeline does not import repositories).

The wrapper is transparent: it forwards the node's ``dict`` update verbatim.
Recorded errors are redacted and truncated; prompts and model output are never
stored, only a coarse output reference.
"""

from __future__ import annotations

import functools
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.observability import get_logger, log_context, redact
from app.telemetry import trace_span

logger = get_logger("research.agent")

_MAX_ERROR_CHARS = 280


def _error_summary(exc: BaseException) -> str:
    text = " ".join(f"{type(exc).__name__}: {redact(exc)}".split())
    return (text[:_MAX_ERROR_CHARS] + "…") if len(text) > _MAX_ERROR_CHARS else text


def _output_reference(result: Any) -> Optional[str]:
    """Coarse, non-sensitive description of a node's output."""
    if not isinstance(result, dict):
        return None
    saved = result.get("saved_report_path")
    if isinstance(saved, str) and saved.strip():
        return Path(saved).name[:500]
    for key, reference in (
        ("report", "report_draft"),
        ("analysis", "analysis"),
        ("critic_evaluation", "critic_evaluation"),
        ("search_results_raw", "search_results"),
        ("scraped_content", "source_content"),
        ("research_request", "research_plan"),
    ):
        if key in result:
            return reference
    return "state_update"


def _safe_publish(publisher: Any, event_type: str, payload: Dict[str, Any]) -> None:
    if publisher is None:
        return
    try:
        publisher.publish(event_type, payload)
    except Exception:  # telemetry must never break the pipeline
        logger.exception("agent.event_publish_failed")


def _safe_start(recorder: Any, name: str) -> Optional[str]:
    if recorder is None:
        return None
    try:
        return recorder.started(name)
    except Exception:  # telemetry must never break the pipeline
        logger.exception("agent.execution_start_record_failed")
        return None


def _safe_finish(
    recorder: Any,
    execution_id: Optional[str],
    *,
    name: str,
    status: str,
    duration_ms: float,
    output_reference: Optional[str],
    error_summary: Optional[str],
) -> None:
    if recorder is None or not execution_id:
        return
    try:
        recorder.finished(
            execution_id,
            agent_name=name,
            status=status,
            duration_ms=duration_ms,
            output_reference=output_reference,
            error_summary=error_summary,
        )
    except Exception:  # telemetry must never break the pipeline
        logger.exception("agent.execution_finish_record_failed")


def instrument_node(
    name: str,
    fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    recorder: Any = None,
    event_publisher: Any = None,
):
    """Wrap a LangGraph node function with stage logging and optional recording."""

    @functools.wraps(fn)
    def wrapper(state: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        execution_id = _safe_start(recorder, name)
        _safe_publish(
            event_publisher,
            "agent.started",
            {"agent_name": name, "execution_id": execution_id},
        )
        with trace_span(f"agent.{name}", attributes={"agent.name": name}):
            with log_context(agent=name, stage=name):
                logger.info("agent.stage.started", extra={"agent": name, "stage": name})
                try:
                    result = fn(state)
                except Exception as exc:
                    duration_ms = round((time.perf_counter() - started) * 1000, 1)
                    logger.exception(
                        "agent.stage.failed",
                        extra={
                            "agent": name,
                            "stage": name,
                            "duration_ms": duration_ms,
                            "error_code": "agent_failed",
                        },
                    )
                    _safe_finish(
                        recorder,
                        execution_id,
                        name=name,
                        status="failed",
                        duration_ms=duration_ms,
                        output_reference=None,
                        error_summary=_error_summary(exc),
                    )
                    _safe_publish(
                        event_publisher,
                        "agent.failed",
                        {
                            "agent_name": name,
                            "execution_id": execution_id,
                            "duration_ms": duration_ms,
                            "error_code": "agent_failed",
                            "message": _error_summary(exc),
                        },
                    )
                    raise
                duration_ms = round((time.perf_counter() - started) * 1000, 1)
                logger.info(
                    "agent.stage.finished",
                    extra={
                        "agent": name,
                        "stage": name,
                        "duration_ms": duration_ms,
                        "status": (result or {}).get("status") if isinstance(result, dict) else None,
                    },
                )
                _safe_finish(
                    recorder,
                    execution_id,
                    name=name,
                    status="completed",
                    duration_ms=duration_ms,
                    output_reference=_output_reference(result),
                    error_summary=None,
                )
                _safe_publish(
                    event_publisher,
                    "agent.completed",
                    {
                        "agent_name": name,
                        "execution_id": execution_id,
                        "duration_ms": duration_ms,
                        "output_reference": _output_reference(result),
                    },
                )
                return result

    return wrapper
