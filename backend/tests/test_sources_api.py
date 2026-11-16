"""
tests/test_sources_api.py
=========================
Search source management API tests.

Covers
------
* GET /research/{research_id}/sources — list with pagination, filters, sorting
* GET /sources/{source_id} — get one source
* GET /research/{research_id}/sources/statistics — aggregate counts
* PATCH /sources/{source_id}/status — update citation validation status
* DELETE /sources/{source_id} — soft delete
* Pagination (limit / offset)
* Filtering (source type, publisher, citation status, date)
* Invalid project/session IDs (422)
* Unauthenticated access (401)
* Cross-user access (404)
* Source status transitions
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.factory import create_app
from app.schemas.enums import CitationStatus, SourceType
from app.security.dependencies import get_current_user
from app.services.container import get_source_service
from app.services.source_service import SourceService


class FakeSourceRepository:
    """In-memory double of SourceRepository for API tests."""

    def __init__(self) -> None:
        self._sources: Dict[str, Dict[str, Any]] = {}

    def available(self) -> bool:
        return True

    def create_source(self, *, session_id, owner_id, url, title,
                      source_type, author=None, publisher=None,
                      publication_date=None, snippet=None,
                      citation_status="unverified", metadata=None):
        import uuid as _uuid
        sid = str(_uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "source_id": sid,
            "session_id": session_id,
            "url": url,
            "title": title,
            "author": author,
            "publisher": publisher,
            "publication_date": publication_date,
            "source_type": source_type,
            "snippet": snippet,
            "retrieval_date": now,
            "relevance": None,
            "credibility_evaluation_reference": None,
            "citation_validation_status": citation_status,
            "created_at": now,
            "updated_at": now,
            "deleted": False,
            "_owner_id": owner_id,
        }
        self._sources[sid] = row
        return row

    def get_source(self, *, source_id, owner_id):
        row = self._sources.get(source_id)
        if row is None or row.get("deleted") or row["_owner_id"] != owner_id:
            return None
        return row

    def list_sources(self, *, owner_id, session_id=None, source_type=None,
                     publisher=None, citation_status=None,
                     created_after=None, created_before=None,
                     sort="created_at", order="desc", limit=20, offset=0):
        rows = [
            r for r in self._sources.values()
            if not r.get("deleted") and r["_owner_id"] == owner_id
        ]
        if session_id:
            rows = [r for r in rows if r["session_id"] == session_id]
        if source_type:
            rows = [r for r in rows if r["source_type"] == source_type]
        if publisher:
            rows = [r for r in rows if r["publisher"] == publisher]
        if citation_status:
            rows = [
                r for r in rows
                if r["citation_validation_status"] == citation_status
            ]
        if created_after:
            rows = [
                r for r in rows
                if r["created_at"] >= created_after.isoformat()
            ]
        if created_before:
            rows = [
                r for r in rows
                if r["created_at"] <= created_before.isoformat()
            ]
        reverse = order == "desc"
        rows.sort(key=lambda r: r.get(sort, r["created_at"]), reverse=reverse)
        total = len(rows)
        page = rows[offset:offset + limit]
        clean = [{k: v for k, v in r.items() if k != "_owner_id"} for r in page]
        return clean, total

    def update_source_status(self, *, source_id, owner_id, citation_status):
        row = self._sources.get(source_id)
        if row is None or row.get("deleted") or row["_owner_id"] != owner_id:
            return None
        row["citation_validation_status"] = citation_status
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        return row

    def delete_source(self, *, source_id, owner_id):
        row = self._sources.get(source_id)
        if row is None or row.get("deleted") or row["_owner_id"] != owner_id:
            return False
        row["deleted"] = True
        return True

    def statistics(self, *, owner_id, session_id=None):
        rows = [
            r for r in self._sources.values()
            if not r.get("deleted") and r["_owner_id"] == owner_id
        ]
        if session_id:
            rows = [r for r in rows if r["session_id"] == session_id]
        by_type: Dict[str, int] = {}
        by_publisher: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        for r in rows:
            by_type[r["source_type"]] = by_type.get(r["source_type"], 0) + 1
            pub = r["publisher"] or "unknown"
            by_publisher[pub] = by_publisher.get(pub, 0) + 1
            by_status[r["citation_validation_status"]] = (
                by_status.get(r["citation_validation_status"], 0) + 1
            )
        return {
            "total": len(rows),
            "by_source_type": by_type,
            "by_publisher": by_publisher,
            "by_citation_status": by_status,
        }


def _make_service() -> SourceService:
    service = SourceService.__new__(SourceService)
    service._settings = MagicMock()
    service._repo = FakeSourceRepository()
    return service


@pytest.fixture
def user_state() -> dict:
    return {"user_id": "user-a"}


def _build_client(user_state: dict, service: SourceService) -> TestClient:
    application = create_app(get_settings())
    application.dependency_overrides[get_current_user] = lambda: dict(user_state)
    application.dependency_overrides[get_source_service] = lambda: service
    return TestClient(application, raise_server_exceptions=False)


# ── Fixture: shared service + client ──────────────────────────────────

@pytest.fixture
def env(user_state):
    """A SourceService and TestClient that share the same repository."""
    service = _make_service()
    client = _build_client(user_state, service)
    return service, client


# ── List sources ───────────────────────────────────────────────────────

class TestListSources:
    def test_list_returns_created_sources(self, env):
        service, client = env
        session = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        service.create_source(
            owner_id="user-a",
            session_id=session,
            url="https://example.com/1",
            title="First Source",
            source_type="academic",
            publisher="Nature",
            citation_status="unverified",
        )
        service.create_source(
            owner_id="user-a",
            session_id=session,
            url="https://example.com/2",
            title="Second Source",
            source_type="web",
            publisher="Wikipedia",
            citation_status="verified",
        )

        response = client.get(f"/research/{session}/sources")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 2
        assert len(body["sources"]) == 2
        titles = {s["title"] for s in body["sources"]}
        assert titles == {"First Source", "Second Source"}

    def test_list_pagination(self, env):
        service, client = env
        session = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        for i in range(5):
            service.create_source(
                owner_id="user-a",
                session_id=session,
                url=f"https://example.com/{i}",
                title=f"Source {i}",
                source_type="web",
            )
        response = client.get(
            f"/research/{session}/sources?limit=2&offset=1"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 5
        assert len(body["sources"]) == 2
        assert body["limit"] == 2
        assert body["offset"] == 1

    def test_list_filter_by_source_type(self, env):
        service, client = env
        session = "cccccccccccccccccccccccccccccccc"
        service.create_source(
            owner_id="user-a",
            session_id=session,
            url="https://example.com/1",
            title="Academic Source",
            source_type="academic",
        )
        service.create_source(
            owner_id="user-a",
            session_id=session,
            url="https://example.com/2",
            title="Web Source",
            source_type="web",
        )
        response = client.get(
            f"/research/{session}/sources?source_type={SourceType.ACADEMIC.value}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["sources"][0]["source_type"] == "academic"

    def test_list_filter_by_citation_status(self, env):
        service, client = env
        session = "dddddddddddddddddddddddddddddddd"
        service.create_source(
            owner_id="user-a",
            session_id=session,
            url="https://example.com/1",
            title="Verified",
            citation_status="verified",
        )
        service.create_source(
            owner_id="user-a",
            session_id=session,
            url="https://example.com/2",
            title="Unverified",
            citation_status="unverified",
        )
        response = client.get(
            f"/research/{session}/sources?citation_status={CitationStatus.VERIFIED.value}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["sources"][0]["citation_validation_status"] == "verified"

    def test_list_sort_ascending(self, env):
        service, client = env
        session = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        service.create_source(
            owner_id="user-a", session_id=session,
            url="https://example.com/1", title="A",
            source_type="web",
        )
        service.create_source(
            owner_id="user-a", session_id=session,
            url="https://example.com/2", title="B",
            source_type="web",
        )
        response = client.get(f"/research/{session}/sources?sort=title&order=asc")
        assert response.status_code == 200
        body = response.json()
        assert [s["title"] for s in body["sources"]] == ["A", "B"]

    def test_invalid_session_id_is_422(self, env):
        _, client = env
        response = client.get("/research/not-a-hex-id/sources")
        assert response.status_code == 422, response.text


# ── Get one source ────────────────────────────────────────────────────

class TestGetSource:
    def test_get_returns_source(self, env):
        service, client = env
        row = service.create_source(
            owner_id="user-a",
            session_id="ffffffffffffffffffffffffffffffff",
            url="https://example.com/one",
            title="One Source",
            source_type="academic",
            publisher="Nature",
        )
        response = client.get(f"/sources/{row['source_id']}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source_id"] == row["source_id"]
        assert body["title"] == "One Source"
        assert body["url"] == "https://example.com/one"

    def test_get_unknown_source_is_404(self, env):
        _, client = env
        import uuid as _uuid
        response = client.get(f"/sources/{_uuid.uuid4()}")
        assert response.status_code == 404, response.text

    def test_malformed_source_id_is_422(self, env):
        _, client = env
        response = client.get("/sources/not-a-uuid")
        assert response.status_code == 422, response.text


# ── Statistics ────────────────────────────────────────────────────────

class TestStatistics:
    def test_statistics_aggregates(self, env):
        service, client = env
        session = "11111111111111111111111111111111"
        service.create_source(
            owner_id="user-a", session_id=session,
            url="https://example.com/1", title="A",
            source_type="academic", publisher="Nature",
            citation_status="verified",
        )
        service.create_source(
            owner_id="user-a", session_id=session,
            url="https://example.com/2", title="B",
            source_type="web", publisher="Wikipedia",
            citation_status="unverified",
        )
        response = client.get(f"/research/{session}/sources/statistics")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 2
        assert body["by_source_type"]["academic"] == 1
        assert body["by_source_type"]["web"] == 1
        assert body["by_citation_status"]["verified"] == 1
        assert body["by_citation_status"]["unverified"] == 1


# ── Update status ─────────────────────────────────────────────────────

class TestUpdateStatus:
    def test_update_status(self, env):
        service, client = env
        row = service.create_source(
            owner_id="user-a",
            session_id="22222222222222222222222222222222",
            url="https://example.com/status",
            title="Status Source",
            source_type="web",
            citation_status="unverified",
        )
        response = client.patch(
            f"/sources/{row['source_id']}/status",
            params={"citation_status": CitationStatus.VERIFIED.value},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["citation_validation_status"] == "verified"

    def test_update_invalid_status_is_422(self, env):
        service, client = env
        row = service.create_source(
            owner_id="user-a",
            session_id="33333333333333333333333333333333",
            url="https://example.com/badstatus",
            title="Bad Status",
            source_type="web",
        )
        response = client.patch(
            f"/sources/{row['source_id']}/status",
            params={"citation_status": "not_a_status"},
        )
        assert response.status_code == 422, response.text


# ── Delete ────────────────────────────────────────────────────────────

class TestDelete:
    def test_delete_source(self, env):
        service, client = env
        row = service.create_source(
            owner_id="user-a",
            session_id="44444444444444444444444444444444",
            url="https://example.com/delete",
            title="Delete Me",
            source_type="web",
        )
        response = client.delete(f"/sources/{row['source_id']}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "deleted"
        assert body["source_id"] == row["source_id"]

    def test_delete_unknown_source_is_404(self, env):
        _, client = env
        import uuid as _uuid
        response = client.delete(f"/sources/{_uuid.uuid4()}")
        assert response.status_code == 404, response.text


# ── Authentication ────────────────────────────────────────────────────

class TestAuthentication:
    def test_all_endpoints_require_auth(self):
        application = create_app(get_settings())
        import uuid as _uuid
        with TestClient(application, raise_server_exceptions=False) as tc:
            session = "55555555555555555555555555555555"
            assert tc.get(f"/research/{session}/sources").status_code == 401
            assert tc.get(f"/sources/{_uuid.uuid4()}").status_code == 401
            assert tc.get(f"/research/{session}/sources/statistics").status_code == 401
            assert tc.patch(
                f"/sources/{_uuid.uuid4()}/status",
                params={"citation_status": "verified"},
            ).status_code == 401
            assert tc.delete(f"/sources/{_uuid.uuid4()}").status_code == 401


# ── Cross-user isolation ──────────────────────────────────────────────

class TestCrossUserIsolation:
    def test_cannot_access_another_users_source(self, env):
        service, client = env
        row = service.create_source(
            owner_id="user-b",  # different owner
            session_id="66666666666666666666666666666666",
            url="https://example.com/private",
            title="Private Source",
            source_type="web",
        )
        response = client.get(f"/sources/{row['source_id']}")
        assert response.status_code == 404, response.text
        response = client.patch(
            f"/sources/{row['source_id']}/status",
            params={"citation_status": CitationStatus.VERIFIED.value},
        )
        assert response.status_code == 404, response.text
        response = client.delete(f"/sources/{row['source_id']}")
        assert response.status_code == 404, response.text

    def test_list_hides_other_users_sources(self, env):
        service, client = env
        session = "77777777777777777777777777777777"
        service.create_source(
            owner_id="user-b",
            session_id=session,
            url="https://example.com/private",
            title="Private",
            source_type="web",
        )
        service.create_source(
            owner_id="user-a",
            session_id=session,
            url="https://example.com/public",
            title="Public",
            source_type="web",
        )
        response = client.get(f"/research/{session}/sources")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["sources"][0]["title"] == "Public"
