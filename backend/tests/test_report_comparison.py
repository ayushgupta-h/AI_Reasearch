"""
backend/tests/test_report_comparison.py
=======================================
End-to-end tests for Report Comparison & Analysis Service and API.
Tests Scenarios A, B, and C, dual-report chat, persistence, authorization, and export.
"""

import json
import uuid
import pytest
from fastapi.testclient import TestClient

from app.factory import create_app
from app.infrastructure import postgres_bridge
from app.security.dependencies import get_current_user


_CURRENT_TEST_USER = {"user_id": "user_111", "email": "user1@example.com"}


def override_get_current_user():
    return _CURRENT_TEST_USER


@pytest.fixture
def client():
    app = create_app()
    app.dependency_overrides[get_current_user] = override_get_current_user
    return TestClient(app)


def _upload_unique_doc(client, name: str, content: str) -> str:
    """Upload a document with unique content to prevent 409 SHA-256 duplicates."""
    unique_content = f"{content}\n\nSeed: {uuid.uuid4().hex}".encode("utf-8")
    res = client.post(
        "/documents/upload",
        files={"file": (name, unique_content, "text/plain")},
    )
    if res.status_code == 201:
        return res.json().get("document_id") or res.json().get("id")
    assert res.status_code == 201, f"Upload failed: {res.text}"
    return ""


def test_comparison_service_scenarios(client):
    """Test Scenarios A, B, and C with actual created documents and sessions."""
    global _CURRENT_TEST_USER
    _CURRENT_TEST_USER = {"user_id": "user_111", "email": "user1@example.com"}

    # 1. Create two uploaded documents with unique content
    doc_a_id = _upload_unique_doc(
        client,
        f"trial_a_{uuid.uuid4().hex[:6]}.txt",
        "# Trial A Methodology\nConducted on 500 patients over 12 weeks. Observed 34% efficacy in symptom reduction.",
    )
    doc_b_id = _upload_unique_doc(
        client,
        f"trial_b_{uuid.uuid4().hex[:6]}.txt",
        "# Trial B Design\nTested on 120 patients for 4 weeks. No statistically significant effect was noted.",
    )

    # 2. Create a generated research session in postgres_bridge
    sess_id = f"sess_comp_test_{uuid.uuid4().hex[:8]}"
    postgres_bridge.persist_session({
        "session_id": sess_id,
        "topic": "Evaluation of Modern AI Agents in Medical Research",
        "timestamp": "2026-11-18T10:00:00Z",
        "user_id": "user_111",
        "status": "completed",
        "report_content": "# Evaluation of Modern AI Agents\n\n## Executive Summary\nAI models demonstrated 42% faster discovery cycles.\n\n## Methodology\nRetrospective meta-analysis of 14 peer-reviewed studies.\n\n## Key Findings\nAccuracy reached 94.2% across verified clinical datasets.",
    })

    # SCENARIO A: Uploaded Report vs Generated Report
    res_a = client.post(
        "/report-comparisons",
        json={
            "report_a_id": doc_a_id,
            "report_a_type": "uploaded",
            "report_b_id": sess_id,
            "report_b_type": "generated",
            "title": "Scenario A: Clinical Trial A vs AI Meta-Analysis",
        },
    )
    assert res_a.status_code == 201
    data_a = res_a.json()
    assert data_a["status"] == "completed"
    assert "dimensions" in data_a["analysis_json"]
    assert len(data_a["analysis_json"]["dimensions"]) > 0
    cmp_a_id = data_a["comparison_id"]

    # SCENARIO B: Generated vs Generated (create second session)
    sess_id_2 = f"sess_comp_test_{uuid.uuid4().hex[:8]}"
    postgres_bridge.persist_session({
        "session_id": sess_id_2,
        "topic": "Impact of Autonomous AI Agents on Labor Markets",
        "timestamp": "2026-11-18T10:05:00Z",
        "user_id": "user_111",
        "status": "completed",
        "report_content": "# Impact on Labor Markets\n\n## Executive Summary\nProductivity rose while employment shifts remained concentrated in routine tasks.\n\n## Methodology\nEconometric survey covering 2,400 enterprises.",
    })
    res_b = client.post(
        "/report-comparisons",
        json={
            "report_a_id": sess_id,
            "report_a_type": "generated",
            "report_b_id": sess_id_2,
            "report_b_type": "generated",
        },
    )
    assert res_b.status_code == 201
    data_b = res_b.json()
    assert data_b["status"] == "completed"

    # SCENARIO C: Uploaded vs Uploaded
    res_c = client.post(
        "/report-comparisons",
        json={
            "report_a_id": doc_a_id,
            "report_a_type": "uploaded",
            "report_b_id": doc_b_id,
            "report_b_type": "uploaded",
        },
    )
    assert res_c.status_code == 201
    data_c = res_c.json()
    assert data_c["status"] == "completed"

    # VALIDATION: Cannot compare identical reports
    res_invalid = client.post(
        "/report-comparisons",
        json={
            "report_a_id": doc_a_id,
            "report_a_type": "uploaded",
            "report_b_id": doc_a_id,
            "report_b_type": "uploaded",
        },
    )
    assert res_invalid.status_code in (400, 422)

    # 3. Test Dual-Report Chat
    chat_res = client.post(
        f"/report-comparisons/{cmp_a_id}/chat",
        json={"question": "What are the biggest differences in sample size between Report A and Report B?"},
    )
    assert chat_res.status_code == 200
    chat_data = chat_res.json()
    assert "message" in chat_data
    assert len(chat_data["message"]) > 10

    # 4. Test List Messages
    msgs_res = client.get(f"/report-comparisons/{cmp_a_id}/messages")
    assert msgs_res.status_code == 200
    msgs = msgs_res.json()["messages"]
    assert len(msgs) >= 2  # user + assistant

    # 5. Test Persistence (List and Get)
    list_res = client.get("/report-comparisons")
    assert list_res.status_code == 200
    comps = list_res.json()["comparisons"]
    assert any(c["comparison_id"] == cmp_a_id for c in comps)

    get_res = client.get(f"/report-comparisons/{cmp_a_id}")
    assert get_res.status_code == 200
    assert get_res.json()["comparison_id"] == cmp_a_id

    # 6. Test Export
    export_res = client.get(f"/report-comparisons/{cmp_a_id}/export?format=markdown")
    assert export_res.status_code == 200
    assert len(export_res.content) > 0


def test_comparison_authorization(client):
    """Ensure User 2 cannot access or compare User 1's private reports."""
    global _CURRENT_TEST_USER
    # User 1 uploads a private document
    _CURRENT_TEST_USER = {"user_id": "user_111", "email": "user1@example.com"}
    doc_id = _upload_unique_doc(
        client,
        f"confidential_{uuid.uuid4().hex[:6]}.txt",
        "# Confidential User 1 Report\nSecret internal findings.",
    )

    # User 2 tries to compare with User 1's document
    _CURRENT_TEST_USER = {"user_id": "user_222", "email": "user2@example.com"}
    bad_comp = client.post(
        "/report-comparisons",
        json={
            "report_a_id": doc_id,
            "report_a_type": "uploaded",
            "report_b_id": "dummy_b",
            "report_b_type": "uploaded",
        },
    )
    assert bad_comp.status_code in (404, 403, 401)
