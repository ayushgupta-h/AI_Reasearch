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
All stored customer content is encrypted with authenticated AES-256-GCM.
"""

from __future__ import annotations

import hashlib
import sys
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

try:
    from crypto_utils import encrypt_text, decrypt_text
except ImportError:
    root_dir = str(Path(__file__).resolve().parent.parent.parent)
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)
    from crypto_utils import encrypt_text, decrypt_text

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
        self._user_vectorstore = None  # lazy
        self._collection_name = self._collection_name_for_level()
        self._user_collection_name = f"rai-mem-user-{_id_hash(self._user_id)}"

    # ── Collection naming ──────────────────────────────────────────────

    def _collection_name_for_level(self) -> str:
        """Pick the collection based on the narrowest configured scope."""
        if self._session_id:
            return f"rai-mem-session-{_id_hash(self._session_id)}"
        if self._project_id:
            return f"rai-mem-project-{_id_hash(self._project_id)}"
        return f"rai-mem-user-{_id_hash(self._user_id)}"

    # ── ChromaDB access ────────────────────────────────────────────────

    def _create_vectorstore(self, collection_name: str):
        try:
            from langchain_chroma import Chroma  # type: ignore
        except ImportError:
            from langchain_community.vectorstores import Chroma  # type: ignore

        chroma_api_key = self._settings.chroma_api_key
        chroma_tenant = self._settings.chroma_tenant
        chroma_database = self._settings.chroma_database

        if chroma_api_key and chroma_tenant and chroma_database:
            try:
                import chromadb  # type: ignore

                chroma_client = chromadb.CloudClient(
                    tenant=chroma_tenant,
                    database=chroma_database,
                    api_key=chroma_api_key,
                )
                return Chroma(
                    client=chroma_client,
                    collection_name=collection_name,
                    embedding_function=self._embeddings,
                )
            except Exception as exc:
                logger.warning(
                    f"Chroma CloudClient connection failed: {exc}. Falling back to local Chroma storage."
                )

        Path(self._settings.chroma_dir).mkdir(parents=True, exist_ok=True)
        return Chroma(
            collection_name=collection_name,
            embedding_function=self._embeddings,
            persist_directory=str(self._settings.chroma_dir),
        )

    def _get_vectorstore(self):
        """Lazy-initialise the ChromaDB LangChain vectorstore for this scope."""
        if self._vectorstore is None:
            self._vectorstore = self._create_vectorstore(self._collection_name)
        return self._vectorstore

    def _get_user_vectorstore(self):
        """Vectorstore for user master collection containing all user knowledge."""
        if self._collection_name == self._user_collection_name:
            return self._get_vectorstore()
        if self._user_vectorstore is None:
            self._user_vectorstore = self._create_vectorstore(self._user_collection_name)
        return self._user_vectorstore

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
        """Embed and store *content* as a memory record with AES-256-GCM encryption.

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
        metadata["source_type"] = "research"
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
        embeddings = self._embeddings.embed_documents([chunks[0]])
        encrypted_text = encrypt_text(chunks[0])

        vs._collection.upsert(
            ids=[doc_id],
            documents=[encrypted_text],
            embeddings=embeddings,
            metadatas=[metadata],
        )
        if self._collection_name != self._user_collection_name:
            user_vs = self._get_user_vectorstore()
            user_vs._collection.upsert(
                ids=[doc_id],
                documents=[encrypted_text],
                embeddings=embeddings,
                metadatas=[metadata],
            )
        return doc_id

    def add_document_chunks(
        self,
        *,
        document_id: str,
        filename: str,
        chunks: List[Any],
        project_id: Optional[str] = None,
        source_label: Optional[str] = None,
    ) -> int:
        """Embed, encrypt, and store document chunks into ChromaDB."""
        if not chunks:
            return 0
        proj_id = project_id or self._project_id or ""
        created_at = _utcnow_iso()
        ids: List[str] = []
        encrypted_docs: List[str] = []
        texts_to_embed: List[str] = []
        metadatas: List[Dict[str, Any]] = []

        for idx, item in enumerate(chunks):
            if isinstance(item, dict):
                chunk_text = str(item.get("text", "")).strip()
                page_num = item.get("page_number")
                sec = item.get("section", "")
            else:
                chunk_text = str(item).strip()
                page_num = None
                sec = ""
            if not chunk_text:
                continue
            chunk_id = f"doc-{document_id}-chk-{idx}"
            ids.append(chunk_id)
            texts_to_embed.append(chunk_text)
            encrypted_docs.append(encrypt_text(chunk_text))
            meta = {
                "user_id": self._user_id,
                "document_id": document_id,
                "filename": filename,
                "title": source_label or filename,
                "chunk_index": str(idx),
                "total_chunks": str(len(chunks)),
                "source_type": "document",
                "content_type": "document_chunk",
                "verification_status": "verified",
                "created_at": created_at,
            }
            if page_num is not None:
                meta["page_number"] = str(page_num)
            if sec:
                meta["section"] = str(sec)
            if proj_id:
                meta["project_id"] = proj_id
            metadatas.append(meta)

        if not texts_to_embed:
            return 0

        embeddings = self._embeddings.embed_documents(texts_to_embed)

        vs = self._get_vectorstore()
        vs._collection.upsert(
            ids=ids,
            documents=encrypted_docs,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        if self._collection_name != self._user_collection_name:
            user_vs = self._get_user_vectorstore()
            user_vs._collection.upsert(
                ids=ids,
                documents=encrypted_docs,
                embeddings=embeddings,
                metadatas=metadatas,
            )
        return len(texts_to_embed)

    def delete_document_chunks(self, *, document_id: str) -> int:
        """Purge all chunks associated with a deleted document."""
        target_stores = [self._get_vectorstore()]
        if self._collection_name != self._user_collection_name:
            target_stores.append(self._get_user_vectorstore())

        deleted_count = 0
        for vs in target_stores:
            try:
                res = vs._collection.get(
                    where={"document_id": {"$eq": document_id}},
                    include=["metadatas"]
                )
                if res and res.get("ids"):
                    vs._collection.delete(ids=res["ids"])
                    deleted_count = max(deleted_count, len(res["ids"]))
            except Exception as exc:
                logger.warning(f"Failed deleting chunks for doc {document_id}: {exc}")
        return deleted_count

    def update_memory(
        self,
        *,
        memory_id: str,
        content: Optional[str] = None,
        verification_status: Optional[str] = None,
        confidence: Optional[float] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Update an existing memory record's metadata/content."""
        doc_id = f"mem-{memory_id}"
        updated_any = False
        target_stores = [self._get_vectorstore()]
        if self._collection_name != self._user_collection_name:
            target_stores.append(self._get_user_vectorstore())

        for vs in target_stores:
            try:
                existing = vs._collection.get(ids=[doc_id])
                if not existing or not existing.get("ids"):
                    continue

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

                if content is not None:
                    new_text = content
                    new_emb = self._embeddings.embed_documents([new_text])
                    enc_text = encrypt_text(new_text)
                    vs._collection.upsert(
                        ids=[doc_id],
                        documents=[enc_text],
                        embeddings=new_emb,
                        metadatas=[metadata],
                    )
                else:
                    vs._collection.update(
                        ids=[doc_id],
                        metadatas=[metadata],
                    )
                updated_any = True
            except Exception as exc:
                logger.warning(f"Error updating memory {doc_id}: {exc}")

        return updated_any

    def delete_memory(self, *, memory_id: str) -> bool:
        """Delete a memory record by ID. Returns ``True`` if deleted."""
        doc_id = f"mem-{memory_id}"
        deleted = False
        target_stores = [self._get_vectorstore()]
        if self._collection_name != self._user_collection_name:
            target_stores.append(self._get_user_vectorstore())

        for vs in target_stores:
            try:
                existing = vs._collection.get(ids=[doc_id])
                if existing and existing.get("ids"):
                    vs._collection.delete(ids=[doc_id])
                    deleted = True
            except Exception:
                pass
        return deleted

    def get_memory(self, *, memory_id: str) -> Optional[Dict[str, Any]]:
        """Fetch one memory record by ID with decrypted content."""
        doc_id = f"mem-{memory_id}"
        vs = self._get_user_vectorstore()
        result = vs._collection.get(ids=[doc_id], include=["documents", "metadatas"])
        if not result or not result.get("ids"):
            vs = self._get_vectorstore()
            result = vs._collection.get(ids=[doc_id], include=["documents", "metadatas"])
        if not result or not result.get("ids"):
            return None

        meta = result["metadatas"][0] if result.get("metadatas") else {}
        if meta.get("user_id") != self._user_id:
            return None

        raw_content = result["documents"][0] if result.get("documents") else ""
        return {
            "id": result["ids"][0],
            "content": decrypt_text(raw_content),
            "metadata": meta,
        }

    # ── Retrieval ─────────────────────────────────────────────────────

    def search_memories(
        self,
        *,
        query: str,
        k: int = 6,
        source_type: Optional[str] = None,
        content_type: Optional[str] = None,
        verification_status: Optional[str] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        document_id: Optional[str] = None,
        document_ids: Optional[List[str]] = None,
        include_unverified: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Semantically search memory for *query*, scoped by ownership and decrypted.
        """
        target_vs = (
            self._get_user_vectorstore()
            if (not self._project_id and not self._session_id)
            else self._get_vectorstore()
        )

        filter_meta: Dict[str, Any] = {"user_id": {"$eq": self._user_id}}
        active_proj = project_id or self._project_id
        active_sess = session_id or self._session_id

        if active_proj:
            filter_meta["project_id"] = {"$eq": active_proj}
        if active_sess:
            filter_meta["session_id"] = {"$eq": active_sess}
        if content_type:
            filter_meta["content_type"] = {"$eq": content_type}
        if document_id:
            filter_meta["document_id"] = {"$eq": document_id}

        try:
            docs = target_vs.similarity_search(query, k=max(k * 2, 10), filter=filter_meta)
        except Exception as exc:
            logger.warning("memory.search_filter_failed", extra={"error": str(exc)})
            docs = target_vs.similarity_search(query, k=max(k * 2, 10))

        allowed_doc_ids = set(document_ids) if document_ids else None
        results: List[Dict[str, Any]] = []
        for d in docs:
            meta = d.metadata or {}
            # Enforce ownership strictly at read time
            if meta.get("user_id") != self._user_id:
                continue
            if active_proj and meta.get("project_id") != active_proj:
                continue
            if active_sess and meta.get("session_id") != active_sess:
                continue
            if content_type and meta.get("content_type") != content_type:
                continue
            if document_id and meta.get("document_id") != document_id:
                continue
            if allowed_doc_ids and meta.get("document_id") not in allowed_doc_ids:
                continue

            # Filter by source_type (all / document / research)
            if source_type and source_type != "all":
                is_doc = meta.get("source_type") == "document" or meta.get("content_type") == "document_chunk"
                if source_type == "document" and not is_doc:
                    continue
                if source_type == "research" and is_doc:
                    continue

            # Filter by verification_status
            if verification_status and verification_status != "all":
                if verification_status == "verified" and meta.get("verification_status") not in {"verified", "partially_verified"}:
                    continue
                elif verification_status != "verified" and meta.get("verification_status") != verification_status:
                    continue
            elif not include_unverified:
                if meta.get("verification_status") not in {"verified", "partially_verified", "contradicted"}:
                    continue

            decrypted_content = decrypt_text(d.page_content)
            clean_id = meta.get("chroma_id") or meta.get("document_id") or (d.metadata.get("id") if hasattr(d, "metadata") else "") or ""
            results.append(
                {
                    "id": clean_id,
                    "content": decrypted_content,
                    "metadata": meta,
                    "score": None,
                }
            )
            if len(results) >= k:
                break

        return results

    def list_memories(
        self,
        *,
        source_type: Optional[str] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        verification_status: Optional[str] = None,
        content_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List memory records for this scope, decrypted and newest first."""
        target_vs = (
            self._get_user_vectorstore()
            if (not self._project_id and not self._session_id)
            else self._get_vectorstore()
        )
        where: Dict[str, Any] = {"user_id": {"$eq": self._user_id}}
        active_proj = project_id or self._project_id
        active_sess = session_id or self._session_id
        if active_proj:
            where["project_id"] = {"$eq": active_proj}
        if active_sess:
            where["session_id"] = {"$eq": active_sess}
        if content_type:
            where["content_type"] = {"$eq": content_type}

        try:
            result = target_vs._collection.get(
                where=where,
                limit=limit * 2,
                include=["documents", "metadatas"],
            )
        except Exception as exc:
            logger.warning("memory.list_failed", extra={"error": str(exc)})
            try:
                result = target_vs._collection.get(
                    limit=limit * 2,
                    include=["documents", "metadatas"],
                )
            except Exception:
                return []

        records: List[Dict[str, Any]] = []
        raw_ids = result.get("ids", [])
        raw_docs = result.get("documents", [])
        raw_metas = result.get("metadatas", [])

        for i, doc_id in enumerate(raw_ids):
            meta = raw_metas[i] if i < len(raw_metas) else {}
            if meta.get("user_id") != self._user_id:
                continue
            if active_proj and meta.get("project_id") != active_proj:
                continue
            if active_sess and meta.get("session_id") != active_sess:
                continue
            if content_type and meta.get("content_type") != content_type:
                continue

            if source_type and source_type != "all":
                is_doc = meta.get("source_type") == "document" or meta.get("content_type") == "document_chunk"
                if source_type == "document" and not is_doc:
                    continue
                if source_type == "research" and is_doc:
                    continue

            if verification_status and verification_status != "all":
                if verification_status == "verified" and meta.get("verification_status") not in {"verified", "partially_verified"}:
                    continue
                elif verification_status != "verified" and meta.get("verification_status") != verification_status:
                    continue

            raw_txt = raw_docs[i] if i < len(raw_docs) else ""
            clean_id = doc_id.replace("mem-", "", 1) if doc_id.startswith("mem-") else doc_id
            records.append(
                {
                    "id": clean_id,
                    "content": decrypt_text(raw_txt),
                    "metadata": meta,
                }
            )
            if len(records) >= limit:
                break

        records.sort(key=lambda r: r["metadata"].get("created_at", ""), reverse=True)
        return records

    def get_stats(self) -> Dict[str, int]:
        """Aggregate real-time knowledge base metrics for the authenticated user."""
        target_vs = self._get_user_vectorstore()
        try:
            result = target_vs._collection.get(
                where={"user_id": {"$eq": self._user_id}},
                include=["metadatas"],
            )
            metas = result.get("metadatas") or []
            doc_ids = set()
            session_ids = set()
            total_chunks = 0
            total_memories = 0
            for m in metas:
                if not m:
                    continue
                if m.get("document_id"):
                    doc_ids.add(m["document_id"])
                if m.get("session_id"):
                    session_ids.add(m["session_id"])
                if m.get("source_type") == "document" or m.get("content_type") == "document_chunk":
                    total_chunks += 1
                else:
                    total_memories += 1
            return {
                "total_documents": len(doc_ids),
                "total_chunks": total_chunks,
                "total_memories": total_memories,
                "contributing_sessions": len(session_ids),
            }
        except Exception as exc:
            logger.warning("memory.get_stats_failed", extra={"error": str(exc)})
            return {
                "total_documents": 0,
                "total_chunks": 0,
                "total_memories": 0,
                "contributing_sessions": 0,
            }


__all__ = ["ResearchMemoryStore"]
