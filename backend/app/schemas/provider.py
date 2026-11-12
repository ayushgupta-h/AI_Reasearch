"""Provider status schema: non-sensitive capability/availability metadata."""

from __future__ import annotations

from typing import List

from pydantic import Field

from app.schemas.base import OutputSchema
from app.schemas.enums import LLMProvider, ProviderCapability, ProviderState


class ProviderStatus(OutputSchema):
    """Public description of one configured LLM provider.

    Contains no credentials — only whether configuration is present and which
    capabilities the provider is used for.
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
