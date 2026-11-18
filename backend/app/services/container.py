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
from app.orchestration.pipeline import build_checkpointer
from app.repositories.agent_history import AgentHistoryRepository
from app.repositories.documents import DocumentRepository
from app.repositories.projects import ProjectRepository
from app.repositories.reports import ReportRepository
from app.repositories.reviews import (
    NullReviewRepository,
    ReviewRepository,
    build_review_repository,
)
from app.repositories.model_usage import ModelUsageRepository
from app.repositories.model_usage import ModelUsageRepository
from app.repositories.completeness import (
    CompletenessRepository,
    NullCompletenessRepository,
    build_completeness_repository,
)
from app.repositories.contradictions import (
    ContradictionRepository,
    NullContradictionRepository,
    build_contradiction_repository,
)
from app.repositories.claims import (
    ClaimRepository,
    NullClaimRepository,
    build_claim_repository,
)
from app.repositories.sources import (
    NullSourceRepository,
    SourceRepository,
    build_source_repository,
)
from app.repositories.notifications import (
    NullNotificationRepository,
    NotificationRepository,
    build_notification_repository,
)
from app.repositories.source_credibility import (
    NullSourceCredibilityRepository,
    SourceCredibilityRepository,
    build_source_credibility_repository,
)
from app.services.agent_history_service import AgentHistoryService
from app.services.health_service import HealthService
from app.services.source_service import SourceService
from app.services.chat_service import ChatService
from app.services.contradiction_detection import ContradictionDetectionService
from app.services.document_service import DocumentService
from app.services.document_chat_service import DocumentChatService
from app.services.report_comparison_service import ReportComparisonService
from app.services.project_service import ProjectService
from app.services.report_management_service import ReportManagementService
from app.services.report_generation_service import ReportGenerationService
from app.services.report_service import ReportService
from app.services.research_service import ResearchService
from app.services.review_service import ResearchReviewService
from app.services.session_service import SessionService
from app.services.source_credibility import SourceCredibilityService
from app.services.usage_service import UsageService
from app.services.storage_service import SecureLocalStorageService, StorageService
from app.services.research_memory_service import ResearchMemoryService


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
        self._document_chat: Optional[DocumentChatService] = None
        self._report_management: Optional[ReportManagementService] = None
        self._storage_service: Optional[StorageService] = None
        self._report_comparison: Optional[ReportComparisonService] = None
        self._event_journal: Optional[EventJournal] = None
        self._event_reader: Optional[object] = None
        self._research_stream: Optional[ResearchStreamService] = None
        self._source_credibility_service: Optional[SourceCredibilityService] = None
        self._source_credibility_repository: Optional[
            SourceCredibilityRepository | NullSourceCredibilityRepository
        ] = None
        self._contradiction_service: Optional[ContradictionDetectionService] = None
        self._contradiction_repository: Optional[
            ContradictionRepository | NullContradictionRepository
        ] = None
        self._claim_repository: Optional[
            ClaimRepository | NullClaimRepository
        ] = None
        self._completeness_repository: Optional[
            CompletenessRepository | NullCompletenessRepository
        ] = None
        self._completeness_service: Optional[ResearchCompletenessEvaluator] = None
        self._source_repository: Optional[
            SourceRepository | NullSourceRepository
        ] = None
        self._source_service: Optional[SourceService] = None
        self._notification_repository: Optional[
            NotificationRepository | NullNotificationRepository
        ] = None
        self._notification_service: Optional[NotificationService] = None
        self._memory_service: Optional[ResearchMemoryService] = None
        self._review_repository: Optional[
            ReviewRepository | NullReviewRepository
        ] = None
        self._review_service: Optional[ResearchReviewService] = None
        self._report_generator: Optional[ReportGenerationService] = None
        self._usage: Optional[UsageService] = None
        self._health: Optional[HealthService] = None
        self._monitoring: Optional[Any] = None

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
    def document_chat(self) -> DocumentChatService:
        if self._document_chat is None:
            self._document_chat = DocumentChatService(
                repository=DocumentRepository(self.database),
                document_service=self.documents,
                chat_service=self.chat,
            )
        return self._document_chat

    @property
    def report_comparison(self) -> ReportComparisonService:
        if self._report_comparison is None:
            self._report_comparison = ReportComparisonService(
                settings=self.settings,
                chat_service=self.chat,
                document_service=self.documents,
                session_service=self.sessions,
                report_service=self.reports,
            )
        return self._report_comparison

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
    def source_credibility_service(self) -> SourceCredibilityService:
        if self._source_credibility_service is None:
            self._source_credibility_service = SourceCredibilityService()
        return self._source_credibility_service

    @property
    def source_credibility_repository(
        self,
    ) -> SourceCredibilityRepository | NullSourceCredibilityRepository:
        if self._source_credibility_repository is None:
            self._source_credibility_repository = build_source_credibility_repository(
                self.settings
            )
        return self._source_credibility_repository

    @property
    def contradiction_service(self) -> ContradictionDetectionService:
        if self._contradiction_service is None:
            self._contradiction_service = ContradictionDetectionService()
        return self._contradiction_service

    @property
    def contradiction_repository(
        self,
    ) -> ContradictionRepository | NullContradictionRepository:
        if self._contradiction_repository is None:
            self._contradiction_repository = build_contradiction_repository(
                self.settings
            )
        return self._contradiction_repository

    @property
    def claim_repository(self) -> ClaimRepository | NullClaimRepository:
        if self._claim_repository is None:
            self._claim_repository = build_claim_repository(self.settings)
        return self._claim_repository

    @property
    def completeness_repository(
        self,
    ) -> CompletenessRepository | NullCompletenessRepository:
        if self._completeness_repository is None:
            self._completeness_repository = build_completeness_repository(
                self.settings
            )
        return self._completeness_repository

    @property
    def completeness_service(self) -> ResearchCompletenessEvaluator:
        if self._completeness_service is None:
            self._completeness_service = ResearchCompletenessEvaluator(
                self.settings, self.completeness_repository
            )
        return self._completeness_service

    @property
    def source_repository(self) -> SourceRepository | NullSourceRepository:
        if self._source_repository is None:
            self._source_repository = build_source_repository(self.settings)
        return self._source_repository

    @property
    def source_service(self) -> SourceService:
        if self._source_service is None:
            self._source_service = SourceService(self.settings)
        return self._source_service

    @property
    def notification_repository(
        self,
    ) -> NotificationRepository | NullNotificationRepository:
        if self._notification_repository is None:
            self._notification_repository = build_notification_repository(
                self.settings
            )
        return self._notification_repository

    @property
    def notification_service(self) -> NotificationService:
        if self._notification_service is None:
            self._notification_service = NotificationService(
                self.settings, self.events
            )
        return self._notification_service

    @property
    def memory_service(self) -> ResearchMemoryService:
        if self._memory_service is None:
            self._memory_service = ResearchMemoryService(self.settings)
        return self._memory_service

    @property
    def review_repository(self) -> ReviewRepository | NullReviewRepository:
        if self._review_repository is None:
            self._review_repository = build_review_repository(self.settings)
        return self._review_repository

    @property
    def review_service(self) -> ResearchReviewService:
        if self._review_service is None:
            self._review_service = ResearchReviewService(
                self.review_repository, self.research_executor
            )
        return self._review_service

    @property
    def usage(self) -> UsageService:
        if self._usage is None:
            self._usage = UsageService(
                ModelUsageRepository(self.database),
                run_repository=AgentHistoryRepository(self.database),
            )
        return self._usage

    @property
    def monitoring(self) -> Any:
        if self._monitoring is None:
            from app.services.monitoring_service import MonitoringService
            self._monitoring = MonitoringService(self.settings)
        return self._monitoring

    @property
    def health(self) -> HealthService:
        if self._health is None:
            self._health = HealthService(
                self.settings,
                database=self.database,
                cache=self.cache,
                jobs=self.jobs,
                storage=self.storage,
            )
        return self._health

    @property
    def report_generator(self) -> ReportGenerationService:
        if self._report_generator is None:
            self._report_generator = ReportGenerationService(self.settings)
        return self._report_generator

    @property
    def research_executor(self) -> ResearchExecutor:
        if self._research_executor is None:
            self._research_executor = LocalResearchExecutor(
                recorder_factory=self.agent_history.recorder_for,
                credibility_service=self.source_credibility_service,
                credibility_repository=self.source_credibility_repository,
                contradiction_service=self.contradiction_service,
                contradiction_repository=self.contradiction_repository,
                claim_repository=self.claim_repository,
                review_repository=self.review_repository,
                checkpointer=build_checkpointer(self.settings),
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


def get_review_service(request: Request) -> ResearchReviewService:
    return get_container(request).review_service


def get_agent_history_service(request: Request) -> AgentHistoryService:
    return get_container(request).agent_history


def get_document_service(request: Request) -> DocumentService:
    return get_container(request).documents


def get_document_chat_service(request: Request) -> DocumentChatService:
    return get_container(request).document_chat


def get_report_management_service(request: Request) -> ReportManagementService:
    return get_container(request).report_management


def get_research_stream_service(request: Request) -> ResearchStreamService:
    return get_container(request).research_stream


def get_claim_repository(request: Request):
    return get_container(request).claim_repository


def get_report_generator(request: Request):
    return get_container(request).report_generator


def get_usage_service(request: Request) -> UsageService:
    return get_container(request).usage


def get_health_service(request: Request) -> HealthService:
    return get_container(request).health


def get_source_service(request: Request) -> SourceService:
    return get_container(request).source_service


def get_completeness_service(request: Request) -> ResearchCompletenessEvaluator:
    return get_container(request).completeness_service


def get_completeness_repository(
    request: Request,
) -> CompletenessRepository | NullCompletenessRepository:
    return get_container(request).completeness_repository


def get_notification_service(request: Request) -> NotificationService:
    return get_container(request).notification_service


def get_memory_service(request: Request) -> ResearchMemoryService:
    return get_container(request).memory_service


def get_report_comparison_service(request: Request) -> ReportComparisonService:
    return get_container(request).report_comparison


def get_monitoring_service(request: Request) -> Any:
    return get_container(request).monitoring



def get_contradiction_repository(
    request: Request,
) -> ContradictionRepository | NullContradictionRepository:
    return get_container(request).contradiction_repository


def get_contradiction_service(
    request: Request,
) -> ContradictionDetectionService:
    return get_container(request).contradiction_service

