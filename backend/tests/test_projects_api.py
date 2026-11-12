"""
tests/test_projects_api.py
==========================
Projects and research-session CRUD over the real SQLite repositories.

Auth is overridden with a mutable identity so cross-user access can be tested
without a live Supabase instance. No network calls and no LLM keys are needed.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.factory import create_app
from app.security.dependencies import get_current_user


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture
def api_settings(tmp_path: Path):
    base = get_settings()
    return dataclasses.replace(
        base,
        reports_dir=tmp_path / "reports",
        exports_dir=tmp_path / "reports" / "exports",
        sessions_db_path=tmp_path / "sessions.db",
        log_file=None,
    )


@pytest.fixture
def user_state() -> dict:
    return {"user_id": "user-a"}


@pytest.fixture
def client(api_settings, user_state):
    application = create_app(api_settings)
    application.dependency_overrides[get_current_user] = lambda: dict(user_state)
    with TestClient(application, raise_server_exceptions=False) as test_client:
        test_client.user_state = user_state  # type: ignore[attr-defined]
        yield test_client


@pytest.fixture
def anonymous_client(api_settings):
    application = create_app(api_settings)
    with TestClient(application, raise_server_exceptions=False) as test_client:
        yield test_client


def _create_project(client: TestClient, name: str = "Alpha", **extra) -> dict:
    response = client.post("/projects", json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def _create_research(
    client: TestClient, project_id: str, topic: str = "AI in medicine", **extra
) -> dict:
    response = client.post(
        f"/projects/{project_id}/research", json={"topic": topic, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


# ─── Creation ───────────────────────────────────────────────────────────────


class TestProjectCreation:
    def test_create_returns_owned_project(self, client: TestClient) -> None:
        body = _create_project(
            client, "Clinical AI", description="Diagnostics", research_domain="healthcare"
        )

        uuid.UUID(body["project_id"])
        assert body["owner_id"] == "user-a"
        assert body["name"] == "Clinical AI"
        assert body["description"] == "Diagnostics"
        assert body["research_domain"] == "healthcare"
        assert body["status"] == "active"
        assert body["created_at"].endswith("Z")
        assert body["updated_at"].endswith("Z")

    def test_body_ownership_is_ignored(self, client: TestClient) -> None:
        body = _create_project(
            client, "Spoofed", owner_id="attacker", user_id="attacker"
        )
        assert body["owner_id"] == "user-a"

    def test_blank_name_is_rejected(self, client: TestClient) -> None:
        response = client.post("/projects", json={"name": "   "})
        assert response.status_code == 422
        assert response.json()["error_code"] == "validation_error"

    def test_create_requires_authentication(self, anonymous_client: TestClient) -> None:
        response = anonymous_client.post("/projects", json={"name": "No auth"})
        assert response.status_code == 401


# ─── Retrieval ──────────────────────────────────────────────────────────────


class TestProjectRetrieval:
    def test_get_owned_project(self, client: TestClient) -> None:
        created = _create_project(client)
        response = client.get(f"/projects/{created['project_id']}")

        assert response.status_code == 200
        assert response.json()["project_id"] == created["project_id"]

    def test_unknown_project_is_404(self, client: TestClient) -> None:
        response = client.get(f"/projects/{uuid.uuid4()}")
        assert response.status_code == 404
        assert response.json()["error_code"] == "not_found"

    def test_malformed_project_id_is_422(self, client: TestClient) -> None:
        response = client.get("/projects/not-a-uuid")
        assert response.status_code == 422

    def test_other_users_project_is_hidden(self, client: TestClient) -> None:
        created = _create_project(client)
        client.user_state["user_id"] = "user-b"

        response = client.get(f"/projects/{created['project_id']}")
        assert response.status_code == 404

    def test_list_only_returns_own_projects(self, client: TestClient) -> None:
        _create_project(client, "Mine")
        client.user_state["user_id"] = "user-b"
        _create_project(client, "Theirs")

        body = client.get("/projects").json()
        assert [item["name"] for item in body["projects"]] == ["Theirs"]
        assert body["count"] == 1


# ─── Listing, pagination, filtering ─────────────────────────────────────────


class TestProjectListing:
    def test_pagination_metadata(self, client: TestClient) -> None:
        for name in ("One", "Two", "Three", "Four", "Five"):
            _create_project(client, name)

        body = client.get("/projects", params={"limit": 2, "offset": 1}).json()
        assert body["count"] == 2
        assert body["pagination"]["total"] == 5
        assert body["pagination"]["has_more"] is True

        last = client.get("/projects", params={"limit": 2, "offset": 4}).json()
        assert last["pagination"]["has_more"] is False

    def test_sorting_and_status_filter(self, client: TestClient) -> None:
        _create_project(client, "Beta")
        _create_project(client, "Alpha")
        _create_project(client, "Gamma", status="archived")

        ascending = client.get(
            "/projects", params={"sort": "name", "order": "asc"}
        ).json()
        assert [item["name"] for item in ascending["projects"]] == [
            "Alpha",
            "Beta",
            "Gamma",
        ]

        archived = client.get("/projects", params={"status": "archived"}).json()
        assert [item["name"] for item in archived["projects"]] == ["Gamma"]

    def test_search_matches_name_and_description(self, client: TestClient) -> None:
        _create_project(client, "Climate models", description="Ocean temperature")
        _create_project(client, "Quantum chips")

        body = client.get("/projects", params={"q": "ocean"}).json()
        assert [item["name"] for item in body["projects"]] == ["Climate models"]

    def test_date_filters_and_invalid_range(self, client: TestClient) -> None:
        _create_project(client)

        past = _iso(datetime(2000, 1, 1, tzinfo=timezone.utc))
        assert client.get(
            "/projects", params={"created_before": past}
        ).json()["count"] == 0
        assert client.get(
            "/projects", params={"created_after": past}
        ).json()["count"] == 1

        future = _iso(datetime(2100, 1, 1, tzinfo=timezone.utc))
        invalid = client.get(
            "/projects", params={"created_after": future, "created_before": past}
        )
        assert invalid.status_code == 400
        assert invalid.json()["error_code"] == "bad_request"


# ─── Updates ────────────────────────────────────────────────────────────────


class TestProjectUpdates:
    def test_patch_updates_fields(self, client: TestClient) -> None:
        created = _create_project(client)
        response = client.patch(
            f"/projects/{created['project_id']}",
            json={
                "name": "Renamed",
                "status": "paused",
                "research_domain": "climate",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Renamed"
        assert body["status"] == "paused"
        assert body["research_domain"] == "climate"
        assert body["updated_at"] >= body["created_at"]

    def test_explicit_null_clears_optional_field(self, client: TestClient) -> None:
        created = _create_project(client, description="Temporary")
        response = client.patch(
            f"/projects/{created['project_id']}", json={"description": None}
        )

        assert response.status_code == 200
        assert response.json()["description"] is None

    def test_empty_patch_is_rejected(self, client: TestClient) -> None:
        created = _create_project(client)
        response = client.patch(f"/projects/{created['project_id']}", json={})

        assert response.status_code == 400
        assert response.json()["error_code"] == "bad_request"

    def test_patch_other_users_project_is_404(self, client: TestClient) -> None:
        created = _create_project(client)
        client.user_state["user_id"] = "user-b"

        response = client.patch(
            f"/projects/{created['project_id']}", json={"name": "Hijacked"}
        )
        assert response.status_code == 404

    def test_invalid_status_is_422(self, client: TestClient) -> None:
        created = _create_project(client)
        response = client.patch(
            f"/projects/{created['project_id']}", json={"status": "deleted"}
        )
        assert response.status_code == 422


# ─── Deletion ───────────────────────────────────────────────────────────────


class TestProjectDeletion:
    def test_delete_then_get_is_404(self, client: TestClient) -> None:
        created = _create_project(client)
        response = client.delete(f"/projects/{created['project_id']}")

        assert response.status_code == 200
        assert response.json() == {
            "status": "deleted",
            "project_id": created["project_id"],
        }
        assert client.get(f"/projects/{created['project_id']}").status_code == 404
        assert client.delete(f"/projects/{created['project_id']}").status_code == 404

    def test_cross_user_delete_is_404(self, client: TestClient) -> None:
        created = _create_project(client)
        client.user_state["user_id"] = "user-b"

        assert client.delete(f"/projects/{created['project_id']}").status_code == 404

        client.user_state["user_id"] = "user-a"
        assert client.get(f"/projects/{created['project_id']}").status_code == 200

    def test_delete_cascades_to_research_sessions(self, client: TestClient) -> None:
        project = _create_project(client)
        research = _create_research(client, project["project_id"])

        client.delete(f"/projects/{project['project_id']}")
        assert client.get(f"/research/{research['research_id']}").status_code == 404


# ─── Research creation ──────────────────────────────────────────────────────


class TestResearchCreation:
    def test_create_in_owned_project(self, client: TestClient) -> None:
        project = _create_project(client)
        body = _create_research(
            client,
            project["project_id"],
            "AI diagnostics",
            provider="gemini",
            priority="high",
            notes="Initial scoping",
        )

        assert len(body["research_id"]) == 32
        assert body["project_id"] == project["project_id"]
        assert body["owner_id"] == "user-a"
        assert body["topic"] == "AI diagnostics"
        assert body["provider"] == "gemini"
        assert body["status"] == "pending"
        assert body["priority"] == "high"
        assert body["notes"] == "Initial scoping"

    def test_body_ownership_and_project_are_ignored(self, client: TestClient) -> None:
        project = _create_project(client)
        response = client.post(
            f"/projects/{project['project_id']}/research",
            json={
                "topic": "Scoped",
                "owner_id": "attacker",
                "project_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["owner_id"] == "user-a"
        assert body["project_id"] == project["project_id"]

    def test_unknown_project_is_404(self, client: TestClient) -> None:
        response = client.post(
            f"/projects/{uuid.uuid4()}/research", json={"topic": "Anything"}
        )
        assert response.status_code == 404

    def test_other_users_project_is_404(self, client: TestClient) -> None:
        project = _create_project(client)
        client.user_state["user_id"] = "user-b"

        response = client.post(
            f"/projects/{project['project_id']}/research", json={"topic": "Nope"}
        )
        assert response.status_code == 404

    def test_invalid_topic_is_422(self, client: TestClient) -> None:
        project = _create_project(client)
        response = client.post(
            f"/projects/{project['project_id']}/research", json={"topic": "ai"}
        )
        assert response.status_code == 422


# ─── Research listing ───────────────────────────────────────────────────────


class TestResearchListing:
    def test_lists_only_sessions_of_that_project(self, client: TestClient) -> None:
        first = _create_project(client, "First")
        second = _create_project(client, "Second")
        _create_research(client, first["project_id"], "One")
        _create_research(client, first["project_id"], "Two")
        _create_research(client, second["project_id"], "Three")

        body = client.get(f"/projects/{first['project_id']}/research").json()
        assert body["count"] == 2
        assert {item["topic"] for item in body["research_sessions"]} == {"One", "Two"}

    def test_pagination_and_sorting(self, client: TestClient) -> None:
        project = _create_project(client)
        for topic in ("Beta study", "Alpha study", "Gamma study"):
            _create_research(client, project["project_id"], topic)

        page = client.get(
            f"/projects/{project['project_id']}/research",
            params={"limit": 2, "offset": 1, "sort": "topic", "order": "asc"},
        ).json()

        assert page["pagination"]["total"] == 3
        assert page["pagination"]["has_more"] is False
        assert [item["topic"] for item in page["research_sessions"]] == [
            "Beta study",
            "Gamma study",
        ]

    def test_status_and_provider_filters(self, client: TestClient) -> None:
        project = _create_project(client)
        pending = _create_research(client, project["project_id"], "Pending", provider="groq")
        completed = _create_research(
            client, project["project_id"], "Done", provider="gemini"
        )
        _create_research(client, project["project_id"], "Failed", provider="gemini")

        client.patch(f"/research/{completed['research_id']}", json={"status": "completed"})

        by_status = client.get(
            f"/projects/{project['project_id']}/research", params={"status": "completed"}
        ).json()
        assert [item["research_id"] for item in by_status["research_sessions"]] == [
            completed["research_id"]
        ]

        by_provider = client.get(
            f"/projects/{project['project_id']}/research", params={"provider": "gemini"}
        ).json()
        assert by_provider["count"] == 2
        assert pending["research_id"] not in {
            item["research_id"] for item in by_provider["research_sessions"]
        }

    def test_search_and_date_filters(self, client: TestClient) -> None:
        project = _create_project(client)
        _create_research(client, project["project_id"], "Ocean warming")
        _create_research(client, project["project_id"], "Quantum chips")

        found = client.get(
            f"/projects/{project['project_id']}/research", params={"q": "ocean"}
        ).json()
        assert found["count"] == 1

        past = _iso(datetime(2000, 1, 1, tzinfo=timezone.utc))
        assert client.get(
            f"/projects/{project['project_id']}/research",
            params={"created_before": past},
        ).json()["count"] == 0

        future = _iso(datetime(2100, 1, 1, tzinfo=timezone.utc))
        invalid = client.get(
            f"/projects/{project['project_id']}/research",
            params={"created_after": future, "created_before": past},
        )
        assert invalid.status_code == 400


# ─── Research retrieval ─────────────────────────────────────────────────────


class TestResearchRetrieval:
    def test_get_own_research(self, client: TestClient) -> None:
        project = _create_project(client)
        created = _create_research(client, project["project_id"])

        response = client.get(f"/research/{created['research_id']}")
        assert response.status_code == 200
        assert response.json()["topic"] == created["topic"]

    def test_unknown_research_is_404(self, client: TestClient) -> None:
        response = client.get(f"/research/{uuid.uuid4().hex}")
        assert response.status_code == 404

    def test_malformed_research_id_is_422(self, client: TestClient) -> None:
        assert client.get("/research/not-a-research-id").status_code == 422

    def test_other_users_research_is_404(self, client: TestClient) -> None:
        project = _create_project(client)
        created = _create_research(client, project["project_id"])
        client.user_state["user_id"] = "user-b"

        assert client.get(f"/research/{created['research_id']}").status_code == 404


# ─── Research updates and deletion ─────────────────────────────────────────


class TestResearchUpdates:
    def test_patch_updates_fields(self, client: TestClient) -> None:
        project = _create_project(client)
        created = _create_research(client, project["project_id"])

        response = client.patch(
            f"/research/{created['research_id']}",
            json={"topic": "Updated topic", "status": "running", "notes": "Started"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["topic"] == "Updated topic"
        assert body["status"] == "running"
        assert body["notes"] == "Started"
        assert body["updated_at"] >= body["created_at"]

    def test_empty_patch_is_rejected(self, client: TestClient) -> None:
        project = _create_project(client)
        created = _create_research(client, project["project_id"])

        response = client.patch(f"/research/{created['research_id']}", json={})
        assert response.status_code == 400

    def test_cross_user_patch_is_404(self, client: TestClient) -> None:
        project = _create_project(client)
        created = _create_research(client, project["project_id"])
        client.user_state["user_id"] = "user-b"

        response = client.patch(
            f"/research/{created['research_id']}", json={"topic": "Hijacked"}
        )
        assert response.status_code == 404


class TestResearchDeletion:
    def test_delete_then_get_is_404(self, client: TestClient) -> None:
        project = _create_project(client)
        created = _create_research(client, project["project_id"])

        response = client.delete(f"/research/{created['research_id']}")
        assert response.status_code == 200
        assert response.json()["status"] == "deleted"
        assert client.get(f"/research/{created['research_id']}").status_code == 404

    def test_cross_user_delete_is_404(self, client: TestClient) -> None:
        project = _create_project(client)
        created = _create_research(client, project["project_id"])
        client.user_state["user_id"] = "user-b"

        assert client.delete(f"/research/{created['research_id']}").status_code == 404


# ─── Authentication sweep ───────────────────────────────────────────────────


class TestAuthenticationRequired:
    @pytest.mark.parametrize(
        "method,path,kwargs",
        [
            ("get", "/projects", {}),
            ("post", "/projects", {"json": {"name": "No auth"}}),
            ("get", f"/projects/{uuid.uuid4()}", {}),
            ("patch", f"/projects/{uuid.uuid4()}", {"json": {"name": "No auth"}}),
            ("delete", f"/projects/{uuid.uuid4()}", {}),
            ("post", f"/projects/{uuid.uuid4()}/research", {"json": {"topic": "No auth"}}),
            ("get", f"/projects/{uuid.uuid4()}/research", {}),
            ("get", f"/research/{uuid.uuid4().hex}", {}),
            ("patch", f"/research/{uuid.uuid4().hex}", {"json": {"status": "completed"}}),
            ("delete", f"/research/{uuid.uuid4().hex}", {}),
        ],
    )
    def test_endpoints_require_auth(
        self, anonymous_client: TestClient, method: str, path: str, kwargs: dict
    ) -> None:
        response = getattr(anonymous_client, method)(path, **kwargs)
        assert response.status_code == 401, response.text
