"""OpenAPI contract tests: every documented schema must exist and resolve."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Iterator

import pytest

from app.config import get_settings
from app.factory import create_app
from app.schemas.registry import CONTRACT_SCHEMAS, REQUIRED_CONTRACT_SCHEMAS


@pytest.fixture(scope="module")
def openapi_doc() -> dict[str, Any]:
    settings = dataclasses.replace(
        get_settings(), app_env="test", log_file=None, reports_dir=Path("reports")
    )
    application = create_app(settings)
    return application.openapi()


def _iter_refs(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            yield ref.rsplit("/", 1)[1]
        for value in node.values():
            yield from _iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_refs(item)


def test_all_required_contract_schemas_are_documented(openapi_doc):
    schemas = openapi_doc["components"]["schemas"]
    missing = [name for name in REQUIRED_CONTRACT_SCHEMAS if name not in schemas]
    assert missing == [], f"missing from OpenAPI: {missing}"


def test_all_registry_schemas_are_documented(openapi_doc):
    schemas = openapi_doc["components"]["schemas"]
    missing = [name for name in CONTRACT_SCHEMAS if name not in schemas]
    assert missing == []


def test_every_component_reference_resolves(openapi_doc):
    schemas = openapi_doc["components"]["schemas"]
    unresolved = sorted({ref for ref in _iter_refs(openapi_doc) if ref not in schemas})
    assert unresolved == []


def test_domain_schemas_are_described_with_fields(openapi_doc):
    schemas = openapi_doc["components"]["schemas"]
    for name in ("Project", "ResearchTask", "Claim", "Evidence", "Contradiction", "Citation"):
        assert schemas[name]["properties"], f"{name} has no documented properties"


def test_error_contract_is_used_by_protected_endpoints(openapi_doc):
    paths = openapi_doc["paths"]
    for path, method in (
        ("/sessions/{session_id}", "delete"),
        ("/reports/{filename}", "get"),
        ("/reports/{filename}/share", "get"),
        ("/chat", "post"),
    ):
        response = paths[path][method]["responses"]
        refs = set(_iter_refs(response))
        assert {"ApiError"} & refs, f"{method.upper()} {path} does not document ApiError"


def test_sse_endpoint_documents_the_event_schema(openapi_doc):
    responses = openapi_doc["paths"]["/research"]["post"]["responses"]
    assert "ResearchEvent" in set(_iter_refs(responses))
    content = responses["200"]["content"]
    assert "text/event-stream" in content


def test_health_documents_flat_and_enveloped_variants(openapi_doc):
    responses = openapi_doc["paths"]["/health"]["get"]["responses"]
    refs = set(_iter_refs(responses))
    assert {"HealthStatus", "HealthEnvelope"} <= refs


def test_pagination_parameters_are_documented(openapi_doc):
    params = openapi_doc["paths"]["/sessions"]["get"]["parameters"]
    names = {param["name"] for param in params}
    assert {"limit", "offset", "envelope"} <= names
    limit = next(param for param in params if param["name"] == "limit")
    schema = limit["schema"]
    if "anyOf" in schema:  # Optional[int] renders bounds inside the union branch
        schema = next(
            branch for branch in schema["anyOf"] if branch.get("type") == "integer"
        )
    assert schema["maximum"] == 100
    assert schema["minimum"] == 1
