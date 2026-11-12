"""Query intake tests: validation models, decomposition and plan structure."""

from __future__ import annotations

import copy
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from query_intake import (
    QueryIntakeSystem,
    QueryValidation,
    ResearchRequest as IntakeResearchRequest,
)

PLAN = {
    "original_query": "quantum computing",
    "refined_query": "Quantum computing applications in drug discovery",
    "research_type": "exploratory",
    "sub_queries": [
        "quantum algorithms for molecular simulation",
        "quantum computing pharma case studies",
        "hybrid classical-quantum workflows",
        "quantum advantage benchmarks",
    ],
    "focus_areas": ["Algorithms", "Industry adoption"],
    "scope": "focused",
    "time_sensitivity": "medium",
    "suggested_source_types": ["academic", "news"],
}


def _intake(validation=None, plan=None) -> QueryIntakeSystem:
    """Build an intake system with both LLM chains replaced by fakes."""
    intake = QueryIntakeSystem.__new__(QueryIntakeSystem)
    intake.validation_chain = MagicMock()
    intake.validation_chain.invoke.return_value = copy.deepcopy(validation)
    intake.decomposition_chain = MagicMock()
    intake.decomposition_chain.invoke.return_value = copy.deepcopy(plan)
    return intake


class TestQueryValidationModel:
    def test_defaults_are_empty_lists(self) -> None:
        validation = QueryValidation(is_valid=True, clarity_score=9)
        assert validation.issues == []
        assert validation.suggestions == []

    def test_invalid_payload_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QueryValidation(is_valid=True, clarity_score="crystal clear")

    def test_validate_query_returns_model(self) -> None:
        intake = _intake(
            validation={
                "is_valid": False,
                "clarity_score": 4,
                "issues": ["Too broad"],
                "suggestions": ["Narrow the topic"],
            }
        )
        result = intake.validate_query("AI")
        assert isinstance(result, QueryValidation)
        assert result.is_valid is False
        assert result.issues == ["Too broad"]
        intake.validation_chain.invoke.assert_called_once_with({"query": "AI"})


class TestQueryDecomposition:
    def test_decompose_returns_structured_plan(self) -> None:
        intake = _intake(plan=PLAN)
        request = intake.decompose_query("quantum computing")
        assert isinstance(request, IntakeResearchRequest)
        assert request.refined_query.startswith("Quantum computing")
        assert len(request.sub_queries) == 4
        assert request.research_type == "exploratory"
        assert request.scope == "focused"
        assert IntakeResearchRequest(**request.model_dump()) == request

    def test_missing_sub_queries_is_rejected(self) -> None:
        plan = dict(PLAN)
        plan.pop("sub_queries")
        with pytest.raises(ValidationError):
            _intake(plan=plan).decompose_query("quantum computing")

    def test_plan_is_serialisable_for_state_storage(self) -> None:
        payload = _intake(plan=PLAN).decompose_query("quantum computing").model_dump()
        assert set(payload) >= {
            "refined_query",
            "sub_queries",
            "research_type",
            "focus_areas",
            "suggested_source_types",
        }

    def test_empty_query_short_circuits_before_any_llm_call(self, monkeypatch) -> None:
        monkeypatch.setattr("query_intake.console", MagicMock())
        intake = _intake(plan=PLAN)
        assert intake.process("   ") is None
        intake.validation_chain.invoke.assert_not_called()
        intake.decomposition_chain.invoke.assert_not_called()
