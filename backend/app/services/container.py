"""
app/services/container.py
========================
Dependency injection container and FastAPI dependency providers.

Services are created lazily and cached on ``app.state``, so a single
``SessionManager`` (and therefore one SQLite configuration) is shared across
requests instead of being instantiated per handler.

Infrastructure defaults to the local adapters in :mod:`app.infrastructure`.
Any of them can be replaced without touching routes or services::

    ServiceContainer(settings, cache=MyRedisCache(), database=MyPostgresDb())

The container owns no connections until a property is first used.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Request

from app.config import Settings, get_settings
from app.events import (
    EventJournal,
    ResearchStreamService,
    build_event_journal,
    build_event_reader,
)
from app.infrastructure.local_cache import LocalCacheService
from app.infrastructure.local_events import InProcessEventBus
from app.infrastructure.local_queue import LocalJobQueue
from app.infrastructure.local_storage import LocalObjectStorage
from app.infrastructure.ports import (
    CacheService,
    Database,
    EventBus,
    JobQueue,
    ObjectStorage,
    ResearchExecutor,
)
from app.infrastructure.research_executor import LocalResearchExecutor
from app.infrastructure.sqlite_database import SQLiteDatabase
from app.repositories.agent_history import AgentHistoryRepository
from app.repositories.documents import DocumentRepository
from app.repositories.projects import ProjectRepository
from app.repositories.reports import ReportRepository
from app.services.agent_history_service import AgentHistoryService
from app.services.chat_service import ChatService
from app.services.document_service import DocumentService
from app.services.project_service import ProjectService
from app.services.report_management_service import ReportManagementService
from app.services.report_service import ReportService
from app.services.research_service import ResearchService
from app.services.session_service import SessionService
from app.services.storage_service import SecureLocalStorageService, StorageService


class ServiceContainer:
    """Lazy service registry bound to one application instance."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        cache: Optional[CacheService] = None,
        database: Optional[Database] = None,
        job_queue: Optional[JobQueue] = None,
        object_storage: Optional[ObjectStorage] = None,
        events: Optional[EventBus] = None,
        research_executor: Optional[ResearchExecutor] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._sessions: Optional[SessionService] = None
        self._reports: Optional[ReportService] = None
        self._chat: Optional[ChatService] = None
        self._research: Optional[ResearchService] = None
        self._projects: Optional[ProjectService] = None
        self._agent_history: Optional[AgentHistoryService] = None
        self._documents: Optional[DocumentService] = None
        self._report_management: Optional[ReportManagementService] = None
        self._storage_service: Optional[StorageService] = None
        self._event_journal: Optional[EventJournal] = None
        self._event_reader: Optional[object] = None
        self._research_stream: Optional[ResearchStreamService] = None

        # Injected ports take precedence over the lazy local defaults.
        self._cache: Optional[CacheService] = cache
        self._database: Optional[Database] = database
        self._jobs: Optional[JobQueue] = job_queue
        self._storage: Optional[ObjectStorage] = object_storage
        self._events: Optional[EventBus] = events
        self._research_executor: Optional[ResearchExecutor] = research_executor

    # ── Application services ──────────────────────────────────────────────

    @property
    def sessions(self) -> SessionService:
        if self._sessions is None:
            from session_manager import SessionManager

            self._sessions = SessionService(
                SessionManager(str(self.settings.sessions_db_path))
            )
        return self._sessions

    @property
    def reports(self) -> ReportService:
        if self._reports is None:
            self._reports = ReportService(self.settings, self.sessions)
        return self._reports

    @property
    def chat(self) -> ChatService:
        if self._chat is None:
            self._chat = ChatService(self.settings)
        return self._chat

    @property
    def research(self) -> ResearchService:
        if self._research is None:
            self._research = ResearchService(
                self.settings,
                self.sessions,
                self.research_executor,
                history=self.agent_history,
                reports=self.report_management,
            )
        return self._research

    @property
    def report_management(self) -> ReportManagementService:
        if self._report_management is None:
            self._report_management = ReportManagementService(
                ReportRepository(self.database),
                self.storage,
                self.projects,
                self.agent_history,
                self.reports,
            )
        return self._report_management

    @property
    def projects(self) -> ProjectService:
        if self._projects is None:
            self._projects = ProjectService(
                ProjectRepository(self.database), self.agent_history
            )
        return self._projects

    @property
    def documents(self) -> DocumentService:
        if self._documents is None:
            self._documents = DocumentService(
                DocumentRepository(self.database),
                self.storage,
                self.projects,
                self.agent_history,
                max_bytes=self.settings.max_document_bytes,
            )
        return self._documents

    @property
    def agent_history(self) -> AgentHistoryService:
        if self._agent_history is None:
            self._agent_history = AgentHistoryService(
                AgentHistoryRepository(self.database)
            )
        return self._agent_history

    @property
    def event_journal(self) -> EventJournal:
        if self._event_journal is None:
            self._event_journal = build_event_journal(self.settings)
        return self._event_journal

    @property
    def event_reader(self) -> Optional[object]:
        if self._event_reader is None:
            self._event_reader = build_event_reader(self.settings)
        return self._event_reader

    @property
    def research_stream(self) -> ResearchStreamService:
        if self._research_stream is None:
            self._research_stream = ResearchStreamService(
                self.event_reader,  # type: ignore[arg-type]
                self.agent_history,
                self.event_journal,
                self.settings,
            )
        return self._research_stream

    @property
    def storage_service(self) -> StorageService:
        if self._storage_service is None:
            self._storage_service = SecureLocalStorageService(
                storage=self.storage,
                documents=DocumentRepository(self.database),
                reports=ReportRepository(self.database),
                projects=self.projects,
                max_document_bytes=self.settings.max_document_bytes,
            )
        return self._storage_service

    # ── Infrastructure ports ──────────────────────────────────────────────

    @property
    def cache(self) -> CacheService:
        if self._cache is None:
            self._cache = LocalCacheService()
        return self._cache

    @property
    def database(self) -> Database:
        if self._database is None:
            self._database = SQLiteDatabase(self.settings.sessions_db_path)
        return self._database

    @property
    def jobs(self) -> JobQueue:
        if self._jobs is None:
            self._jobs = LocalJobQueue()
        return self._jobs

    @property
    def storage(self) -> ObjectStorage:
        if self._storage is None:
            # Kept beside reports so local artifacts stay in one place; the
            # directory is created only on first upload.
            self._storage = LocalObjectStorage(self.settings.reports_dir / "objects")
        return self._storage

    @property
    def events(self) -> EventBus:
        if self._events is None:
            self._events = InProcessEventBus()
        return self._events

    @property
    def research_executor(self) -> ResearchExecutor:
        if self._research_executor is None:
            self._research_executor = LocalResearchExecutor(
                recorder_factory=self.agent_history.recorder_for
            )
        return self._research_executor

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Release local resources; safe to call more than once.

        Only adapters that own threads or files need teardown. Injected ports
        are left to their owner.
        """
        if isinstance(self._jobs, LocalJobQueue):
            self._jobs.close()


def get_container(request: Request) -> ServiceContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        container = ServiceContainer(get_settings())
        request.app.state.container = container
    return container


# ── FastAPI dependencies ─────────────────────────────────────────────────────


def get_session_service(request: Request) -> SessionService:
    return get_container(request).sessions


def get_report_service(request: Request) -> ReportService:
    return get_container(request).reports


def get_chat_service(request: Request) -> ChatService:
    return get_container(request).chat


def get_research_service(request: Request) -> ResearchService:
    return get_container(request).research


def get_project_service(request: Request) -> ProjectService:
    return get_container(request).projects


def get_agent_history_service(request: Request) -> AgentHistoryService:
    return get_container(request).agent_history


def get_document_service(request: Request) -> DocumentService:
    return get_container(request).documents


def get_report_management_service(request: Request) -> ReportManagementService:
    return get_container(request).report_management


def get_research_stream_service(request: Request) -> ResearchStreamService:
    return get_container(request).research_stream
