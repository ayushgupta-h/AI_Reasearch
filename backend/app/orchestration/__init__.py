"""LangGraph orchestration boundary: factory and node instrumentation.

Kept import-light: nothing here imports LangGraph at module load, so the API
server still starts without the pipeline's optional dependencies.
"""

__all__ = ["build_orchestrator", "instrument_node"]


def build_orchestrator(*args, **kwargs):  # pragma: no cover - thin alias
    from app.orchestration.pipeline import build_orchestrator as _build

    return _build(*args, **kwargs)


def instrument_node(name, fn):  # pragma: no cover - thin alias
    from app.orchestration.instrumentation import instrument_node as _instrument

    return _instrument(name, fn)
