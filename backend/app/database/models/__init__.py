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
    ReviewComment,
    ReviewRequest,
)
from app.database.models.research import (  # noqa: E402,F401
    ClaimEvidence,
    ResearchClaim,
    ResearchContradiction,
    ResearchEvidence,
    ResearchSource,
)

__all__ = [
    "AgentExecution",
    "AuditLog",
    "Base",
    "ClaimEvidence",
    "ModelUsage",
    "OwnershipMixin",
    "Project",
    "ReportVersion",
    "ResearchClaim",
    "ResearchContradiction",
    "ResearchEventRecord",
    "ResearchEvidence",
    "ResearchPlan",
    "ResearchRunStatus",
    "ResearchReport",
    "ResearchSession",
    "ResearchSource",
    "ResearchTask",
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
