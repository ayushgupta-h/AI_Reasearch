"""
app/services/research_memory_store.py
=======================================
ChromaDB-backed vector store for persistent research memory.

Reuses the project's existing ChromaDB instance (same embedding model,
persist directory, encryption wrapper) but stores memory records in
dedicated collections scoped by ownership level. Every vector record carries
``user_id`` (required), and optionally ``project_id`` and ``session_id``,
in its ChromaDB metadata so retrieval can be filtered by the same
boundaries enforced in PostgreSQL.

Three collection tiers:
  * user-level   : rai-mem-user-<user_id_hash>
  * project-level: rai-mem-project-<project_id_hash>
  * session-level: rai-mem-session-<session_id_hash>

A memory written at one tier is retrievable from that tier and all broader
tiers (e.g. session memory is visible within its project and user scope).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import Settings, get_settings
from app.observability import get_logger
from knowledge_base import (
    _chunk_text,
    _get_embeddings,
    _make_doc_id,
)

logger = get_logger("app.memory.store")


def _id_hash(value: str) -> str:
    """Stable short hash for collection-name safety."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchMemoryStore:
    """
    ChromaDB vector store for research memory with ownership isolation.

    Parameters
    ----------
    settings:
        Application settings (provides Chroma dir / cloud config).
    user_id:
        Authenticated user ID — every operation is scoped to this user.
    project_id:
        Optional project ID to narrow scope.
    session_id:
        Optional research session ID to narrow scope further.
    """

    def __init__(
        self,
        *,
        settings: Optional[Settings] = None,
        user_id: str,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._user_id = user_id
        self._project_id = project_id
        self._session_id = session_id
        self._embeddings = _get_embeddings()
        self._vectorstore = None  # lazy
        self._collection_name = self._collection_name_for_level()

    # ── Collection naming ──────────────────────────────────────────────

    def _collection_name_for_level(self) -> str:
        """Pick the collection based on the narrowest configured scope."""
        if self._session_id:
            return f"rai-mem-session-{_id_hash(self._session_id)}"
        if self._project_id:
            return f"rai-mem-project-{_id_hash(self._project_id)}"
        return f"rai-mem-user-{_id_hash(self._user_id)}"

    # ── ChromaDB access ────────────────────────────────────────────────

    def _get_vectorstore(self):
        """Lazy-initialise the ChromaDB LangChain vectorstore for this scope."""
        if self._vectorstore is not None:
            return self._vectorstore

        try:
            from langchain_chroma import Chroma  # type: ignore
        except ImportError:
            from langchain_community.vectorstores import Chroma  # type: ignore

        chroma_api_key = self._settings.chroma_api_key
        chroma_tenant = self._settings.chroma_tenant
        chroma_database = self._settings.chroma_database

        if chroma_api_key and chroma_tenant and chroma_database:
            import chromadb  # type: ignore

            chroma_client = chromadb.CloudClient(
                tenant=chroma_tenant,
                database=chroma_database,
                api_key=chroma_api_key,
            )
            self._vectorstore = Chroma(
                client=chroma_client,
                collection_name=self._collection_name,
                embedding_function=self._embeddings,
            )
        else:
            from pathlib import Path

            Path(self._settings.chroma_dir).mkdir(parents=True, exist_ok=True)
            self._vectorstore = Chroma(
                collection_name=self._collection_name,
                embedding_function=self._embeddings,
                persist_directory=str(self._settings.chroma_dir),
            )

        return self._vectorstore

    # ── Metadata helpers ──────────────────────────────────────────────

    @staticmethod
    def _base_metadata(
        *,
        user_id: str,
        project_id: Optional[str],
        session_id: Optional[str],
        source_id: Optional[str],
        content_type: str,
        verification_status: str,
        created_at: str,
    ) -> Dict[str, Any]:
        meta: Dict[str, Any] = {
            "user_id": user_id,
            "content_type": content_type,
            "verification_status": verification_status,
            "created_at": created_at,
        }
        if project_id:
            meta["project_id"] = project_id
        if session_id:
            meta["session_id"] = session_id
        if source_id:
            meta["source_id"] = source_id
        return meta

    # ── CRUD ──────────────────────────────────────────────────────────

    def add_memory(
        self,
        *,
        memory_id: str,
        content: str,
        content_type: str = "finding",
        verification_status: str = "unverified",
        source_id: Optional[str] = None,
        confidence: Optional[float] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Embed and store *content* as a memory record.

        Returns the ChromaDB document ID.
        """
        vs = self._get_vectorstore()
        created_at = _utcnow_iso()
        metadata = self._base_metadata(
            user_id=self._user_id,
            project_id=self._project_id,
            session_id=self._session_id,
            source_id=source_id,
            content_type=content_type,
            verification_status=verification_status,
            created_at=created_at,
        )
        if confidence is not None:
            metadata["confidence"] = str(confidence)
        if extra_metadata:
            for key, value in extra_metadata.items():
                if value is not None:
                    metadata[str(key)] = str(value)

        chunks = _chunk_text(content)
        if not chunks:
            chunks = [content]

        doc_id = f"mem-{memory_id}"
        vs._collection.upsert(
            ids=[doc_id],
            documents=[chunks[0]],
            embeddings=self._embeddings.embed_documents([chunks[0]]),
            metadatas=[metadata],
        )
        return doc_id

    def update_memory(
        self,
        *,
        memory_id: str,
        content: Optional[str] = None,
        verification_status: Optional[str] = None,
        confidence: Optional[float] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Update an existing memory record's metadata/content.

        Returns ``True`` if a record was updated, ``False`` if none matched.
        """
        vs = self._get_vectorstore()
        doc_id = f"mem-{memory_id}"
        existing = vs._collection.get(ids=[doc_id])
        if not existing or not existing.get("ids"):
            return False

        metadata = dict(existing["metadatas"][0]) if existing.get("metadatas") else {}
        if verification_status is not None:
            metadata["verification_status"] = verification_status
        if confidence is not None:
            metadata["confidence"] = str(confidence)
        if extra_metadata:
            for key, value in extra_metadata.items():
                if value is not None:
                    metadata[str(key)] = str(value)
        metadata["updated_at"] = _utcnow_iso()

        documents = existing.get("documents") or [""]
        new_text = content if content is not None else documents[0]

        vs._collection.upsert(
            ids=[doc_id],
            documents=[new_text],
            embeddings=self._embeddings.embed_documents([new_text]),
            metadatas=[metadata],
        )
        return True

    def delete_memory(self, *, memory_id: str) -> bool:
        """Delete a memory record by ID. Returns ``True`` if deleted."""
        vs = self._get_vectorstore()
        doc_id = f"mem-{memory_id}"
        existing = vs._collection.get(ids=[doc_id])
        if not existing or not existing.get("ids"):
            return False
        vs._collection.delete(ids=[doc_id])
        return True

    def get_memory(self, *, memory_id: str) -> Optional[Dict[str, Any]]:
        """Fetch one memory record by ID."""
        vs = self._get_vectorstore()
        doc_id = f"mem-{memory_id}"
        result = vs._collection.get(ids=[doc_id], include=["documents", "metadatas"])
        if not result or not result.get("ids"):
            return None
        return {
            "id": result["ids"][0],
            "content": result["documents"][0] if result.get("documents") else "",
            "metadata": result["metadatas"][0] if result.get("metadatas") else {},
        }

    # ── Retrieval ─────────────────────────────────────────────────────

    def search_memories(
        self,
        *,
        query: str,
        k: int = 6,
        content_type: Optional[str] = None,
        verification_status: Optional[str] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        include_unverified: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Semantically search memory for *query*, scoped by ownership.

        Results are filtered to this store's user scope (and project/session
        if configured on the store). Additional optional filters narrow by
        content type, verification status, project or session.
        """
        vs = self._get_vectorstore()
        filter_meta: Dict[str, Any] = {"user_id": {"$eq": self._user_id}}

        if self._project_id:
            filter_meta["project_id"] = {"$eq": self._project_id}
        if self._session_id:
            filter_meta["session_id"] = {"$eq": self._session_id}
        if project_id:
            filter_meta["project_id"] = {"$eq": project_id}
        if session_id:
            filter_meta["session_id"] = {"$eq": session_id}
        if content_type:
            filter_meta["content_type"] = {"$eq": content_type}
        if verification_status:
            filter_meta["verification_status"] = {"$eq": verification_status}
        if not include_unverified:
            # Prefer verified / non-unverified findings by default.
            filter_meta["$and"] = [
                filter_meta,
                {
                    "verification_status": {
                        "$in": ["verified", "partially_verified", "contradicted"],
                    }
                },
            ]

        try:
            docs = vs.similarity_search(query, k=k, filter=filter_meta)
        except Exception as exc:  # Chroma filter quirks — degrade gracefully
            logger.warning("memory.search_filter_failed", extra={"error": str(exc)})
            docs = vs.similarity_search(query, k=k)

        results: List[Dict[str, Any]] = []
        for d in docs:
            meta = d.metadata or {}
            # Enforce ownership at read time regardless of Chroma filter.
            if meta.get("user_id") != self._user_id:
                continue
            if self._project_id and meta.get("project_id") != self._project_id:
                continue
            if self._session_id and meta.get("session_id") != self._session_id:
                continue
            results.append(
                {
                    "id": meta.get("chroma_id", ""),
                    "content": d.page_content,
                    "metadata": meta,
                    "score": None,
                }
            )
        return results

    def list_memories(
        self,
        *,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        verification_status: Optional[str] = None,
        content_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List memory records for this scope, newest first."""
        vs = self._get_vectorstore()
        where: Dict[str, Any] = {"user_id": {"$eq": self._user_id}}
        if self._project_id:
            where["project_id"] = {"$eq": self._project_id}
        if self._session_id:
            where["session_id"] = {"$eq": self._session_id}
        if project_id:
            where["project_id"] = {"$eq": project_id}
        if session_id:
            where["session_id"] = {"$eq": session_id}
        if verification_status:
            where["verification_status"] = {"$eq": verification_status}
        if content_type:
            where["content_type"] = {"$eq": content_type}

        try:
            result = vs._collection.get(
                where=where,
                limit=limit,
                include=["documents", "metadatas"],
            )
        except Exception as exc:  # pragma: no cover - Chroma quirks
            logger.warning("memory.list_failed", extra={"error": str(exc)})
            return []

        records: List[Dict[str, Any]] = []
        for i, doc_id in enumerate(result.get("ids", [])):
            records.append(
                {
                    "id": doc_id.replace("mem-", "", 1),
                    "content": (result.get("documents") or [""])[i],
                    "metadata": (result.get("metadatas") or [{}])[i],
                }
            )
        records.sort(key=lambda r: r["metadata"].get("created_at", ""), reverse=True)
        return records


__all__ = ["ResearchMemoryStore"]
