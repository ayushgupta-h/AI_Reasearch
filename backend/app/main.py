"""
app/main.py
===========
Application entry point for the new modular package layout.

Usage
-----
From the backend/ directory:

    # Development
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

    # Production
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2

The ``app`` instance is also importable by Gunicorn, Railway, Render, etc.::

    gunicorn app.main:app -k uvicorn.workers.UvicornWorker

Relationship to server.py
--------------------------
``server.py`` (the original flat-module entry point) continues to work as a
compatibility shim.  New deployments should point uvicorn at ``app.main:app``
instead.
"""

from __future__ import annotations

from app.factory import create_app

# This module-level ``app`` is the ASGI callable uvicorn / gunicorn bind to.
app = create_app()

