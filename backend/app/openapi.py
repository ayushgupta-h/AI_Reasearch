"""
app/openapi.py
==============
OpenAPI customization.

FastAPI only emits schemas referenced by a route. The contract registry also
contains domain models that have no endpoint yet (projects, tasks, claims, ...).
They are injected into ``components.schemas`` so ``/docs`` and the exported
``openapi.json`` document the complete data model.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI

from app.schemas.registry import CONTRACT_SCHEMAS


def inject_contract_schemas(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Merge every registry model (and its nested definitions) into the document."""
    components = schema.setdefault("components", {}).setdefault("schemas", {})

    for name, model in CONTRACT_SCHEMAS.items():
        generated = model.model_json_schema(ref_template="#/components/schemas/{model}")
        nested = generated.pop("$defs", {}) or {}
        for definition_name, definition in nested.items():
            components.setdefault(definition_name, definition)
        components.setdefault(name, generated)

    return schema


def install_contract_documentation(app: FastAPI) -> None:
    """Wrap ``app.openapi`` so generated docs include the contract registry."""
    base_openapi = app.openapi

    def openapi() -> Dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = base_openapi()
        inject_contract_schemas(schema)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
