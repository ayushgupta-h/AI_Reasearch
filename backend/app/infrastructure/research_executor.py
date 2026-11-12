"""
app/infrastructure/research_executor.py
=======================================
Adapter that runs the research pipeline in the current process.

The service layer depends on the ``ResearchExecutor`` port instead of importing
LangGraph directly. A future queue- or worker-backed executor can implement the
same contract; this one simply builds the orchestrator and calls ``run``.

An optional ``recorder_factory`` lets the composition root attach execution
history without making this adapter (or the pipeline) depend on repositories.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from app.errors import OrchestrationError
from app.infrastructure.ports import ResearchExecutionRequest
from app.observability import get_logger

logger = get_logger("app.infrastructure.executor")

RecorderFactory = Callable[[str, str, str], Any]


class LocalResearchExecutor:
    """Runs one research topic synchronously in the calling thread."""

    def __init__(
        self,
        recorder_factory: Optional[RecorderFactory] = None,
        event_publisher: Optional[Any] = None,
    ) -> None:
        self._recorder_factory = recorder_factory
        self._event_publisher = event_publisher

    def execute(
        self,
        request: ResearchExecutionRequest,
        *,
        user_id: Optional[str] = None,
    ) -> Mapping[str, Any]:
        from app.orchestration.pipeline import build_orchestrator

        recorder = None
        if self._recorder_factory is not None and request.research_id and user_id:
            try:
                recorder = self._recorder_factory(
                    request.research_id, user_id, request.topic
                )
            except Exception:  # history must never prevent the run
                logger.exception("executor.recorder_creation_failed")

        build_kwargs = {
            "provider": request.provider,
            "max_revisions": request.max_revisions,
            "approval_threshold": request.approval_threshold,
            "output_dir": request.output_dir,
            "recorder": recorder,
        }
        # Preserve the historical build signature for callers that do not use
        # distributed events (and for existing tests that assert the kwargs).
        if self._event_publisher is not None:
            build_kwargs["event_publisher"] = self._event_publisher
        orchestrator = build_orchestrator(**build_kwargs)
        state: Any = orchestrator.run(topic=request.topic)
        if not isinstance(state, Mapping):
            raise OrchestrationError("The research pipeline returned an invalid state.")
        return state


__all__ = ["LocalResearchExecutor"]
