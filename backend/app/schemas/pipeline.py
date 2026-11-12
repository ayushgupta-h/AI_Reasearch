"""
app/schemas/pipeline.py
=======================
Pipeline-facing schemas.

The authoritative definitions live with the pipeline code itself
(``query_intake.py`` and ``agent.py``); this module exposes stable aliases so
API/service code never imports LangChain-dependent modules at startup. Access is
lazy, so ``import app.schemas`` stays cheap and side-effect free.
"""

from __future__ import annotations

from typing import Any

_LAZY = {
    "QueryPlan": ("query_intake", "ResearchRequest"),
    "QueryValidation": ("query_intake", "QueryValidation"),
    "CriticEvaluation": ("agent", "CriticEvaluation"),
}

__all__ = list(_LAZY)


def __getattr__(name: str) -> Any:  # PEP 562 lazy module attributes
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value  # cache for subsequent lookups
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
