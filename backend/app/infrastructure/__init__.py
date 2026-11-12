"""
app/infrastructure
==================
Ports and local adapters for infrastructure the application does not yet run
in production.

Import contracts from here when a service needs a database, cache, queue,
object store, event bus or research executor. Import a concrete adapter only at
the composition root (:mod:`app.services.container`).

No managed-service client is implemented in this package; local adapters are
single-process and explicitly non-production.
"""

from app.infrastructure.local_cache import LocalCacheService
from app.infrastructure.local_events import InProcessEventBus
from app.infrastructure.local_queue import LocalJobQueue
from app.infrastructure.local_storage import InvalidSignedUrl, LocalObjectStorage
from app.infrastructure.ports import (
    CacheService,
    Database,
    DatabaseTransaction,
    Event,
    EventBus,
    EventHandler,
    EventPublisher,
    EventSubscriber,
    JobContext,
    JobHandler,
    JobQueue,
    JobRecord,
    JobStatus,
    ObjectStorage,
    ResearchExecutionRequest,
    ResearchExecutor,
    SignedUrl,
    StoredObject,
    TERMINAL_JOB_STATUSES,
    Unsubscribe,
)
from app.infrastructure.research_executor import LocalResearchExecutor
from app.infrastructure.sqlite_database import SQLiteDatabase

__all__ = [
    "CacheService",
    "Database",
    "DatabaseTransaction",
    "Event",
    "EventBus",
    "EventHandler",
    "EventPublisher",
    "EventSubscriber",
    "InProcessEventBus",
    "InvalidSignedUrl",
    "JobContext",
    "JobHandler",
    "JobQueue",
    "JobRecord",
    "JobStatus",
    "LocalCacheService",
    "LocalJobQueue",
    "LocalObjectStorage",
    "LocalResearchExecutor",
    "ObjectStorage",
    "ResearchExecutionRequest",
    "ResearchExecutor",
    "SQLiteDatabase",
    "SignedUrl",
    "StoredObject",
    "TERMINAL_JOB_STATUSES",
    "Unsubscribe",
]
