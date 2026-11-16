"""Human-in-the-loop plan/report review tests.

These exercise durable projection/audit behavior, tenant isolation, duplicate
decisions, idempotent resume, and the LangGraph plan-gate checkpoint flow.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, TypedDict

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.models import (
    Base,
    Project,
    ResearchPlan,
    ResearchReport,
    ResearchSession,
    ReviewAudit,
    User,
)
from app.errors import ConflictError, NotFoundError
from app.repositories.reviews import ReviewRepository
from app.services.review_service import ResearchReviewService


@pytest.fixture
def review_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        Base.metadata.tables[name]
        for name in (
            "users",
            "projects",
            "research_sessions",
            "research_plans",
            "research_reports",
            "report_versions",
            "review_requests",
            "review_comments",
            "review_audits",
        )
    ]
    Base.metadata.create_all(engine, tables=tables)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    user = User(id=uuid.uuid4(), supabase_id=str(uuid.uuid4()), email="owner@example.com")
    other = User(id=uuid.uuid4(), supabase_id=str(uuid.uuid4()), email="other@example.com")
    project = Project(id=uuid.uuid4(), owner_id=user.id, name="Project")
    session = ResearchSession(
        id=uuid.uuid4(),
        project_id=project.id,
        owner_id=user.id,
        topic="Review topic",
        provider="groq",
    )
    report = ResearchReport(
        id=uuid.uuid4(),
        session_id=session.id,
        title="Draft report",
        status="draft",
    )
    db.add_all([user, other, project, session, report])
    db.commit()
    ids = {
        "owner": str(user.id),
        "other": str(other.id),
        "session": str(session.id),
        "report": str(report.id),
    }
    db.close()

    repository = ReviewRepository(SimpleNamespace(database_url="postgresql://test"))
    repository._factory = lambda: factory  # type: ignore[method-assign]
    return repository, ids, factory, engine


def _plan() -> Dict[str, Any]:
    return {
        "title": "Proposed plan",
        "objective": "Test the review gate",
        "query": "Review topic",
        "token_budget": 1000,
        "agent_count": 2,
    }


def test_plan_review_transitions_and_audit_history(review_db) -> None:
    repo, ids, factory, _ = review_db
    plan = repo.upsert_plan(
        session_id=ids["session"], owner_id=ids["owner"], plan=_plan()
    )
    assert plan is not None
    assert plan["status"] == "pending_review"

    accepted, audit = repo.decide_plan(
        session_id=ids["session"],
        owner_id=ids["owner"],
        decision="approved",
        reviewer_id=ids["owner"],
        comment="Looks good",
    )
    assert accepted["status"] == "approved"
    assert audit is not None
    assert audit["action"] == "plan_approved"
    assert audit["previous_status"] == "pending_review"
    assert audit["new_status"] == "approved"

    # Repeating the same decision is safe: no second audit or state mutation.
    duplicate, duplicate_audit = repo.decide_plan(
        session_id=ids["session"],
        owner_id=ids["owner"],
        decision="approved",
        reviewer_id=ids["other"],
        comment="again",
    )
    assert duplicate["status"] == "approved"
    assert duplicate_audit is None

    history = repo.history(ids["session"], ids["owner"])
    assert [entry["action"] for entry in history] == [
        "plan_submitted",
        "plan_approved",
    ]
    assert all(entry["session_id"] for entry in history)

    db = factory()
    try:
        assert db.query(ReviewAudit).count() == 2
        stored = db.query(ResearchPlan).one()
        assert stored.status == "approved"
        assert stored.reviewed_by == ids["owner"]
        assert stored.reviewed_at is not None
    finally:
        db.close()


def test_service_returns_typed_plan_and_history(review_db) -> None:
    repo, ids, _, _ = review_db
    repo.upsert_plan(session_id=ids["session"], owner_id=ids["owner"], plan=_plan())
    service = ResearchReviewService(repo)
    plan = service.get_plan(ids["session"], ids["owner"])
    assert plan.status.value == "pending_review"
    assert plan.details["query"] == "Review topic"
    history = service.history(ids["session"], ids["owner"])
    assert history.count == 1
    assert history.entries[0].reviewer_id == ids["owner"]


def test_plan_review_is_owner_scoped(review_db) -> None:
    repo, ids, _, _ = review_db
    repo.upsert_plan(session_id=ids["session"], owner_id=ids["owner"], plan=_plan())

    assert repo.get_plan(ids["session"], ids["other"]) is None
    with pytest.raises(NotFoundError):
        repo.decide_plan(
            session_id=ids["session"],
            owner_id=ids["other"],
            decision="approved",
            reviewer_id=ids["other"],
        )
    duplicate, _ = repo.decide_plan(
        session_id=ids["session"],
        owner_id=ids["owner"],
        decision="approved",
        reviewer_id=ids["owner"],
    )
    assert duplicate["status"] == "approved"
    with pytest.raises(ConflictError):
        # A different final decision cannot overwrite the accepted plan.
        repo.decide_plan(
            session_id=ids["session"],
            owner_id=ids["owner"],
            decision="rejected",
            reviewer_id=ids["owner"],
            comment="No",
        )


def test_report_states_comments_and_revision(review_db) -> None:
    repo, ids, factory, _ = review_db
    pending, submit_audit = repo.submit_report_for_review(
        report_id=ids["report"], owner_id=ids["owner"], reviewer_id=ids["owner"]
    )
    assert pending["status"] == "pending_review"
    assert submit_audit["previous_status"] == "draft"

    report, audit = repo.decide_report(
        report_id=ids["report"],
        owner_id=ids["owner"],
        decision="changes_requested",
        reviewer_id=ids["owner"],
        comment="Please support claim 2",
    )
    assert report["status"] == "changes_requested"
    assert audit["action"] == "report_changes_requested"

    comment = repo.add_report_comment(
        report_id=ids["report"],
        owner_id=ids["owner"],
        reviewer_id=ids["owner"],
        comment="Claim 2 has no citation",
        kind="unsupported_claim",
        claim_ids=[str(uuid.uuid4())],
    )
    assert comment["kind"] == "unsupported_claim"
    assert len(comment["claim_ids"]) == 1

    revised, revised_audit = repo.mark_report_revised(
        report_id=ids["report"],
        owner_id=ids["owner"],
        reviewer_id=ids["owner"],
        comment="New version submitted",
    )
    assert revised["status"] == "revised"
    assert revised_audit["previous_status"] == "changes_requested"

    history = repo.history(ids["session"], ids["owner"])
    assert [entry["action"] for entry in history] == [
        "report_submitted",
        "report_changes_requested",
        "comment_added",
        "report_revised",
    ]
    db = factory()
    try:
        assert db.query(ResearchReport).one().status == "revised"
    finally:
        db.close()


def test_report_review_is_owner_scoped(review_db) -> None:
    repo, ids, _, _ = review_db
    assert repo.get_report(ids["report"], ids["other"]) is None
    with pytest.raises(NotFoundError):
        repo.decide_report(
            report_id=ids["report"],
            owner_id=ids["other"],
            decision="approved",
            reviewer_id=ids["other"],
        )


class _FakeExecutor:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    def resume_plan(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("checkpoint missing")
        return {"status": "plan_approved"}


class _FakeReviewRepository:
    def __init__(self, executor: _FakeExecutor) -> None:
        self.executor = executor
        self.calls = 0

    def decide_plan(self, **kwargs: Any):
        self.calls += 1
        row = {
            "plan_id": uuid.uuid4(),
            "session_id": uuid.uuid4(),
            "title": "Plan",
            "objective": None,
            "query": "Topic",
            "token_budget": 0,
            "agent_count": 1,
            "status": kwargs["decision"],
            "review_note": kwargs.get("comment"),
            "reviewed_by": kwargs["reviewer_id"],
            "reviewed_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        return row, None

    def get_plan(self, *args: Any):
        return None

    def history(self, *args: Any):
        return []


def test_service_does_not_resume_on_duplicate_decision() -> None:
    executor = _FakeExecutor()
    repo = _FakeReviewRepository(executor)
    service = ResearchReviewService(repo, executor)
    result = service.decide_plan(
        session_id=str(uuid.uuid4()),
        owner_id=str(uuid.uuid4()),
        decision=__import__("app.schemas.enums", fromlist=["ApprovalDecision"]).ApprovalDecision.APPROVED,
    )
    assert result.workflow_resumed is False
    assert executor.calls == []
    assert repo.calls == 1


def test_plan_route_requires_auth_and_uses_jwt_owner() -> None:
    from fastapi.testclient import TestClient

    from app.factory import create_app
    from app.schemas.review import ResearchPlanReviewRecord
    from app.security.dependencies import get_current_user
    from app.services.container import get_review_service

    app = create_app()
    session_id = uuid.uuid4().hex
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get(f"/research/{session_id}/plan").status_code == 401

    class FakeReviewService:
        def __init__(self) -> None:
            self.owners: list[str] = []

        def get_plan(self, research_id: str, owner: str):
            self.owners.append(owner)
            now = datetime.now(timezone.utc)
            return ResearchPlanReviewRecord(
                plan_id=uuid.uuid4(),
                session_id=uuid.UUID(hex=research_id),
                title="Plan",
                query="Topic",
                status="pending_review",
                created_at=now,
                updated_at=now,
            )

    fake = FakeReviewService()
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "owner-from-jwt"}
    app.dependency_overrides[get_review_service] = lambda: fake
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"/research/{session_id}/plan")
    assert response.status_code == 200
    assert fake.owners == ["owner-from-jwt"]


class _GateState(TypedDict):
    plan_status: str
    status: str
    research_plan: dict[str, Any]
    logs: list[str]


def test_langgraph_plan_checkpoint_resumes_only_after_approval() -> None:
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    from orchestrator import MultiAgentOrchestrator

    orchestrator = MultiAgentOrchestrator()
    executed: list[str] = []

    builder = StateGraph(_GateState)
    builder.add_node("intake", lambda state: {"status": "intake_completed"})
    builder.add_node("gate", orchestrator.plan_approval_gate_node)
    builder.add_node("search", lambda state: (executed.append("search") or {"status": "completed"}))
    builder.add_edge(START, "intake")
    builder.add_edge("intake", "gate")
    builder.add_conditional_edges(
        "gate",
        lambda state: "search" if state.get("plan_status") == "approved" else "END",
        {"search": "search", "END": END},
    )
    graph = builder.compile(
        checkpointer=MemorySaver(), interrupt_before=["gate"]
    )
    orchestrator.graph = graph
    orchestrator.checkpointer = MemorySaver()

    config = {"configurable": {"thread_id": "session-1", "owner_id": "owner-1"}}
    paused = graph.invoke(
        {"plan_status": "pending_review", "status": "started", "research_plan": {}, "logs": []},
        config=config,
    )
    assert executed == []
    assert "gate" in tuple(graph.get_state(config).next)

    orchestrator.checkpointer = graph.checkpointer
    resumed = orchestrator.resume_plan(
        session_id="session-1",
        user_id="owner-1",
        decision="approved",
        comment="Approved",
    )
    assert executed == ["search"]
    assert resumed["status"] == "completed"


def test_langgraph_plan_rejection_does_not_execute_research() -> None:
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    from orchestrator import MultiAgentOrchestrator

    orchestrator = MultiAgentOrchestrator()
    executed: list[str] = []
    builder = StateGraph(_GateState)
    builder.add_node("intake", lambda state: {"status": "intake_completed"})
    builder.add_node("gate", orchestrator.plan_approval_gate_node)
    builder.add_node("search", lambda state: (executed.append("search") or {"status": "completed"}))
    builder.add_edge(START, "intake")
    builder.add_edge("intake", "gate")
    builder.add_conditional_edges(
        "gate",
        lambda state: "search" if state.get("plan_status") == "approved" else "END",
        {"search": "search", "END": END},
    )
    graph = builder.compile(checkpointer=MemorySaver(), interrupt_before=["gate"])
    orchestrator.graph = graph
    orchestrator.checkpointer = graph.checkpointer
    config = {"configurable": {"thread_id": "session-2", "owner_id": "owner-1"}}
    graph.invoke(
        {"plan_status": "pending_review", "status": "started", "research_plan": {}, "logs": []},
        config=config,
    )
    result = orchestrator.resume_plan(
        session_id="session-2",
        user_id="owner-1",
        decision="rejected",
        comment="Unsafe plan",
    )
    assert executed == []
    assert result["status"] == "rejected"
