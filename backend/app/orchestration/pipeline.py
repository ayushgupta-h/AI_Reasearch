"""
app/orchestration/pipeline.py
=============================
Factory that isolates the HTTP layer from LangGraph.

``orchestrator.py`` (the graph, its nodes and state schema) is untouched except
for node instrumentation. Service code asks this module for a ready
orchestrator; it never imports LangGraph directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.errors import OrchestrationError


def build_orchestrator(
    *,
    provider: str,
    max_revisions: int,
    approval_threshold: int,
    output_dir: str | Path,
    interactive: bool = False,
    recorder: Any = None,
    event_publisher: Any = None,
) -> Any:
    """Create a ``MultiAgentOrchestrator`` with resolved configuration.

    Provider/LLM errors are intentionally propagated untouched so the research
    service can classify them and fall back to the next provider.
    """
    try:
        from orchestrator import MultiAgentOrchestrator
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise OrchestrationError(
            "The research engine dependencies are not installed."
        ) from exc

    return MultiAgentOrchestrator(
        provider=provider,
        max_revisions=max_revisions,
        approval_threshold=approval_threshold,
        output_dir=str(output_dir),
        interactive=interactive,
        recorder=recorder,
        event_publisher=event_publisher,
    )
