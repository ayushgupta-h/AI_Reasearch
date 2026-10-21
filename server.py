"""
Root entrypoint proxy for Research AI FastAPI server.
Allows starting the backend directly from the project root using:
    uvicorn server:app --port 8000
or:
    python server.py
"""

from __future__ import annotations

import sys
import importlib.util
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Explicitly load backend/server.py
_backend_server_file = _BACKEND_DIR / "server.py"
_spec = importlib.util.spec_from_file_location("backend_server", _backend_server_file)
_backend_mod = importlib.util.module_from_spec(_spec)
sys.modules["backend_server"] = _backend_mod
_spec.loader.exec_module(_backend_mod)

app = _backend_mod.app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
