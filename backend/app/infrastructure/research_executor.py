"""
app/infrastructure/research_executor.py
=======================================
Adapter that runs the research pipeline in the current process.

The service layer depends on the ``ResearchExecutor`` port instead of importing
LangGraph directly. A future queue- or worker-backed executor can implement the
same contract; this one simply builds the orchestrator and calls ``run``.

An optional ``recorder_factory`` lets the composition root attach execution
history without making this adapter (or the pipeline) depend on repositories.

Checkpointing: when ``DATABASE_URL`` is configured, a
:class:`app.checkpointing.PostgresCheckpointer` is built and the orchestrator's
graph is compiled with it. Every run passes ``thread_id`` (= session id) and
``owner_id`` (= user id) in the LangGraph config, so checkpoints are persisted
and tenant-isolated.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Mapping, Optional

from app.checkpointing import PostgresCheckpointer
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
        checkpointer: Optional[PostgresCheckpointer] = None,
        credibility_service: Optional[Any] = None,
        credibility_repository: Optional[Any] = None,
        contradiction_service: Optional[Any] = None,
        contradiction_repository: Optional[Any] = None,
        claim_repository: Optional[Any] = None,
        review_repository: Optional[Any] = None,
    ) -> None:
        self._recorder_factory = recorder_factory
        self._event_publisher = event_publisher
        self._checkpointer = checkpointer
        self._credibility_service = credibility_service
        self._credibility_repository = credibility_repository
        self._contradiction_service = contradiction_service
        self._contradiction_repository = contradiction_repository
        self._claim_repository = claim_repository
        self._review_repository = review_repository

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
        if getattr(request, "require_plan_approval", False):
            build_kwargs["require_plan_approval"] = True
        if self._checkpointer is not None:
            build_kwargs["checkpointer"] = self._checkpointer
        # Preserve the historical build signature for callers that do not use
        # distributed events (and for existing tests that assert the kwargs).
        if self._event_publisher is not None:
            build_kwargs["event_publisher"] = self._event_publisher
        if self._credibility_service is not None:
            build_kwargs["credibility_service"] = self._credibility_service
        if self._contradiction_service is not None:
            build_kwargs["contradiction_service"] = self._contradiction_service
        orchestrator = build_orchestrator(**build_kwargs)

        # LangGraph runtime config: thread_id = session id, owner_id = user id.
        # These scope checkpoints and enforce tenant isolation.
        session_id = request.research_id or str(__import__("uuid").uuid4())
        owner_id = user_id or "anonymous"
        runtime_config = {
            "configurable": {
                "thread_id": session_id,
                "owner_id": owner_id,
            }
        }

        run_kwargs: dict[str, Any] = {"topic": request.topic}
        try:
            run_parameters = inspect.signature(orchestrator.run).parameters
        except (TypeError, ValueError):
            run_parameters = {}
        if "session_id" in run_parameters:
            run_kwargs.update(
                session_id=session_id,
                user_id=owner_id,
                project_id=getattr(request, "project_id", None),
                config=runtime_config,
            )
        state: Any = orchestrator.run(**run_kwargs)
        if not isinstance(state, Mapping):
            raise OrchestrationError("The research pipeline returned an invalid state.")

        # Persist the proposed plan as soon as the run pauses so the review API
        # can display it even if the process or worker is restarted.
        if (
            self._review_repository is not None
            and state.get("status") in ("awaiting_plan_approval", "pending_review")
            and state.get("research_plan")
        ):
            try:
                self._review_repository.upsert_plan(
                    session_id=session_id,
                    owner_id=owner_id,
                    plan=state.get("research_plan"),
                )
            except Exception:
                logger.exception("executor.plan_persist_failed")

        # Credibility persistence is best effort. A database outage must never
        # turn a completed research run into a failure or hide its report.
        if self._credibility_repository is not None:
            evaluations = state.get("source_credibility", [])
            if evaluations:
                try:
                    self._credibility_repository.save_evaluations(
                        research_id=request.research_id,
                        evaluations=evaluations,
                    )
                except Exception:
                    logger.exception("executor.credibility_persist_failed")

        # Claim/evidence materialization must happen before contradiction
        # persistence, because the contradiction adapter intentionally links
        # only claim and evidence rows that already exist.
        claims = state.get("claims", [])
        results = state.get("claim_verification_results", [])
        if self._claim_repository is not None:
            if claims:
                claim_mappings = [
                    dict(item) if isinstance(item, Mapping) else dict(item.model_dump(mode="json"))
                    for item in claims
                ]
                try:
                    self._claim_repository.save_claims(
                        session_id=state.get("session_id"),
                        claims=claim_mappings,
                    )
                except Exception:
                    logger.exception("executor.claims_persist_failed")

                evidence: list[dict[str, Any]] = []
                links: list[dict[str, Any]] = []
                for claim in claim_mappings:
                    for field in ("supporting_evidence", "contradicting_evidence"):
                        for item in claim.get(field, []) or []:
                            if not isinstance(item, Mapping):
                                continue
                            evidence_item = dict(item)
                            evidence.append(evidence_item)
                            links.append(
                                {
                                    "claim_id": claim.get("claim_id"),
                                    "evidence_id": evidence_item.get("evidence_id"),
                                    "stance": evidence_item.get("stance"),
                                    "weight": evidence_item.get("strength", 1.0),
                                }
                            )

                if evidence:
                    try:
                        self._claim_repository.save_evidence(
                            session_id=state.get("session_id"),
                            evidence=evidence,
                        )
                    except Exception:
                        logger.exception("executor.evidence_persist_failed")
                if links:
                    try:
                        self._claim_repository.save_claim_evidence_links(
                            session_id=state.get("session_id"),
                            links=links,
                        )
                    except Exception:
                        logger.exception("executor.claim_evidence_links_persist_failed")

            if results:
                try:
                    self._claim_repository.save_verification_results(
                        session_id=state.get("session_id"),
                        results=results,
                    )
                except Exception:
                    logger.exception("executor.verification_persist_failed")

        if self._contradiction_repository is not None:
            analyses = state.get("contradictions", [])
            if analyses:
                try:
                    self._contradiction_repository.save_analyses(
                        session_id=state.get("session_id"),
                        analyses=analyses,
                    )
                except Exception:
                    logger.exception("executor.contradiction_persist_failed")
        return state

    def resume_plan(
        self,
        *,
        session_id: str,
        user_id: str,
        decision: str,
        comment: Optional[str] = None,
        provider: Optional[str] = None,
        max_revisions: int = 2,
        approval_threshold: int = 8,
        output_dir: str = "reports",
    ) -> Mapping[str, Any]:
        """Resume a checkpoint paused at the plan approval gate.

        The same checkpointer/config (thread_id = session id, owner_id = user
        id) is used, so the graph resumes from the persisted intake state
        instead of starting a second research run.
        """
        from app.orchestration.pipeline import build_orchestrator

        if self._checkpointer is None:
            raise OrchestrationError(
                "Persistent checkpointing is required to resume plan review."
            )
        orchestrator = build_orchestrator(
            provider=provider or "groq",
            max_revisions=max_revisions,
            approval_threshold=approval_threshold,
            output_dir=output_dir,
            checkpointer=self._checkpointer,
            require_plan_approval=True,
        )
        if not hasattr(orchestrator, "resume_plan"):
            raise OrchestrationError("The orchestrator does not support plan resume.")
        state = orchestrator.resume_plan(
            session_id=session_id,
            user_id=user_id,
            decision=decision,
            comment=comment,
        )
        if not isinstance(state, Mapping):
            raise OrchestrationError("The research pipeline returned an invalid state.")
        return state


__all__ = ["LocalResearchExecutor"]
