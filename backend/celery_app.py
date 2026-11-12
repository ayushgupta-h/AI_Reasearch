"""
celery_app.py
=============
Celery application factory for Research AI (TASK 2.2).

Redis is the broker (and optional result backend). The result backend is used
only for transient *metadata* (status, provider, paths) — it is never the
authoritative research database. Research status is persisted by the job
itself through the existing repository/service layer; reports are written to
object storage / the report store, never into Redis.

Configuration comes from ``Settings`` (``backend/.env`` / environment). URLs
are secrets and are never logged.

Worker invocation::

    cd backend
    python -m celery -A celery_app worker --loglevel=info --concurrency=4

The app registers a graceful-shutdown hook so in-flight research tasks can
finish before the worker exits (see ``worker_config.py``).
"""

from __future__ import annotations

import ssl
from typing import Optional

from celery import Celery

from app.config import Settings, get_settings

# Celery reads the broker from the app's conf; we resolve it here so the
# fallback (REDIS_URL -> CELERY_BROKER_URL) is applied in one place.
_settings = get_settings()


def resolve_broker_url(settings: Settings = _settings) -> str:
    """Resolve the broker URL with a safe Redis fallback.

    The URL is only ever used to configure the transport; it is never printed.
    """
    url = (settings.celery_broker_url or settings.redis_url).strip()
    if url:
        return url
    # No credentials here, just a localhost default for local development.
    return "redis://localhost:6379/0"


def resolve_result_backend(settings: Settings = _settings) -> str:
    """Result backend URL (Redis by default, as it is already the transport)."""
    url = settings.celery_result_backend.strip()
    if url:
        return url
    return settings.redis_url.strip() or "redis://localhost:6379/0"


def _is_tls(url: str) -> bool:
    """True when the transport URL is TLS (rediss://)."""
    return url.strip().lower().startswith("rediss://")


def _ssl_opts() -> dict:
    """Verify TLS with the system trust store (CERT_REQUIRED).

    We deliberately do NOT use CERT_NONE: the broker/backend certificate must
    be validated against the default CA bundle.
    """
    return {
        "ssl_cert_reqs": ssl.CERT_REQUIRED,
        # No ssl_ca_certs -> ssl.create_default_context() uses the OS trust store.
    }


def make_celery() -> Celery:
    broker_url = resolve_broker_url()
    backend_url = resolve_result_backend()
    app = Celery(
        "research_ai",
        broker=broker_url,
        backend=backend_url,
    )

    # ── TLS (rediss://) ───────────────────────────────────────────────────
    # When the broker or result backend uses TLS, tell Kombu/Redis to verify
    # the certificate (CERT_REQUIRED). Without this, Celery rejects the URL
    # with "must have parameter ssl_cert_reqs".
    if _is_tls(broker_url):
        app.conf.broker_use_ssl = _ssl_opts()
    if _is_tls(backend_url):
        app.conf.redis_backend_use_ssl = _ssl_opts()

    # ── Serialization ─────────────────────────────────────────────────────
    # JSON only: safe with Redis, avoids pickle-based RCE in the trusted app.
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        broker_connection_max_retries=10,
    )

    # ── Durability / retries ──────────────────────────────────────────────
    # acks_late + reject_on_worker_lost: a crashed worker re-reads the task
    # instead of silently dropping it, so research never silently vanishes.
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_acks_on_failure_or_timeout=False,
        # Defaults; research_tasks.research_task overrides per-task.
        task_time_limit=_settings.celery_task_time_limit,
        task_soft_time_limit=_settings.celery_task_soft_time_limit,
        task_max_retries=_settings.celery_max_retries,
        task_retry_backoff=_settings.celery_retry_backoff,
        task_retry_backoff_max=_settings.celery_retry_backoff_max,
        task_retry_jitter=True,
    )
    return app


celery_app = make_celery()
__all__ = ["celery_app", "make_celery", "resolve_broker_url", "resolve_result_backend"]
