"""
Unit tests for ChromaDB AES-256-GCM Encryption & RAG Knowledge Base.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from langchain_core.embeddings import FakeEmbeddings

from crypto_utils import (
    encrypt_text,
    decrypt_text,
    is_encrypted,
    get_or_create_chroma_key,
)
from knowledge_base import ResearchKnowledgeBase


class TestChromaCrypto:
    """Test cryptographic functions for AES-256-GCM document encryption."""

    def test_key_length_and_validity(self):
        key = get_or_create_chroma_key()
        assert isinstance(key, bytes)
        assert len(key) == 32

    def test_encrypt_decrypt_roundtrip(self):
        original = "Proprietary customer research: confidential benchmark data €12,500 and 99.8% precision."
        ciphertext = encrypt_text(original)
        assert ciphertext != original
        assert ciphertext.startswith("ENC:GCM:")
        assert is_encrypted(ciphertext)

        decrypted = decrypt_text(ciphertext)
        assert decrypted == original

    def test_encryption_idempotent(self):
        text = "Confidential clinical trial data"
        enc1 = encrypt_text(text)
        enc2 = encrypt_text(enc1)
        assert enc1 == enc2

    def test_decrypt_plaintext_fallback(self):
        plaintext = "Legacy unencrypted document text"
        assert decrypt_text(plaintext) == plaintext

    def test_empty_string_handling(self):
        assert encrypt_text("") == ""
        assert decrypt_text("") == ""
        assert encrypt_text(None) == ""
        assert decrypt_text(None) == ""


class TestChromaVectorStoreEncryption:
    """Test that ChromaDB stores ciphertext while vector search returns decrypted text."""

    @pytest.fixture
    def temp_kb(self):
        import uuid
        temp_dir = tempfile.mkdtemp(prefix="chroma_test_")
        embeddings = FakeEmbeddings(size=384)
        topic = f"test-enc-{uuid.uuid4().hex[:8]}"
        kb = ResearchKnowledgeBase(
            topic=topic,
            persist_dir=temp_dir,
            embeddings=embeddings,
        )
        yield kb
        try:
            kb.reset()
        except Exception:
            pass
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_stored_documents_are_ciphertext_in_chromadb(self, temp_kb):
        secret_docs = [
            "Customer X secret revenue figures: $4.2B in Q3 2026.",
            "Customer Y internal AI architectural blueprint and weights.",
        ]
        count = temp_kb.index(secret_docs, source_label="test_secret")
        assert count == 2

        # Inspect raw ChromaDB collection directly to verify physical storage
        vs = temp_kb._get_vectorstore()
        raw_data = vs._collection.get(include=["documents", "metadatas", "embeddings"])

        stored_docs = raw_data["documents"]
        assert len(stored_docs) == 2

        # ChromaDB dashboard / storage displays ciphertext, NOT readable text
        for doc in stored_docs:
            assert doc.startswith("ENC:GCM:")
            assert "secret revenue" not in doc
            assert "internal AI" not in doc

        # Embeddings are preserved and not empty
        assert raw_data["embeddings"] is not None
        assert len(raw_data["embeddings"]) == 2
        assert len(raw_data["embeddings"][0]) == 384

    def test_retrieval_returns_decrypted_text(self, temp_kb):
        secret_docs = [
            "Customer X secret revenue figures: $4.2B in Q3 2026.",
            "Customer Y internal AI architectural blueprint and weights.",
        ]
        temp_kb.index(secret_docs, source_label="test_secret")

        # Semantic retrieval
        results = temp_kb.retrieve(query="revenue figures", k=2)
        assert len(results) > 0

        # Retrieved content must be cleanly decrypted
        first_content = results[0]["content"]
        assert not first_content.startswith("ENC:GCM:")
        assert "revenue figures" in first_content or "architectural blueprint" in first_content

    def test_retrieve_for_analysis_formats_decrypted_context(self, temp_kb):
        docs = [
            "Clinical evaluation indicates high diagnostic efficacy of neural models.",
        ]
        temp_kb.index(docs, source_label="clinical_study")

        rag_str = temp_kb.retrieve_for_analysis(query="diagnostic efficacy", k=2)
        assert rag_str != ""
        assert "ENC:GCM:" not in rag_str
        assert "high diagnostic efficacy" in rag_str
        assert "[Chunk 1 — " in rag_str
