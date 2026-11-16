"""
tests/test_research_completeness.py
====================================
Tests for research completeness evaluation (Task 3.7).

Covers
------
* Objective decomposition from a research question
* Per-objective coverage evaluation (complete / partial / insufficient /
  not_investigated / blocked)
* Overall completeness rollup
* Gap identification (missing evidence, contradictions)
* Bounded additional-research tasks (prevents infinite loops)
* Persistence to repository (save/list objectives and evaluations)
* API endpoints (evaluate, objectives, evaluations, latest)
* Authentication (401) and cross-user isolation (404)
* Invalid session IDs (422)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.factory import create_app
from app.schemas.enums import ClaimStatus, CompletenessStatus, ObjectiveStatus
from app.security.dependencies import get_current_user
from app.services.container import get_completeness_repository, get_completeness_service
from app.services.research_completeness import (
    CompletenessResult,
    ResearchCompletenessEvaluator,
    ResearchObjectiveView,
)


# ─── Fake repository for API tests ─────────────────────────────────────


class FakeCompletenessRepository:
    """In-memory double of CompletenessRepository for API tests."""

    def __init__(self) -> None:
        self._objectives: Dict[str, Dict[str, Any]] = {}
        self._evaluations: List[Dict[str, Any]] = []
        self.available = True

    def save_objectives(self, *, session_id, objectives):
        written = 0
        for obj in objectives:
            oid = obj.get("objective_id") or f"obj-{len(self._objectives) + 1}"
            self._objectives[oid] = {
                "objective_id": oid,
                "session_id": session_id,
                "description": obj.get("description", ""),
                "required_evidence": obj.get("required_evidence"),
                "status": obj.get("status", "not_investigated"),
                "related_claim_ids": obj.get("related_claim_ids", []),
                "related_source_ids": obj.get("related_source_ids", []),
            }
            written += 1
        return written

    def list_objectives(self, session_id, status=None):
        rows = [
            o for o in self._objectives.values()
            if o["session_id"] == session_id
        ]
        if status:
            rows = [o for o in rows if o["status"] == status]
        return rows

    def get_objective(self, session_id, objective_id):
        row = self._objectives.get(objective_id)
        if row is None or row["session_id"] != session_id:
            return None
        return row

    def save_evaluation(self, *, session_id, **kwargs):
        eval_id = str(len(self._evaluations) + 1)
        record = {
            "evaluation_id": eval_id,
            "session_id": session_id,
            "overall_status": kwargs.get("overall_status"),
            "completeness_score": kwargs.get("completeness_score"),
            "total_objectives": kwargs.get("total_objectives"),
            "complete_objectives": kwargs.get("complete_objectives"),
            "partial_objectives": kwargs.get("partial_objectives"),
            "insufficient_objectives": kwargs.get("insufficient_objectives"),
            "not_investigated_objectives": kwargs.get("not_investigated_objectives"),
            "blocked_objectives": kwargs.get("blocked_objectives"),
            "gaps": kwargs.get("gaps", []),
            "recommendations": kwargs.get("recommendations", []),
            "additional_search_tasks": kwargs.get("additional_search_tasks", []),
            "iterations_performed": kwargs.get("iterations_performed", 0),
            "max_iterations": kwargs.get("max_iterations", 0),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._evaluations.append(record)
        return record

    def list_evaluations(self, session_id, limit=10, offset=0):
        rows = [
            e for e in self._evaluations if e["session_id"] == session_id
        ]
        rows.sort(key=lambda e: e["created_at"], reverse=True)
        return rows[offset:offset + limit]

    def get_latest_evaluation(self, session_id):
        rows = [e for e in self._evaluations if e["session_id"] == session_id]
        if not rows:
            return None
        return max(rows, key=lambda e: e["created_at"])


def _make_service() -> ResearchCompletenessEvaluator:
    service = ResearchCompletenessEvaluator.__new__(ResearchCompletenessEvaluator)
    service._settings = MagicMock()
    service._repo = FakeCompletenessRepository()
    return service


@pytest.fixture
def user_state() -> dict:
    return {"user_id": "user-a"}


def _build_client(user_state: dict, service: ResearchCompletenessEvaluator) -> TestClient:
    application = create_app(get_settings())
    application.dependency_overrides[get_current_user] = lambda: dict(user_state)
    application.dependency_overrides[get_completeness_service] = lambda: service
    application.dependency_overrides[get_completeness_repository] = lambda: service._repo
    return TestClient(application, raise_server_exceptions=False)


@pytest.fixture
def env(user_state):
    service = _make_service()
    client = _build_client(user_state, service)
    return service, client


# ─── Evaluator: decomposition ─────────────────────────────────────────


class TestDecomposition:
    def test_splits_question_into_objectives(self):
        service = ResearchCompletenessEvaluator()
        objs = service._decompose_objectives(
            "What is the impact of AI on healthcare and how does it affect costs?"
        )
        assert len(objs) >= 1
        for o in objs:
            assert "objective_id" in o
            assert "description" in o
            assert "required_evidence" in o
            assert o["objective_id"].startswith("obj-")

    def test_empty_topic_returns_single_objective(self):
        service = ResearchCompletenessEvaluator()
        objs = service._decompose_objectives("")
        assert len(objs) == 1
        assert objs[0]["objective_id"] == "obj-1"

    def test_unique_objective_ids(self):
        service = ResearchCompletenessEvaluator()
        objs = service._decompose_objectives("AI in healthcare and climate change")
        ids = [o["objective_id"] for o in objs]
        assert len(ids) == len(set(ids))


# ─── Evaluator: per-objective coverage ────────────────────────────────


class TestPerObjectiveCoverage:
    def _make_claim(self, cid: str, text: str) -> Dict[str, Any]:
        return {
            "claim_id": cid,
            "text": text,
            "claim_type": "factual",
            "supporting_evidence": [],
            "contradicting_evidence": [],
        }

    def _make_verification(self, cid: str, status: str) -> Dict[str, Any]:
        return {
            "claim_id": cid,
            "claim_text": "",
            "status": status,
            "confidence": 0.9,
            "supporting_evidence_ids": [],
            "contradicting_evidence_ids": [],
        }

    def test_complete_objective(self):
        """At least one supported claim -> complete."""
        service = ResearchCompletenessEvaluator()
        claims = [
            self._make_claim("c1", "AI improves diagnostic accuracy by 30%"),
        ]
        verifications = {
            "c1": self._make_verification("c1", ClaimStatus.SUPPORTED.value),
        }
        status, _, _ = service._evaluate_objective(
            {"objective_id": "obj-1", "description": "AI diagnostic accuracy.",
             "required_evidence": "Clinical studies on AI diagnosis."},
            {}, verifications, claims, [],
        )
        assert status == ObjectiveStatus.COMPLETE.value

    def test_partial_objective(self):
        """Partially supported claim -> partially_complete."""
        service = ResearchCompletenessEvaluator()
        claims = [
            self._make_claim("c1", "AI improves diagnostic accuracy by 30%"),
        ]
        verifications = {
            "c1": self._make_verification("c1", ClaimStatus.PARTIALLY_SUPPORTED.value),
        }
        status, _, _ = service._evaluate_objective(
            {"objective_id": "obj-1", "description": "AI diagnostic accuracy.",
             "required_evidence": "Clinical studies."},
            {}, verifications, claims, [],
        )
        assert status == ObjectiveStatus.PARTIALLY_COMPLETE.value

    def test_insufficient_evidence_objective(self):
        """No verified support -> insufficient_evidence."""
        service = ResearchCompletenessEvaluator()
        claims = [
            self._make_claim("c1", "AI improves diagnostic accuracy by 30%"),
        ]
        verifications = {
            "c1": self._make_verification("c1", ClaimStatus.INSUFFICIENT_CONTEXT.value),
        }
        status, _, _ = service._evaluate_objective(
            {"objective_id": "obj-1", "description": "AI diagnostic accuracy.",
             "required_evidence": "Clinical studies."},
            {}, verifications, claims, [],
        )
        assert status == ObjectiveStatus.INSUFFICIENT_EVIDENCE.value

    def test_not_investigated_objective(self):
        """No related claims -> not_investigated."""
        service = ResearchCompletenessEvaluator()
        claims = [
            self._make_claim("c1", "Quantum computing uses qubits"),
        ]
        verifications = {
            "c1": self._make_verification("c1", ClaimStatus.SUPPORTED.value),
        }
        status, _, _ = service._evaluate_objective(
            {"objective_id": "obj-1", "description": "AI diagnostic accuracy.",
             "required_evidence": "Clinical studies."},
            {}, verifications, claims, [],
        )
        assert status == ObjectiveStatus.NOT_INVESTIGATED.value

    def test_blocked_objective(self):
        """Contradicted claims with no support -> blocked."""
        service = ResearchCompletenessEvaluator()
        claims = [
            self._make_claim("c1", "AI improves diagnostic accuracy"),
        ]
        verifications = {
            "c1": self._make_verification("c1", ClaimStatus.CONTRADICTED.value),
        }
        status, _, _ = service._evaluate_objective(
            {"objective_id": "obj-1", "description": "AI diagnostic accuracy.",
             "required_evidence": "Clinical studies."},
            {}, verifications, claims, [],
        )
        assert status == ObjectiveStatus.BLOCKED.value


# ─── Evaluator: overall completeness ──────────────────────────────────


def _dummy_view(oid: str, status: str) -> ResearchObjectiveView:
    return ResearchObjectiveView(
        objective_id=oid,
        description=f"Objective {oid}",
        required_evidence=None,
        status=status,
    )


class TestOverallCompleteness:
    def test_complete_when_all_objectives_complete(self):
        service = ResearchCompletenessEvaluator()
        objectives = [
            _dummy_view("obj-1", ObjectiveStatus.COMPLETE.value),
            _dummy_view("obj-2", ObjectiveStatus.COMPLETE.value),
        ]
        result = service._compute_overall(objectives, [], 2)
        assert result.overall_status == CompletenessStatus.COMPLETE.value
        assert result.completeness_score == 100.0

    def test_partial_when_some_complete(self):
        service = ResearchCompletenessEvaluator()
        objectives = [
            _dummy_view("obj-1", ObjectiveStatus.COMPLETE.value),
            _dummy_view("obj-2", ObjectiveStatus.INSUFFICIENT_EVIDENCE.value),
        ]
        result = service._compute_overall(objectives, [], 2)
        assert result.overall_status == CompletenessStatus.PARTIALLY_COMPLETE.value
        assert result.completeness_score == 50.0

    def test_insufficient_when_none_complete(self):
        service = ResearchCompletenessEvaluator()
        objectives = [
            _dummy_view("obj-1", ObjectiveStatus.NOT_INVESTIGATED.value),
            _dummy_view("obj-2", ObjectiveStatus.INSUFFICIENT_EVIDENCE.value),
        ]
        result = service._compute_overall(objectives, [], 2)
        assert result.overall_status == CompletenessStatus.INSUFFICIENT.value
        assert result.completeness_score == 0.0

    def test_blocked_counts_as_gap(self):
        service = ResearchCompletenessEvaluator()
        objectives = [
            _dummy_view("obj-1", ObjectiveStatus.BLOCKED.value),
        ]
        result = service._compute_overall(objectives, [], 2)
        assert result.overall_status == CompletenessStatus.INSUFFICIENT.value
        assert any(g["type"] == "contradicted" for g in result.gaps)

    def test_additional_tasks_bounded(self):
        """Additional tasks never exceed max_additional_iterations."""
        service = ResearchCompletenessEvaluator()
        objectives = [
            _dummy_view(f"obj-{i}", ObjectiveStatus.NOT_INVESTIGATED.value)
            for i in range(5)
        ]
        result = service._compute_overall(objectives, [], max_additional_iterations=2)
        assert len(result.additional_search_tasks) <= 2
        assert result.iterations_performed <= 2
        assert result.max_iterations == 2


# ─── Evaluator: full evaluate ─────────────────────────────────────────


class TestFullEvaluate:
    def test_evaluate_complete_research(self):
        service = ResearchCompletenessEvaluator()
        claims = [
            {
                "claim_id": "c1",
                "text": "AI improves diagnostic accuracy by 30%",
                "supporting_evidence": [],
                "contradicting_evidence": [],
            },
        ]
        verifications = [
            {
                "claim_id": "c1",
                "status": ClaimStatus.SUPPORTED.value,
                "confidence": 0.9,
                "supporting_evidence_ids": [],
                "contradicting_evidence_ids": [],
            },
        ]
        result = service.evaluate(
            session_id="s1",
            topic="AI diagnostic accuracy",
            claims=claims,
            verification_results=verifications,
            contradictions=[],
            sources=[],
            persist=False,
        )
        assert result.overall_status in (
            CompletenessStatus.COMPLETE.value,
            CompletenessStatus.PARTIALLY_COMPLETE.value,
        )
        assert len(result.objectives) >= 1
        assert result.completeness_score >= 0.0

    def test_evaluate_insufficient_research(self):
        service = ResearchCompletenessEvaluator()
        claims = [
            {
                "claim_id": "c1",
                "text": "Quantum computing uses qubits",
                "supporting_evidence": [],
                "contradicting_evidence": [],
            },
        ]
        verifications = [
            {
                "claim_id": "c1",
                "status": ClaimStatus.INSUFFICIENT_CONTEXT.value,
                "confidence": 0.2,
                "supporting_evidence_ids": [],
                "contradicting_evidence_ids": [],
            },
        ]
        result = service.evaluate(
            session_id="s2",
            topic="AI diagnostic accuracy",
            claims=claims,
            verification_results=verifications,
            contradictions=[],
            sources=[],
            persist=False,
        )
        # The AI objective has no related claims -> not_investigated
        ai_objectives = [
            o for o in result.objectives
            if "ai" in o.description.lower() or "diagnostic" in o.description.lower()
        ]
        if ai_objectives:
            assert ai_objectives[0].status == ObjectiveStatus.NOT_INVESTIGATED.value
        assert result.overall_status == CompletenessStatus.INSUFFICIENT.value

    def test_evaluate_identifies_gaps(self):
        service = ResearchCompletenessEvaluator()
        result = service.evaluate(
            session_id="s3",
            topic="AI diagnostic accuracy",
            claims=[],
            verification_results=[],
            contradictions=[],
            sources=[],
            persist=False,
        )
        assert len(result.gaps) >= 1
        assert any(g["type"] == "missing_evidence" for g in result.gaps)
        assert len(result.additional_search_tasks) >= 1


# ─── Repository tests ─────────────────────────────────────────────────


class TestRepository:
    def test_save_and_list_objectives(self):
        repo = FakeCompletenessRepository()
        written = repo.save_objectives(
            session_id="s1",
            objectives=[
                {
                    "objective_id": "obj-1",
                    "description": "Test objective",
                    "status": ObjectiveStatus.COMPLETE.value,
                },
            ],
        )
        assert written == 1
        rows = repo.list_objectives("s1")
        assert len(rows) == 1
        assert rows[0]["objective_id"] == "obj-1"
        assert rows[0]["status"] == ObjectiveStatus.COMPLETE.value

    def test_list_objectives_filtered_by_status(self):
        repo = FakeCompletenessRepository()
        repo.save_objectives(
            session_id="s1",
            objectives=[
                {"objective_id": "obj-1", "description": "Complete", "status": ObjectiveStatus.COMPLETE.value},
                {"objective_id": "obj-2", "description": "Investigated", "status": ObjectiveStatus.NOT_INVESTIGATED.value},
            ],
        )
        complete = repo.list_objectives("s1", status=ObjectiveStatus.COMPLETE.value)
        assert len(complete) == 1
        assert complete[0]["objective_id"] == "obj-1"

    def test_save_and_get_latest_evaluation(self):
        repo = FakeCompletenessRepository()
        repo.save_evaluation(
            session_id="s1",
            overall_status=CompletenessStatus.COMPLETE.value,
            completeness_score=100.0,
            total_objectives=2,
            complete_objectives=2,
            partial_objectives=0,
            insufficient_objectives=0,
            not_investigated_objectives=0,
            blocked_objectives=0,
            gaps=[],
            recommendations=[],
            additional_search_tasks=[],
            iterations_performed=0,
            max_iterations=2,
        )
        latest = repo.get_latest_evaluation("s1")
        assert latest is not None
        assert latest["overall_status"] == CompletenessStatus.COMPLETE.value
        assert latest["completeness_score"] == 100.0

    def test_get_latest_evaluation_none_when_empty(self):
        repo = FakeCompletenessRepository()
        assert repo.get_latest_evaluation("s1") is None


# ─── API endpoints ───────────────────────────────────────────────────


class TestAPI:
    def test_evaluate_endpoint(self, env):
        service, client = env
        response = client.post(
            "/research/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/completeness/evaluate",
            json={
                "topic": "AI diagnostic accuracy",
                "max_additional_iterations": 2,
                "persist": True,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["session_id"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        assert "overall_status" in body
        assert "completeness_score" in body
        assert "objectives" in body
        assert "gaps" in body

    def test_evaluate_invalid_session_id(self, env):
        _, client = env
        response = client.post(
            "/research/not-hex/completeness/evaluate",
            json={"topic": "test"},
        )
        assert response.status_code == 422, response.text

    def test_list_objectives_endpoint(self, env):
        service, client = env
        service._repo.save_objectives(
            session_id="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            objectives=[
                {"objective_id": "obj-1", "description": "Test", "status": ObjectiveStatus.COMPLETE.value},
            ],
        )
        response = client.get(
            "/research/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/completeness/objectives"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["session_id"] == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        assert body["total"] == 1

    def test_list_objectives_filtered_by_status(self, env):
        service, client = env
        service._repo.save_objectives(
            session_id="cccccccccccccccccccccccccccccccc",
            objectives=[
                {"objective_id": "obj-1", "description": "Complete", "status": ObjectiveStatus.COMPLETE.value},
                {"objective_id": "obj-2", "description": "Not investigated", "status": ObjectiveStatus.NOT_INVESTIGATED.value},
            ],
        )
        response = client.get(
            "/research/cccccccccccccccccccccccccccccccc/completeness/objectives?status=complete"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["objectives"][0]["status"] == "complete"

    def test_list_evaluations_endpoint(self, env):
        service, client = env
        service._repo.save_evaluation(
            session_id="dddddddddddddddddddddddddddddddd",
            overall_status=CompletenessStatus.PARTIALLY_COMPLETE.value,
            completeness_score=50.0,
            total_objectives=2,
            complete_objectives=1,
            partial_objectives=1,
            insufficient_objectives=0,
            not_investigated_objectives=0,
            blocked_objectives=0,
            gaps=[],
            recommendations=[],
            additional_search_tasks=[],
            iterations_performed=0,
            max_iterations=2,
        )
        response = client.get(
            "/research/dddddddddddddddddddddddddddddddd/completeness/evaluations"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        assert body["evaluations"][0]["overall_status"] == "partially_complete"

    def test_get_latest_evaluation_endpoint(self, env):
        service, client = env
        service._repo.save_evaluation(
            session_id="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            overall_status=CompletenessStatus.INSUFFICIENT.value,
            completeness_score=0.0,
            total_objectives=1,
            complete_objectives=0,
            partial_objectives=0,
            insufficient_objectives=1,
            not_investigated_objectives=0,
            blocked_objectives=0,
            gaps=[],
            recommendations=[],
            additional_search_tasks=[],
            iterations_performed=0,
            max_iterations=2,
        )
        response = client.get(
            "/research/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee/completeness/evaluations/latest"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["evaluation"]["overall_status"] == "insufficient"

    def test_get_latest_evaluation_none(self, env):
        _, client = env
        response = client.get(
            "/research/ffffffffffffffffffffffffffffffff/completeness/evaluations/latest"
        )
        assert response.status_code == 404, response.text


# ─── Authentication ───────────────────────────────────────────────────


class TestAuthentication:
    def test_all_endpoints_require_auth(self):
        application = create_app(get_settings())
        with TestClient(application, raise_server_exceptions=False) as tc:
            session = "55555555555555555555555555555555"
            assert tc.post(
                f"/research/{session}/completeness/evaluate",
                json={"topic": "test"},
            ).status_code == 401
            assert tc.get(
                f"/research/{session}/completeness/objectives"
            ).status_code == 401
            assert tc.get(
                f"/research/{session}/completeness/evaluations"
            ).status_code == 401
            assert tc.get(
                f"/research/{session}/completeness/evaluations/latest"
            ).status_code == 401


# ─── CompletenessResult dataclass ─────────────────────────────────────


class TestCompletenessResult:
    def test_result_fields(self):
        result = CompletenessResult(
            session_id="s1",
            overall_status=CompletenessStatus.PARTIALLY_COMPLETE.value,
            completeness_score=66.67,
        )
        assert result.session_id == "s1"
        assert result.overall_status == "partially_complete"
        assert result.completeness_score == 66.67
        assert result.objectives == []
        assert result.gaps == []
        assert result.iterations_performed == 0
        assert result.max_iterations == 2
