"""
tests/test_monitoring_dashboard.py
==================================
Unit and integration tests for the Infrastructure Monitoring & Observability Dashboard.
"""

import pytest
from fastapi.testclient import TestClient

from app.factory import create_app
from app.services.monitoring_service import MonitoringService


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_monitoring_overview_endpoint(client: TestClient):
    """Verify GET /monitoring/overview returns all sections with real structure."""
    res = client.get("/monitoring/overview")
    assert res.status_code == 200
    data = res.json()

    assert "overview" in data
    assert "infrastructure" in data
    assert "providers" in data
    assert "search_sources" in data
    assert "research_execution" in data
    assert "agent_stages" in data
    assert "api_performance" in data
    assert "llm_usage" in data
    assert "storage" in data
    assert "recent_events" in data

    # 1. Overview checks
    ov = data["overview"]
    assert ov["status"] in ("operational", "degraded", "partial_outage", "unavailable", "unknown")
    assert "version" in ov
    assert "environment" in ov
    assert ov["uptime_seconds"] >= 0
    assert "uptime_human" in ov
    assert ov["process_id"] > 0
    assert "python_version" in ov
    assert "platform" in ov

    # 2. Infrastructure cards check (all 6 required services)
    infra = {c["id"]: c for c in data["infrastructure"]}
    assert "fastapi" in infra
    assert "postgres" in infra
    assert "redis" in infra
    assert "celery" in infra
    assert "chromadb" in infra
    assert "storage" in infra

    # Confirm FastAPI is operational
    assert infra["fastapi"]["status"] == "operational"

    # Confirm ChromaDB is operational or not_configured
    assert infra["chromadb"]["status"] in ("operational", "not_configured", "unavailable")

    # Confirm storage reports real disk information
    assert infra["storage"]["status"] in ("operational", "degraded")
    assert "disk_total_gb" in infra["storage"]["details"]
    assert "disk_free_gb" in infra["storage"]["details"]

    # 3. AI Providers check
    provs = {p["provider"]: p for p in data["providers"]}
    assert "gemini" in provs
    assert "groq" in provs
    assert "mistral" in provs
    assert "huggingface" in provs

    for p in provs.values():
        assert p["fallback_rank"] in (1, 2, 3, 4)
        assert p["reachability"] in ("available", "reachable", "unavailable", "not_probed", "missing_credentials")
        assert len(p["default_model"]) > 0

    # 4. Search sources check
    sources = {s["id"]: s for s in data["search_sources"]}
    assert "arxiv" in sources
    assert "wikipedia" in sources
    assert "duckduckgo" in sources
    assert "tavily" in sources
    assert "google_scholar" in sources
    assert "newsapi" in sources
    assert "huggingface" in sources
    assert "pubmed" in sources
    assert "semantic_scholar" in sources

    # 5. Research execution checks
    rex = data["research_execution"]
    assert rex["total_sessions"] >= 0
    assert rex["completed_sessions"] >= 0
    assert rex["active_sessions"] >= 0
    assert isinstance(rex["celery_configured"], bool)

    # 6. Agent stages check (all 14 LangGraph instrumented nodes)
    stages = [s["stage_id"] for s in data["agent_stages"]]
    assert "intake_node" in stages
    assert "search_node" in stages
    assert "reader_node" in stages
    assert "analyst_node" in stages
    assert "claims_node" in stages
    assert "writer_node" in stages
    assert "critic_node" in stages
    assert "refiner_node" in stages
    assert "exporter_node" in stages
    assert len(stages) >= 11

    # 7. API performance metrics
    api_m = data["api_performance"]
    assert api_m["total_requests"] >= 0
    assert api_m["average_latency_ms"] >= 0

    # 8. Storage breakdown
    stor = data["storage"]
    assert "postgres" in stor
    assert "chromadb" in stor
    assert "local_storage" in stor
    assert stor["local_storage"]["read_write_verified"] is True


def test_monitoring_sub_endpoints(client: TestClient):
    """Verify individual sub-endpoints under /monitoring."""
    res_sys = client.get("/monitoring/system")
    assert res_sys.status_code == 200
    assert "uptime_seconds" in res_sys.json()

    res_infra = client.get("/monitoring/infrastructure")
    assert res_infra.status_code == 200
    assert len(res_infra.json()) == 6

    res_prov = client.get("/monitoring/providers")
    assert res_prov.status_code == 200
    assert len(res_prov.json()) == 4

    res_sources = client.get("/monitoring/search-sources")
    assert res_sources.status_code == 200
    assert len(res_sources.json()) == 9

    res_exec = client.get("/monitoring/research-executions")
    assert res_exec.status_code == 200

    res_agents = client.get("/monitoring/agent-metrics")
    assert res_agents.status_code == 200
    assert len(res_agents.json()) >= 11

    res_api = client.get("/monitoring/api-performance")
    assert res_api.status_code == 200

    res_usage = client.get("/monitoring/usage")
    assert res_usage.status_code == 200

    res_stor = client.get("/monitoring/storage")
    assert res_stor.status_code == 200

    res_ev = client.get("/monitoring/events?limit=10")
    assert res_ev.status_code == 200
    assert isinstance(res_ev.json(), list)


def test_health_endpoints_backward_compatibility(client: TestClient):
    """Verify /health and /health/live still function with uptime_seconds."""
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["uptime_seconds"] is not None
    assert data["uptime_seconds"] >= 0

    res_live = client.get("/health/live")
    assert res_live.status_code == 200
    assert res_live.json()["uptime_seconds"] >= 0
