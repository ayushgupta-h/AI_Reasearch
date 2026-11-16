"""Provider status schema: non-sensitive capability/availability metadata."""

from __future__ import annotations

from typing import List, Optional

from pydantic import Field

from app.schemas.base import OutputSchema
from app.schemas.enums import LLMProvider, ProviderCapability, ProviderState


class ProviderStatus(OutputSchema):
    """Public description of one configured LLM provider.

    Contains no credentials — only whether configuration is present, which
    capabilities the provider is used for, and (when actually probed) the
    last availability result.
    """

    provider: LLMProvider
    model: str = Field(..., min_length=1, max_length=120)
    state: ProviderState = ProviderState.READY
    capabilities: List[ProviderCapability] = Field(
        default_factory=lambda: [ProviderCapability.CHAT, ProviderCapability.RESEARCH],
        min_length=1,
        max_length=4,
    )
    fallback_rank: int = Field(..., ge=1, le=10)
    # Availability is only populated when a check actually ran (see HealthService).
    # ``None`` means "configured but not probed" — never a lie about reachability.
    available: Optional[bool] = Field(
        None, description="True if a live availability check succeeded; null if not probed."
    )
    supported_models: List[str] = Field(
        default_factory=list, max_length=20, min_length=0
    )
    last_check: Optional[str] = Field(
        None, description="ISO-8601 UTC timestamp of the last successful availability probe."
    )
