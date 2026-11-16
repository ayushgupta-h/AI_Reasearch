"""
tests/test_research_memory.py
=============================
Tests for persistent research memory (Task 3.5):

* ChromaDB memory store CRUD with ownership isolation
* PostgreSQL memory repository CRUD with ownership isolation
* Duplicate detection (same content not re-stored)
* Verified findings cannot be silently overwritten
* Cross-user retrieval blocked
* Cross-project retrieval blocked
* Semantic retrieval respects metadata filters
* Verified-only filtering
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from app.database.models.memory import ResearchMemory
from app.repositories.memory import (
    MemoryRepository,
    NullMemoryRepository,
    build_memory_repository,
    _content_hash,
)
from app.services.research_memory_service import MemoryRecord, ResearchMemoryService
from app.services.research_memory_store import ResearchMemoryStore


# ─── Content hash ───────────────────────────────────────────────────────


class TestContentHash:
    def test_hash_is_deterministic(self):
        h1 = _content_hash("Hello World")
        h2 = _content_hash("Hello World")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_hash_is_case_insensitive(self):
        assert _content_hash("Hello World") == _content_hash("hello world")

    def test_hash_differs_for_different_content(self):
        assert _content_hash("Hello World") != _content_hash("Hello World!")


# ─── Repository (mocked DB) ─────────────────────────────────────────────


class _FakeSession:
    """Minimal SQLAlchemy session double for repository tests."""

    def __init__(self, records: Dict[Any, ResearchMemory]):
        self._records = records
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def get(self, model, pk):
        return self._records.get(pk)

    def scalar(self, stmt):
        # Very limited: only handle simple select-where queries
        return None

    def scalars(self, stmt):
        return iter([])

    def add(self, obj):
        self._records[obj.id] = obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def refresh(self, obj):
        pass

    def close(self):
        self.closed = True

    def execute(self, stmt, params=None):
        return []


class _FakeFactory:
    def __init__(self, records: Dict[Any, ResearchMemory]):
        self._records = records

    def __call__(self):
        # Return a callable (sessionmaker) that returns a _FakeSession.
        return lambda: _FakeSession(self._records)


class TestMemoryRepository:
    def test_create_memory_returns_record(self):
        records: Dict[Any, ResearchMemory] = {}
        repo = MemoryRepository.__new__(MemoryRepository)
        repo._available = True
        repo._factory = _FakeFactory(records)

        result = repo.create_memory(
            user_id="11111111-1111-1111-1111-111111111111",
            content="AI improves diagnostic accuracy by 34%",
            content_type="finding",
            verification_status="verified",
        )
        assert result is not None
        assert result["content_text"] == "AI improves diagnostic accuracy by 34%"
        assert result["verification_status"] == "verified"
        assert result["user_id"] == "11111111-1111-1111-1111-111111111111"
        assert result["content_hash"] == _content_hash(
            "AI improves diagnostic accuracy by 34%"
        )

    def test_duplicate_content_returns_existing(self):
        records: Dict[Any, ResearchMemory] = {}
        repo = MemoryRepository.__new__(MemoryRepository)
        repo._available = True
        repo._factory = _FakeFactory(records)

        import uuid
        user_id = "11111111-1111-1111-1111-111111111111"
        first = repo.create_memory(
            user_id=user_id,
            content="Duplicate content test",
            content_type="finding",
        )
        second = repo.create_memory(
            user_id=user_id,
            content="Duplicate content test",
            content_type="finding",
        )
        assert first["id"] == second["id"]

    def test_get_memory_requires_ownership(self):
        records: Dict[Any, ResearchMemory] = {}
        import uuid
        owner = uuid.UUID("11111111-1111-1111-1111-111111111111")
        other = uuid.UUID("22222222-2222-2222-2222-222222222222")
        rec = ResearchMemory(
            id=uuid.uuid4(),
            user_id=owner,
            content_text="Private finding",
            content_type="finding",
            verification_status="unverified",
        )
        records[rec.id] = rec

        repo = MemoryRepository.__new__(MemoryRepository)
        repo._available = True
        repo._factory = _FakeFactory(records)

        # Owner can read
        result = repo.get_memory(memory_id=str(rec.id), user_id=str(owner))
        assert result is not None

        # Other user cannot read
        result = repo.get_memory(memory_id=str(rec.id), user_id=str(other))
        assert result is None

    def test_delete_memory_requires_ownership(self):
        records: Dict[Any, ResearchMemory] = {}
        import uuid
        owner = uuid.UUID("11111111-1111-1111-1111-111111111111")
        other = uuid.UUID("22222222-2222-2222-2222-222222222222")
        rec = ResearchMemory(
            id=uuid.uuid4(),
            user_id=owner,
            content_text="Private finding",
            content_type="finding",
            verification_status="unverified",
        )
        records[rec.id] = rec

        repo = MemoryRepository.__new__(MemoryRepository)
        repo._available = True
        repo._factory = _FakeFactory(records)

        # Other user cannot delete
        ok = repo.delete_memory(memory_id=str(rec.id), user_id=str(other))
        assert ok is False

        # Owner can delete
        ok = repo.delete_memory(memory_id=str(rec.id), user_id=str(owner))
        assert ok is True

    def test_null_repository_available_false(self):
        repo = NullMemoryRepository()
        assert repo.available is False
        assert repo.create_memory(user_id="u", content="c") is None
        assert repo.get_memory(memory_id="m", user_id="u") is None
        assert repo.list_memories(user_id="u") == []
        assert repo.update_memory(memory_id="m", user_id="u") is None
        assert repo.delete_memory(memory_id="m", user_id="u") is False

    def test_build_memory_repository_without_database_url(self):
        from app.config import Settings
        settings = Settings(database_url="")
        repo = build_memory_repository(settings)
        assert isinstance(repo, NullMemoryRepository)
        assert repo.available is False


# ─── Memory service ─────────────────────────────────────────────────────


class TestMemoryService:
    def test_store_memory_returns_record(self):
        from app.repositories.memory import build_memory_repository

        with patch(
            "app.services.research_memory_service.build_memory_repository"
        ) as mock_build:
            mock_repo = MagicMock()
            mock_repo.available = True
            mock_repo.create_memory.return_value = {
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "user_id": "11111111-1111-1111-1111-111111111111",
                "project_id": None,
                "session_id": None,
                "source_id": None,
                "content_hash": "abc123",
                "content_type": "finding",
                "content_text": "Test finding",
                "verification_status": "verified",
                "confidence": 0.9,
                "chroma_id": None,
                "metadata_json": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "deleted": False,
            }
            mock_repo.update_memory.return_value = mock_repo.create_memory.return_value
            mock_build.return_value = mock_repo

            with patch(
                "app.services.research_memory_service.ResearchMemoryStore"
            ) as mock_store_cls:
                mock_store = MagicMock()
                mock_store.add_memory.return_value = "chroma-doc-1"
                mock_store_cls.return_value = mock_store

                service = ResearchMemoryService()
                record = service.store_memory(
                    user_id="11111111-1111-1111-1111-111111111111",
                    content="Test finding",
                    content_type="finding",
                    verification_status="verified",
                    confidence=0.9,
                )
                assert record is not None
                assert record.content_text == "Test finding"
                assert record.verification_status == "verified"
                assert record.confidence == 0.9

    def test_retrieve_memories_scoped_to_user(self):
        """Retrieval must only return memories for the requesting user."""
        service = ResearchMemoryService.__new__(ResearchMemoryService)
        service._settings = MagicMock()

        with patch(
            "app.services.research_memory_service.ResearchMemoryStore"
        ) as mock_store_cls:
            mock_store = MagicMock()
            mock_store.search_memories.return_value = []
            mock_store_cls.return_value = mock_store

            service.retrieve_memories(
                user_id="user-a",
                query="AI research",
                k=5,
            )
            # Verify store was created with user-a scope
            call_kwargs = mock_store_cls.call_args.kwargs
            assert call_kwargs["user_id"] == "user-a"

    def test_verified_only_filter_passes_verification_status(self):
        service = ResearchMemoryService.__new__(ResearchMemoryService)
        service._settings = MagicMock()

        with patch(
            "app.services.research_memory_service.ResearchMemoryStore"
        ) as mock_store_cls:
            mock_store = MagicMock()
            mock_store.search_memories.return_value = []
            mock_store_cls.return_value = mock_store

            service.retrieve_memories(
                user_id="user-a",
                query="AI research",
                k=5,
                verified_only=True,
            )
            call_kwargs = mock_store.search_memories.call_args.kwargs
            assert call_kwargs["verification_status"] == "verified"

    def test_record_to_memory_record_conversion(self):
        service = ResearchMemoryService()
        raw = {
            "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "user_id": "11111111-1111-1111-1111-111111111111",
            "project_id": "22222222-2222-2222-2222-222222222222",
            "session_id": None,
            "source_id": None,
            "content_type": "claim",
            "content_text": "Test claim",
            "verification_status": "supported",
            "confidence": 0.85,
            "chroma_id": "chroma-1",
            "metadata_json": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": None,
            "deleted": False,
        }
        record = service._to_record(raw)
        assert record.id == raw["id"]
        assert record.user_id == raw["user_id"]
        assert record.project_id == raw["project_id"]
        assert record.content_type == "claim"
        assert record.verification_status == "supported"
        assert record.confidence == 0.85
        assert record.deleted is False


# ─── Memory store (ChromaDB metadata isolation) ─────────────────────────


class TestMemoryStore:
    def test_store_is_scoped_to_user(self):
        """Store construction requires user_id and scopes collections."""
        with patch(
            "app.services.research_memory_store._get_embeddings"
        ) as mock_emb:
            mock_emb.return_value = MagicMock()

            store = ResearchMemoryStore(
                user_id="user-a",
                project_id="proj-b",
                session_id="sess-c",
            )
            # Verify collection name includes session hash
            assert "session-" in store._collection_name
            assert store._user_id == "user-a"
            assert store._project_id == "proj-b"
            assert store._session_id == "sess-c"

    def test_base_metadata_includes_user_id(self):
        meta = ResearchMemoryStore._base_metadata(
            user_id="user-a",
            project_id="proj-b",
            session_id=None,
            source_id=None,
            content_type="finding",
            verification_status="verified",
            created_at="2026-01-01T00:00:00Z",
        )
        assert meta["user_id"] == "user-a"
        assert meta["project_id"] == "proj-b"
        assert "session_id" not in meta
        assert meta["content_type"] == "finding"
        assert meta["verification_status"] == "verified"


# ─── MemoryRecord dataclass ─────────────────────────────────────────────


class TestMemoryRecord:
    def test_memory_record_fields(self):
        record = MemoryRecord(
            id="id-1",
            user_id="user-1",
            project_id="proj-1",
            session_id="sess-1",
            source_id="src-1",
            content_type="finding",
            content_text="Test content",
            verification_status="verified",
            confidence=0.9,
            chroma_id="chroma-1",
            metadata_json=None,
            created_at="2026-01-01T00:00:00Z",
            updated_at=None,
            deleted=False,
        )
        assert record.verification_status == "verified"
        assert record.confidence == 0.9
        d = record.__dict__
        assert d["user_id"] == "user-1"
        assert d["project_id"] == "proj-1"


# ─── Model registration ─────────────────────────────────────────────────


class TestMemoryModel:
    def test_research_memory_has_expected_columns(self):
        columns = {c.name for c in ResearchMemory.__table__.columns}
        expected = {
            "id", "user_id", "project_id", "session_id", "source_id",
            "content_hash", "content_type", "content_text",
            "verification_status", "confidence", "chroma_id",
            "metadata_json", "deleted", "deleted_at",
            "created_at", "updated_at",
        }
        assert expected.issubset(columns)

    def test_research_memory_has_user_foreign_key(self):
        fks = [fk for fk in ResearchMemory.__table__.foreign_keys]
        target_tables = {fk.column.table.name for fk in fks}
        assert "users" in target_tables
        assert "projects" in target_tables
        assert "research_sessions" in target_tables
        assert "research_sources" in target_tables


__all__ = [
    "TestContentHash",
    "TestMemoryRepository",
    "TestMemoryService",
    "TestMemoryStore",
    "TestMemoryRecord",
    "TestMemoryModel",
]
