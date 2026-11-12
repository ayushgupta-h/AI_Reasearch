"""Encryption migration tests: plaintext is encrypted, embeddings preserved."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from crypto_utils import decrypt_text, is_encrypted
from migrate_chroma_encryption import backup_local_chromadb, migrate_collection


class FakeCollection:
    """Minimal Chroma collection double (no vector store required)."""

    def __init__(self, documents: List[str]) -> None:
        self.documents = list(documents)
        self.embeddings = [[float(i), float(i + 1)] for i in range(len(documents))]
        self.metadatas = [{"index": i} for i in range(len(documents))]
        self.updates: List[Dict[str, Any]] = []

    def _id(self, index: int) -> str:
        return f"doc-{index}"

    def _index(self, doc_id: str) -> int:
        return int(doc_id.split("-", 1)[1])

    def get(self, include=None, ids=None):
        if ids is not None:
            # Verification read: serve the live (post-update) documents.
            return {
                "ids": list(ids),
                "documents": [self.documents[self._index(i)] for i in ids],
            }
        return {
            "ids": [self._id(i) for i in range(len(self.documents))],
            "documents": list(self.documents),
            "metadatas": list(self.metadatas),
            "embeddings": list(self.embeddings),
        }

    def update(self, **kwargs):
        self.updates.append(kwargs)
        for doc_id, doc in zip(kwargs["ids"], kwargs["documents"]):
            self.documents[self._index(doc_id)] = doc


@pytest.fixture(autouse=True)
def quiet_console(monkeypatch):
    monkeypatch.setattr("migrate_chroma_encryption.console", MagicMock())


class TestMigration:
    def test_plaintext_documents_are_encrypted(self) -> None:
        collection = FakeCollection(["first secret doc", "second secret doc"])
        stats = migrate_collection(collection)

        assert stats == {"total": 2, "already_encrypted": 0, "migrated": 2}
        assert all(is_encrypted(doc) for doc in collection.documents)
        assert decrypt_text(collection.documents[0]) == "first secret doc"

    def test_embeddings_are_forwarded_unchanged(self) -> None:
        collection = FakeCollection(["doc one", "doc two"])
        original_embeddings = [list(vector) for vector in collection.embeddings]

        migrate_collection(collection)

        assert collection.updates
        forwarded = collection.updates[0]["embeddings"]
        assert forwarded == original_embeddings

    def test_already_encrypted_documents_are_skipped(self) -> None:
        from crypto_utils import encrypt_text

        encrypted = encrypt_text("already protected")
        collection = FakeCollection([encrypted, "plaintext"])

        stats = migrate_collection(collection)

        assert stats == {"total": 2, "already_encrypted": 1, "migrated": 1}
        assert collection.documents[0] == encrypted
        assert is_encrypted(collection.documents[1])

    def test_empty_collection_is_a_noop(self) -> None:
        collection = FakeCollection([])
        stats = migrate_collection(collection)
        assert stats == {"total": 0, "already_encrypted": 0, "migrated": 0}
        assert collection.updates == []

    def test_batches_are_limited_to_one_hundred(self) -> None:
        collection = FakeCollection([f"document {i}" for i in range(250)])
        migrate_collection(collection)
        assert len(collection.updates) == 3
        assert [len(update["ids"]) for update in collection.updates] == [100, 100, 50]


class TestBackup:
    def test_directory_backup_copies_files(self, tmp_path: Path) -> None:
        source = tmp_path / "chroma_db"
        source.mkdir()
        (source / "chroma.sqlite3").write_text("sqlite-bytes", encoding="utf-8")
        (source / "index.bin").write_bytes(b"\x00\x01")
        (source / "meta.json").write_text(json.dumps({"v": 1}), encoding="utf-8")

        backup = backup_local_chromadb(source)

        assert backup is not None
        assert backup.is_dir()
        assert (backup / "chroma.sqlite3").read_text(encoding="utf-8") == "sqlite-bytes"
        shutil.rmtree(backup, ignore_errors=True)
