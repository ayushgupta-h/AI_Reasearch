"""
app/repositories/model_usage.py
================================
SQL persistence for LLM usage records and provider fallback events.

Every usage row carries ``user_id`` and ``research_id``; queries always filter
on the owner, so the storage layer enforces ownership. Rows are written only
by :class:`app.services.usage_service.UsageRecorder` (driven by real provider
responses) — counts are never synthesised by the API.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.infrastructure.ports import Database

_CREATE_USAGE = """
CREATE TABLE IF NOT EXISTS model_usage (
    usage_id           TEXT PRIMARY KEY,
    research_id        TEXT NOT NULL,
    user_id            TEXT NOT NULL,
    agent_name         TEXT,
    provider           TEXT NOT NULL,
    model              TEXT NOT NULL,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    total_tokens       INTEGER NOT NULL DEFAULT 0,
    duration_ms        REAL,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    request_status      TEXT NOT NULL,
    error_code         TEXT,
    occurred_at        TEXT NOT NULL
);
"""

_CREATE_FALLBACKS = """
CREATE TABLE IF NOT EXISTS provider_fallbacks (
    fallback_id  TEXT PRIMARY KEY,
    research_id  TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    from_provider TEXT NOT NULL,
    to_provider  TEXT NOT NULL,
    reason       TEXT,
    occurred_at  TEXT NOT NULL
);
"""

_CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_model_usage_user_time "
    "ON model_usage(user_id, occurred_at);",
    "CREATE INDEX IF NOT EXISTS idx_model_usage_research "
    "ON model_usage(research_id, user_id, occurred_at);",
    "CREATE INDEX IF NOT EXISTS idx_model_usage_provider "
    "ON model_usage(user_id, provider);",
    "CREATE INDEX IF NOT EXISTS idx_model_usage_model "
    "ON model_usage(user_id, model);",
    "CREATE INDEX IF NOT EXISTS idx_fallbacks_user "
    "ON provider_fallbacks(user_id, occurred_at);",
)

_USAGE_COLUMNS = (
    "usage_id, research_id, user_id, agent_name, provider, model, input_tokens, "
    "output_tokens, total_tokens, duration_ms, estimated_cost_usd, request_status, "
    "error_code, occurred_at"
)


class ModelUsageRepository:
    def __init__(self, database: Database) -> None:
        self._db = database
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    # ── Schema ────────────────────────────────────────────────────────────

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            self._db.execute(_CREATEATE_USAGE)
            self._db.execute(_CREATE_FALLBACKS)
            for statement in _CREATE_INDEXES:
                self._db.execute(statement)
            self._schema_ready = True

    # ── Write ─────────────────────────────────────────────────────────────

    def record_usage(
        self,
        *,
        research_id: str,
        user_id: str,
        agent_name: Optional[str],
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        duration_ms: Optional[float],
        estimated_cost_usd: float,
        request_status: str,
        error_code: Optional[str] = None,
        occurred_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure_schema()
        usage_id = uuid.uuid4().hex
        self._db.execute(
            """
            INSERT INTO model_usage (
                usage_id, research_id, user_id, agent_name, provider, model,
                input_tokens, output_tokens, total_tokens, duration_ms,
                estimated_cost_usd, request_status, error_code, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                usage_id,
                research_id,
                user_id,
                agent_name,
                provider,
                model,
                input_tokens,
                output_tokens,
                total_tokens,
                duration_ms,
                estimated_cost_usd,
                request_status,
                error_code,
                occurred_at,
            ),
        )
        row = self._db.fetch_one(
            f"SELECT {_USAGE_COLUMNS} FROM model_usage WHERE usage_id = ?",
            (usage_id,),
        )
        if row is None:  # pragma: no cover - insert just succeeded
            raise RuntimeError("Model usage insert was not readable back")
        return row

    def record_fallback(
        self,
        *,
        research_id: str,
        user_id: str,
        from_provider: str,
        to_provider: str,
        reason: Optional[str],
        occurred_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure_schema()
        fallback_id = uuid.uuid4().hex
        self._db.execute(
            """
            INSERT INTO provider_fallbacks (
                fallback_id, research_id, user_id, from_provider, to_provider,
                reason, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (fallback_id, research_id, user_id, from_provider, to_provider, reason,
             occurred_at),
        )
        row = self._db.fetch_one(
            "SELECT fallback_id, research_id, user_id, from_provider, to_provider, "
            "reason, occurred_at FROM provider_fallbacks WHERE fallback_id = ?",
            (fallback_id,),
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("Fallback insert was not readable back")
        return row

    # ── Read / aggregate ─────────────────────────────────────────────────

    def list_research_usage(
        self, research_id: str, user_id: str
    ) -> List[Dict[str, Any]]:
        self.ensure_schema()
        return self._db.fetch_all(
            f"SELECT {_USAGE_COLUMNS} FROM model_usage "
            "WHERE research_id = ? AND user_id = ? ORDER BY occurred_at ASC",
            (research_id, user_id),
        )

    def research_summary(
        self, research_id: str, user_id: str
    ) -> Dict[str, Any]:
        self.ensure_schema()
        totals = self._db.fetch_one(
            """
            SELECT COUNT(*) AS requests,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd,
                   SUM(CASE WHEN request_status = 'failed' THEN 1 ELSE 0 END)
                       AS failures
              FROM model_usage
             WHERE research_id = ? AND user_id = ?
            """,
            (research_id, user_id),
        )
        by_provider = self._db.fetch_all(
            """
            SELECT provider, COUNT(*) AS requests,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd,
                   SUM(CASE WHEN request_status = 'failed' THEN 1 ELSE 0 END)
                       AS failures
              FROM model_usage
             WHERE research_id = ? AND user_id = ?
             GROUP BY provider ORDER BY provider
            """,
            (research_id, user_id),
        )
        by_agent = self._db.fetch_all(
            """
            SELECT COALESCE(agent_name, 'unknown') AS agent_name,
                   COUNT(*) AS requests,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd
              FROM model_usage
             WHERE research_id = ? AND user_id = ?
             GROUP BY agent_name ORDER BY agent_name
            """,
            (research_id, user_id),
        )
        fallbacks = self._db.fetch_one(
            "SELECT COUNT(*) AS fallbacks FROM provider_fallbacks "
            "WHERE research_id = ? AND user_id = ?",
            (research_id, user_id),
        )
        return {
            "requests": int((totals or {}).get("requests", 0)),
            "input_tokens": int((totals or {}).get("input_tokens", 0)),
            "output_tokens": int((totals or {}).get("output_tokens", 0)),
            "total_tokens": int((totals or {}).get("total_tokens", 0)),
            "estimated_cost_usd": float(
                (totals or {}).get("estimated_cost_usd", 0.0)
            ),
            "failures": int((totals or {}).get("failures", 0)),
            "fallbacks": int((fallbacks or {}).get("fallbacks", 0)),
            "per_provider": by_provider,
            "per_agent": by_agent,
        }

    def user_summary(self, user_id: str) -> Dict[str, Any]:
        self.ensure_schema()
        totals = self._db.fetch_one(
            """
            SELECT COUNT(*) AS requests,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd,
                   SUM(CASE WHEN request_status = 'failed' THEN 1 ELSE 0 END)
                       AS failures
              FROM model_usage
             WHERE user_id = ?
            """,
            (user_id,),
        )
        by_research = self._db.fetch_all(
            """
            SELECT research_id, COUNT(*) AS requests,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd
              FROM model_usage
             WHERE user_id = ?
             GROUP BY research_id ORDER BY MAX(occurred_at) DESC
            """,
            (user_id,),
        )
        by_provider = self._db.fetch_all(
            """
            SELECT provider, COUNT(*) AS requests,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd,
                   SUM(CASE WHEN request_status = 'failed' THEN 1 ELSE 0 END)
                       AS failures
              FROM model_usage
             WHERE user_id = ?
             GROUP BY provider ORDER BY provider
            """,
            (user_id,),
        )
        by_agent = self._db.fetch_all(
            """
            SELECT COALESCE(agent_name, 'unknown') AS agent_name,
                   COUNT(*) AS requests,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd
              FROM model_usage
             WHERE user_id = ?
             GROUP BY agent_name ORDER BY agent_name
            """,
            (user_id,),
        )
        fallbacks = self._db.fetch_one(
            "SELECT COUNT(*) AS fallbacks FROM provider_fallbacks WHERE user_id = ?",
            (user_id,),
        )
        return {
            "requests": int((totals or {}).get("requests", 0)),
            "input_tokens": int((totals or {}).get("input_tokens", 0)),
            "output_tokens": int((totals or {}).get("output_tokens", 0)),
            "total_tokens": int((totals or {}).get("total_tokens", 0)),
            "estimated_cost_usd": float(
                (totals or {}).get("estimated_cost_usd", 0.0)
            ),
            "failures": int((totals or {}).get("failures", 0)),
            "fallbacks": int((fallbacks or {}).get("fallbacks", 0)),
            "per_research": by_research,
            "per_provider": by_provider,
            "per_agent": by_agent,
        }

    def providers_summary(self, user_id: str) -> List[Dict[str, Any]]:
        self.ensure_schema()
        return self._db.fetch_all(
            """
            SELECT provider, COUNT(*) AS requests,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd,
                   SUM(CASE WHEN request_status = 'failed' THEN 1 ELSE 0 END)
                       AS failures
              FROM model_usage
             WHERE user_id = ?
             GROUP BY provider ORDER BY provider
            """,
            (user_id,),
        )

    def models_summary(self, user_id: str) -> List[Dict[str, Any]]:
        self.ensure_schema()
        return self._db.fetch_all(
            """
            SELECT provider, model, COUNT(*) AS requests,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd,
                   SUM(CASE WHEN request_status = 'failed' THEN 1 ELSE 0 END)
                       AS failures
              FROM model_usage
             WHERE user_id = ?
             GROUP BY provider, model ORDER BY model, provider
            """,
            (user_id,),
        )

    def fallbacks_summary(self, user_id: str) -> List[Dict[str, Any]]:
        self.ensure_schema()
        return self._db.fetch_all(
            """
            SELECT from_provider, to_provider, COUNT(*) AS fallbacks
              FROM provider_fallbacks
             WHERE user_id = ?
             GROUP BY from_provider, to_provider ORDER BY fallbacks DESC
            """,
            (user_id,),
        )


__all__ = ["ModelUsageRepository"]
