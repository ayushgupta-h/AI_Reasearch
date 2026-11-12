"""
worker_config.py
================
Celery worker hooks for Research AI (TASK 2.2).

Graceful shutdown policy:
* Default worker exit (SIGTERM/SIGINT) is allowed to finish the *current*
  in-flight task before terminating, because ``task_acks_late`` is on and we
  do not want to lose a half-finished research run.
* ``worker_shutdown`` is a cleanup hook (closing connection pools); it must not
  kill running tasks.

Also registers a ``task_failure`` signal handler that logs the failure at
error level without exposing credentials.
"""

from __future__ import annotations

import logging

from celery import signals

logger = logging.getLogger("research.celery.worker")


@signals.worker_shutdown.connect
def on_worker_shutdown(**kwargs) -> None:  # pragma: no cover - signal hook
    """Clean up worker-side resources (best effort)."""
    logger.info("worker.shutdown")


@signals.task_failure.connect
def on_task_failure(sender=None, task_id=None, exception=None, **kwargs) -> None:
    """Log any task failure. The exception message may contain a provider
    trace; log only its type and a redacted one-line summary."""
    name = getattr(sender, "name", "unknown")
    exc_type = type(exception).__name__ if exception is not None else "unknown"
    logger.error(
        "task.failed",
        extra={
            "task": name,
            "task_id": task_id,
            "error_type": exc_type,
        },
    )


__all__ = ["on_worker_shutdown", "on_task_failure"]
