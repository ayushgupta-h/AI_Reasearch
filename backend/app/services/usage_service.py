"""
app/services/usage_service.py
=============================
Usage and cost tracking for LLM invocations.

Records are produced by :class:`UsageRecorder` from real provider responses
(token counts passed by the caller), or by
:func:`UsageService.record_usage`. Missing token counts are recorded as ``0``
and cost is then ``0`` — the module never invents token counts. Aggregate
queries run through :class:`app.repositories.model_usage.ModelUsageRepository`
which scopes every read by ``user_id``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from app.errors import AuthenticationError, NotFoundError
from app.observability import get_logger
from app.pricing import get_pricing
from app.repositories.model_usage import ModelUsageRepository

logger = get_logger("app.services.usage")


def _utc_iso(value: Optional[datetime] = None) -> str:
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


class UsageRecorder:
    """Records usage rows bound to one research session/user context."""

    def __init__(self, repository: ModelUsageRepository, research_id: str,
                 user_id: str, agent_name: Optional[str] = None) -> None:
        self._repository = repository
        self._research_id = research_id
        self._user_id = user_id
        self._agent = agent_name

    def record(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: Optional[float] = None,
        request_status: str = "success",
        error_code: Optional[str] = None,
        occurred_at: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Record one usage row. Never invents token counts."""
        try:
            input_tokens = max(int(input_tokens or 0), 0)
            output_tokens = max(int(output_tokens or 0), 0)
            total_tokens = input_tokens + output_tokens
            cost = get_pricing().estimate_cost(
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            return self._repository.record_usage(
                research_id=self._research_id,
                user_id=self._user_id,
                agent_name=self._agent,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                duration_ms=duration_ms,
                estimated_cost_usd=cost,
                request_status=request_status,
                error_code=error_code,
                occurred_at=_utc_iso(occurred_at),
            )
        except Exception:  # telemetry must never break the pipeline
            logger.exception("usage.record_failed")
            return None

    def fallback(
        self, *, from_provider: str, to_provider: str,
        reason: Optional[str] = None,
        occurred_at: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            return self._repository.record_fallback(
                research_id=self._research_id,
                user_id=self._user_id,
                from_provider=from_provider,
                to_provider=to_provider,
                reason=reason,
                occurred_at=_utc_iso(occurred_at),
            )
        except Exception:
            logger.exception("usage.fallback_record_failed")
            return None


class UsageService:
    def __init__(
        self,
        repository: ModelUsageRepository,
        *,
        run_repository: Optional[Any] = None,
    ) -> None:
        self._repository = repository
        self._runs = run_repository

    # ── Recording entry points ────────────────────────────────────────────

    def recorder_for(
        self,
        research_id: str,
        user_id: Optional[str],
        agent_name: Optional[str] = None,
    ) -> Optional[UsageRecorder]:
        if not user_id:
            return None
        return UsageRecorder(
            self._repository, research_id, user_id, agent_name
        )

    def record_usage(self, *, research_id: str, user_id: str, **kwargs: Any) -> Optional[dict]:
        if not user_id:
            return None
        return self.recorder_for(research_id, user_id).record(**kwargs)

    def record_fallback(self, *, research_id: str, user_id: str, **kwargs: Any) -> Optional[dict]:
        if not user_id:
            return None
        return self.recorder_for(research_id, user_id).fallback(**kwargs)

    # ── Read API ──────────────────────────────────────────────────────────

    def summary(self, user_id: str) -> Dict[str, Any]:
        self._require_auth(user_id)
        data = self._repository.user_summary(user_id)
        return {
            "user_id": user_id,
            "total_requests": data["requests"],
            "total_input_tokens": data["input_tokens"],
            "total_output_tokens": data["output_tokens"],
            "total_tokens": data["total_tokens"],
            "total_estimated_cost_usd": data["estimated_cost_usd"],
            "provider_failure_count": data["failures"],
            "fallback_count": data["fallbacks"],
            "per_research": data["per_research"],
            "per_provider": data["per_provider"],
            "per_agent": data["per_agent"],
            "estimated": True,
        }

    def research_usage(self, research_id: str, user_id: str) -> Dict[str, Any]:
        self._require_research(research_id, user_id)
        rows = self._repository.list_research_usage(research_id, user_id)
        summary = self._repository.research_summary(research_id, user_id)
        return {
            "research_id": research_id,
            "user_id": user_id,
            "requests": summary["requests"],
            "input_tokens": summary["input_tokens"],
            "output_tokens": summary["output_tokens"],
            "total_tokens": summary["total_tokens"],
            "estimated_cost_usd": summary["estimated_cost_usd"],
            "failures": summary["failures"],
            "fallbacks": summary["fallbacks"],
            "usage": rows,
            "per_provider": summary["per_provider"],
            "per_agent": summary["per_agent"],
            "estimated": True,
        }

    def providers(self, user_id: str) -> Dict[str, Any]:
        self._require_auth(user_id)
        return {
            "user_id": user_id,
            "providers": self._repository.providers_summary(user_id),
            "estimated": True,
        }

    def models(self, user_id: str) -> Dict[str, Any]:
        self._require_auth(user_id)
        return {
            "user_id": user_id,
            "models": self._repository.models_summary(user_id),
            "estimated": True,
        }

    # ── Helpers ───────────────────────────────────────────────────────────

    def _require_auth(self, user_id: str) -> str:
        user = (user_id or "").strip()
        if not user:
            raise AuthenticationError("Authentication required.")
        return user

    def _require_research(self, research_id: str, user_id: str) -> Mapping[str, Any]:
        user = self._require_auth(user_id)
        self._repository.ensure_schema()
        if self._runs is not None:
            run = self._runs.get_run(research_id, user)
            if run is None:
                raise NotFoundError("Research session not found.")
            return run
        # Without a run repository, ownership is enforced by scoping every read
        # on user_id (the repository layer), not by an explicit existence check.
        return {}


__all__ = ["UsageRecorder", "UsageService"]
