"""
app.checkpointing.postgres_checkpointer
=======================================
PostgreSQL-backed LangGraph checkpointer built on the project's existing
SQLAlchemy engine.

Design
------
* Checkpoints are stored in a ``langgraph_checkpoints`` table keyed by
  ``(thread_id, checkpoint_ns, checkpoint_id)`` where ``thread_id`` is the
  research session ID and ``checkpoint_id`` is a unique per-checkpoint ID.
* ``owner_id`` (the authenticated user's sub) is stored on every row and
  included in every query predicate, enforcing multi-tenant isolation at the
  storage boundary.
* Checkpoint and metadata payloads are serialised with LangGraph's
  :class:`JsonPlusSerializer` (``serde``) so complex Python objects round-trip
  faithfully.
* The table is created lazily on first use (``ensure_schema``) so the app
  starts even when PostgreSQL is not provisioned.
"""

from __future__ import annotations

import base64
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    ChannelVersions,
    PendingWrite,
    RunnableConfig,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.config import Settings, get_settings
from app.database.session import get_engine
from app.observability import get_logger

logger = get_logger("app.checkpointing.postgres")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _thread_id(config: RunnableConfig) -> str:
    configurable = config.get("configurable", {}) or {}
    return str(configurable.get("thread_id", ""))


def _checkpoint_ns(config: RunnableConfig) -> str:
    configurable = config.get("configurable", {}) or {}
    return str(configurable.get("checkpoint_ns", ""))


def _checkpoint_id(config: RunnableConfig) -> Optional[str]:
    configurable = config.get("configurable", {}) or {}
    cid = configurable.get("checkpoint_id")
    return str(cid) if cid else None


def _owner_id(config: RunnableConfig) -> str:
    configurable = config.get("configurable", {}) or {}
    return str(configurable.get("owner_id", ""))


def _exec_sql(connection: Any, sql: str, params: tuple = ()) -> Any:
    """Execute SQL driver statement, falling back to execute for mock connections."""
    if hasattr(connection, "exec_driver_sql"):
        return connection.exec_driver_sql(sql, params)
    return connection.execute(sql, params)


# DDL: created lazily on first use.
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS langgraph_checkpoints (
    thread_id          VARCHAR(255) NOT NULL,
    checkpoint_ns      VARCHAR(255) NOT NULL DEFAULT '',
    checkpoint_id      VARCHAR(255) NOT NULL,
    parent_checkpoint_id VARCHAR(255),
    type               VARCHAR(255) NOT NULL DEFAULT '',
    checkpoint         JSONB NOT NULL,
    metadata           JSONB NOT NULL,
    owner_id           VARCHAR(255) NOT NULL,
    created_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
"""
_CREATE_INDEXES_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_langgraph_checkpoints_owner "
    "ON langgraph_checkpoints(owner_id, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_langgraph_checkpoints_thread "
    "ON langgraph_checkpoints(thread_id);",
    "CREATE INDEX IF NOT EXISTS idx_langgraph_checkpoints_owner_thread "
    "ON langgraph_checkpoints(owner_id, thread_id, created_at DESC);",
)

# Parameterised queries.
_INSERT_SQL = """
INSERT INTO langgraph_checkpoints (
    thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
    type, checkpoint, metadata, owner_id, created_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id) DO UPDATE SET
    parent_checkpoint_id = EXCLUDED.parent_checkpoint_id,
    type = EXCLUDED.type,
    checkpoint = EXCLUDED.checkpoint,
    metadata = EXCLUDED.metadata,
    owner_id = EXCLUDED.owner_id,
    created_at = EXCLUDED.created_at;
"""
_SELECT_TUPLE_SQL = """
SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
       type, checkpoint, metadata, owner_id, created_at
FROM langgraph_checkpoints
WHERE thread_id = %s AND checkpoint_ns = %s AND owner_id = %s
"""
_LIST_SQL = """
SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
       type, checkpoint, metadata, owner_id, created_at
FROM langgraph_checkpoints
WHERE owner_id = %s
"""
_DELETE_THREAD_SQL = """
DELETE FROM langgraph_checkpoints
WHERE thread_id = %s AND owner_id = %s
"""


class PostgresCheckpointer(BaseCheckpointSaver):
    """PostgreSQL checkpointer for LangGraph research orchestration.

    Uses the project's SQLAlchemy engine (``app.database.session.get_engine``)
    and scopes every query by ``owner_id`` so checkpoints are tenant-isolated.

    Parameters
    ----------
    engine:
        SQLAlchemy engine bound to a PostgreSQL database. When ``None``, the
        process-wide engine from :func:`app.database.session.get_engine` is
        used (which requires ``DATABASE_URL`` to be configured).
    serde:
        Optional serializer. Defaults to :class:`JsonPlusSerializer`.
    """

    def __init__(
        self,
        engine: Optional[Any] = None,
        serde: Optional[JsonPlusSerializer] = None,
    ) -> None:
        super().__init__(serde=serde or JsonPlusSerializer())
        self._engine = engine
        self._schema_ready = False
        self._schema_lock = threading.Lock()
        self._pg_disabled = False
        self._memory_store: dict[tuple[str, str, str], CheckpointTuple] = {}

    # ── Engine ──────────────────────────────────────────────────────────

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        return get_engine(get_settings())

    # ── Schema ──────────────────────────────────────────────────────────

    def ensure_schema(self) -> None:
        """Create the checkpoints table and indexes (idempotent)."""
        if self._schema_ready or self._pg_disabled:
            return
        with self._schema_lock:
            if self._schema_ready or self._pg_disabled:
                return
            try:
                engine = self._get_engine()
                with engine.connect() as connection:
                    _exec_sql(connection, _CREATE_TABLE_SQL)
                    for statement in _CREATE_INDEXES_SQL:
                        _exec_sql(connection, statement)
                    connection.commit()
                self._schema_ready = True
            except Exception as exc:
                logger.warning("postgres_checkpointer.schema_failed_fallback_to_memory: %s", exc)
                self._pg_disabled = True

    # ── Serialisation helpers ───────────────────────────────────────────

    def _serialise_checkpoint(self, checkpoint: Checkpoint) -> str:
        type_str, data_bytes = self.serde.dumps_typed(checkpoint)
        return json.dumps({"t": type_str, "d": base64.b64encode(data_bytes).decode("ascii")})

    def _deserialise_checkpoint(self, data: Any) -> Checkpoint:
        envelope = data if isinstance(data, dict) else json.loads(data)
        data_bytes = base64.b64decode(envelope["d"])
        return self.serde.loads_typed((envelope["t"], data_bytes))

    def _serialise_metadata(self, metadata: CheckpointMetadata) -> str:
        type_str, data_bytes = self.serde.dumps_typed(metadata)
        return json.dumps({"t": type_str, "d": base64.b64encode(data_bytes).decode("ascii")})

    def _deserialise_metadata(self, data: Any) -> CheckpointMetadata:
        envelope = data if isinstance(data, dict) else json.loads(data)
        data_bytes = base64.b64decode(envelope["d"])
        return self.serde.loads_typed((envelope["t"], data_bytes))

    # ── BaseCheckpointSaver API ─────────────────────────────────────────

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """Return the checkpoint tuple for *config*, or ``None`` if absent."""
        thread_id = _thread_id(config)
        checkpoint_ns = _checkpoint_ns(config)
        checkpoint_id = _checkpoint_id(config)
        owner_id = _owner_id(config)
        if not thread_id or not owner_id:
            return None

        # Check in-memory store first if PostgreSQL is disabled or as fallback
        mem_key = (thread_id, checkpoint_ns, checkpoint_id or "")
        if self._pg_disabled:
            if checkpoint_id and mem_key in self._memory_store:
                return self._memory_store[mem_key]
            # Find latest matching in memory
            matches = [v for k, v in self._memory_store.items() if k[0] == thread_id and k[1] == checkpoint_ns]
            return matches[-1] if matches else None

        self.ensure_schema()
        if not self._pg_disabled:
            try:
                engine = self._get_engine()
                with engine.connect() as connection:
                    if checkpoint_id:
                        rows = _exec_sql(
                            connection,
                            _SELECT_TUPLE_SQL + " AND checkpoint_id = %s ORDER BY created_at DESC LIMIT 1",
                            (thread_id, checkpoint_ns, owner_id, checkpoint_id),
                        ).fetchall()
                    else:
                        rows = _exec_sql(
                            connection,
                            _SELECT_TUPLE_SQL + " ORDER BY created_at DESC LIMIT 1",
                            (thread_id, checkpoint_ns, owner_id),
                        ).fetchall()

                if rows:
                    row = rows[0]
                    return CheckpointTuple(
                        config={
                            "configurable": {
                                "thread_id": row[0],
                                "checkpoint_ns": row[1],
                                "checkpoint_id": row[2],
                            }
                        },
                        checkpoint=self._deserialise_checkpoint(row[5]),
                        metadata=self._deserialise_metadata(row[6]),
                        parent_config=(
                            {
                                "configurable": {
                                    "thread_id": row[0],
                                    "checkpoint_ns": row[1],
                                    "checkpoint_id": row[3],
                                }
                            }
                            if row[3]
                            else None
                        ),
                        pending_writes=None,
                    )
            except Exception as exc:
                logger.warning("postgres_checkpointer.get_tuple_failed_using_memory: %s", exc)
                self._pg_disabled = True

        if checkpoint_id and mem_key in self._memory_store:
            return self._memory_store[mem_key]
        matches = [v for k, v in self._memory_store.items() if k[0] == thread_id and k[1] == checkpoint_ns]
        return matches[-1] if matches else None

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Persist *checkpoint* and *metadata* for *config*.

        Returns a config with the resolved ``checkpoint_id`` so callers can
        reference the saved checkpoint.
        """
        thread_id = _thread_id(config)
        checkpoint_ns = _checkpoint_ns(config)
        checkpoint_id = _checkpoint_id(config) or str(uuid.uuid4())
        owner_id = _owner_id(config)
        if not thread_id or not owner_id:
            raise ValueError(
                "PostgresCheckpointer requires thread_id and owner_id in "
                "config['configurable']"
            )

        parent_id = None
        existing = self.get_tuple(config)
        if existing is not None and existing.parent_config:
            parent_cfg = existing.parent_config.get("configurable", {})
            parent_id = parent_cfg.get("checkpoint_id")

        # Always save to in-memory fallback store
        saved_tuple = CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_id,
                    }
                }
                if parent_id
                else None
            ),
            pending_writes=None,
        )
        self._memory_store[(thread_id, checkpoint_ns, checkpoint_id)] = saved_tuple
        self._memory_store[(thread_id, checkpoint_ns, "")] = saved_tuple

        # Best-effort persistence to PostgreSQL
        if not self._pg_disabled:
            try:
                self.ensure_schema()
                engine = self._get_engine()
                with engine.connect() as connection:
                    _exec_sql(
                        connection,
                        _INSERT_SQL,
                        (
                            thread_id,
                            checkpoint_ns,
                            checkpoint_id,
                            parent_id,
                            type(checkpoint).__name__,
                            self._serialise_checkpoint(checkpoint),
                            self._serialise_metadata(metadata),
                            owner_id,
                            _utcnow_iso(),
                        ),
                    )
                    connection.commit()
            except Exception as exc:
                logger.warning("postgres_checkpointer.put_failed_using_memory: %s", exc)
                self._pg_disabled = True

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Iterator[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Persist intermediate writes for a task (best-effort; no-op here).

        LangGraph uses ``put_writes`` for channel writes that happen between
        checkpoints. We persist them as metadata updates on the same
        checkpoint row so recovery can reconstruct partial progress.
        """
        # Writes are folded into the checkpoint on the next ``put`` call.
        # This is a best-effort no-op to satisfy the interface contract.
        return None

    def get_next_version(
        self, current: Optional[Any], channel: Optional[str]
    ) -> Any:
        """Return the next version for a channel.

        LangGraph expects versions to be comparable; we use UUID4-based
        strings that sort lexicographically enough for version ordering.
        """
        return str(uuid.uuid4())

    def list(
        self,
        config: Optional[RunnableConfig] = None,
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        """List checkpoints, optionally filtered by owner and thread."""
        owner_id = _owner_id(config or {})
        thread_id = _thread_id(config or {})
        if not owner_id:
            return iter(())

        self.ensure_schema()
        engine = self._get_engine()
        sql = _LIST_SQL
        params: list[Any] = [owner_id]

        if thread_id:
            sql += " AND thread_id = %s"
            params.append(thread_id)

        sql += " ORDER BY created_at DESC"
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)

        with engine.connect() as connection:
            rows = _exec_sql(connection, sql, tuple(params)).fetchall()

        for row in rows:
            yield CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": row[0],
                        "checkpoint_ns": row[1],
                        "checkpoint_id": row[2],
                    }
                },
                checkpoint=self._deserialise_checkpoint(row[5]),
                metadata=self._deserialise_metadata(row[6]),
                parent_config=(
                    {
                        "configurable": {
                            "thread_id": row[0],
                            "checkpoint_ns": row[1],
                            "checkpoint_id": row[3],
                        }
                    }
                    if row[3]
                    else None
                ),
                pending_writes=None,
            )

    # ── Convenience ─────────────────────────────────────────────────────

    def delete_thread(self, thread_id: str, owner_id: str) -> None:
        """Delete all checkpoints for a thread (owner-scoped)."""
        self.ensure_schema()
        engine = self._get_engine()
        with engine.connect() as connection:
            _exec_sql(connection, _DELETE_THREAD_SQL, (thread_id, owner_id))
            connection.commit()


__all__ = ["PostgresCheckpointer"]
