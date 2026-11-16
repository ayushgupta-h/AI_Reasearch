"""
app/services/research_memory_service.py
=======================================
Research memory service: durable, isolated, reusable research memory.

Combines:
* PostgreSQL (via :class:`app.repositories.memory.MemoryRepository`) as the
  authoritative metadata store,
* ChromaDB (via :class:`app.services.research_memory_store.ResearchMemoryStore`)
  for semantic retrieval.

All operations are scoped to an authenticated user and optionally narrowed
to a project or research session. Cross-user and cross-project retrieval is
impossible by construction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from app.config import Settings, get_settings
from app.database.models.memory import ResearchMemory
from app.observability import get_logger
from app.repositories.memory import (
    NullMemoryRepository,
    build_memory_repository,
)
from app.services.research_memory_store import ResearchMemoryStore

logger = get_logger("app.memory.service")


@dataclass
class MemoryRecord:
    """A single memory record returned to callers."""

    id: str
    user_id: str
    project_id: Optional[str]
    session_id: Optional[str]
    source_id: Optional[str]
    content_type: str
    content_text: str
    verification_status: str
    confidence: Optional[float]
    chroma_id: Optional[str]
    metadata_json: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    deleted: bool


class ResearchMemoryService:
    """High-level research memory operations with ownership enforcement."""

    def __init__(
        self,
        *,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repo = build_memory_repository(self._settings)

    # ── Write ──────────────────────────────────────────────────────────

    def store_memory(
        self,
        *,
        user_id: str,
        content: str,
        content_type: str = "finding",
        verification_status: str = "unverified",
        confidence: Optional[float] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        source_id: Optional[str] = None,
        metadata_json: Optional[str] = None,
    ) -> Optional[MemoryRecord]:
        """
        Persist one memory record to PostgreSQL + ChromaDB.

        Duplicate detection: identical content (same user/project/session)
        returns the existing record instead of creating a new one.
        """
        # Persist to PostgreSQL first (authoritative).
        pg_record = self._repo.create_memory(
            user_id=user_id,
            content=content,
            content_type=content_type,
            verification_status=verification_status,
            confidence=confidence,
            project_id=project_id,
            session_id=session_id,
            source_id=source_id,
            metadata_json=metadata_json,
        )
        if pg_record is None:
            # Either duplicate (returned existing) or not available.
            if self._repo.available:
                # Duplicate — fetch and return the existing record.
                # create_memory returns the existing dict on duplicate.
                existing = pg_record  # type: ignore[assignment]
                return self._to_record(existing) if existing else None
            return None

        # Mirror into ChromaDB for semantic retrieval.
        chroma_id: Optional[str] = None
        try:
            store = ResearchMemoryStore(
                settings=self._settings,
                user_id=user_id,
                project_id=project_id,
                session_id=session_id,
            )
            chroma_id = store.add_memory(
                memory_id=pg_record["id"],
                content=content,
                content_type=content_type,
                verification_status=verification_status,
                source_id=source_id,
                confidence=confidence,
                extra_metadata=(
                    {"metadata_json": metadata_json} if metadata_json else None
                ),
            )
            # Link the chroma_id back to the PG record.
            self._repo.update_memory(
                memory_id=pg_record["id"],
                user_id=user_id,
                chroma_id=chroma_id,
            )
            pg_record["chroma_id"] = chroma_id
        except Exception as exc:  # Chroma failure must not break persistence
            logger.warning("memory.chroma_mirror_failed", extra={"error": str(exc)})

        return self._to_record(pg_record)

    def update_memory(
        self,
        *,
        memory_id: str,
        user_id: str,
        content: Optional[str] = None,
        verification_status: Optional[str] = None,
        confidence: Optional[float] = None,
        metadata_json: Optional[str] = None,
    ) -> Optional[MemoryRecord]:
        """Update an owned memory record in PostgreSQL + ChromaDB.

        Verified findings cannot be silently overwritten — the update is
        rejected if the existing record is verified and content changes.
        """
        pg_record = self._repo.update_memory(
            memory_id=memory_id,
            user_id=user_id,
            content=content,
            verification_status=verification_status,
            confidence=confidence,
            metadata_json=metadata_json,
        )
        if pg_record is None:
            return None

        # Mirror into ChromaDB.
        try:
            store = ResearchMemoryStore(
                settings=self._settings,
                user_id=user_id,
                project_id=pg_record.get("project_id"),
                session_id=pg_record.get("session_id"),
            )
            store.update_memory(
                memory_id=memory_id,
                content=content,
                verification_status=verification_status,
                confidence=confidence,
                extra_metadata=(
                    {"metadata_json": metadata_json} if metadata_json else None
                ),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("memory.chroma_update_failed", extra={"error": str(exc)})

        return self._to_record(pg_record)

    def delete_memory(self, *, memory_id: str, user_id: str) -> bool:
        """Soft-delete an owned memory from PostgreSQL + ChromaDB."""
        ok = self._repo.delete_memory(memory_id=memory_id, user_id=user_id)
        if not ok:
            return False
        record = self._repo.get_memory(memory_id=memory_id, user_id=user_id)
        chroma_id = record.get("chroma_id") if record else None
        if chroma_id:
            try:
                store = ResearchMemoryStore(
                    settings=self._settings,
                    user_id=user_id,
                )
                store.delete_memory(memory_id=memory_id)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("memory.chroma_delete_failed", extra={"error": str(exc)})
        return True

    # ── Read ───────────────────────────────────────────────────────────

    def get_memory(self, *, memory_id: str, user_id: str) -> Optional[MemoryRecord]:
        """Fetch one owned memory record."""
        record = self._repo.get_memory(memory_id=memory_id, user_id=user_id)
        return self._to_record(record) if record else None

    def list_memories(
        self,
        *,
        user_id: str,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        verification_status: Optional[str] = None,
        content_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MemoryRecord]:
        """List owned memories, newest first."""
        records = self._repo.list_memories(
            user_id=user_id,
            project_id=project_id,
            session_id=session_id,
            verification_status=verification_status,
            content_type=content_type,
            limit=limit,
            offset=offset,
        )
        return [self._to_record(r) for r in records]

    # ── Semantic retrieval ─────────────────────────────────────────────

    def retrieve_memories(
        self,
        *,
        user_id: str,
        query: str,
        k: int = 6,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        content_type: Optional[str] = None,
        verified_only: bool = False,
    ) -> List[MemoryRecord]:
        """
        Semantically search previous research findings relevant to *query*.

        Results are strictly scoped to *user_id* (and *project_id* /
        *session_id* when provided). ``verified_only`` excludes
        unverified/contradicted findings.

        Memory supplements fresh research — it never replaces it. Callers
        should still run live searches when current information is required.
        """
        store = ResearchMemoryStore(
            settings=self._settings,
            user_id=user_id,
            project_id=project_id,
            session_id=session_id,
        )
        results = store.search_memories(
            query=query,
            k=k,
            content_type=content_type,
            verification_status="verified" if verified_only else None,
            project_id=project_id,
            session_id=session_id,
            include_unverified=not verified_only,
        )

        records: List[MemoryRecord] = []
        for item in results:
            meta = item.get("metadata", {})
            records.append(
                MemoryRecord(
                    id=meta.get("chroma_id", item.get("id", "")),
                    user_id=meta.get("user_id", user_id),
                    project_id=meta.get("project_id"),
                    session_id=meta.get("session_id"),
                    source_id=meta.get("source_id"),
                    content_type=meta.get("content_type", "finding"),
                    content_text=item.get("content", ""),
                    verification_status=meta.get(
                        "verification_status", "unverified"
                    ),
                    confidence=(
                        float(meta["confidence"])
                        if meta.get("confidence")
                        else None
                    ),
                    chroma_id=item.get("id"),
                    metadata_json=meta.get("metadata_json"),
                    created_at=meta.get("created_at"),
                    updated_at=meta.get("updated_at"),
                    deleted=False,
                )
            )
        return records

    # ── Internal ──────────────────────────────────────────────────────

    @staticmethod
    def _to_record(record: Mapping[str, Any]) -> MemoryRecord:
        return MemoryRecord(
            id=str(record["id"]),
            user_id=str(record["user_id"]),
            project_id=record.get("project_id"),
            session_id=record.get("session_id"),
            source_id=record.get("source_id"),
            content_type=record.get("content_type", "finding"),
            content_text=record.get("content_text", ""),
            verification_status=record.get("verification_status", "unverified"),
            confidence=record.get("confidence"),
            chroma_id=record.get("chroma_id"),
            metadata_json=record.get("metadata_json"),
            created_at=record.get("created_at"),
            updated_at=record.get("updated_at"),
            deleted=bool(record.get("deleted", False)),
        )


__all__ = ["ResearchMemoryService", "MemoryRecord"]
