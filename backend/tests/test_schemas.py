"""Contract tests for the Pydantic schema layer (stage 1.4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

import app.errors as errors_module
from app.errors import AppError
from app.schemas import (
    AgentExecution,
    ApiError,
    ChatRequest,
    ChatResponse,
    Citation,
    Claim,
    Contradiction,
    ErrorCode,
    Evidence,
    FileUploadMetadata,
    HealthStatus,
    PaginationMeta,
    PaginationParams,
    Project,
    ProviderStatus,
    ReportVersion,
    ResearchApproval,
    ResearchEvent,
    ResearchReport,
    ResearchRequest,
    ResearchResponse,
    ResearchSession,
    ResearchSource,
    ResearchTask,
    SessionStatus,
    SourceType,
)
from app.schemas.common import ApiResponse
from app.schemas.envelopes import HealthEnvelope
from app.schemas.registry import CONTRACT_SCHEMAS, REQUIRED_CONTRACT_SCHEMAS

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=5)
UID = UUID("12345678-1234-4234-8234-123456789abc")
OTHER_UID = UUID("87654321-4321-4321-8321-cba987654321")
SESSION_HEX = "a" * 32


# ─── Registry & serialization ────────────────────────────────────────────────


def test_registry_covers_all_required_families():
    assert len(REQUIRED_CONTRACT_SCHEMAS) == 20
    missing = [name for name in REQUIRED_CONTRACT_SCHEMAS if name not in CONTRACT_SCHEMAS]
    assert missing == []


@pytest.mark.parametrize("name", REQUIRED_CONTRACT_SCHEMAS)
def test_every_contract_schema_generates_json_schema(name):
    schema = CONTRACT_SCHEMAS[name].model_json_schema()
    assert schema["title"]
    assert "properties" in schema


def test_utc_timestamps_serialize_with_z_suffix():
    status = HealthStatus(
        version="1.0.0",
        timestamp=datetime(2026, 1, 1, 14, 0, tzinfo=timezone(timedelta(hours=2))),
    )
    assert status.to_payload()["timestamp"] == "2026-01-01T12:00:00Z"


def test_naive_timestamps_are_rejected():
    with pytest.raises(ValidationError):
        ResearchSession(
            session_id=UID,
            topic="Naive timestamps",
            created_at=datetime(2026, 1, 1, 12, 0),  # no tzinfo
        )


def test_uuid_fields_validate_string_input():
    session = ResearchSession(session_id=str(UID), topic="Valid session ID")
    assert session.session_id == UID
    with pytest.raises(ValidationError):
        ResearchSession(session_id="not-a-uuid", topic="Invalid session")


# ─── Research request / response / event ─────────────────────────────────────


def test_research_request_validates_and_defaults():
    request = ResearchRequest(topic="Impact of retrieval augmented generation")
    assert request.provider.value == "groq"
    assert request.source_types == [SourceType.ACADEMIC, SourceType.WEB, SourceType.NEWS]
    assert request.max_revisions == 2


def test_research_request_rejects_short_topic_and_unknown_fields():
    with pytest.raises(ValidationError):
        ResearchRequest(topic="ab")
    with pytest.raises(ValidationError):
        ResearchRequest(topic="A valid topic", unexpected="nope")
    with pytest.raises(ValidationError):
        ResearchRequest(topic="A valid topic", max_revisions=99)


def test_research_request_deduplicates_source_types():
    request = ResearchRequest(
        topic="Duplicate sources",
        source_types=["web", "web", "academic"],
    )
    assert request.source_types == [SourceType.WEB, SourceType.ACADEMIC]


def test_research_response_matches_sse_complete_payload():
    response = ResearchResponse(
        status="completed",
        provider="gemini",
        critic_score=8,
        revision_count=1,
        saved_report_path="reports/report_abc.md",
        sources_analyzed=12,
        logs=["done"],
        research_id=SESSION_HEX,
    )
    payload = response.to_payload()
    assert payload["status"] == "completed"
    assert payload["sources_analyzed"] == 12
    with pytest.raises(ValidationError):
        ResearchResponse(status="running", provider="gemini")


def test_research_response_rejects_non_hex_research_id():
    with pytest.raises(ValidationError):
        ResearchResponse(
            status="completed",
            provider="gemini",
            research_id="../../etc/passwd",
        )


def test_research_event_accepts_sse_frames():
    event = ResearchEvent(event="progress", message="Searching", agent="search")
    assert event.to_payload()["event"] == "progress"
    with pytest.raises(ValidationError):
        ResearchEvent(event="unknown-event", message="nope")


# ─── Sessions, projects, tasks ───────────────────────────────────────────────


def test_research_session_consistency():
    session = ResearchSession(
        session_id=UID,
        topic="Session consistency",
        status=SessionStatus.COMPLETED,
        critic_score=9,
        citations_verified=3,
        citations_total=5,
        duration_seconds=42.5,
        created_at=NOW,
        updated_at=LATER,
    )
    assert session.status is SessionStatus.COMPLETED
    with pytest.raises(ValidationError):
        ResearchSession(
            session_id=UID,
            topic="Bad citations",
            citations_verified=2,
            citations_total=1,
        )
    with pytest.raises(ValidationError):
        ResearchSession(session_id=UID, topic="Bad order", created_at=LATER, updated_at=NOW)


def test_project_tags_and_status():
    project = Project(name="RAG survey", tags=["rag", "2026"])
    assert project.status.value == "active"
    with pytest.raises(ValidationError):
        Project(name="Bad tags", tags=["<script>"])


def test_research_task_timeline_and_dependencies():
    task = ResearchTask(
        title="Collect sources",
        created_at=NOW,
        started_at=LATER,
        completed_at=LATER,
    )
    assert task.status.value == "queued"
    with pytest.raises(ValidationError):
        ResearchTask(title="Bad timeline", created_at=LATER, started_at=NOW)
    with pytest.raises(ValidationError):
        ResearchTask(title="Self dependency", depends_on=[UID], task_id=UID)


def test_agent_execution_requires_error_for_failures():
    running = AgentExecution(session_id=UID, agent="critic", iterations=2)
    assert running.execution_id  # auto-generated UUID
    assert running.status.value == "pending"
    with pytest.raises(ValidationError):
        AgentExecution(session_id=UID, agent="critic", status="failed")
    failed = AgentExecution(
        session_id=UID,
        agent="critic",
        status="failed",
        error_code=ErrorCode.ORCHESTRATION_FAILED,
    )
    assert failed.error_code is ErrorCode.ORCHESTRATION_FAILED
    with pytest.raises(ValidationError):
        AgentExecution(session_id=UID, agent="not-an-agent")


# ─── Sources, citations, claims, evidence, contradictions ────────────────────


def test_research_source_requires_url_or_doi():
    source = ResearchSource(
        title="Retrieval augmented generation",
        url="https://arxiv.org/abs/2005.11401",
        source_type="academic",
        credibility_score=0.9,
    )
    assert str(source.url).startswith("https://arxiv.org")
    with pytest.raises(ValidationError):
        ResearchSource(title="No anchor")
    with pytest.raises(ValidationError):
        ResearchSource(title="Bad scheme", url="ftp://example.com/file")


def test_research_source_rejects_future_publish_date():
    with pytest.raises(ValidationError):
        ResearchSource(
            title="Time traveller",
            url="https://example.com/x",
            published_at=LATER,
            retrieved_at=NOW,
        )


def test_citation_verification_rules():
    citation = Citation(
        raw_text="Doe, J. (2026). A paper.",
        url="https://example.com/paper",
        status="verified",
        verified_at=NOW,
        http_status=200,
        response_ms=120.5,
    )
    assert citation.style.value == "apa"
    with pytest.raises(ValidationError):
        Citation(raw_text="Missing anchor", status="verified", verified_at=NOW)
    with pytest.raises(ValidationError):
        Citation(raw_text="No status", url="https://e.com/a", status="verified", verified_at=NOW)
    with pytest.raises(ValidationError):
        Citation(raw_text="Unverified but timestamped", url="https://e.com/a", verified_at=NOW)


def test_evidence_requires_anchor():
    evidence = Evidence(quote="RAG improves factuality.", source_id=UID)
    assert evidence.stance.value == "supports"
    with pytest.raises(ValidationError):
        Evidence(quote="Floating evidence")
    with pytest.raises(ValidationError):
        Evidence(quote="Too strong", source_id=UID, strength=1.5)


def test_claim_requires_matching_evidence():
    claim = Claim(
        text="RAG reduces hallucination rates.",
        status="supported",
        supporting_evidence=[Evidence(quote="Supported by study.", source_id=UID)],
    )
    assert claim.status.value == "supported"
    with pytest.raises(ValidationError):
        Claim(text="Unbacked claim", status="supported")
    with pytest.raises(ValidationError):
        Claim(
            text="Misaligned stance",
            supporting_evidence=[
                Evidence(quote="Contradicts.", source_id=UID, stance="contradicts")
            ],
        )


def test_contradiction_requires_two_claims_and_resolution_note():
    contradiction = Contradiction(
        claim_a_id=UID,
        claim_b_id=OTHER_UID,
        description="Studies disagree on effect size.",
        severity="high",
    )
    assert contradiction.status.value == "unresolved"
    with pytest.raises(ValidationError):
        Contradiction(claim_a_id=UID, claim_b_id=UID, description="Same claim twice.")
    with pytest.raises(ValidationError):
        Contradiction(
            claim_a_id=UID,
            claim_b_id=OTHER_UID,
            description="Resolved without note.",
            status="resolved",
        )


# ─── Reports, versions, approvals, uploads ───────────────────────────────────


def test_research_report_requires_approval_timestamp():
    report = ResearchReport(title="RAG report", created_at=NOW)
    assert report.status.value == "draft"
    with pytest.raises(ValidationError):
        ResearchReport(title="Approved report", created_at=NOW, status="approved")
    approved = ResearchReport(
        title="Approved report",
        created_at=NOW,
        status="approved",
        approved_at=LATER,
    )
    assert approved.approved_at == LATER


def test_report_version_bounds():
    version = ReportVersion(report_id=UID, version=1, content_markdown="# Report")
    assert version.is_current is False
    with pytest.raises(ValidationError):
        ReportVersion(report_id=UID, version=0, content_markdown="# Report")


def test_research_approval_decisions():
    approval = ResearchApproval(report_id=UID, decision="approved", decided_at=NOW)
    assert approval.decision.value == "approved"
    with pytest.raises(ValidationError):
        ResearchApproval(report_id=UID, decision="rejected", decided_at=NOW)


def test_file_upload_metadata_validation():
    upload = FileUploadMetadata(
        filename="paper.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        checksum_sha256="a" * 64,
    )
    assert upload.purpose.value == "attachment"
    with pytest.raises(ValidationError):
        FileUploadMetadata(filename="../evil.pdf", content_type="application/pdf", size_bytes=1)
    with pytest.raises(ValidationError):
        FileUploadMetadata(filename="paper.pdf", content_type="pdf", size_bytes=1)
    with pytest.raises(ValidationError):
        FileUploadMetadata(
            filename="huge.pdf", content_type="application/pdf", size_bytes=10**9
        )


# ─── Chat, errors, provider & health status ──────────────────────────────────


def test_chat_contracts():
    request = ChatRequest(message="What is RAG?")
    assert request.message == "What is RAG?"
    with pytest.raises(ValidationError):
        ChatRequest(message="")
    assert ChatResponse(reply="Answer", provider="groq").reply == "Answer"


def test_api_error_shape_matches_existing_handlers():
    error = ApiError(
        detail="Session not found.",
        error_code="not_found",
        request_id="abc123",
        details={"session_id": "x"},
    )
    payload = error.to_payload(exclude_none=True)
    assert set(payload) == {"detail", "error_code", "request_id", "details"}
    with pytest.raises(ValidationError):
        ApiError(detail="Bad code", error_code="not_a_real_code", request_id="abc")


def test_error_code_enum_covers_handler_codes():
    handler_codes = {
        value
        for value in vars(errors_module).values()
        if isinstance(value, type) and issubclass(value, AppError)
        for value in [getattr(value, "code", None)]
        if value
    }
    assert handler_codes, "expected AppError subclasses with a `code` attribute"
    missing = handler_codes - {code.value for code in ErrorCode}
    assert missing == set()


def test_provider_status_is_non_secret():
    status = ProviderStatus(provider="groq", model="llama-3.3-70b-versatile", fallback_rank=1)
    payload = status.to_payload()
    assert payload["state"] == "ready"
    assert "api_key" not in payload
    with pytest.raises(ValidationError):
        ProviderStatus(provider="openai", model="gpt", fallback_rank=1)
    with pytest.raises(ValidationError):
        ProviderStatus(provider="groq", model="llama", fallback_rank=0)


def test_health_status_defaults_and_enum():
    status = HealthStatus(version="1.0.0")
    assert status.to_payload()["status"] == "ok"
    with pytest.raises(ValidationError):
        HealthStatus(version="1.0.0", status="unknown")


# ─── Pagination & envelope ───────────────────────────────────────────────────


def test_pagination_params_bounds():
    assert PaginationParams().limit == 20
    with pytest.raises(ValidationError):
        PaginationParams(limit=0)
    with pytest.raises(ValidationError):
        PaginationParams(limit=101)
    with pytest.raises(ValidationError):
        PaginationParams(offset=-1)


def test_pagination_meta_consistency():
    meta = PaginationMeta(limit=10, offset=0, total=5, returned=5, has_more=False)
    assert meta.has_more is False
    with pytest.raises(ValidationError):
        PaginationMeta(limit=10, offset=0, total=5, returned=5, has_more=True)
    with pytest.raises(ValidationError):
        PaginationMeta(limit=10, offset=4, total=5, returned=5, has_more=False)


def test_envelope_serialization():
    envelope = HealthEnvelope(
        data=HealthStatus(version="1.0.0"),
        message="Service healthy",
        request_id="req-1",
    )
    payload = envelope.to_payload(exclude_none=True)
    assert payload["success"] is True
    assert payload["message"] == "Service healthy"
    assert payload["request_id"] == "req-1"
    assert payload["data"]["status"] == "ok"


def test_envelope_rejects_failure_flag():
    with pytest.raises(ValidationError):
        ApiResponse(success=False, data={"anything": True})
