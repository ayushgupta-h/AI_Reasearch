"""
Regenerate ``backend/openapi.json`` from the application factory.

Usage::

    cd backend
    python export_openapi.py

The generated document includes every model in :mod:`app.schemas.registry`
(including domain schemas not yet bound to an endpoint), the SSE event schema
and the documented error contracts. ``/docs`` and ``/openapi.json`` serve the
same document live.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.config import Settings  # noqa: E402  (path bootstrap must run first)
from app.factory import create_app  # noqa: E402


def main() -> int:
    # Docs generation must not depend on local secrets or a real LLM key.
    settings = Settings.from_env(env={"APP_ENV": "development", "LOG_LEVEL": "WARNING"})
    application = create_app(settings)
    schema = application.openapi()

    output = _BACKEND_DIR / "openapi.json"
    output.write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    component_count = len(schema.get("components", {}).get("schemas", {}))
    path_count = len(schema.get("paths", {}))
    print(f"Wrote {output} ({component_count} schemas, {path_count} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
