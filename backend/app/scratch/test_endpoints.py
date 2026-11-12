"""
Quick smoke-test for the new app.main ASGI app.
Run from backend/ with:
    python app/scratch/test_endpoints.py
"""
import sys
import os

# Allow running from anywhere
BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VENV_SITE = os.path.join(BACKEND, "..", ".venv", "Lib", "site-packages")
sys.path.insert(0, os.path.normpath(VENV_SITE))
sys.path.insert(0, BACKEND)

# Change to backend/ so relative config paths resolve
os.chdir(BACKEND)

print("Importing app.main ...")
from app.main import app  # noqa: E402
print("OK — app title:", app.title)

print("Setting up TestClient ...")
from fastapi.testclient import TestClient  # noqa: E402
client = TestClient(app, raise_server_exceptions=True)

PASS = []
FAIL = []

def check(name, r, expected_status=200, body_contains=None):
    ok = r.status_code == expected_status
    if body_contains:
        ok = ok and body_contains in r.text
    symbol = "✅" if ok else "❌"
    print(f"  {symbol} {name}: HTTP {r.status_code}  {r.text[:200]}")
    (PASS if ok else FAIL).append(name)

print("\n── GET /health ──")
check("health", client.get("/health"))

print("\n── GET /providers ──")
check("providers", client.get("/providers"))

print("\n── GET /auth/config ──")
check("auth/config", client.get("/auth/config"))

print("\n── GET /sessions (no auth) ──")
check("sessions-no-auth", client.get("/sessions"))

print("\n── GET /reports/nonexistent.md (no auth) ──")
check("report-no-auth", client.get("/reports/nonexistent.md"), expected_status=401)

print("\n── POST /research (no body) ──")
check("research-bad-body", client.post("/research", json={}), expected_status=422)

print("\n── POST /chat (no body) ──")
check("chat-bad-body", client.post("/chat", json={}), expected_status=422)

print(f"\n{'='*50}")
print(f"PASSED: {len(PASS)}  FAILED: {len(FAIL)}")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
print("All smoke tests passed.")

