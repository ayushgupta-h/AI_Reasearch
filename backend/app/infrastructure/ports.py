"""
app/infrastructure/ports.py
===========================
Dependency-inversion contracts for the application's infrastructure edges.

Services depend on these structural ``Protocol`` types; concrete adapters are
injected at the application boundary. Nothing here imports a database driver,
cache client, broker or object-store SDK, so today's local implementations can
be swapped for managed services without touching ``app.services`` or
``app.api``.

The local adapters under :mod:`app.infrastructure` are deliberately honest
single-process mocks: they implement the same contract but make no attempt to
pretend they are Redis, PostgreSQL, Celery or S3.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any,
    BinaryIO,
    Callable,
    ContextManager,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    runtime_checkable,
)


# ─── Cache ───────────────────────────────────────────────────────────────────


@runtime_checkable
class CacheService(Protocol):
    """String-keyed cache with optional per-entry TTL."""

    def get(self, key: str) -> Optional[str]:
        """Return the cached value, or ``None`` when absent or expired."""

    def set(self, key: str, value: str, ttl_seconds: Optional[float] = None) -> None:
        """Store *value*; ``ttl_seconds=None`` means no expiry."""

    def delete(self, key: str) -> bool:
        """Remove *key*, returning whether it was present."""

    def exists(self, key: str) -> bool:
        """True when *key* holds a live (non-expired) value."""


# ─── Job queue ───────────────────────────────────────────────────────────────


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_JOB_STATUSES = frozenset(
    {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
)


@dataclass
class JobRecord:
    """Snapshot of one queued job."""

    id: str
    name: str
    status: JobStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    result: Any = None
    cancel_requested: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_JOB_STATUSES


@dataclass(frozen=True)
class JobContext:
    """Passed to a job handler so long-running work can observe cancellation."""

    job_id: str
    cancel_event: threading.Event

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()


JobHandler = Callable[[JobContext, Mapping[str, Any]], Any]


@runtime_checkable
class JobQueue(Protocol):
    """Named-handler job queue with status inspection and cancellation.

    ``cancel`` is cooperative: it always sets the job's cancellation event and
    returns ``True`` only when the job had not started and was removed from the
    queue. Running handlers are expected to poll ``JobContext.cancelled``.
    """

    def register(self, name: str, handler: JobHandler) -> None:
        """Bind *handler* to the job name used by ``enqueue``."""

    def enqueue(self, name: str, payload: Optional[Mapping[str, Any]] = None) -> str:
        """Submit a job, returning its identifier."""

    def get_status(self, job_id: str) -> JobRecord:
        """Return a snapshot; raises ``KeyError`` for an unknown id."""

    def cancel(self, job_id: str) -> bool:
        """Request cancellation; ``True`` only if the job never started."""


# ─── Object storage ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    content_type: str
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SignedUrl:
    """Time-limited, method-scoped reference to one object."""

    url: str
    key: str
    expires_at: datetime


@runtime_checkable
class ObjectStorage(Protocol):
    """Content-addressed-agnostic blob store keyed by relative object paths."""

    def upload(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        content_type: str = "application/octet-stream",
        metadata: Optional[Mapping[str, str]] = None,
    ) -> StoredObject:
        """Write an object, replacing any previous value at *key*."""

    def download(self, key: str) -> bytes:
        """Return the object's bytes; raises ``FileNotFoundError`` when absent."""

    def delete(self, key: str) -> bool:
        """Remove the object, returning whether it existed."""

    def exists(self, key: str) -> bool:
        """True when an object is stored at *key*."""

    def generate_signed_url(
        self, key: str, *, expires_in: float = 3600, method: str = "GET"
    ) -> SignedUrl:
        """Mint a read URL that expires after *expires_in* seconds."""


# ─── Events ──────────────────────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Event:
    """An immutable event envelope."""

    name: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    occurred_at: datetime = field(default_factory=_utcnow)


EventHandler = Callable[[Event], None]
Unsubscribe = Callable[[], None]


@runtime_checkable
class EventPublisher(Protocol):
    def publish(self, name: str, payload: Optional[Mapping[str, Any]] = None) -> Event:
        """Publish an event and return the envelope that was dispatched."""


@runtime_checkable
class EventSubscriber(Protocol):
    def subscribe(self, name: str, handler: EventHandler) -> Unsubscribe:
        """Register *handler* for *name* (``"*"`` for all); returns unsubscribe."""


@runtime_checkable
class EventBus(EventPublisher, EventSubscriber, Protocol):
    """A publisher and subscriber sharing one transport."""


# ─── Application database ────────────────────────────────────────────────────


@runtime_checkable
class DatabaseTransaction(Protocol):
    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a statement inside the transaction; returns affected rows."""

    def fetch_one(
        self, sql: str, params: Sequence[Any] = ()
    ) -> Optional[Mapping[str, Any]]:
        """Return the first row as a mapping, or ``None``."""

    def fetch_all(
        self, sql: str, params: Sequence[Any] = ()
    ) -> list[Mapping[str, Any]]:
        """Return every row as mappings."""


@runtime_checkable
class Database(Protocol):
    """Transactional SQL boundary; implementations own driver specifics."""

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run one statement in its own transaction."""

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> int:
        """Run one statement for every parameter tuple."""

    def fetch_one(
        self, sql: str, params: Sequence[Any] = ()
    ) -> Optional[Mapping[str, Any]]:
        """Return the first row as a mapping, or ``None``."""

    def fetch_all(
        self, sql: str, params: Sequence[Any] = ()
    ) -> list[Mapping[str, Any]]:
        """Return every row as mappings."""

    def transaction(self) -> ContextManager[DatabaseTransaction]:
        """Context manager that commits on success and rolls back on error."""

    def healthcheck(self) -> bool:
        """Best-effort connectivity probe; never raises."""


# ─── Research execution ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResearchExecutionRequest:
    """Everything an executor needs to run one research topic."""

    topic: str
    provider: str
    max_revisions: int = 2
    approval_threshold: int = 8
    output_dir: str = "reports"
    # Set by the research service so execution history can be recorded against
    # the same id that the SSE stream exposes.
    research_id: Optional[str] = None


@runtime_checkable
class ResearchExecutor(Protocol):
    """Runs the research pipeline and returns its final state mapping."""

    def execute(
        self,
        request: ResearchExecutionRequest,
        *,
        user_id: Optional[str] = None,
    ) -> Mapping[str, Any]:
        """Execute *request*; ``user_id`` is available for future attribution."""


__all__ = [
    "CacheService",
    "Database",
    "DatabaseTransaction",
    "Event",
    "EventBus",
    "EventHandler",
    "EventPublisher",
    "EventSubscriber",
    "JobContext",
    "JobHandler",
    "JobQueue",
    "JobRecord",
    "JobStatus",
    "ObjectStorage",
    "ResearchExecutionRequest",
    "ResearchExecutor",
    "SignedUrl",
    "StoredObject",
    "TERMINAL_JOB_STATUSES",
    "Unsubscribe",
]
