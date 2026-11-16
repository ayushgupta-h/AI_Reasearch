"""API schemas for model usage and cost tracking."""

from __future__ import annotations

from typing import Annotated, List, Optional

from pydantic import Field, StringConstraints

from app.schemas.base import OutputSchema, UtcDatetime

UsageId = Annotated[
    str, StringConstraints(pattern=r"^[a-f0-9]{32}$", min_length=32, max_length=32)
]
ResearchId = Annotated[
    str, StringConstraints(pattern=r"^[a-f0-9]{32}$", min_length=32, max_length=32)
]
ProviderName = Annotated[
    str, StringConstraints(min_length=1, max_length=40, strip_whitespace=True)
]
ModelName = Annotated[
    str, StringConstraints(min_length=1, max_length=120, strip_whitespace=True)
]
AgentNameText = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$", min_length=1, max_length=64)
]


class UsageRecord(OutputSchema):
    """One captured LLM invocation."""

    usage_id: UsageId
    research_id: ResearchId
    user_id: str = Field(..., min_length=1, max_length=128)
    agent_name: Optional[AgentNameText] = None
    provider: ProviderName
    model: ModelName
    input_tokens: int = Field(0, ge=0)
    output_tokens: int = Field(0, ge=0)
    total_tokens: int = Field(0, ge=0)
    duration_ms: Optional[float] = Field(None, ge=0, le=86_400_000)
    estimated_cost_usd: float = Field(0.0, ge=0)
    request_status: str = Field(..., min_length=1, max_length=20)
    occurred_at: UtcDatetime


class ProviderAggregate(OutputSchema):
    provider: ProviderName
    requests: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    failures: int = Field(ge=0)


class ModelAggregate(OutputSchema):
    provider: ProviderName
    model: ModelName
    requests: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    failures: int = Field(ge=0)


class ResearchCostBreakdown(OutputSchema):
    research_id: ResearchId
    requests: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)


class AgentCostBreakdown(OutputSchema):
    agent_name: AgentNameText
    requests: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)


class ResearchProviderBreakdown(OutputSchema):
    provider: ProviderName
    requests: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    failures: int = Field(ge=0)


class UsageSummaryResponse(OutputSchema):
    """Aggregated usage dashboard.

    ``estimated_cost_usd`` values are estimates derived from a pricing config,
    not invoices.
    """

    user_id: str = Field(..., min_length=1, max_length=128)
    total_requests: int = Field(ge=0)
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    total_estimated_cost_usd: float = Field(ge=0)
    provider_failure_count: int = Field(ge=0)
    fallback_count: int = Field(ge=0)
    per_research: List[ResearchCostBreakdown] = Field(default_factory=list)
    per_provider: List[ResearchProviderBreakdown] = Field(default_factory=list)
    per_agent: List[AgentCostBreakdown] = Field(default_factory=list)
    estimated: bool = Field(True, description="All cost fields are estimates.")


class ResearchUsageResponse(OutputSchema):
    research_id: ResearchId
    user_id: str = Field(..., min_length=1, max_length=128)
    requests: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    failures: int = Field(ge=0)
    fallbacks: int = Field(ge=0)
    usage: List[UsageRecord] = Field(default_factory=list)
    per_provider: List[ResearchProviderBreakdown] = Field(default_factory=list)
    per_agent: List[AgentCostBreakdown] = Field(default_factory=list)
    estimated: bool = Field(True, description="All cost fields are estimates.")


class ProvidersUsageResponse(OutputSchema):
    user_id: str = Field(..., min_length=1, max_length=128)
    providers: List[ProviderAggregate] = Field(default_factory=list)
    estimated: bool = Field(True, description="Cost fields are estimates.")


class ModelsUsageResponse(OutputSchema):
    user_id: str = Field(..., min_length=1, max_length=128)
    models: List[ModelAggregate] = Field(default_factory=list)
    estimated: bool = Field(True, description="Cost fields are estimates.")


class FallbackRecord(OutputSchema):
    fallback_id: UsageId
    research_id: ResearchId
    user_id: str = Field(..., min_length=1, max_length=128)
    from_provider: ProviderName
    to_provider: ProviderName
    reason: Optional[str] = Field(None, max_length=300)
    occurred_at: UtcDatetime


__all__ = [
    "AgentCostBreakdown",
    "FallbackRecord",
    "ModelAggregate",
    "ModelsUsageResponse",
    "ProviderAggregate",
    "ProvidersUsageResponse",
    "ResearchCostBreakdown",
    "ResearchProviderBreakdown",
    "ResearchUsageResponse",
    "UsageRecord",
    "UsageSummaryResponse",
]
