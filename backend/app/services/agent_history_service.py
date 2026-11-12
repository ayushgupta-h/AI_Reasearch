"""
app/services/agent_history_service.py
=====================================
Read API for execution history plus the recorder that produces it.

The recorder is called by :func:`app.orchestration.instrumentation.instrument_node`
for each real pipeline stage, so every row corresponds to an execution that
actually happened. Stored error summaries are redacted and truncated; prompts,
model output and credentials are never persisted or returned.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.errors import AuthenticationError, NotFoundError
from app.observability import get_logger, redact
from app.repositories.agent_history import AgentHistoryRepository

logger = get_logger("app.services.agent_history")

_MAX_ERROR_CHARS = 280


def _summarise_error(error: BaseException | str) -> str:
    # Redact strings as well: the API must never expose a caller-supplied secret.
    if isinstance(error, str):
        text = redact(error)
    else:
        text = f"{type(error).__name__}: {redact(error)}"
    compact = " ".join(str(text).split())
    if len(compact) > _MAX_ERROR_CHARS:
        return compact[:_MAX_ERROR_CHARS] + "…"
    return compact


class AgentExecutionRecorder:
    """Records one agent stage; safe to call from the pipeline worker thread."""

    def __init__(
        self, repository: AgentHistoryRepository, research_id: str, owner_id: str
    ) -> None:
        self._repository = repository
        self._research_id = research_id
        self._owner_id = owner_id

    def started(self, agent_name: str) -> Optional[str]:
        """Open an execution row; returns ``None`` if recording failed."""
        try:
            retries = self._repository.count_agent_executions(
                self._research_id, self._owner_id, agent_name
            )
            row = self._repository.create_execution(
                research_id=self._research_id,
                owner_id=self._owner_id,
                agent_name=agent_name,
                status="running",
                retry_count=retries,
            )
            self._repository.record_event(
                research_id=self._research_id,
                owner_id=self._owner_id,
                event_type="agent_started",
                agent_name=agent_name,
                execution_id=row["execution_id"],
                message=f"{agent_name} execution started.",
            )
            return str(row["execution_id"])
        except Exception:  # telemetry must never break a research run
            logger.exception("agent.execution_start_record_failed")
            return None

    def finished(
        self,
        execution_id: str,
        *,
        agent_name: str,
        status: str,
        duration_ms: Optional[float],
        output_reference: Optional[str] = None,
        error_summary: Optional[str] = None,
    ) -> None:
        if not execution_id:
            return
        try:
            self._repository.finish_execution(
                execution_id,
                self._owner_id,
                status=status,
                duration_ms=duration_ms,
                error_summary=_summarise_error(error_summary) if error_summary else None,
                output_reference=output_reference,
            )
            event_type = "agent_completed" if status == "completed" else "agent_failed"
            message = (
                f"{agent_name} execution completed."
                if status == "completed"
                else f"{agent_name} execution failed."
            )
            self._repository.record_event(
                research_id=self._research_id,
                owner_id=self._owner_id,
                event_type=event_type,
                agent_name=agent_name,
                execution_id=execution_id,
                message=message,
            )
        except Exception:  # telemetry must never break a research run
            logger.exception("agent.execution_finish_record_failed")


class AgentHistoryService:
    def __init__(self, repository: AgentHistoryRepository) -> None:
        self._repository = repository

    # ── Recording entry points ────────────────────────────────────────────

    def register_research(
        self, research_id: str, owner_id: Optional[str], topic: Optional[str]
    ) -> None:
        """Record creation of a research session (pending execution)."""
        if not owner_id:
            return
        self._repository.ensure_run(
            research_id, owner_id, topic=topic, status="pending"
        )
        self._repository.record_event(
            research_id=research_id,
            owner_id=owner_id,
            event_type="research_created",
            message="Research created.",
        )

    def recorder_for(
        self, research_id: str, owner_id: Optional[str], topic: Optional[str] = None
    ) -> Optional[AgentExecutionRecorder]:
        """Create a recorder for a run that is about to execute."""
        if not owner_id:
            return None
        self._repository.ensure_run(
            research_id, owner_id, topic=topic, status="running"
        )
        self._repository.update_run_status(research_id, owner_id, "running")
        self._repository.record_event(
            research_id=research_id,
            owner_id=owner_id,
            event_type="research_started",
            message="Research execution started.",
        )
        return AgentExecutionRecorder(self._repository, research_id, owner_id)

    def complete_run(
        self,
        research_id: str,
        owner_id: Optional[str],
        *,
        status: str = "completed",
    ) -> None:
        if not owner_id:
            return
        self._repository.update_run_status(research_id, owner_id, status)
        self._repository.record_event(
            research_id=research_id,
            owner_id=owner_id,
            event_type=(
                "research_completed" if status == "completed" else "research_cancelled"
            ),
            message=(
                "Research execution completed."
                if status == "completed"
                else "Research execution cancelled."
            ),
        )

    def fail_run(
        self, research_id: str, owner_id: Optional[str], error: BaseException | str
    ) -> None:
        if not owner_id:
            return
        summary = _summarise_error(error)
        self._repository.update_run_status(research_id, owner_id, "failed")
        self._repository.record_event(
            research_id=research_id,
            owner_id=owner_id,
            event_type="research_failed",
            message=f"Research execution failed: {summary}",
        )

    def cancel_run(self, research_id: str, owner_id: Optional[str]) -> None:
        if not owner_id:
            return
        self._repository.update_run_status(research_id, owner_id, "cancelled")
        self._repository.record_event(
            research_id=research_id,
            owner_id=owner_id,
            event_type="research_cancelled",
            message="Research execution cancelled.",
        )

    # ── Read API ──────────────────────────────────────────────────────────

    def list_executions(
        self,
        research_id: str,
        owner_id: str,
        *,
        status: Optional[Any] = None,
        agent_name: Optional[str] = None,
        started_after: Optional[datetime] = None,
        started_before: Optional[datetime] = None,
        sort: str = "started_at",
        order: str = "asc",
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        self._require_run(research_id, owner_id)
        return self._repository.list_executions(
            research_id=research_id,
            owner_id=owner_id,
            status=status,
            agent_name=agent_name,
            started_after=started_after,
            started_before=started_before,
            sort=sort,
            order=order,
            limit=limit,
            offset=offset,
        )

    def get_execution(
        self, research_id: str, execution_id: str, owner_id: str
    ) -> Dict[str, Any]:
        self._require_run(research_id, owner_id)
        row = self._repository.get_execution(execution_id, research_id, owner_id)
        if row is None:
            raise NotFoundError("Agent execution not found.")
        return row

    def timeline(
        self,
        research_id: str,
        owner_id: str,
        *,
        event_type: Optional[Any] = None,
        agent_name: Optional[str] = None,
        occurred_after: Optional[datetime] = None,
        occurred_before: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        self._require_run(research_id, owner_id)
        return self._repository.list_events(
            research_id=research_id,
            owner_id=owner_id,
            event_type=event_type,
            agent_name=agent_name,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            limit=limit,
            offset=offset,
        )

    def progress(self, research_id: str, owner_id: str) -> Dict[str, Any]:
        run = self._require_run(research_id, owner_id)
        counts = self._repository.execution_counts(research_id, owner_id)
        total = sum(counts.values())
        completed = counts.get("completed", 0)
        latest = self._repository.latest_event(research_id, owner_id)

        return {
            "research_id": research_id,
            "status": run["status"],
            "total_executions": total,
            "completed_executions": completed,
            "failed_executions": counts.get("failed", 0),
            "running_executions": counts.get("running", 0),
            "pending_executions": counts.get("pending", 0),
            "percent_complete": round(100.0 * completed / total, 1) if total else 0.0,
            "current_agent": self._repository.current_agent(research_id, owner_id),
            "started_at": run["created_at"],
            "updated_at": run["updated_at"],
            "latest_event": latest["message"] if latest else None,
        }

    # ── Helpers ───────────────────────────────────────────────────────────

    def _require_run(self, research_id: str, owner_id: str) -> Mapping[str, Any]:
        owner = (owner_id or "").strip()
        if not owner:
            raise AuthenticationError("Authentication required.")
        run = self._repository.get_run(research_id, owner)
        if run is None:
            raise NotFoundError("Research session not found.")
        return run


__all__ = ["AgentExecutionRecorder", "AgentHistoryService"]
