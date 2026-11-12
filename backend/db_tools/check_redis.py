"""
tools/check_redis.py
====================
Redis health check for Research AI (TASK 2.2).

Connects to the Redis URL from ``REDIS_URL`` / ``CELERY_BROKER_URL`` (never
printed) and verifies:
  1. TCP + auth connectivity (``PING``),
  2. read/write round-trip on a throwaway key,
  3. the configured Celery broker URL points at a reachable Redis.

Exit code 0 = healthy, 1 = unhealthy. Safe to run locally and in CI.
"""

from __future__ import annotations

import sys
from typing import Optional

from app.config import get_settings
from celery_app import resolve_broker_url


def _client_from_url(url: str):
    import redis

    return redis.Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5)


def check_redis(
    url: Optional[str] = None, *, report: bool = True
) -> bool:
    settings = get_settings()
    url = (url or settings.redis_url or settings.celery_broker_url).strip()
    if not url:
        if report:
            print("ERROR: no Redis URL configured (REDIS_URL / CELERY_BROKER_URL).")
        return False
    try:
        client = _client_from_url(url)
        ok = client.ping()
        if not ok:
            if report:
                print("ERROR: Redis PING returned false.")
            return False
        # Round-trip on a throwaway key.
        key = "research:healthcheck"
        client.set(key, "ok", ex=60)
        value = client.get(key)
        client.delete(key)
        if value != b"ok":
            if report:
                print("ERROR: Redis write/read round-trip failed.")
            return False
        if report:
            print("OK: Redis reachable, PING and round-trip passed.")
        return True
    except Exception as exc:  # noqa: BLE001 - report any connection failure
        if report:
            print(f"ERROR: Redis health check failed: {type(exc).__name__}: {exc}")
        return False


def check_broker(report: bool = True) -> bool:
    """Verify the Celery broker URL resolves to a reachable Redis."""
    url = resolve_broker_url()
    if not url:
        if report:
            print("ERROR: CELERY_BROKER_URL / REDIS_URL not configured.")
        return False
    ok = check_redis(url, report=report)
    if report and ok:
        print("OK: Celery broker (Redis) is reachable.")
    return ok


def main() -> int:
    redis_ok = check_redis()
    broker_ok = check_broker()
    return 0 if (redis_ok and broker_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
