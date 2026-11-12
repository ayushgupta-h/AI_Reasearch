"""
app/config.py
=============
Centralised, validated configuration for the Research AI Quest backend.

All environment variables are read here (and only here). Every other module
imports settings instead of calling ``os.getenv()`` directly.

Two layers are exposed:

1. ``Settings`` — an immutable snapshot built by ``Settings.from_env()``. Use
   ``get_settings()`` for the process-wide instance. This is the modern API and
   is what the service/API layers consume.
2. Module-level constants (``REPORTS_DIR``, ``PROVIDER_ORDER``, ...) — kept for
   backwards compatibility with existing flat modules and scripts.

Secrets are never hardcoded. Development may run with missing optional secrets
(warnings are logged); production refuses to start without them.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Optional, Sequence

from app.errors import ConfigurationError

__all__ = [
    "Settings",
    "get_settings",
    "load_env",
    "BASE_DIR",
    "PROJECT_ROOT",
    "REPORTS_DIR",
    "EXPORTS_DIR",
    "CHROMA_DIR",
    "SESSIONS_DB",
    "CHROMA_KEY_FILE",
    "PROVIDER_ORDER",
    "DEFAULT_LLM_PROVIDER",
    "GATEWAY_ENABLED",
    "GATEWAY_CONFIG",
    "GATEWAY_MODEL",
    "API_VERSION",
    "SERVER_HOST",
    "SERVER_PORT",
    "ALLOWED_ORIGINS",
    "SHARE_SECRET",
    "DEFAULT_MAX_REVISIONS",
    "DEFAULT_APPROVAL_THRESHOLD",
]

APP_VERSION = "1.0.0"
PROVIDER_ORDER: tuple[str, ...] = ("groq", "gemini", "mistral", "huggingface")
VALID_ENVIRONMENTS = ("development", "staging", "production", "test")

# ── Path constants ───────────────────────────────────────────────────────────

BASE_DIR: Path = Path(__file__).resolve().parent.parent      # backend/
PROJECT_ROOT: Path = BASE_DIR.parent                         # repository root
REPORTS_DIR: Path = BASE_DIR / "reports"
EXPORTS_DIR: Path = REPORTS_DIR / "exports"
CHROMA_DIR: Path = BASE_DIR / "chroma_db"
SESSIONS_DB: Path = BASE_DIR / "sessions.db"
CHROMA_KEY_FILE: Path = BASE_DIR / "chroma.key"


# ── .env bootstrap (exactly once per process) ───────────────────────────────


def load_env() -> None:
    """Load ``.env`` files once, without overriding already-set variables.

    Order matters: ``backend/.env`` wins over the repository-root ``.env`` so
    the backend keeps its existing precedence.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv is a declared dependency
        return
    load_dotenv(BASE_DIR / ".env", override=False)
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    load_dotenv(override=False)


load_env()


# ── Typed env helpers ────────────────────────────────────────────────────────


def _get(env: Mapping[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = env.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return default


def _get_int(
    env: Mapping[str, str], name: str, default: int, minimum: int = 1, maximum: int = 10_000
) -> int:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _get_bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = env.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


# ── Settings ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Settings:
    """Immutable configuration snapshot."""

    # Runtime
    app_env: str = "development"
    debug: bool = False
    api_version: str = APP_VERSION
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # Paths
    reports_dir: Path = REPORTS_DIR
    exports_dir: Path = EXPORTS_DIR
    chroma_dir: Path = CHROMA_DIR
    sessions_db_path: Path = SESSIONS_DB
    chroma_key_path: Path = CHROMA_KEY_FILE

    # HTTP / CORS
    allowed_origins: tuple[str, ...] = (
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:8123",
        "http://127.0.0.1:8123",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    )
    cors_allow_credentials: bool = False

    # Logging
    log_level: str = "INFO"
    log_format: str = "console"          # "console" | "json"
    log_file: Optional[Path] = None

    # Pipeline defaults
    default_provider: str = "groq"
    max_revisions: int = 2
    approval_threshold: int = 8

    # Document uploads
    max_document_bytes: int = 20 * 1024 * 1024

    # Auth
    supabase_url: str = ""
    supabase_anon_key: str = field(default="", repr=False)
    supabase_jwt_secret: str = field(default="", repr=False)
    supabase_jwks_endpoint: str = ""
    auth_cache_ttl_seconds: int = 60

    # Report sharing
    share_secret: str = field(default="", repr=False)
    share_token_ttl_hours: int = 168

    # LLM providers (secrets are excluded from repr)
    groq_api_key: str = field(default="", repr=False)
    gemini_api_key: str = field(default="", repr=False)
    mistral_api_key: str = field(default="", repr=False)
    hf_token: str = field(default="", repr=False)
    groq_model: str = "openai/gpt-oss-120b"
    gemini_model: str = "gemini-3.8-flash"
    mistral_model: str = "mistral-small-latest"
    hf_model: str = "mistral"

    # Respan Gateway (optional in-process routing for the chat workflow)
    gateway_enabled: bool = False
    gateway_config: str = ""             # path to an optional gateway.toml
    gateway_model: str = ""              # routed model, e.g. "groq/openai/gpt-oss-120b"

    # Search providers
    tavily_api_key: str = field(default="", repr=False)
    serp_api_key: str = field(default="", repr=False)
    news_api_key: str = field(default="", repr=False)

    # ChromaDB
    chroma_api_key: str = field(default="", repr=False)
    chroma_tenant: str = ""
    chroma_database: str = ""
    chroma_encryption_key: str = field(default="", repr=False)

    # PostgreSQL (primary relational store; the URL is a secret and is
    # excluded from the dataclass repr so it never appears in logs).
    database_url: str = field(default="", repr=False)

    # Redis + Celery (TASK 2.2). Broker/result URLs are secrets and are
    # excluded from repr. ``celery_broker_url`` falls back to ``redis_url``
    # when only the latter is set.
    redis_url: str = field(default="", repr=False)
    celery_broker_url: str = field(default="", repr=False)
    celery_result_backend: str = field(default="", repr=False)
    celery_max_retries: int = 3
    celery_retry_backoff: bool = True
    celery_retry_backoff_max: int = 300
    celery_task_time_limit: int = 1800
    celery_task_soft_time_limit: int = 1500

    # Distributed research event transport (Redis Streams -> SSE).
    event_stream_maxlen: int = 10_000
    event_stream_ttl_seconds: int = 86_400
    event_stream_replay_limit: int = 1_000
    event_stream_read_count: int = 100
    event_stream_block_ms: int = 1_000

    # ── Constructors ─────────────────────────────────────────────────────

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        source: Mapping[str, str] = os.environ if env is None else env

        raw_env = _get(source, "APP_ENV", "ENVIRONMENT", "ENV", default="development").lower()
        if raw_env in {"dev", "local"}:
            raw_env = "development"
        if raw_env in {"prod", "live"}:
            raw_env = "production"
        if raw_env not in VALID_ENVIRONMENTS:
            raw_env = "development"

        origins = _split_csv(
            _get(
                source,
                "ALLOWED_ORIGINS",
                default=(
                    "http://localhost:5500,http://127.0.0.1:5500,"
                    "http://localhost:8123,http://127.0.0.1:8123,"
                    "http://localhost:3000,http://127.0.0.1:3000"
                ),
            )
        )

        raw_provider = _get(source, "LLM_PROVIDER", default="groq").lower()
        # The gateway is a transport in front of the same vendors, so naming it
        # as the provider is shorthand for enabling it.
        gateway_enabled = _get_bool(
            source, "GATEWAY_ENABLED", default=raw_provider == "gateway"
        )
        if raw_provider == "gateway":
            raw_provider = "groq"
        elif raw_provider not in PROVIDER_ORDER:
            raw_provider = "groq"

        log_format = _get(source, "LOG_FORMAT", default="console").lower()
        if log_format not in {"console", "json"}:
            log_format = "console"

        log_level = _get(source, "LOG_LEVEL", default="INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            log_level = "INFO"

        log_file_raw = _get(source, "LOG_FILE", default="")
        log_file = Path(log_file_raw).expanduser() if log_file_raw else None

        reports_dir = Path(
            _get(source, "REPORTS_DIR", default=str(REPORTS_DIR))
        ).expanduser()

        settings = cls(
            app_env=raw_env,
            debug=_get_bool(source, "DEBUG", default=raw_env == "development"),
            server_host=_get(source, "SERVER_HOST", default="0.0.0.0"),
            server_port=_get_int(source, "SERVER_PORT", 8000, 1, 65535),
            reports_dir=reports_dir,
            exports_dir=reports_dir / "exports",
            chroma_dir=Path(_get(source, "CHROMA_DIR", default=str(CHROMA_DIR))),
            sessions_db_path=Path(
                _get(source, "SESSIONS_DB_PATH", "SESSIONS_DB", default=str(SESSIONS_DB))
            ),
            chroma_key_path=Path(
                _get(source, "CHROMA_KEY_PATH", default=str(CHROMA_KEY_FILE))
            ),
            allowed_origins=origins or ("http://localhost:3000",),
            cors_allow_credentials=False,
            log_level=log_level,
            log_format=log_format,
            log_file=log_file,
            default_provider=raw_provider,
            max_revisions=_get_int(source, "DEFAULT_MAX_REVISIONS", 2, 0, 5),
            approval_threshold=_get_int(source, "DEFAULT_APPROVAL_THRESHOLD", 8, 1, 10),
            max_document_bytes=_get_int(
                source, "MAX_DOCUMENT_BYTES", 20 * 1024 * 1024, 1_024, 104_857_600
            ),
            supabase_url=(
                _get(source, "SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL").rstrip("/")
            ),
            supabase_anon_key=_get(
                source,
                "SUPABASE_ANON_KEY",
                "SUPABASE_PUBLISHABLE_KEY",
                "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
            ),
            supabase_jwt_secret=_get(source, "SUPABASE_JWT_SECRET"),
            supabase_jwks_endpoint=_get(source, "SUPABASE_JWKS_ENDPOINT", "SUPABASE_JWKS_URL"),
            auth_cache_ttl_seconds=_get_int(source, "AUTH_CACHE_TTL_SECONDS", 60, 1, 3600),
            share_secret=_get(source, "SHARE_SECRET", "CHROMA_ENCRYPTION_KEY"),
            share_token_ttl_hours=_get_int(source, "SHARE_TOKEN_TTL_HOURS", 168, 1, 8760),
            groq_api_key=_get(source, "GROQ_API_KEY"),
            gemini_api_key=_get(source, "GEMINI_API_KEY", "GOOGLE_API_KEY"),
            mistral_api_key=_get(source, "MISTRAL_API_KEY"),
            hf_token=_get(source, "HF_TOKEN", "HUGGINGFACEHUB_API_TOKEN"),
            groq_model=_get(source, "GROQ_MODEL", default="openai/gpt-oss-120b"),
            gemini_model=_get(source, "GEMINI_MODEL", default="gemini-3.8-flash"),
            mistral_model=_get(source, "MISTRAL_MODEL", default="mistral-small-latest"),
            hf_model=_get(source, "HF_MODEL", default="mistral"),
            gateway_enabled=gateway_enabled,
            gateway_config=_get(source, "GATEWAY_CONFIG", default=""),
            gateway_model=_get(source, "GATEWAY_MODEL", default=""),
            tavily_api_key=_get(source, "TAVILY_API_KEY"),
            serp_api_key=_get(source, "SERPAPI_API_KEY", "SERP_API_KEY"),
            news_api_key=_get(source, "NEWS_API_KEY"),
            chroma_api_key=_get(source, "CHROMA_API_KEY"),
            chroma_tenant=_get(source, "CHROMA_TENANT"),
            chroma_database=_get(source, "CHROMA_DATABASE"),
            chroma_encryption_key=_get(source, "CHROMA_ENCRYPTION_KEY"),
            database_url=_get(
                source,
                "DATABASE_URL",
                "DB_URL",
                "POSTGRESQL_URL",
                "POSTGRES_DATABASE_URL",
            ),
            redis_url=_get(
                source, "REDIS_URL", "REDIS_URI", "REDIS_CONNECTION_URL"
            ),
            celery_broker_url=_get(
                source,
                "CELERY_BROKER_URL",
                "BROKER_URL",
                "CELERY_BROKER",
            ),
            celery_result_backend=_get(
                source,
                "CELERY_RESULT_BACKEND",
                "RESULT_BACKEND",
                "CELERY_BACKEND",
            ),
            celery_max_retries=_get_int(
                source, "CELERY_MAX_RETRIES", 3, 0, 10
            ),
            celery_retry_backoff=_get_bool(
                source, "CELERY_RETRY_BACKOFF", True
            ),
            celery_retry_backoff_max=_get_int(
                source, "CELERY_RETRY_BACKOFF_MAX", 300, 1, 3600
            ),
            celery_task_time_limit=_get_int(
                source, "CELERY_TASK_TIME_LIMIT", 1800, 60, 7200
            ),
            celery_task_soft_time_limit=_get_int(
                source, "CELERY_TASK_SOFT_TIME_LIMIT", 1500, 60, 7200
            ),
            event_stream_maxlen=_get_int(
                source, "EVENT_STREAM_MAXLEN", 10_000, 100, 1_000_000
            ),
            event_stream_ttl_seconds=_get_int(
                source, "EVENT_STREAM_TTL_SECONDS", 86_400, 60, 604_800
            ),
            event_stream_replay_limit=_get_int(
                source, "EVENT_STREAM_REPLAY_LIMIT", 1_000, 1, 10_000
            ),
            event_stream_read_count=_get_int(
                source, "EVENT_STREAM_READ_COUNT", 100, 1, 500
            ),
            event_stream_block_ms=_get_int(
                source, "EVENT_STREAM_BLOCK_MS", 1_000, 0, 60_000
            ),
        )

        if settings.app_env == "production" and settings.debug:
            # Never allow debug defaults to leak into production.
            settings = cls(**{**settings.__dict__, "debug": False})
        return settings

    # ── Derived properties ───────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env in {"development", "test"}

    @property
    def share_secret_bytes(self) -> bytes:
        return self.share_secret.encode("utf-8")

    @property
    def cors_wildcard(self) -> bool:
        return "*" in self.allowed_origins

    def configured_providers(self) -> tuple[str, ...]:
        """Providers whose credentials are configured, in fallback order."""
        available = {
            "groq": bool(self.groq_api_key),
            "gemini": bool(self.gemini_api_key),
            "mistral": bool(self.mistral_api_key),
            "huggingface": bool(self.hf_token),
        }
        return tuple(p for p in PROVIDER_ORDER if available[p])

    def provider_api_key(self, provider: str) -> str:
        return {
            "groq": self.groq_api_key,
            "gemini": self.gemini_api_key,
            "mistral": self.mistral_api_key,
            "huggingface": self.hf_token,
            "hf": self.hf_token,
        }.get(provider, "")

    def model_for(self, provider: str) -> str:
        return {
            "groq": self.groq_model,
            "gemini": self.gemini_model,
            "mistral": self.mistral_model,
            "huggingface": self.hf_model,
            "hf": self.hf_model,
        }.get(provider, "")

    @property
    def gateway_ready(self) -> bool:
        """True when the gateway has a provider to route to."""
        return self.gateway_enabled and bool(
            self.gateway_config or self.configured_providers()
        )

    @property
    def gateway_model_name(self) -> str:
        """Model name the gateway routes on (``vendor/model`` by default)."""
        if self.gateway_model:
            return self.gateway_model
        vendor = (
            self.default_provider if self.default_provider in PROVIDER_ORDER else "groq"
        )
        model = self.model_for(vendor)
        return f"{vendor}/{model}" if model else vendor

    # ── Validation ───────────────────────────────────────────────────────

    def validate(self, *, strict: Optional[bool] = None) -> list[str]:
        """Check required configuration.

        Returns a list of human-readable problems for non-fatal issues.
        Raises :class:`ConfigurationError` when ``strict`` is true and a fatal
        problem exists. ``strict`` defaults to ``is_production``.
        """
        if strict is None:
            strict = self.is_production

        fatal: list[str] = []
        warnings: list[str] = []

        if not self.configured_providers() and not self.gateway_ready:
            fatal.append(
                "No LLM provider API key is configured "
                "(set GROQ_API_KEY, GEMINI_API_KEY, MISTRAL_API_KEY or HF_TOKEN)."
            )
        if self.gateway_enabled and not self.gateway_ready:
            warnings.append(
                "GATEWAY_ENABLED is set but no provider key or GATEWAY_CONFIG is "
                "available — gateway requests will fail until one is configured."
            )

        if not self.share_secret:
            fatal.append(
                "SHARE_SECRET (or CHROMA_ENCRYPTION_KEY) is required to sign report "
                "share links. Generate one with: python -c \"import secrets; "
                "print(secrets.token_urlsafe(32))\""
            )

        if not self.supabase_url:
            warnings.append(
                "SUPABASE_URL is not set — /auth/config returns an empty URL and "
                "JWT validation cannot reach Supabase."
            )
        if not self.supabase_anon_key:
            warnings.append("SUPABASE_ANON_KEY is not set — Supabase Auth API checks may fail.")
        if not self.tavily_api_key:
            warnings.append("TAVILY_API_KEY is not set — Tavily web search will be skipped.")

        if self.cors_wildcard and self.cors_allow_credentials:
            warnings.append(
                "ALLOWED_ORIGINS contains '*' together with credentialed CORS; "
                "wildcard origins are served without credentials."
            )
        if self.is_production and self.cors_wildcard:
            warnings.append(
                "ALLOWED_ORIGINS contains '*' in production — restrict it to known "
                "frontend origins."
            )

        if strict and fatal:
            raise ConfigurationError(" ".join(fatal))

        return warnings + fatal

    def ensure_directories(self) -> None:
        for path in (self.reports_dir, self.exports_dir, self.chroma_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings snapshot (cached)."""
    return Settings.from_env()


# Pre-import Windows UTF-8 fix, applied once.
if sys.platform.startswith("win"):  # pragma: no cover - platform specific
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ── Backwards-compatible module-level constants ──────────────────────────────

_SETTINGS = get_settings()

API_VERSION: str = _SETTINGS.api_version
SERVER_HOST: str = _SETTINGS.server_host
SERVER_PORT: int = _SETTINGS.server_port
ALLOWED_ORIGINS: list[str] = list(_SETTINGS.allowed_origins)

DEFAULT_LLM_PROVIDER: str = _SETTINGS.default_provider
GROQ_API_KEY: Optional[str] = _SETTINGS.groq_api_key or None
GEMINI_API_KEY: Optional[str] = _SETTINGS.gemini_api_key or None
MISTRAL_API_KEY: Optional[str] = _SETTINGS.mistral_api_key or None
HF_TOKEN: Optional[str] = _SETTINGS.hf_token or None

GROQ_MODEL: str = _SETTINGS.groq_model
GEMINI_MODEL: str = _SETTINGS.gemini_model
MISTRAL_MODEL: str = _SETTINGS.mistral_model
HF_MODEL: str = _SETTINGS.hf_model

GATEWAY_ENABLED: bool = _SETTINGS.gateway_enabled
GATEWAY_CONFIG: str = _SETTINGS.gateway_config
GATEWAY_MODEL: str = _SETTINGS.gateway_model_name

TAVILY_API_KEY: Optional[str] = _SETTINGS.tavily_api_key or None
SERPAPI_API_KEY: Optional[str] = _SETTINGS.serp_api_key or None
NEWS_API_KEY: Optional[str] = _SETTINGS.news_api_key or None

CHROMA_API_KEY: Optional[str] = _SETTINGS.chroma_api_key or None
CHROMA_TENANT: Optional[str] = _SETTINGS.chroma_tenant or None
CHROMA_DATABASE: Optional[str] = _SETTINGS.chroma_database or None
CHROMA_KEY_PATH: Path = _SETTINGS.chroma_key_path
CHROMA_ENCRYPTION_KEY: Optional[str] = _SETTINGS.chroma_encryption_key or None
CHROMA_CLOUD_ENABLED: bool = bool(
    CHROMA_API_KEY and CHROMA_TENANT and CHROMA_DATABASE
)

RAG_CHUNK_SIZE: int = _get_int(os.environ, "RAG_CHUNK_SIZE", 800)
RAG_CHUNK_OVERLAP: int = _get_int(os.environ, "RAG_CHUNK_OVERLAP", 100, 0, 4000)
RAG_TOP_K: int = _get_int(os.environ, "RAG_TOP_K", 6)

SUPABASE_URL: str = _SETTINGS.supabase_url
SUPABASE_ANON_KEY: str = _SETTINGS.supabase_anon_key
SUPABASE_JWT_SECRET: str = _SETTINGS.supabase_jwt_secret
SUPABASE_JWKS_ENDPOINT: str = _SETTINGS.supabase_jwks_endpoint
AUTH_CACHE_TTL_SECONDS: int = _SETTINGS.auth_cache_ttl_seconds

SHARE_SECRET: bytes = _SETTINGS.share_secret_bytes
SHARE_TOKEN_TTL_HOURS: int = _get_int(os.environ, "SHARE_TOKEN_TTL_HOURS", 168)

DEFAULT_MAX_REVISIONS: int = _SETTINGS.max_revisions
DEFAULT_APPROVAL_THRESHOLD: int = _SETTINGS.approval_threshold

# PostgreSQL connection string read from the environment. Never logged; use
# ``settings.database_url`` instead of this raw string in new code.
DATABASE_URL: str = _SETTINGS.database_url
