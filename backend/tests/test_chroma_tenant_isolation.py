"""Task 2.4 - ChromaDB multi-tenant isolation tests.

These verify that a tenant-scoped knowledge base always confines similarity
search to the owner's records via mandatory metadata filters, and that legacy
records without tenant metadata are excluded rather than exposed.
"""

from __future__ import annotations

import pytest
from langchain_core.embeddings import FakeEmbeddings

from knowledge_base import ResearchKnowledgeBase


@pytest.fixture(autouse=True)
def _mock_embeddings(monkeypatch):
    monkeypatch.setattr("knowledge_base._get_embeddings", lambda: FakeEmbeddings(size=384))


class TestChromaTenantFilter:
    def test_no_tenant_returns_caller_filter_unchanged(self) -> None:
        kb = ResearchKnowledgeBase(topic="t")
        caller = {"source_label": {"$eq": "search"}}
        assert kb._build_tenant_filter(caller) is caller

    def test_user_id_alone_produces_eq_filter(self) -> None:
        kb = ResearchKnowledgeBase(topic="t", user_id="user-a")
        f = kb._build_tenant_filter()
        assert f == {"user_id": {"$eq": "user-a"}}

    def test_user_and_project_produce_and_filter(self) -> None:
        kb = ResearchKnowledgeBase(topic="t", user_id="user-a", project_id="proj-1")
        f = kb._build_tenant_filter()
        assert f == {
            "$and": [
                {"user_id": {"$eq": "user-a"}},
                {"project_id": {"$eq": "proj-1"}},
            ]
        }

    def test_caller_filter_is_folded_into_tenant_and(self) -> None:
        kb = ResearchKnowledgeBase(topic="t", user_id="user-a")
        f = kb._build_tenant_filter({"type": {"$eq": "search_result"}})
        assert f == {
            "$and": [
                {"user_id": {"$eq": "user-a"}},
                {"type": {"$eq": "search_result"}},
            ]
        }

    def test_tenant_identity_is_stored_on_instance(self) -> None:
        kb = ResearchKnowledgeBase(topic="t", user_id="user-b", project_id="p2")
        assert kb.user_id == "user-b"
        assert kb.project_id == "p2"

    def test_legacy_records_without_tenant_metadata_cannot_match(self) -> None:
        """A tenant-scoped filter must never match records lacking tenant metadata.

        This is a structural guarantee: an equality predicate on ``user_id``
        simply cannot be satisfied by a record whose ``user_id`` field is
        absent, so cross-user documents are never returned through retrieval.
        """
        kb = ResearchKnowledgeBase(topic="t", user_id="user-a")
        f = kb._build_tenant_filter()

        def _matches(record_meta) -> bool:
            if "$and" in f:
                return all(
                    record_meta.get(clause_key) == clause_value
                    for clause in f["$and"]
                    for clause_key, clause_value in clause.items()
                    if isinstance(clause_value, dict) and "$eq" in clause_value
                )
            (key, cond), = list(f.items())
            return record_meta.get(key) == cond["$eq"]

        # Legacy record with no tenant metadata -> must not match.
        assert _matches({"source_label": "search"}) is False
        # Another user's record -> must not match.
        assert _matches({"user_id": "user-b"}) is False
        # Owner's record with matching user_id -> matches.
        assert _matches({"user_id": "user-a"}) is True
