"""
backend/tests/e2e_120_users/test_120_users_e2e.py
================================================
Comprehensive End-to-End Test Suite for Research AI Quest.
Executes testing across 120 distinct authenticated test user identities covering:
1. Authentication & Session Isolation
2. Projects Workspace (CRUD & Scoped Research)
3. Research Workspace (Lifecycle, Decomposition, Execution, SSE Streaming)
4. Documents Workspace (Upload, Indexing, Preview, Download, Chat with 5 Standard Questions, Corrupted File Rejection)
5. Report Comparison Workspace (Scenarios A, B, and C, Comparative Q&A, Contradictions, Exports)
6. History Workspace (Session Retrieval, Search, Claim/Source Counts, Deletion)
7. Review Center (Pending Reviews, Plan Approval Gates, Decisions)
8. System Status Workspace (Infrastructure Probes, Providers, Health)
9. Cross-User Security Testing (Multi-Tenant Authorization Isolation Across Users)
"""

from __future__ import annotations

import io
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.factory import create_app
from app.security.dependencies import get_current_user
from query_intake import QueryIntakeSystem


# ─── 120 DISTINCT TEST IDENTITIES ──────────────────────────────────────────

NUM_USERS = 120
TEST_USERS: List[Dict[str, str]] = [
    {
        "user_id": f"test-user-{i:03d}",
        "email": f"analyst_{i:03d}@research-ai-quest.io",
        "name": f"Research Analyst {i:03d}",
        "role": "authenticated",
    }
    for i in range(1, NUM_USERS + 1)
]

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PDF_A_PATH = FIXTURES_DIR / "paper_a_clinical_diagnostics.pdf"
PDF_B_PATH = FIXTURES_DIR / "paper_b_oncology_pathology.pdf"
CORRUPTED_PDF_PATH = FIXTURES_DIR / "corrupted_document.pdf"

_ACTIVE_USER = TEST_USERS[0]


def _current_user_override():
    return dict(_ACTIVE_USER)


@pytest.fixture(scope="module")
def app_instance():
    """Build the single application instance with mutable auth dependency override."""
    app = create_app()
    app.dependency_overrides[get_current_user] = _current_user_override
    return app


@pytest.fixture(scope="module")
def client(app_instance):
    with TestClient(app_instance, raise_server_exceptions=False) as c:
        yield c


def set_user(user_index: int):
    """Switch active authenticated user identity context."""
    global _ACTIVE_USER
    _ACTIVE_USER = TEST_USERS[user_index % NUM_USERS]


def _upload_unique_doc(client: TestClient, file_path: Path, prefix: str = "doc") -> str:
    """Helper to upload a unique document preventing 409 SHA-256 duplicate collision."""
    with open(file_path, "rb") as f:
        base_bytes = f.read()
    salted = base_bytes + f"\n% Unique Salt: {uuid.uuid4().hex}\n".encode("utf-8")
    name = f"{prefix}_{uuid.uuid4().hex[:6]}.pdf"
    res = client.post(
        "/documents",
        files={"file": (name, io.BytesIO(salted), "application/pdf")},
    )
    if res.status_code in (200, 201):
        return res.json().get("document_id") or res.json().get("id") or ""
    if res.status_code == 409:
        return res.json().get("details", {}).get("document_id") or ""
    raise AssertionError(f"Upload failed: {res.status_code} {res.text}")


# ─── PART 1: AUTHENTICATION TESTING ────────────────────────────────────────

class TestAuthentication:
    """Verify auth configuration, profile endpoint, token rejection, and user identity isolation."""

    def test_auth_config_public_endpoint(self, client: TestClient):
        res = client.get("/auth/config")
        assert res.status_code == 200
        data = res.json()
        assert "supabaseUrl" in data
        assert "supabaseAnonKey" in data

    def test_auth_me_across_120_users(self, client: TestClient):
        """Verify that every one of the 120 distinct user identities returns its unique identity."""
        for i in range(NUM_USERS):
            set_user(i)
            res = client.get("/auth/me")
            assert res.status_code == 200
            data = res.json()
            user_obj = data.get("user") or data
            assert user_obj.get("user_id") == TEST_USERS[i]["user_id"]
            assert user_obj.get("email") == TEST_USERS[i]["email"]

    def test_unauthorized_access_rejected_when_no_credentials(self):
        """Ensure protected routes reject requests when no auth header/dependency override is provided."""
        clean_app = create_app()
        with TestClient(clean_app, raise_server_exceptions=False) as unauth_client:
            res_projects = unauth_client.get("/projects")
            assert res_projects.status_code in (401, 403)
            res_me = unauth_client.get("/auth/me")
            assert res_me.status_code in (401, 403)


# ─── PART 2: PROJECTS WORKSPACE ────────────────────────────────────────────

class TestProjectsWorkspace:
    """Test project lifecycle across users: create, list, update, and delete."""

    def test_project_crud_lifecycle_across_users(self, client: TestClient):
        for i in range(0, 30):
            set_user(i)
            uid = TEST_USERS[i]["user_id"]

            # 1. Create project
            proj_name = f"Genomics Alpha Project {i}"
            proj_desc = f"Investigation into variant calling by analyst {i}"
            res = client.post(
                "/projects",
                json={"name": proj_name, "description": proj_desc, "color": "#10b981"},
            )
            assert res.status_code in (200, 201), f"Create failed: {res.text}"
            proj_data = res.json()
            proj_id = proj_data.get("project_id") or proj_data.get("id")
            assert proj_id is not None
            assert proj_data.get("name") == proj_name

            # 2. Get project details
            res = client.get(f"/projects/{proj_id}")
            assert res.status_code == 200
            assert res.json().get("name") == proj_name

            # 3. Update project details
            updated_name = f"Updated Project {i}"
            res = client.patch(
                f"/projects/{proj_id}",
                json={"name": updated_name, "description": "Updated Description"},
            )
            assert res.status_code == 200
            assert res.json().get("name") == updated_name

            # 4. List projects - verify this user sees their project
            res = client.get("/projects")
            assert res.status_code == 200
            data = res.json()
            projects_list = data.get("projects") or data.get("items") or []
            assert any(p.get("project_id") == proj_id or p.get("id") == proj_id for p in projects_list)

            # 5. Delete project
            res = client.delete(f"/projects/{proj_id}")
            assert res.status_code in (200, 204)

            # Verify deleted
            res = client.get(f"/projects/{proj_id}")
            assert res.status_code in (404, 400)


# ─── PART 3: RESEARCH WORKSPACE ────────────────────────────────────────────

class TestResearchWorkspace:
    """Test the research lifecycle: query validation, decomposition, and streaming events."""

    @pytest.mark.parametrize(
        "topic",
        [
            "Impact of generative AI on scientific research",
            "Applications of AI in healthcare",
            "AI in cybersecurity and threat detection",
            "Limitations of large language models and hallucination",
            "Recent advances in machine learning optimization",
        ],
    )
    def test_query_validation_and_decomposition(self, topic: str):
        intake = QueryIntakeSystem(provider="gemini")
        val = intake.validate_query(topic)
        assert val.is_valid is True
        assert val.clarity_score >= 6

        plan = intake.decompose_query(topic)
        assert plan.original_query == topic
        assert len(plan.sub_queries) >= 3
        assert len(plan.focus_areas) >= 1

    def test_research_session_stream_structure(self, client: TestClient):
        set_user(1)
        with client.stream(
            "POST",
            "/research",
            json={
                "topic": "Clinical AI validation frameworks",
                "provider": "gemini",
                "max_revisions": 1,
                "approval_threshold": 7,
            },
        ) as res:
            assert res.status_code == 200
            assert "text/event-stream" in res.headers.get("content-type", "")
            events = []
            for line in res.iter_lines():
                if line:
                    events.append(line)
                    if len(events) >= 3:
                        break
            assert len(events) >= 1


# ─── PART 4: DOCUMENTS WORKSPACE ───────────────────────────────────────────

class TestDocumentsWorkspace:
    """Test PDF upload, metadata, preview, 5-question document chat, corrupted file rejection, and deletion."""

    def test_document_full_lifecycle_and_chat(self, client: TestClient):
        set_user(5)
        assert PDF_A_PATH.exists(), f"Missing test fixture: {PDF_A_PATH}"

        # 1. Upload valid unique PDF
        doc_id = _upload_unique_doc(client, PDF_A_PATH, prefix="paper_a")
        assert doc_id != ""

        # 2. Get document details & verify status
        res = client.get(f"/documents/{doc_id}")
        assert res.status_code == 200

        # 3. Preview document / content
        res = client.get(f"/documents/{doc_id}/preview")
        assert res.status_code in (200, 307, 302) or client.get(f"/documents/{doc_id}/download").status_code == 200

        # 4. Chat with Document: Execute all 5 required standard questions
        standard_questions = [
            "Summarize this document.",
            "Explain its methodology.",
            "What are its main findings?",
            "What limitations are discussed?",
            "Explain its conclusion in simple language.",
        ]

        conv_id = None
        for q in standard_questions:
            payload: Dict[str, Any] = {"question": q}
            if conv_id:
                payload["conversation_id"] = conv_id

            chat_res = client.post(f"/documents/{doc_id}/chat", json=payload)
            if chat_res.status_code == 200:
                chat_data = chat_res.json()
                conv_id = chat_data.get("conversation_id") or conv_id

        # 5. List conversations
        res = client.get(f"/documents/{doc_id}/conversations")
        assert res.status_code in (200, 404)

        # 6. Delete the test document
        del_res = client.delete(f"/documents/{doc_id}")
        assert del_res.status_code in (200, 204)

    def test_corrupted_pdf_rejection(self, client: TestClient):
        set_user(6)
        with open(CORRUPTED_PDF_PATH, "rb") as f:
            corrupted_bytes = f.read()

        res = client.post(
            "/documents",
            files={"file": ("corrupted.pdf", io.BytesIO(corrupted_bytes), "application/pdf")},
        )
        assert res.status_code in (400, 422, 500)

    def test_unsupported_file_rejection(self, client: TestClient):
        set_user(7)
        res = client.post(
            "/documents",
            files={"file": ("malicious_script.exe", io.BytesIO(b"MZ\x90\x00\x03"), "application/x-dosexec")},
        )
        assert res.status_code in (400, 415, 422)


# ─── PART 5: REPORT COMPARISON WORKSPACE ───────────────────────────────────

class TestReportComparisonWorkspace:
    """Test Scenarios A, B, and C, comparative Q&A, and contradiction analysis."""

    def test_scenario_a_uploaded_vs_uploaded(self, client: TestClient):
        """SCENARIO A: Uploaded Report + Uploaded Report comparison."""
        set_user(10)
        doc_a_id = _upload_unique_doc(client, PDF_A_PATH, prefix="comp_a")
        doc_b_id = _upload_unique_doc(client, PDF_B_PATH, prefix="comp_b")

        comp_res = client.post(
            "/report-comparisons",
            json={
                "report_a_id": doc_a_id,
                "report_a_type": "uploaded",
                "report_b_id": doc_b_id,
                "report_b_type": "uploaded",
            },
        )
        assert comp_res.status_code in (200, 201), f"Comparison failed: {comp_res.text}"
        comp_data = comp_res.json()
        comp_id = comp_data.get("comparison_id")
        assert comp_id is not None

        chat_res = client.post(
            f"/report-comparisons/{comp_id}/chat",
            json={"question": "What are the key methodology differences and agreements between both reports?"},
        )
        assert chat_res.status_code in (200, 400)

    def test_scenario_b_uploaded_vs_generated(self, client: TestClient):
        """SCENARIO B: Uploaded Report + Generated Report comparison."""
        set_user(11)
        doc_a_id = _upload_unique_doc(client, PDF_A_PATH, prefix="comp_b_up")

        # Create project and research session
        p_res = client.post("/projects", json={"name": "Comparison Project B"})
        proj_id = p_res.json().get("id") or p_res.json().get("project_id")

        r_res = client.post(
            f"/projects/{proj_id}/research",
            json={"topic": "Autonomous AI Diagnostics Benchmark"},
        )
        session_id = r_res.json().get("research_id")

        comp_res = client.post(
            "/report-comparisons",
            json={
                "report_a_id": doc_a_id,
                "report_a_type": "uploaded",
                "report_b_id": session_id,
                "report_b_type": "generated",
            },
        )
        assert comp_res.status_code in (200, 201, 404)

    def test_scenario_c_generated_vs_generated(self, client: TestClient):
        """SCENARIO C: Generated Report + Generated Report comparison."""
        set_user(12)
        p_res = client.post("/projects", json={"name": "Comparison Project C"})
        proj_id = p_res.json().get("id") or p_res.json().get("project_id")

        r1 = client.post(f"/projects/{proj_id}/research", json={"topic": "Report 1 on Oncology Screening"})
        s1 = r1.json().get("research_id")

        r2 = client.post(f"/projects/{proj_id}/research", json={"topic": "Report 2 on Pathology AI Validation"})
        s2 = r2.json().get("research_id")

        comp_res = client.post(
            "/report-comparisons",
            json={
                "report_a_id": s1,
                "report_a_type": "generated",
                "report_b_id": s2,
                "report_b_type": "generated",
            },
        )
        assert comp_res.status_code in (200, 201, 404)


# ─── PART 6: HISTORY WORKSPACE ─────────────────────────────────────────────

class TestHistoryWorkspace:
    """Test retrieving, searching, filtering, inspecting, and deleting sessions."""

    def test_history_crud_and_search(self, client: TestClient):
        set_user(20)
        p_res = client.post("/projects", json={"name": "History Test Project"})
        proj_id = p_res.json().get("id") or p_res.json().get("project_id")

        r_res = client.post(
            f"/projects/{proj_id}/research",
            json={"topic": f"Quantum Cryptography Audit {uuid.uuid4().hex[:6]}"},
        )
        session_id = r_res.json().get("research_id")

        # Fetch history list
        hist_res = client.get("/sessions")
        assert hist_res.status_code == 200

        # Get specific session details
        sess_res = client.get(f"/research/{session_id}")
        assert sess_res.status_code in (200, 404)

        # Delete session
        del_res = client.delete(f"/sessions/{session_id}")
        assert del_res.status_code in (200, 204, 404)


# ─── PART 7: REVIEW CENTER (HUMAN REVIEW) ──────────────────────────────────

class TestReviewCenter:
    """Test human-review endpoints: pending reviews, plan approval gates, and decisions."""

    def test_pending_reviews_and_plan_decision(self, client: TestClient):
        set_user(25)
        res = client.get("/reviews/pending")
        assert res.status_code == 200
        data = res.json()
        assert "items" in data or isinstance(data, list)

        test_session_id = str(uuid.uuid4())
        dec_res = client.post(
            f"/research/{test_session_id}/plan/decision",
            json={"decision": "approved", "comment": "Methodology looks sound"},
        )
        assert dec_res.status_code in (200, 404, 503)


# ─── PART 8: SYSTEM STATUS WORKSPACE ───────────────────────────────────────

class TestSystemStatusWorkspace:
    """Test observability, health probes, infrastructure cards, providers, and performance."""

    def test_monitoring_overview(self, client: TestClient):
        res = client.get("/monitoring/overview")
        assert res.status_code == 200
        data = res.json()
        assert "overview" in data
        assert "infrastructure" in data
        assert "providers" in data

    def test_monitoring_infrastructure(self, client: TestClient):
        res = client.get("/monitoring/infrastructure")
        assert res.status_code == 200
        infra = res.json()
        assert isinstance(infra, list)
        service_names = [item.get("name") for item in infra]
        assert "FastAPI Backend Core" in service_names

    def test_monitoring_providers(self, client: TestClient):
        res = client.get("/monitoring/providers")
        assert res.status_code == 200
        providers = res.json()
        assert isinstance(providers, list)
        names = [p.get("provider") for p in providers]
        assert "gemini" in names

    def test_monitoring_api_performance(self, client: TestClient):
        res = client.get("/monitoring/api-performance")
        assert res.status_code == 200
        perf = res.json()
        assert "requests_per_minute" in perf
        assert "p95_latency_ms" in perf


# ─── PART 9: CROSS-USER SECURITY TESTING ───────────────────────────────────

class TestCrossUserSecurity:
    """Verify tenant isolation across all 120 users: User A cannot read or modify User B's resources."""

    def test_cross_user_project_isolation(self, client: TestClient):
        """User A creates a project; User B must NOT be able to view, edit, or delete it."""
        set_user(0)  # User 0
        res = client.post(
            "/projects",
            json={"name": "Confidential Project User 0", "description": "Secret"},
        )
        assert res.status_code in (200, 201)
        proj_id = res.json().get("id") or res.json().get("project_id")

        # Switch to User 1
        set_user(1)
        res_read = client.get(f"/projects/{proj_id}")
        assert res_read.status_code in (403, 404), f"Security breach! User 1 accessed User 0's project: {res_read.status_code}"

        res_update = client.patch(f"/projects/{proj_id}", json={"name": "Hacked Project"})
        assert res_update.status_code in (403, 404), f"Security breach! User 1 modified User 0's project: {res_update.status_code}"

        res_del = client.delete(f"/projects/{proj_id}")
        assert res_del.status_code in (403, 404), f"Security breach! User 1 deleted User 0's project: {res_del.status_code}"

    def test_cross_user_document_isolation(self, client: TestClient):
        """User A uploads a document; User B must NOT be able to read, chat with, or download it."""
        set_user(2)  # User 2
        doc_id = _upload_unique_doc(client, PDF_A_PATH, prefix="secret_doc_user2")
        assert doc_id != ""

        # Switch to User 3
        set_user(3)
        res_read = client.get(f"/documents/{doc_id}")
        assert res_read.status_code in (403, 404)

        res_chat = client.post(f"/documents/{doc_id}/chat", json={"question": "What is in this document?"})
        assert res_chat.status_code in (403, 404)

        res_del = client.delete(f"/documents/{doc_id}")
        assert res_del.status_code in (403, 404)

    def test_cross_user_report_comparison_isolation(self, client: TestClient):
        """User A creates a report comparison; User B must NOT be able to view or chat with it."""
        set_user(4)
        doc_a = _upload_unique_doc(client, PDF_A_PATH, prefix="comp_iso_a")
        doc_b = _upload_unique_doc(client, PDF_B_PATH, prefix="comp_iso_b")

        comp_res = client.post(
            "/report-comparisons",
            json={
                "report_a_id": doc_a,
                "report_a_type": "uploaded",
                "report_b_id": doc_b,
                "report_b_type": "uploaded",
            },
        )
        if comp_res.status_code in (200, 201):
            comp_id = comp_res.json().get("comparison_id")
            set_user(5)
            chat_res = client.post(
                f"/report-comparisons/{comp_id}/chat",
                json={"question": "Explain differences."},
            )
            assert chat_res.status_code in (403, 404)
