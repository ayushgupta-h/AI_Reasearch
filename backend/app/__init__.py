"""
app/__init__.py
===============
Research AI Quest backend application package.

Layers:
    api          HTTP routers (thin)
    services     business logic + dependency injection
    schemas      request/response models
    security     auth dependencies, share-token signing
    orchestration LangGraph factory + node instrumentation
    utils        SSE framing, thread-local stdout capture

The package initialiser intentionally performs no imports so importing a
submodule (for example from ``orchestrator.py``) can never create a cycle.
Use ``app.factory.create_app`` to build the ASGI application.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
