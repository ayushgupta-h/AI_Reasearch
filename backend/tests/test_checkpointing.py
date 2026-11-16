"""
tests/test_checkpointing.py
============================
Tests for the PostgreSQL-backed LangGraph checkpointer and the upgraded
orchestrator reliability features:

* PostgresCheckpointer put/get/list round-trip (mocked engine).
* Tenant isolation: one user cannot read another user's checkpoints.
* Node retry logic with retry limits.
* Failure handler node.
* Human approval gate (pause/resume).
* Budget enforcement (token + execution time).
* Infinite-loop prevention via max revision limit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from langgraph.checkpoint.base import CheckpointTuple
from orchestrator import (
    CriticEvaluation,
    MultiAgentOrchestrator,
    ResearchState,
)

from app.checkpointing import PostgresCheckpointer


# ─── Helpers ──────────────────────────────────────────────────────────────


def _make_checkpoint_tuple(
    thread_id: str = "thread-1",
    checkpoint_ns: str = "",
    checkpoint_id: str = "cp-1",
    parent_id: Optional[str] = None,
    owner_id: str = "user-1",
) -> CheckpointTuple:
    return CheckpointTuple(
        config={
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        },
        checkpoint={"v": 1, "channel_values": {"x": 1}},
        metadata={"step": 1},
        parent_config=(
            {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": parent_id,
                }
            }
            if parent_id
            else None
        ),
        pending_writes=None,
    )


def _minimal_state(**overrides: Any) -> ResearchState:
    base: ResearchState = {
        "session_id": "s1",
        "user_id": "u1",
        "project_id": None,
        "topic": "Test Topic",
        "provider": "gemini",
        "interactive": False,
        "max_revisions": 1,
        "approval_threshold": 8,
        "output_dir": "reports",
        "research_request": None,
        "search_results_raw": [],
        "search_results": "",
        "top_urls": [],
        "source_summary": "",
        "scraped_content": "",
        "analysis": "",
        "report": "# Test Report",
        "critic_score": 0,
        "critic_evaluation": None,
        "critic_feedback": "",
        "revision_count": 0,
        "revision_history": [],
        "final_report": "",
        "saved_report_path": "",
        "status": "started",
        "logs": [],
        "rag_context": "",
        "errors": [],
        "node_retry_counts": {},
        "execution_metadata": {},
        "token_budget": 0,
        "execution_budget_seconds": 0,
        "human_approval_required": False,
        "human_approval_stage": None,
        "human_approval_granted": None,
        "paused_at": None,
        "resume_token": None,
    }
    base.update(overrides)
    return base


# ─── PostgresCheckpointer (mocked engine) ───────────────────────────────


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _envelope(obj: Any) -> str:
    """Serialise an object in the same envelope format as the checkpointer."""
    import base64, json
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    serde = JsonPlusSerializer()
    type_str, data_bytes = serde.dumps_typed(obj)
    return json.dumps({"t": type_str, "d": base64.b64encode(data_bytes).decode("ascii")})


class _FakeConnection:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params: tuple = ()) -> _FakeResult:
        self.executed.append((sql, params))
        # Simulate a SELECT returning a stored row for get_tuple/list
        if "SELECT" in sql.upper() and "langgraph_checkpoints" in sql:
            return _FakeResult([
                (
                    "thread-1", "", "cp-1", None, "Checkpoint",
                    _envelope({"v": 1, "channel_values": {"x": 42}}),
                    _envelope({"step": 1}), "user-1",
                    datetime.now(timezone.utc).isoformat(),
                )
            ])
        return _FakeResult([])

    def commit(self):
        self.committed = True

    def close(self):
        pass


class _FakeEngine:
    def __init__(self):
        self.connections: list[_FakeConnection] = []

    def connect(self):
        conn = _FakeConnection()
        self.connections.append(conn)
        return conn


def test_checkpointer_put_and_get_roundtrip():
    """put() stores a checkpoint; get_tuple() retrieves it (owner-scoped)."""
    engine = _FakeEngine()
    cp = PostgresCheckpointer(engine=engine)

    checkpoint = {"v": 1, "channel_values": {"x": 42}}
    metadata = {"step": 2}
    config = {
        "configurable": {
            "thread_id": "thread-1",
            "owner_id": "user-1",
        }
    }

    saved = cp.put(config, checkpoint, metadata, {})
    assert saved["configurable"]["checkpoint_id"] is not None
    # The fake engine records the INSERT on some connection
    all_sql = " ".join(sql for conn in engine.connections for sql, _ in conn.executed)
    assert "INSERT" in all_sql

    # get_tuple with matching owner returns the checkpoint
    fetched = cp.get_tuple(config)
    assert fetched is not None
    assert isinstance(fetched.checkpoint, dict)
    assert isinstance(fetched.metadata, dict)


def test_checkpointer_tenant_isolation():
    """Checkpoints are scoped by owner_id; the SQL predicates include owner_id."""
    engine = _FakeEngine()
    cp = PostgresCheckpointer(engine=engine)

    config_a = {
        "configurable": {"thread_id": "thread-1", "owner_id": "user-a"}
    }
    config_b = {
        "configurable": {"thread_id": "thread-1", "owner_id": "user-b"}
    }

    cp.put(config_a, {"v": 1}, {"step": 1}, {})
    # The SELECT queries generated by the fake all include an owner_id predicate
    # in the WHERE clause, enforcing tenant isolation at the storage boundary.
    select_sql = [
        sql for conn in engine.connections for sql, _ in conn.executed
        if "SELECT" in sql.upper() and "langgraph_checkpoints" in sql
    ]
    assert select_sql
    for sql in select_sql:
        assert "owner_id = %s" in sql, f"Query missing owner predicate: {sql}"

    # A get_tuple for a different owner still returns a row from the fake
    # (the fake doesn't filter by owner), but real PostgreSQL would return None
    # because owner_id is in the WHERE clause. Verify the predicate is present.
    fetched_b = cp.get_tuple(config_b)
    assert fetched_b is not None  # fake returns a row regardless of owner


def test_checkpointer_requires_owner_and_thread():
    """get_tuple returns None when owner_id or thread_id is missing."""
    cp = PostgresCheckpointer()
    assert cp.get_tuple({}) is None
    assert cp.get_tuple({"configurable": {"thread_id": "t"}}) is None
    assert cp.get_tuple({"configurable": {"owner_id": "u"}}) is None


def test_checkpointer_put_raises_without_owner_or_thread():
    """put() raises ValueError when owner_id/thread_id missing."""
    cp = PostgresCheckpointer()
    with pytest.raises(ValueError):
        cp.put({}, {"v": 1}, {"step": 1}, {})


def test_checkpointer_list_is_owner_scoped():
    """list() only returns checkpoints for the configured owner."""
    engine = _FakeEngine()
    cp = PostgresCheckpointer(engine=engine)

    config = {"configurable": {"thread_id": "thread-1", "owner_id": "user-1"}}
    tuples = list(cp.list(config))
    # Fake connection returns one row for SELECT queries; verify owner scope
    # is applied by checking the query contains the owner predicate.
    executed_sql = engine.connections[0].executed[-1][0]
    assert "owner_id" in executed_sql
    assert len(tuples) == 1


# ─── Retry & failure handling ───────────────────────────────────────────


class _FailingNode:
    """A node that fails N times then succeeds."""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    def __call__(self, state):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"transient failure #{self.calls}")
        return {"status": "succeeded", "attempts": self.calls}


def test_node_retries_then_succeeds():
    """A node that fails twice then succeeds is retried and returns success."""
    orc = MultiAgentOrchestrator(max_node_retries=3)
    node = _FailingNode(fail_times=2)
    wrapped = orc._wrap_node_with_retry("test_node", node)
    result = wrapped(_minimal_state())
    assert result["status"] == "succeeded"
    assert node.calls == 3


def test_node_exhausts_retries_and_records_error():
    """A node that always fails records an error and routes to failure state."""
    orc = MultiAgentOrchestrator(max_node_retries=2)
    node = _FailingNode(fail_times=10)
    wrapped = orc._wrap_node_with_retry("test_node", node)
    result = wrapped(_minimal_state())
    assert result["status"] == "failed"
    assert len(result["errors"]) == 1
    assert result["errors"][0]["node"] == "test_node"
    assert node.calls == 3  # initial + 2 retries


def test_failure_handler_marks_run_failed():
    """failure_handler_node returns status 'failed' with the last error."""
    orc = MultiAgentOrchestrator()
    state = _minimal_state(
        errors=[{"node": "writer_node", "error": "boom", "fatal": True}]
    )
    result = orc.failure_handler_node(state)
    assert result["status"] == "failed"
    assert "boom" in result["logs"][-1]


# ─── Human approval gate ────────────────────────────────────────────────


def test_human_approval_gate_pauses_when_not_granted():
    """When approval is not granted, the gate returns awaiting state."""
    orc = MultiAgentOrchestrator()
    state = _minimal_state(
        human_approval_required=True,
        human_approval_stage="writer_node",
    )
    result = orc.human_approval_gate_node(state)
    assert result["status"] == "awaiting_human_approval"
    assert result["human_approval_stage"] == "writer_node"
    assert result["resume_token"] is not None


def test_human_approval_gate_passes_through_when_granted():
    """When approval is granted, the gate clears the pause flags."""
    orc = MultiAgentOrchestrator()
    state = _minimal_state(
        human_approval_required=True,
        human_approval_stage="writer_node",
        human_approval_granted=True,
    )
    result = orc.human_approval_gate_node(state)
    assert result["human_approval_required"] is False
    assert result["human_approval_stage"] is None
    assert result["human_approval_granted"] is None


# ─── Budget enforcement ─────────────────────────────────────────────────


def test_token_budget_exhaustion_records_fatal_error():
    """When token budget is exceeded, a fatal error is recorded."""
    orc = MultiAgentOrchestrator(token_budget=100)
    state = _minimal_state(
        execution_metadata={"token_budget_used": 150},
    )
    result = orc._check_budgets(state)
    assert result is not None
    assert result["status"] == "failed"
    assert result["errors"][0]["fatal"] is True


def test_token_budget_within_limit_passes():
    """When token budget is not exceeded, _check_budgets returns None."""
    orc = MultiAgentOrchestrator(token_budget=100)
    state = _minimal_state(
        execution_metadata={"token_budget_used": 50},
    )
    result = orc._check_budgets(state)
    assert result is None


def test_unlimited_token_budget_passes():
    """token_budget=0 means unlimited; no error recorded."""
    orc = MultiAgentOrchestrator(token_budget=0)
    state = _minimal_state(
        execution_metadata={"token_budget_used": 999999},
    )
    result = orc._check_budgets(state)
    assert result is None


# ─── State seeding ──────────────────────────────────────────────────────


def test_initial_state_seeds_all_new_fields():
    """run() seeds session_id, user_id, errors, retry counts, etc."""
    orc = MultiAgentOrchestrator()
    # Patch graph.invoke to capture the initial state
    captured = {}

    def fake_invoke(state, config=None):
        captured.update(state)
        return state

    orc.graph.invoke = fake_invoke  # type: ignore[assignment]
    orc.run("Test Topic")

    assert captured["session_id"] is not None
    assert captured["user_id"] == "anonymous"
    assert captured["errors"] == []
    assert captured["node_retry_counts"] == {}
    assert captured["execution_metadata"]["started_at"] is not None
    assert captured["human_approval_required"] is False
    assert captured["resume_token"] is None


# ─── Max revision limit prevents infinite loops ─────────────────────────


def test_max_revisions_routes_to_exporter_not_refiner():
    """When max_revisions is reached, the router routes to exporter (not refiner)."""
    orc = MultiAgentOrchestrator(max_revisions=2, approval_threshold=8)
    state = _minimal_state(
        critic_score=5,
        revision_count=2,
        max_revisions=2,
        approval_threshold=8,
    )
    decision = orc.evaluate_critic_decision(state)
    assert decision == "exporter_node"


def test_critic_score_meets_threshold_routes_to_exporter():
    """When critic score >= threshold, route to exporter."""
    orc = MultiAgentOrchestrator(approval_threshold=8)
    state = _minimal_state(critic_score=9, revision_count=0, max_revisions=2)
    decision = orc.evaluate_critic_decision(state)
    assert decision == "exporter_node"


def test_critic_score_below_threshold_routes_to_refiner():
    """When critic score < threshold and revisions remain, route to refiner."""
    orc = MultiAgentOrchestrator(max_revisions=2, approval_threshold=8)
    state = _minimal_state(critic_score=6, revision_count=0, max_revisions=2)
    decision = orc.evaluate_critic_decision(state)
    assert decision == "refiner_node"
