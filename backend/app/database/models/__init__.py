"""
app/database/models
===================
SQLAlchemy declaration models for the PostgreSQL persistence layer.

Importing this package registers every table on ``Base.metadata`` so Alembic
autogenerate and ``create_all`` see the full schema. Relationship strings are
resolved lazily by SQLAlchemy once all modules are imported here.
"""

from __future__ import annotations

from app.database.models.base import (
    Base,
    OwnershipMixin,
    SoftDeleteMixin,
    TimestampMixin,
    active,
    fk_uuid,
    pk_uuid,
)

# Import order matters for relationship string resolution at mapper
# configuration time: domain/asset/report must be imported after research has
# defined the tables they reference. Modules are imported in dependency order.
from app.database.models.asset import AuditLog, ModelUsage, UploadedDocument  # noqa: E402,F401
from app.database.models.domain import (  # noqa: E402,F401
    AgentExecution,
    Project,
    ResearchPlan,
    ResearchSession,
    ResearchTask,
    User,
)
from app.database.models.event import (  # noqa: E402,F401
    ResearchEventRecord,
    ResearchRunStatus,
)
from app.database.models.report import (  # noqa: E402,F401
    ReportVersion,
    ResearchReport,
    ReviewAudit,
    ReviewComment,
    ReviewRequest,
)
from app.database.models.research import (  # noqa: E402,F401
    ClaimEvidence,
    ContradictionEvidence,
    ResearchClaim,
    ResearchContradiction,
    ResearchEvidence,
    ResearchSource,
)
from app.database.models.memory import (  # noqa: E402,F401
    ResearchMemory,
)
from app.database.models.notifications import (  # noqa: E402,F401
    Notification,
)
from app.database.models.completeness import (  # noqa: E402,F401
    ResearchCompletenessEvaluation,
    ResearchObjective,
)
from app.database.models.credibility import (  # noqa: E402,F401
    SourceCredibilityEvaluation,
)

__all__ = [
    "AgentExecution",
    "AuditLog",
    "Base",
    "ClaimEvidence",
    "ContradictionEvidence",
    "ModelUsage",
    "OwnershipMixin",
    "Project",
    "ReportVersion",
    "ResearchClaim",
    "ResearchCompletenessEvaluation",
    "ResearchContradiction",
    "ResearchEvidence",
    "ResearchMemory",
    "ResearchObjective",
    "ResearchPlan",
    "ResearchRunStatus",
    "ResearchReport",
    "ResearchSession",
    "ResearchSource",
    "ResearchTask",
    "SourceCredibilityEvaluation",
    "Notification",
    "ReviewAudit",
    "ReviewComment",
    "ReviewRequest",
    "SoftDeleteMixin",
    "TimestampMixin",
    "UploadedDocument",
    "User",
    "active",
    "fk_uuid",
    "pk_uuid",
]
