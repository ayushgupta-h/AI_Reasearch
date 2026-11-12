"""
app/infrastructure/local_queue.py
=================================
In-process thread-pool job queue.

This adapter exists so the application can depend on the
:class:`app.infrastructure.ports.JobQueue` contract today. Jobs run in worker
threads of the current process; nothing is durable, distributed or retried.
Cancellation is cooperative: handlers receive a :class:`JobContext` and should
poll ``ctx.cancelled`` (or wait on ``ctx.cancel_event``) during long work.
"""

from __future__ import annotations

import dataclasses
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Dict, Mapping, Optional

from app.infrastructure.ports import (
    JobContext,
    JobHandler,
    JobRecord,
    JobStatus,
    TERMINAL_JOB_STATUSES,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LocalJobQueue:
    """Thread-pool queue implementing the ``JobQueue`` port.

    ``close`` stops accepting work and joins running handlers; queued jobs are
    marked cancelled rather than executed.
    """

    def __init__(
        self,
        *,
        max_workers: int = 4,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self._clock = clock
        self._max_workers = max_workers
        self._lock = threading.RLock()
        self._handlers: Dict[str, JobHandler] = {}
        self._records: Dict[str, JobRecord] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._done: Dict[str, threading.Event] = {}
        self._futures: Dict[str, Future] = {}
        self._pool: Optional[ThreadPoolExecutor] = None
        self._closed = False

    # ── JobQueue ──────────────────────────────────────────────────────────

    def register(self, name: str, handler: JobHandler) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("Job name must be a non-empty string")
        if not callable(handler):
            raise TypeError("Job handler must be callable")
        with self._lock:
            if name in self._handlers:
                raise ValueError(f"A handler is already registered for '{name}'")
            self._handlers[name] = handler

    def enqueue(self, name: str, payload: Optional[Mapping[str, Any]] = None) -> str:
        with self._lock:
            if self._closed:
                raise RuntimeError("The job queue has been closed")
            handler = self._handlers.get(name)
            if handler is None:
                raise KeyError(f"No handler registered for job '{name}'")

            job_id = uuid.uuid4().hex
            record = JobRecord(
                id=job_id,
                name=name,
                status=JobStatus.QUEUED,
                created_at=self._clock(),
            )
            self._records[job_id] = record
            self._cancel_events[job_id] = threading.Event()
            self._done[job_id] = threading.Event()
            future = self._ensure_pool_locked().submit(
                self._run, job_id, handler, dict(payload or {})
            )
            self._futures[job_id] = future
            return job_id

    def get_status(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise KeyError(f"Unknown job '{job_id}'")
            return dataclasses.replace(record)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise KeyError(f"Unknown job '{job_id}'")
            if record.status in TERMINAL_JOB_STATUSES:
                return False

            record.cancel_requested = True
            self._cancel_events[job_id].set()

            future = self._futures.get(job_id)
            if record.status is JobStatus.QUEUED and future is not None and future.cancel():
                record.status = JobStatus.CANCELLED
                record.finished_at = self._clock()
                self._done[job_id].set()
                return True
            # Already running: the handler must observe the cancellation event.
            return False

    # ── Extras ────────────────────────────────────────────────────────────

    def wait(self, job_id: str, timeout: Optional[float] = None) -> JobRecord:
        """Block until the job finishes (or *timeout* elapses), then snapshot."""
        with self._lock:
            done = self._done.get(job_id)
            if done is None:
                raise KeyError(f"Unknown job '{job_id}'")
        done.wait(timeout)
        return self.get_status(job_id)

    def close(self) -> None:
        """Stop accepting jobs; queued work is cancelled, running work joins."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for record in self._records.values():
                if record.status is JobStatus.QUEUED:
                    record.status = JobStatus.CANCELLED
                    record.finished_at = self._clock()
                    self._done[record.id].set()
            pool = self._pool
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)

    # ── Execution ─────────────────────────────────────────────────────────

    def _ensure_pool_locked(self) -> ThreadPoolExecutor:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="local-job",
            )
        return self._pool

    def _run(
        self, job_id: str, handler: JobHandler, payload: Mapping[str, Any]
    ) -> None:
        with self._lock:
            record = self._records[job_id]
            cancel_event = self._cancel_events[job_id]
            if record.status is not JobStatus.QUEUED:
                return
            record.started_at = self._clock()
            record.status = JobStatus.RUNNING

        try:
            result = handler(JobContext(job_id=job_id, cancel_event=cancel_event), payload)
        except Exception as exc:  # noqa: BLE001 - surfaced via JobRecord.error
            with self._lock:
                record.status = JobStatus.FAILED
                record.error = f"{type(exc).__name__}: {exc}"
        else:
            with self._lock:
                if cancel_event.is_set():
                    record.status = JobStatus.CANCELLED
                    record.result = result
                else:
                    record.status = JobStatus.SUCCEEDED
                    record.result = result
        finally:
            with self._lock:
                record.finished_at = self._clock()
                self._done[job_id].set()


__all__ = ["LocalJobQueue"]
