"""
tests/test_health_api.py
=========================
Unit tests for Task 4.9 health monitoring: liveness vs readiness separation,
provider availability probing (with caching and graceful failure), dependency
reporting, and the rule that no secret / API key ever appears in a response.

Dependencies (database, cache, storage, probe) are fakes — no network or API
keys are used.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.factory import create_app
from app.schemas.enums import HealthState
from app.services.container import get_health_service
from app.services.health_service import HealthService


# ── Fakes ────────────────────────────────────────────────────────────────────


class FakeDatabase:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls = 0

    def healthcheck(self) -> bool:
        self.calls += 1
        return self.ok


class FakeCache:
    def __init__(self):
        self._store: dict[str, str] = {}

    def set(self, key: str, value: str, ttl_seconds: float | None = None) -> None:
        self._store[key] = value

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def exists(self, key: str) -> bool:
        return key in self._store

    def delete(self, key: str) -> bool:
        return self._store.pop(key, None) is not None


class FakeStorage:
    def exists(self, key: str) -> bool:
        return True


class FakeJobs:
    pass


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _settings(**replace):
    base = get_settings()
    overrides = {
        "app_env": "test",
        "sessions_db_path": Path(":memory:"),
        "log_file": None,
        "groq_api_key": "",
        "gemini_api_key": "",
        "mistral_api_key": "",
        "hf_token": "",
    }
    overrides.update(replace)
    return dataclasses.replace(base, **overrides)


def _make_health(settings, *, db_ok=True, probe=None, clock=None, ttl=30.0) -> HealthService:
    return HealthService(
        settings,
        database=FakeDatabase(db_ok),
        cache=FakeCache(),
        jobs=FakeJobs(),
        storage=FakeStorage(),
        provider_probe=probe,
        probe_ttl_seconds=ttl,
        clock=clock or FakeClock(),
    )


# ── HealthService unit tests ──────────────────────────────────────────────────


class TestLiveness:
    def test_liveness_is_ok_even_when_db_is_down(self):
        hs = _make_health(_settings(), db_ok=False)
        status = hs.liveness()
        assert status.status == HealthState.OK

    def test_liveness_ignores_provider_probe_failures(self):
        def boom(_provider):
            raise RuntimeError("provider on fire")

        hs = _make_health(_settings(groq_api_key="sk-test"), probe=boom)
        assert hs.liveness().status == HealthState.OK


class TestReadiness:
    def test_ready_when_core_dependencies_ok(self):
        hs = _make_health(_settings())
        assert hs.readiness().status == HealthState.OK

    def test_not_ready_when_database_is_down(self):
        hs = _make_health(_settings(), db_ok=False)
        assert hs.readiness().status == HealthState.ERROR

    def test_readiness_is_not_affected_by_optional_provider_failure(self):
        def boom(_provider):
            raise RuntimeError("provider on fire")

        hs = _make_health(_settings(groq_api_key="sk-test"), probe=boom)
        assert hs.readiness().status == HealthState.OK


class TestDependencies:
    def test_dependencies_report_configured_components(self):
        settings = _settings(
            groq_api_key="sk-test",
            redis_url="redis://localhost:6379",
        )
        hs = _make_health(settings)
        rep = hs.dependencies()
        names = {check.name for check in rep.checks}
        assert {"process", "database", "object_storage", "redis", "celery_workers"} <= names
        assert "llm_provider.groq" in names
        # Provider statuses are present and order matches the canonical list.
        assert [p.provider.value for p in rep.providers] == [
            "groq",
            "gemini",
            "mistral",
            "huggingface",
        ]

    def test_dependencies_never_expose_api_keys(self):
        hs = _make_health(_settings(groq_api_key="sk-super-secret"))
        rep = hs.dependencies()
        serialized = str(rep.to_payload()).lower()
        assert "sk-" not in serialized
        assert "api_key" not in serialized
        assert "secret" not in serialized

    def test_unconfigured_providers_are_reported_missing_without_availability(self):
        hs = _make_health(_settings())  # no keys at all
        statuses = hs.provider_statuses(probe=True)
        for status in statuses:
            assert status.state.value == "missing_credentials"
            assert status.available is None


class TestProviderProbing:
    def test_probe_is_cached_within_ttl(self):
        calls: list[str] = []
        clock = FakeClock()

        def probe(provider: str) -> bool:
            calls.append(provider)
            return True

        hs = _make_health(
            _settings(groq_api_key="sk-test"), probe=probe, clock=clock, ttl=30.0
        )
        first = hs.provider_statuses(probe=True)
        assert first[0].available is True
        assert calls == ["groq"]

        # Within TTL: no second probe.
        hs.provider_statuses(probe=True)
        assert calls == ["groq"]

        # After TTL: probe refreshed.
        clock.advance(31.0)
        hs.provider_statuses(probe=True)
        assert calls == ["groq", "groq"]

    def test_probe_exception_is_graceful_degraded(self):
        def boom(_provider):
            raise RuntimeError("timeout")

        hs = _make_health(_settings(groq_api_key="sk-test"), probe=boom)
        statuses = hs.provider_statuses(probe=True)
        groq = next(s for s in statuses if s.provider.value == "groq")
        assert groq.available is False
        rep = hs.dependencies()
        check = next(c for c in rep.checks if c.name == "llm_provider.groq")
        assert check.state == HealthState.DEGRADED

    def test_providers_endpoint_does_not_probe(self):
        calls: list[str] = []

        def probe(provider: str) -> bool:
            calls.append(provider)
            return True

        hs = _make_health(_settings(groq_api_key="sk-test"), probe=probe)
        statuses = hs.provider_statuses(probe=False)
        # provider_statuses(probe=False) never refreshes probes.
        assert calls == []
        assert all(s.available is None for s in statuses)


# ── HTTP API tests ────────────────────────────────────────────────────────────


def _app(settings, health_service):
    app = create_app(settings)
    app.dependency_overrides[get_health_service] = lambda: health_service
    return app


class TestHealthEndpoints:
    @pytest.fixture
    def client(self):
        settings = _settings()
        hs = _make_health(settings)
        with TestClient(_app(settings, hs), raise_server_exceptions=False) as test_client:
            yield test_client

    def test_health_flat(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["version"]
        assert "checks" in body

    def test_health_envelope(self, client):
        body = client.get("/health", params={"envelope": "true"}).json()
        assert set(body) == {"success", "data", "message", "request_id"}
        assert body["data"]["status"] == "ok"

    def test_health_live(self, client):
        assert client.get("/health/live").json()["status"] == "ok"

    def test_health_ready_ok(self, client):
        assert client.get("/health/ready").status_code == 200

    def test_health_ready_503_when_db_down(self):
        settings = _settings()
        hs = _make_health(settings, db_ok=False)
        with TestClient(_app(settings, hs), raise_server_exceptions=False) as test_client:
            response = test_client.get("/health/ready")
            assert response.status_code == 503
            assert response.json()["status"] == "error"

    def test_health_dependencies_shape_and_no_secrets(self):
        settings = _settings(groq_api_key="sk-secret", redis_url="redis://x")
        hs = _make_health(settings)
        with TestClient(_app(settings, hs), raise_server_exceptions=False) as test_client:
            body = test_client.get("/health/dependencies").json()
        assert "checks" in body and "providers" in body and "status" in body
        serialized = str(body).lower()
        assert "sk-" not in serialized
        assert "api_key" not in serialized

    def test_providers_never_include_api_key(self):
        settings = _settings(groq_api_key="sk-secret")
        hs = _make_health(settings)
        with TestClient(_app(settings, hs), raise_server_exceptions=False) as test_client:
            body = test_client.get("/providers").json()
        assert "api_key" not in str(body).lower()
        assert [item["provider"] for item in body["statuses"]] == body["providers"]
