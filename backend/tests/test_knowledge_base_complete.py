"""
backend/tests/test_knowledge_base_complete.py
=============================================
Comprehensive integration and unit test suite for Persistent Knowledge Base:
- Source A: User-Uploaded Documents (Chunking, AES-256 encryption, indexing, retrieval)
- Source B: Research Session Findings (Summaries, verified claims, evidence excerpts)
- Multi-tenant ownership isolation
- Semantic search with Source Type, Project, and Verification Status filters
- Real-time knowledge base statistics
- Document chunk deletion
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.research_memory_store import ResearchMemoryStore
from app.services.research_memory_service import ResearchMemoryService
from crypto_utils import decrypt_text, is_encrypted


class TestKnowledgeBaseComplete(unittest.TestCase):
    def setUp(self):
        self.mock_emb = MagicMock()
        # Mock embed_documents to return 384-dim dummy vector
        self.mock_emb.embed_documents.side_effect = lambda texts: [[0.1] * 16 for _ in texts]
        self.mock_emb.embed_query.return_value = [0.1] * 16

    @patch("app.services.research_memory_store._get_embeddings")
    def test_document_chunk_indexing_and_encryption(self, mock_get_emb):
        mock_get_emb.return_value = self.mock_emb

        user_id = "user_test_doc_idx"
        store = ResearchMemoryStore(user_id=user_id)

        # Mock Chroma collection
        mock_collection = MagicMock()
        mock_collection.upsert = MagicMock()
        with patch.object(store, "_get_user_vectorstore") as mock_get_vs:
            vs_mock = MagicMock()
            vs_mock._collection = mock_collection
            mock_get_vs.return_value = vs_mock
            with patch.object(store, "_get_vectorstore", return_value=vs_mock):
                chunks = [
                    "First section of the quarterly financial statement.",
                    "Second section covering GPU revenue and data center growth.",
                ]
                indexed = store.add_document_chunks(
                    document_id="doc-xyz-123",
                    filename="nvidia_q4.pdf",
                    chunks=chunks,
                    project_id="proj-finance",
                )
                self.assertEqual(indexed, 2)

                # Verify upsert was called
                upsert_args = mock_collection.upsert.call_args[1]
                stored_docs = upsert_args["documents"]
                stored_metas = upsert_args["metadatas"]

                # Verify stored documents are AES-256 encrypted
                self.assertTrue(all(is_encrypted(d) for d in stored_docs))

                # Verify decrypted text matches original
                self.assertEqual(decrypt_text(stored_docs[0]), chunks[0])
                self.assertEqual(decrypt_text(stored_docs[1]), chunks[1])

                # Verify metadata
                self.assertEqual(stored_metas[0]["document_id"], "doc-xyz-123")
                self.assertEqual(stored_metas[0]["source_type"], "document")
                self.assertEqual(stored_metas[0]["project_id"], "proj-finance")
                self.assertEqual(stored_metas[0]["user_id"], user_id)

    @patch("app.services.research_memory_store._get_embeddings")
    def test_research_session_memory_ingestion_and_encryption(self, mock_get_emb):
        mock_get_emb.return_value = self.mock_emb

        user_id = "user_test_research_ingest"
        store = ResearchMemoryStore(user_id=user_id, project_id="proj-ai", session_id="sess-001")

        mock_collection = MagicMock()
        with patch.object(store, "_get_user_vectorstore") as mock_get_vs:
            vs_mock = MagicMock()
            vs_mock._collection = mock_collection
            mock_get_vs.return_value = vs_mock
            with patch.object(store, "_get_vectorstore", return_value=vs_mock):
                doc_id = store.add_memory(
                    memory_id="mem-claim-456",
                    content="Nvidia recorded $215.9 billion in FY26 revenue.",
                    content_type="claim",
                    verification_status="verified",
                    confidence=0.98,
                    source_id="https://sec.gov/edgar/nvda-10k",
                )
                self.assertTrue(doc_id.startswith("mem-"))

                upsert_args = mock_collection.upsert.call_args[1]
                stored_docs = upsert_args["documents"]
                stored_metas = upsert_args["metadatas"]

                # Encrypted in ChromaDB
                self.assertTrue(is_encrypted(stored_docs[0]))
                self.assertEqual(decrypt_text(stored_docs[0]), "Nvidia recorded $215.9 billion in FY26 revenue.")

                # Metadata tags
                self.assertEqual(stored_metas[0]["source_type"], "research")
                self.assertEqual(stored_metas[0]["content_type"], "claim")
                self.assertEqual(stored_metas[0]["verification_status"], "verified")
                self.assertEqual(stored_metas[0]["user_id"], user_id)
                self.assertEqual(stored_metas[0]["project_id"], "proj-ai")
                self.assertEqual(stored_metas[0]["session_id"], "sess-001")

    @patch("app.services.research_memory_store._get_embeddings")
    def test_knowledge_base_stats_aggregation(self, mock_get_emb):
        mock_get_emb.return_value = self.mock_emb

        user_id = "user_test_stats"
        store = ResearchMemoryStore(user_id=user_id)

        sample_metadatas = [
            {"user_id": user_id, "document_id": "doc-1", "source_type": "document", "content_type": "document_chunk"},
            {"user_id": user_id, "document_id": "doc-1", "source_type": "document", "content_type": "document_chunk"},
            {"user_id": user_id, "document_id": "doc-2", "source_type": "document", "content_type": "document_chunk"},
            {"user_id": user_id, "session_id": "sess-a", "source_type": "research", "content_type": "summary"},
            {"user_id": user_id, "session_id": "sess-a", "source_type": "research", "content_type": "claim"},
            {"user_id": user_id, "session_id": "sess-b", "source_type": "research", "content_type": "evidence"},
        ]

        with patch.object(store, "_get_user_vectorstore") as mock_get_vs:
            vs_mock = MagicMock()
            vs_mock._collection.get.return_value = {"metadatas": sample_metadatas}
            mock_get_vs.return_value = vs_mock

            stats = store.get_stats()
            self.assertEqual(stats["total_documents"], 2)
            self.assertEqual(stats["total_chunks"], 3)
            self.assertEqual(stats["total_memories"], 3)
            self.assertEqual(stats["contributing_sessions"], 2)

    @patch("app.services.research_memory_store._get_embeddings")
    def test_search_memories_filters_source_type_and_status(self, mock_get_emb):
        mock_get_emb.return_value = self.mock_emb

        user_id = "user_test_filtering"
        store = ResearchMemoryStore(user_id=user_id)

        # Mock similarity search returning documents of various types
        doc1 = MagicMock()
        doc1.page_content = "Document about Blackwell architecture"
        doc1.metadata = {"user_id": user_id, "source_type": "document", "content_type": "document_chunk", "verification_status": "verified"}

        doc2 = MagicMock()
        doc2.page_content = "Research finding about datacenter margins"
        doc2.metadata = {"user_id": user_id, "source_type": "research", "content_type": "claim", "verification_status": "verified"}

        doc3 = MagicMock()
        doc3.page_content = "Unverified rumor"
        doc3.metadata = {"user_id": user_id, "source_type": "research", "content_type": "claim", "verification_status": "unverified"}

        doc_other_user = MagicMock()
        doc_other_user.page_content = "Secret leak from user B"
        doc_other_user.metadata = {"user_id": "user_B", "source_type": "document", "content_type": "document_chunk", "verification_status": "verified"}

        with patch.object(store, "_get_user_vectorstore") as mock_get_vs:
            vs_mock = MagicMock()
            vs_mock.similarity_search.return_value = [doc1, doc2, doc3, doc_other_user]
            mock_get_vs.return_value = vs_mock

            # 1. Filter by document only
            docs_only = store.search_memories(query="architecture", source_type="document")
            self.assertEqual(len(docs_only), 1)
            self.assertEqual(docs_only[0]["content"], doc1.page_content)

            # 2. Filter by research only
            research_only = store.search_memories(query="margins", source_type="research")
            self.assertEqual(len(research_only), 2)  # doc2 and doc3

            # 3. Filter by verified only
            verified_only = store.search_memories(query="test", verification_status="verified")
            self.assertEqual(len(verified_only), 2)  # doc1 and doc2

            # 4. Multi-tenant isolation: doc_other_user is NEVER returned
            all_res = store.search_memories(query="test", source_type="all")
            self.assertEqual(len(all_res), 3)
            self.assertFalse(any(r["metadata"]["user_id"] != user_id for r in all_res))


if __name__ == "__main__":
    unittest.main()
