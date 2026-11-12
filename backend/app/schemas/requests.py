"""
app/schemas/requests.py
=======================
Request models for the public HTTP API.

These replace the ad-hoc models that lived in ``server.py``. Field names and
constraints match the existing frontend contract; new validation only rejects
values that previously produced undefined behaviour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import get_settings
from app.schemas.base import LongText, MediumText, Name
from app.schemas.enums import ProjectStatus, ResearchPriority, SessionStatus

ProviderName = Literal["groq", "gemini", "mistral", "huggingface"]
ExportFormat = Literal["pdf", "docx", "md", "txt"]
CitationStyle = Literal["apa", "mla", "harvard"]


class ResearchRunRequest(BaseModel):
    """Body accepted by ``POST /research``."""

    model_config = ConfigDict(extra="ignore")

    topic: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="The research topic or question to investigate.",
        examples=["Impact of large language models on scientific discovery"],
    )
    provider: ProviderName = Field(
        default_factory=lambda: get_settings().default_provider,  # type: ignore[arg-type]
        description="LLM provider: 'groq', 'gemini', 'mistral', or 'huggingface'.",
        examples=["groq"],
    )
    max_revisions: int = Field(
        default_factory=lambda: get_settings().max_revisions,
        ge=0,
        le=5,
        description="Maximum self-correction revision cycles (0 = no revisions).",
    )
    approval_threshold: int = Field(
        default_factory=lambda: get_settings().approval_threshold,
        ge=1,
        le=10,
        description="Critic score (out of 10) required to approve the report without revisions.",
    )
    output_dir: str = Field(
        default_factory=lambda: str(get_settings().reports_dir),
        description="Directory where the final report artifacts are saved. Must stay inside the reports directory.",
    )

    @field_validator("topic")
    @classmethod
    def _validate_topic(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("topic must contain at least 3 non-whitespace characters")
        return cleaned

    @field_validator("provider", mode="before")
    @classmethod
    def _normalise_provider(cls, value: object) -> object:
        if isinstance(value, str):
            candidate = value.strip().lower()
            return "huggingface" if candidate == "hf" else candidate
        return value

    @field_validator("output_dir")
    @classmethod
    def _validate_output_dir(cls, value: str) -> str:
        base = get_settings().reports_dir.resolve()
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
        resolved = candidate.resolve()
        if resolved != base and base not in resolved.parents:
            raise ValueError("output_dir must be inside the reports directory")
        return str(resolved)


class ChatRequest(BaseModel):
    """Body accepted by ``POST /chat``."""

    model_config = ConfigDict(extra="ignore")

    message: str = Field(..., min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def _validate_message(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("message cannot be empty")
        return cleaned


class ProjectCreateRequest(BaseModel):
    """Body accepted by ``POST /projects``.

    Ownership is never accepted from the body: ``owner_id``/``user_id`` keys
    are ignored and the JWT identity is used instead.
    """

    model_config = ConfigDict(extra="ignore")

    name: Name = Field(..., description="Human-readable project name.")
    description: Optional[LongText] = Field(None, description="Optional project description.")
    research_domain: Optional[Name] = Field(
        None, description="Research domain, e.g. 'healthcare' or 'climate'."
    )
    status: ProjectStatus = Field(ProjectStatus.ACTIVE, description="Initial project status.")


class ProjectUpdateRequest(BaseModel):
    """Body accepted by ``PATCH /projects/{project_id}``.

    Omitted fields are unchanged; an explicit ``null`` clears an optional field.
    """

    model_config = ConfigDict(extra="ignore")

    name: Optional[Name] = None
    description: Optional[LongText] = None
    research_domain: Optional[Name] = None
    status: Optional[ProjectStatus] = None


class ResearchCreateRequest(BaseModel):
    """Body accepted by ``POST /projects/{project_id}/research``."""

    model_config = ConfigDict(extra="ignore")

    topic: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Research topic or question to investigate.",
    )
    provider: ProviderName = Field(
        default_factory=lambda: get_settings().default_provider,  # type: ignore[arg-type]
        description="Preferred LLM provider for the run.",
    )
    priority: ResearchPriority = Field(
        ResearchPriority.NORMAL, description="Scheduling priority for the run."
    )
    notes: Optional[MediumText] = Field(None, description="Optional notes for the research session.")

    @field_validator("topic")
    @classmethod
    def _validate_topic(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("topic must contain at least 3 non-whitespace characters")
        return cleaned

    @field_validator("provider", mode="before")
    @classmethod
    def _normalise_provider(cls, value: object) -> object:
        if isinstance(value, str):
            candidate = value.strip().lower()
            return "huggingface" if candidate == "hf" else candidate
        return value


class ResearchUpdateRequest(BaseModel):
    """Body accepted by ``PATCH /research/{research_id}``."""

    model_config = ConfigDict(extra="ignore")

    topic: Optional[str] = Field(None, min_length=3, max_length=500)
    provider: Optional[ProviderName] = None
    status: Optional[SessionStatus] = None
    priority: Optional[ResearchPriority] = None
    notes: Optional[MediumText] = None

    @field_validator("topic")
    @classmethod
    def _validate_topic(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if len(cleaned) < 3:
            raise ValueError("topic must contain at least 3 non-whitespace characters")
        return cleaned


class ExportRequest(BaseModel):
    """Body accepted by ``POST /export``."""

    model_config = ConfigDict(extra="ignore")

    content: str = Field(
        ...,
        min_length=1,
        max_length=200_000,
        description="Draft markdown content to export.",
    )
    topic: Optional[str] = Field(
        "Research Report", max_length=200, description="Research topic or report title."
    )
    format: ExportFormat = Field("pdf", description="Target format: 'pdf', 'docx', 'md', or 'txt'.")
    citation_style: CitationStyle = Field(
        "apa", description="Citation style: 'apa', 'mla', or 'harvard'."
    )

    @field_validator("format", mode="before")
    @classmethod
    def _normalise_format(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower().lstrip(".")
        return value

    @field_validator("citation_style", mode="before")
    @classmethod
    def _normalise_citation_style(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value
