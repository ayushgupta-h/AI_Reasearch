"""
backend/tests/e2e_120_users/run_locust_benchmark.py
===================================================
Orchestrates the 120-user concurrent load test:
1. Boots local FastAPI application server on 127.0.0.1:8000.
2. Verifies server readiness.
3. Runs Locust in headless mode ramping up to 120 concurrent virtual users.
4. Collects and parses performance statistics (RPS, P50, P95, P99, error rates).
5. Tears down the test server cleanly.
"""

import os
import subprocess
import sys
import time
from pathlib import Path
import urllib.request
import csv


SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8000
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
LOCUSTFILE = Path(__file__).parent / "locustfile_120_users.py"
CSV_PREFIX = Path(__file__).parent / "locust_stats"


def wait_for_server(timeout=30):
    start = time.time()
    url = f"{BASE_URL}/monitoring/overview"
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    print("Test server is ready and responsive.")
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    venv_python = sys.executable
    print(f"Starting server with: {venv_python}")

    # Launch uvicorn server
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parent.parent.parent)
    env["APP_ENV"] = "testing"

    server_proc = subprocess.Popen(
        [
            venv_python,
            "-m",
            "uvicorn",
            "server:app",
            "--host",
            SERVER_HOST,
            "--port",
            str(SERVER_PORT),
            "--log-level",
            "warning",
        ],
        cwd=str(Path(__file__).parent.parent.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        print("Waiting for server startup on http://127.0.0.1:8000...")
        if not wait_for_server():
            print("Failed to start server within timeout.")
            return

        # Run Locust headless with 120 concurrent users, spawn rate 15 users/sec
        print("Executing Locust concurrent load test (120 users, ramp stages: 10 -> 30 -> 60 -> 90 -> 120)...")
        locust_cmd = [
            venv_python,
            "-m",
            "locust",
            "-f",
            str(LOCUSTFILE),
            "--headless",
            "-u",
            "120",
            "-r",
            "15",
            "--run-time",
            "45s",
            "--host",
            BASE_URL,
            "--csv",
            str(CSV_PREFIX),
        ]

        locust_proc = subprocess.run(locust_cmd, capture_output=True, text=True)
        print("Locust execution completed.")
        print(locust_proc.stdout)
        if locust_proc.stderr:
            print(locust_proc.stderr)

        # Parse CSV results
        stats_csv = Path(str(CSV_PREFIX) + "_stats.csv")
        if stats_csv.exists():
            print("\n=== AGGREGATE LOAD TEST METRICS ===")
            with open(stats_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("Name") == "Aggregated":
                        print(f"Total Requests: {row.get('Request Count')}")
                        print(f"Failure Count: {row.get('Failure Count')}")
                        print(f"Median (P50) Response Time: {row.get('50%')} ms")
                        print(f"P95 Response Time: {row.get('95%')} ms")
                        print(f"P99 Response Time: {row.get('99%')} ms")
                        print(f"Average Response Time: {row.get('Average Response Time')} ms")
                        print(f"Requests Per Second (RPS): {row.get('Requests/s')}")

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
        print("Test server shutdown completed.")


if __name__ == "__main__":
    main()
