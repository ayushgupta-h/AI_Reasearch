"""Agent execution schema: one node run inside the LangGraph pipeline."""

from __future__ import annotations

from typing import Dict, Optional
from uuid import UUID, uuid4

from pydantic import Field, StringConstraints, model_validator
from typing import Annotated

from app.schemas.base import MediumText, OutputSchema, UtcDatetime, utc_now
from app.schemas.enums import (
    AgentExecutionStatus,
    AgentName,
    ErrorCode,
    LLMProvider,
)

MetadataKey = Annotated[str, StringConstraints(min_length=1, max_length=40)]
MetadataValue = Annotated[str, StringConstraints(max_length=200)]


class AgentExecution(OutputSchema):
    """Telemetry for a single agent stage execution."""

    execution_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    agent: AgentName
    status: AgentExecutionStatus = AgentExecutionStatus.PENDING
    started_at: UtcDatetime = Field(default_factory=utc_now)
    finished_at: Optional[UtcDatetime] = None
    duration_ms: Optional[float] = Field(None, ge=0, le=86_400_000)
    provider: Optional[LLMProvider] = None
    model: Optional[str] = Field(None, min_length=1, max_length=120)
    input_tokens: Optional[int] = Field(None, ge=0)
    output_tokens: Optional[int] = Field(None, ge=0)
    iterations: int = Field(0, ge=0, le=50)
    error_code: Optional[ErrorCode] = None
    error_message: Optional[MediumText] = None
    metadata: Dict[MetadataKey, MetadataValue] = Field(default_factory=dict, max_length=20)

    @model_validator(mode="after")
    def _validate_execution(self) -> "AgentExecution":
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must be greater than or equal to started_at")
        if self.status == AgentExecutionStatus.FAILED and not self.error_code:
            raise ValueError("failed executions must include an error_code")
        return self


__all__ = ["AgentExecution"]
