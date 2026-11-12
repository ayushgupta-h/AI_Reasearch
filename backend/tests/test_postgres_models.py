"""
tests/test_postgres_models.py
=============================
Schema-level tests for the PostgreSQL ORM models. These run without a live
database or psycopg; they validate the mapping metadata.
"""

import uuid

import pytest
from sqlalchemy import Uuid
from sqlalchemy.orm import configure_mappers

from app.database.models import Base
from app.database.models.domain import AgentExecution, Project, ResearchPlan, ResearchSession, ResearchTask, User
from app.database.models.report import ReportVersion, ResearchReport, ReviewComment, ReviewRequest
from app.database.models.research import ClaimEvidence, ResearchClaim, ResearchContradiction, ResearchEvidence, ResearchSource
from app.database.models.asset import AuditLog, ModelUsage, UploadedDocument


EXPECTED_TABLES = {
    "users",
    "projects",
    "research_sessions",
    "research_plans",
    "research_tasks",
    "agent_executions",
    "research_sources",
    "research_claims",
    "research_evidence",
    "claim_evidence",
    "research_contradictions",
    "research_reports",
    "report_versions",
    "review_requests",
    "review_comments",
    "uploaded_documents",
    "audit_logs",
    "model_usage",
}

ANY_CORE_MODEL = [
    User,
    Project,
    ResearchSession,
    ResearchPlan,
    ResearchTask,
    AgentExecution,
    ResearchSource,
    ResearchClaim,
    ResearchEvidence,
    ResearchContradiction,
    ResearchReport,
    ReportVersion,
    ReviewRequest,
    ReviewComment,
    UploadedDocument,
    AuditLog,
    ModelUsage,
]


def test_all_expected_tables_registered():
    assert EXPECTED_TABLES.issubset(set(Base.metadata.tables))


def test_every_model_has_table_name():
    for model in ANY_CORE_MODEL + [ClaimEvidence]:
        assert model.__tablename__ in Base.metadata.tables, model


def test_uuid_primary_keys():
    for table_name in EXPECTED_TABLES:
        table = Base.metadata.tables[table_name]
        for pk_col in table.primary_key.columns:
            assert isinstance(pk_col.type, Uuid), f"{table_name}.{pk_col.name}"


def test_users_never_store_credentials():
    cols = Base.metadata.tables["users"].columns
    for forbidden in ("password", "password_hash", "credential", "token"):
        assert forbidden not in cols


@pytest.mark.parametrize(
    "table,expected",
    [
        ("users", {"supabase_id", "is_active", "created_at", "updated_at"}),
        ("projects", {"owner_id", "deleted", "deleted_at", "created_at", "updated_at"}),
        ("research_sessions", {"owner_id", "project_id", "deleted", "created_at"}),
        ("claim_evidence", {"claim_id", "evidence_id", "stance", "weight"}),
        ("research_contradictions", {"claim_a_id", "claim_b_id", "severity"}),
        ("audit_logs", {"actor_id", "action", "occurred_at"}),
        ("model_usage", {"provider", "input_tokens", "cost_usd"}),
    ],
)
def test_required_columns_present(table, expected):
    cols = set(Base.metadata.tables[table].columns.keys())
    assert expected.issubset(cols)


def test_claim_evidence_unique_constraint():
    table = Base.metadata.tables["claim_evidence"]
    uniques = [c for c in table.constraints if c.name and c.name.startswith("uq_")]
    assert uniques, "claim_evidence should have at least one unique constraint"


def test_users_supabase_id_unique():
    cols = Base.metadata.tables["users"].columns
    assert cols["supabase_id"].unique is True


def test_foreign_keys_reference_parent_tables():
    # research_sessions must have an FK to projects.
    table = Base.metadata.tables["research_sessions"]
    targets = {fk.target_fullname for fk in table.foreign_keys}
    assert any(t.startswith("projects.") for t in targets)


def test_relationships_configure_cleanly():
    # configure_mappers() raises if any relationship string fails to resolve.
    configure_mappers()


def test_session_to_report_chain():
    assert "reports" in ResearchSession.__mapper__.relationships
    assert "versions" in ResearchReport.__mapper__.relationships
    assert "review_requests" in ResearchReport.__mapper__.relationships
    assert "comments" in ReviewRequest.__mapper__.relationships
