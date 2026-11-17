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
    source_type: Optional[str] = None
    filename: Optional[str] = None
    document_id: Optional[str] = None
    page_number: Optional[Any] = None
    chunk_index: Optional[Any] = None
    metadata: Optional[Dict[str, Any]] = None
    content: Optional[str] = None


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
        pg_record = None
        try:
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
        except Exception as repo_exc:
            logger.warning(f"PostgreSQL create_memory deferred: {repo_exc}")
        if pg_record is None:
            import uuid
            from datetime import datetime, timezone
            mem_id = uuid.uuid4().hex
            now = datetime.now(timezone.utc).isoformat()
            pg_record = {
                "id": mem_id,
                "user_id": user_id,
                "project_id": project_id,
                "session_id": session_id,
                "source_id": source_id,
                "content_type": content_type,
                "content": content,
                "verification_status": verification_status,
                "confidence": confidence,
                "chroma_id": mem_id,
                "metadata_json": metadata_json,
                "created_at": now,
                "updated_at": now,
            }

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

    def get_stats(self, *, user_id: str) -> Dict[str, int]:
        """Aggregate persistent knowledge metrics across documents and research sessions."""
        store = ResearchMemoryStore(
            settings=self._settings,
            user_id=user_id,
        )
        chroma_stats = store.get_stats()
        repo_stats = (
            self._repo.get_stats(user_id=user_id)
            if hasattr(self._repo, "get_stats")
            else {}
        )

        doc_stats: Dict[str, int] = {}
        try:
            from app.infrastructure.sqlite import SQLiteDatabase
            from app.repositories.documents import DocumentRepository
            db = SQLiteDatabase(self._settings.sessions_db_path)
            doc_repo = DocumentRepository(db)
            doc_stats = doc_repo.get_stats(user_id)
        except Exception:
            pass

        return {
            "total_documents": max(
                chroma_stats.get("total_documents", 0),
                doc_stats.get("total_documents", 0),
            ),
            "total_chunks": max(
                chroma_stats.get("total_chunks", 0),
                doc_stats.get("total_chunks", 0),
            ),
            "total_memories": max(
                chroma_stats.get("total_memories", 0),
                repo_stats.get("total_memories", 0),
            ),
            "contributing_sessions": max(
                chroma_stats.get("contributing_sessions", 0),
                repo_stats.get("contributing_sessions", 0),
            ),
        }

    def add_document_chunks(
        self,
        *,
        user_id: str,
        document_id: str,
        filename: str,
        chunks: List[str],
        project_id: Optional[str] = None,
    ) -> int:
        """Vectorize, encrypt, and index document chunks into persistent knowledge base."""
        store = ResearchMemoryStore(
            settings=self._settings,
            user_id=user_id,
            project_id=project_id,
        )
        return store.add_document_chunks(
            document_id=document_id,
            filename=filename,
            chunks=chunks,
            project_id=project_id,
        )

    def delete_document_chunks(self, *, user_id: str, document_id: str) -> int:
        """Purge indexed vector chunks for a deleted document."""
        store = ResearchMemoryStore(
            settings=self._settings,
            user_id=user_id,
        )
        return store.delete_document_chunks(document_id=document_id)

    # ── Read ───────────────────────────────────────────────────────────

    def get_memory(self, *, memory_id: str, user_id: str) -> Optional[MemoryRecord]:
        """Fetch one owned memory record."""
        record = self._repo.get_memory(memory_id=memory_id, user_id=user_id)
        return self._to_record(record) if record else None

    def list_memories(
        self,
        *,
        user_id: str,
        source_type: Optional[str] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        verification_status: Optional[str] = None,
        content_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MemoryRecord]:
        """List owned memories, newest first. Checks unified ChromaDB store and PostgreSQL."""
        out: List[MemoryRecord] = []
        seen_ids = set()

        # 1. Fetch from ChromaDB (contains document chunks and mirrored research memories)
        try:
            store = ResearchMemoryStore(
                settings=self._settings,
                user_id=user_id,
                project_id=project_id,
                session_id=session_id,
            )
            chroma_records = store.list_memories(
                source_type=source_type,
                project_id=project_id,
                session_id=session_id,
                verification_status=verification_status,
                content_type=content_type,
                limit=limit,
            )
            for item in chroma_records:
                meta = item.get("metadata", {})
                rec = self._to_record({
                    "id": item.get("id", ""),
                    "user_id": meta.get("user_id", user_id),
                    "project_id": meta.get("project_id"),
                    "session_id": meta.get("session_id"),
                    "source_id": meta.get("source_id") or meta.get("document_id") or meta.get("filename"),
                    "content_type": meta.get("content_type", "finding"),
                    "content_text": item.get("content", ""),
                    "verification_status": meta.get("verification_status", "unverified"),
                    "confidence": float(meta["confidence"]) if meta.get("confidence") else None,
                    "chroma_id": item.get("id"),
                    "metadata": meta,
                    "created_at": meta.get("created_at"),
                    "updated_at": meta.get("updated_at"),
                })
                out.append(rec)
                seen_ids.add(rec.id)
                if rec.chroma_id:
                    seen_ids.add(rec.chroma_id)
        except Exception as exc:
            logger.warning(f"ChromaDB list_memories warning: {exc}")

        # 2. If research memories requested or all, also pull from PostgreSQL repo
        if self._repo.available and (source_type in {"research", "all", None} or not source_type):
            try:
                pg_records = self._repo.list_memories(
                    user_id=user_id,
                    project_id=project_id,
                    session_id=session_id,
                    verification_status=verification_status,
                    content_type=content_type,
                    limit=limit,
                    offset=offset,
                )
                for r in pg_records:
                    rec_id = str(r.get("id", ""))
                    chroma_id = r.get("chroma_id")
                    if rec_id not in seen_ids and (not chroma_id or chroma_id not in seen_ids):
                        rec = self._to_record(r)
                        out.append(rec)
                        seen_ids.add(rec_id)
            except Exception as exc:
                logger.warning(f"PostgreSQL list_memories warning: {exc}")

        out.sort(key=lambda r: str(r.created_at or ""), reverse=True)
        return out[:limit]

    # ── Semantic retrieval ─────────────────────────────────────────────

    def retrieve_memories(
        self,
        *,
        user_id: str,
        query: str,
        k: int = 6,
        source_type: Optional[str] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        content_type: Optional[str] = None,
        verified_only: bool = False,
    ) -> List[MemoryRecord]:
        """
        Semantically search previous research findings and indexed documents relevant to *query*.

        Results are strictly scoped to *user_id* (and *project_id* /
        *session_id* when provided). ``verified_only`` excludes
        unverified/contradicted findings.
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
            source_type=source_type,
            content_type=content_type,
            verification_status="verified" if verified_only else None,
            project_id=project_id,
            session_id=session_id,
            include_unverified=not verified_only,
        )

        records: List[MemoryRecord] = []
        for item in results:
            meta = item.get("metadata", {})
            raw_rec = {
                "id": meta.get("chroma_id", item.get("id", "")),
                "user_id": meta.get("user_id", user_id),
                "project_id": meta.get("project_id", project_id),
                "session_id": meta.get("session_id", session_id),
                "source_id": meta.get("source_id"),
                "content_type": meta.get("content_type", "finding"),
                "content_text": item.get("content", ""),
                "verification_status": meta.get("verification_status", "unverified"),
                "confidence": (
                    float(meta["confidence"])
                    if meta.get("confidence")
                    else None
                ),
                "chroma_id": item.get("id"),
                "metadata_json": meta.get("metadata_json"),
                "created_at": meta.get("created_at"),
                "updated_at": meta.get("updated_at"),
                "deleted": False,
                "metadata": meta,
                "filename": meta.get("filename") or meta.get("title"),
                "document_id": meta.get("document_id"),
                "page_number": meta.get("page_number"),
                "chunk_index": meta.get("chunk_index"),
                "source_type": meta.get("source_type", "document" if meta.get("content_type") == "document_chunk" else "research"),
            }
            records.append(self._to_record(raw_rec))
        return records

    # ── Internal ──────────────────────────────────────────────────────

    @staticmethod
    def _to_record(record: Mapping[str, Any]) -> MemoryRecord:
        meta = {}
        raw_meta = record.get("metadata")
        if isinstance(raw_meta, dict):
            meta = dict(raw_meta)
        elif record.get("metadata_json"):
            try:
                import json
                meta = json.loads(str(record["metadata_json"]))
            except Exception:
                meta = {}

        c_text = str(record.get("content_text") or record.get("content") or "")
        c_type = str(record.get("content_type") or meta.get("content_type", "finding"))
        doc_id = record.get("document_id") or meta.get("document_id")
        is_doc = (
            record.get("source_type") == "document"
            or meta.get("source_type") == "document"
            or c_type == "document_chunk"
            or bool(doc_id)
        )
        src_type = "document" if is_doc else "research"
        fname = record.get("filename") or meta.get("filename") or meta.get("title")
        p_num = record.get("page_number") or meta.get("page_number")
        chk_idx = record.get("chunk_index") or meta.get("chunk_index")
        v_status = str(record.get("verification_status") or meta.get("verification_status", "verified" if is_doc else "unverified"))

        return MemoryRecord(
            id=str(record.get("id") or record.get("memory_id") or ""),
            user_id=str(record.get("user_id") or meta.get("user_id", "")),
            project_id=record.get("project_id") or meta.get("project_id"),
            session_id=record.get("session_id") or meta.get("session_id"),
            source_id=record.get("source_id") or meta.get("source_id"),
            content_type=c_type,
            content_text=c_text,
            verification_status=v_status,
            confidence=float(record["confidence"]) if record.get("confidence") is not None else None,
            chroma_id=record.get("chroma_id"),
            metadata_json=record.get("metadata_json"),
            created_at=record.get("created_at") or meta.get("created_at"),
            updated_at=record.get("updated_at") or meta.get("updated_at"),
            deleted=bool(record.get("deleted", False)),
            source_type=src_type,
            filename=fname,
            document_id=doc_id,
            page_number=p_num,
            chunk_index=chk_idx,
            metadata={
                **meta,
                "source_type": src_type,
                "content_type": c_type,
                "filename": fname,
                "document_id": doc_id,
                "page_number": p_num,
                "chunk_index": chk_idx,
                "verification_status": v_status,
            },
            content=c_text,
        )


__all__ = ["ResearchMemoryService", "MemoryRecord"]
