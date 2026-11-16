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
from typing import Any, Optional

from app.checkpointing import PostgresCheckpointer
from app.config import Settings, get_settings
from app.database.session import get_engine
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
    credibility_service: Any = None,
    contradiction_service: Any = None,
    checkpointer: Optional[PostgresCheckpointer] = None,
    max_node_retries: int = 3,
    node_timeout_seconds: int = 300,
    token_budget: int = 0,
    execution_budget_seconds: int = 0,
    interrupt_before: Optional[list[str]] = None,
    require_plan_approval: bool = False,
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

    # Auto-detect checkpointing in the factory so the executor's build contract
    # remains backward compatible for simple/local executors and tests.
    if checkpointer is None:
        checkpointer = build_checkpointer(get_settings())

    return MultiAgentOrchestrator(
        provider=provider,
        max_revisions=max_revisions,
        approval_threshold=approval_threshold,
        output_dir=str(output_dir),
        interactive=interactive,
        recorder=recorder,
        event_publisher=event_publisher,
        credibility_service=credibility_service,
        contradiction_service=contradiction_service,
        checkpointer=checkpointer,
        max_node_retries=max_node_retries,
        node_timeout_seconds=node_timeout_seconds,
        token_budget=token_budget,
        execution_budget_seconds=execution_budget_seconds,
        interrupt_before=interrupt_before,
        require_plan_approval=require_plan_approval,
    )


def build_checkpointer(settings: Optional[Settings] = None) -> Optional[PostgresCheckpointer]:
    """Build a PostgreSQL checkpointer when DATABASE_URL is configured.

    Returns ``None`` when no database URL is available, so the orchestrator
    falls back to in-memory execution without checkpointing.
    """
    cfg = settings or get_settings()
    if not cfg.database_url:
        return None
    try:
        engine = get_engine(cfg)
        return PostgresCheckpointer(engine=engine)
    except Exception:  # never fail startup because checkpointing is optional
        return None


__all__ = ["build_orchestrator", "build_checkpointer"]
