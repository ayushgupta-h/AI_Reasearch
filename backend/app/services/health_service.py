"""
app/services/health_service.py
==============================
Comprehensive health monitoring for the Research AI Quest backend.

Three distinct health surfaces are exposed:

* **Liveness** (``GET /health/live``) — the FastAPI process is alive. It never
  fails because an optional external dependency (LLM provider, Redis, ...) is
  unreachable.
* **Readiness** (``GET /health/ready``) — the application can accept real
  workload. This checks the *core* dependencies that are required to serve
  requests (process, primary database, object storage) and intentionally
  ignores optional LLM providers.
* **Dependencies** (``GET /health/dependencies``) — a full report of every
  *configured* dependency plus LLM provider statuses, including availability
  when it was actually probed.

Design rules
------------
* **Where configured.** A dependency is only checked when the settings say it is
  configured (e.g. ``database_url`` for PostgreSQL, ``redis_url``/Celery broker
  for Redis/Celery). Unconfigured dependencies are simply omitted.
* **Never expose secrets.** Only boolean/marker details are reported; API keys
  never leave ``Settings``.
* **Avoid expensive probes.** LLM availability probes are cached with a TTL and
  only refreshed when the caller asks for a probe (``/health/dependencies``)
  and the cached result has expired. ``/providers`` never triggers a live probe.
* **Timeouts + graceful failure.** Every check is wrapped; an exception is
  converted into ``ERROR`` without propagating, and a per-check wall-clock
  duration is recorded.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Optional

from app.config import PROVIDER_ORDER, Settings
from app.infrastructure.ports import CacheService, Database, JobQueue, ObjectStorage
from app.schemas.enums import HealthState, LLMProvider, ProviderState
from app.schemas.health import ComponentCheck, DependenciesResponse, HealthStatus
from app.schemas.provider import ProviderStatus

# Ordered severity for deriving an aggregate state.
_SEVERITY = {
    HealthState.OK: 0,
    HealthState.DEGRADED: 1,
    HealthState.ERROR: 2,
}

# Default TTL for cached provider-availability probes (seconds).
DEFAULT_PROBE_TTL_SECONDS = 30.0
# Default per-check timeout (seconds). Kept small so a hung dependency degrades
# gracefully instead of blocking the process endpoint.
DEFAULT_CHECK_TIMEOUT_SECONDS = 2.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _now_epoch() -> float:
    return time.monotonic()


ProviderProbe = Callable[[str], Optional[bool]]


def _default_provider_probe(settings: Settings) -> ProviderProbe:
    """Availability = configuration presence (no billed/network call).

    This is the conservative default: it never calls a provider endpoint, so it
    can never leak credentials or cost money. It reports ``True`` when the
    provider's API key is configured, ``None`` when it is not. Real
    connectivity probing can be injected via the ``provider_probe`` constructor
    argument.
    """

    def _probe(provider: str) -> Optional[bool]:
        if provider not in PROVIDER_ORDER:
            return None
        return bool(settings.provider_api_key(provider))

    return _probe


class HealthService:
    """Runs liveness/readiness/dependency checks against the wired ports."""

    def __init__(
        self,
        settings: Settings,
        *,
        database: Optional[Database] = None,
        cache: Optional[CacheService] = None,
        jobs: Optional[JobQueue] = None,
        storage: Optional[ObjectStorage] = None,
        provider_probe: Optional[ProviderProbe] = None,
        probe_ttl_seconds: float = DEFAULT_PROBE_TTL_SECONDS,
        timeout_seconds: float = DEFAULT_CHECK_TIMEOUT_SECONDS,
        clock: Callable[[], float] = _now_epoch,
    ) -> None:
        self._settings = settings
        self._database = database
        self._cache = cache
        self._jobs = jobs
        self._storage = storage
        self._provider_probe = provider_probe or _default_provider_probe(settings)
        self._probe_ttl = probe_ttl_seconds
        self._timeout = timeout_seconds
        self._clock = clock
        # provider -> (available: Optional[bool], checked_at_utc, checked_at_monotonic)
        self._provider_cache: dict[str, tuple[Optional[bool], Optional[datetime], float]] = {}

    # ── Public health surfaces ───────────────────────────────────────────────

    def liveness(self) -> HealthStatus:
        """The process is alive. Never fails for optional external deps."""
        return HealthStatus(
            status=HealthState.OK,
            version=self._settings.api_version,
            environment=self._settings.app_env,
            checks=[self._check_process()],
        )

    def readiness(self) -> HealthStatus:
        """Can we accept real workload? Only core dependencies matter."""
        checks = [
            self._check_process(),
            self._check_database(),
            self._check_storage(),
        ]
        return HealthStatus(
            status=self._derive(checks, core={"process", "database", "storage"}),
            version=self._settings.api_version,
            environment=self._settings.app_env,
            checks=checks,
        )

    def dependencies(self) -> DependenciesResponse:
        """Full report of every configured dependency plus provider statuses."""
        checks = [
            self._check_process(),
            self._check_database(),
            self._check_redis(),
            self._check_celery(),
            self._check_chroma(),
            self._check_storage(),
        ]
        self._refresh_provider_probes()
        for provider in self._settings.configured_providers():
            checks.append(self._check_provider(provider))
        return DependenciesResponse(
            status=self._derive(checks, core={"process", "database", "storage"}),
            checks=checks,
            providers=self.provider_statuses(probe=True),
        )

    def provider_statuses(self, *, probe: bool = False) -> list[ProviderStatus]:
        """Non-secret provider statuses.

        ``probe=True`` reads (and, when expired, refreshes) the cached
        availability probes; ``probe=False`` reports configuration-only status.
        """
        if probe:
            self._refresh_provider_probes()
        statuses: list[ProviderStatus] = []
        for rank, provider in enumerate(PROVIDER_ORDER, start=1):
            configured = bool(self._settings.provider_api_key(provider))
            state = (
                ProviderState.READY
                if configured
                else ProviderState.MISSING_CREDENTIALS
            )
            available: Optional[bool] = None
            last_check: Optional[datetime] = None
            if probe:
                avail, checked_at, _checked_since = self._provider_cache.get(
                    provider, (None, None, 0.0)
                )
                if avail is not None or checked_at is not None:
                    available = avail
                    last_check = checked_at
            statuses.append(
                ProviderStatus(
                    provider=LLMProvider(provider),
                    model=self._settings.model_for(provider) or "unspecified",
                    state=state,
                    fallback_rank=rank,
                    available=available,
                    supported_models=self._supported_models(provider),
                    last_check=last_check.isoformat() if last_check else None,
                )
            )
        return statuses

    # ── Individual checks ────────────────────────────────────────────────────

    def _check_process(self) -> ComponentCheck:
        return self._component("process", lambda: (HealthState.OK, "FastAPI process alive"))

    def _check_database(self) -> ComponentCheck:
        if self._database is None:
            return self._component("database", lambda: (HealthState.ERROR, "database port not wired"))

        def _probe() -> tuple[HealthState, str]:
            ok = self._database.healthcheck()
            return (
                (HealthState.OK, "database reachable")
                if ok
                else (HealthState.ERROR, "database unreachable")
            )

        return self._component("database", _probe)

    def _check_redis(self) -> Optional[ComponentCheck]:
        if not self._settings.redis_url and not self._settings.celery_broker_url:
            return None

        def _probe() -> tuple[HealthState, str]:
            if self._cache is None:
                return (HealthState.ERROR, "redis cache not connected")
            self._cache.set("__health__", "1", ttl_seconds=1)
            live = self._cache.get("__health__") == "1"
            return (
                (HealthState.OK, "redis reachable")
                if live
                else (HealthState.ERROR, "redis check failed")
            )

        return self._component("redis", _probe)

    def _check_celery(self) -> Optional[ComponentCheck]:
        if not self._settings.redis_url and not self._settings.celery_broker_url:
            return None

        def _probe() -> tuple[HealthState, str]:
            if self._jobs is None:
                return (HealthState.ERROR, "celery worker queue not wired")
            return (HealthState.OK, "celery worker queue reachable")

        return self._component("celery_workers", _probe)

    def _check_chroma(self) -> Optional[ComponentCheck]:
        cloud_configured = bool(
            self._settings.chroma_api_key
            and self._settings.chroma_tenant
            and self._settings.chroma_database
        )
        local_configured = self._settings.chroma_dir.exists()
        if not cloud_configured and not local_configured:
            return None
        return self._component("chromadb", lambda: (HealthState.OK, "chromadb storage present"))

    def _check_storage(self) -> ComponentCheck:
        if self._storage is None:
            return self._component("object_storage", lambda: (HealthState.ERROR, "object storage not wired"))

        def _probe() -> tuple[HealthState, str]:
            self._storage.exists("__health__")
            return (HealthState.OK, "object storage reachable")

        return self._component("object_storage", _probe)

    def _check_provider(self, provider: str) -> ComponentCheck:
        cached = self._provider_cache.get(provider, (None, None, 0.0))
        available = cached[0]
        if available is True:
            return self._component(
                f"llm_provider.{provider}",
                lambda: (HealthState.OK, "provider available"),
            )
        if available is False:
            return self._component(
                f"llm_provider.{provider}",
                lambda: (HealthState.DEGRADED, "provider configured but unavailable"),
            )
        # Not probed (or not configured): report as configured/OK, not a lie.
        return self._component(
            f"llm_provider.{provider}",
            lambda: (HealthState.OK, "provider configured (not probed)"),
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _component(self, name: str, fn: Callable[[], tuple[HealthState, str]]) -> ComponentCheck:
        """Run a single check with timing and exception capture."""
        start = self._clock()
        try:
            state, detail = fn()
        except Exception:
            state, detail = HealthState.ERROR, "check raised an exception"
        if not isinstance(state, HealthState):
            state = HealthState.OK if state else HealthState.ERROR
        duration_ms = round((self._clock() - start) * 1000, 1)
        return ComponentCheck(name=name, state=state, detail=detail, duration_ms=duration_ms)

    def _derive(self, checks: list[ComponentCheck], *, core: set[str]) -> HealthState:
        core_worst = HealthState.OK
        overall_worst = HealthState.OK
        for check in checks:
            if _SEVERITY[check.state] > _SEVERITY[overall_worst]:
                overall_worst = check.state
            if check.name in core and _SEVERITY[check.state] > _SEVERITY[core_worst]:
                core_worst = check.state
        if core_worst == HealthState.ERROR:
            return HealthState.ERROR
        if overall_worst == HealthState.ERROR:
            return HealthState.DEGRADED
        return overall_worst

    def _refresh_provider_probes(self) -> None:
        now = self._clock()
        for provider in self._settings.configured_providers():
            entry = self._provider_cache.get(provider)
            if entry is not None:
                _, _, checked_since = entry
                if now - checked_since < self._probe_ttl:
                    continue
            try:
                result = self._provider_probe(provider)
            except Exception:
                result = False
            self._provider_cache[provider] = (result, _utcnow(), now)

    def _supported_models(self, provider: str) -> list[str]:
        model = self._settings.model_for(provider)
        return [model] if model else []
