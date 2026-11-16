"""
tests/test_claim_verification.py
================================
Tests for claim-level evidence verification:

* ClaimExtractor: atomic claim extraction, opinion filtering, compound splitting
* EvidenceAssociator: evidence-to-claim matching, stance assignment
* ClaimVerifier: all five verification statuses, evidence validation
* End-to-end pipeline integration with the orchestrator claims_node
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from app.services.claim_verification import (
    ClaimExtractor,
    EvidenceAssociator,
    ClaimVerifier,
    ExtractedClaim,
    EvidencePassage,
    VerificationResult,
)
from app.schemas.enums import ClaimStatus, EvidenceStance


# ─── ClaimExtractor ───────────────────────────────────────────────────────


class TestClaimExtractor:
    def test_extracts_atomic_claims(self):
        extractor = ClaimExtractor()
        text = (
            "The company reported $50 million in revenue last year. "
            "GPT-4 has approximately 1.76 trillion parameters. "
            "ChromaDB supports local persistent storage."
        )
        claims = extractor.extract(text, session_id="s1")
        assert len(claims) >= 1
        for c in claims:
            assert c.claim_id
            assert c.text
            assert c.claim_type in {
                "factual", "statistical", "causal", "comparative",
                "predictive", "definitional",
            }

    def test_splits_compound_sentences(self):
        extractor = ClaimExtractor()
        text = (
            "The company reported $50 million in revenue last year "
            "but profits declined by 10%."
        )
        claims = extractor.extract(text, session_id="s1")
        # Should produce at least 2 claims (revenue + profit decline)
        assert len(claims) >= 2

    def test_filters_opinions(self):
        extractor = ClaimExtractor()
        text = (
            "The company should increase its marketing budget. "
            "I believe the product is great. "
            "Revenue grew 20% last quarter."
        )
        claims = extractor.extract(text, session_id="s1")
        # Only the factual claim should survive
        claim_texts = [c.text for c in claims]
        assert any("20%" in t for t in claim_texts)
        assert not any("should" in t.lower() for t in claim_texts)
        assert not any("believe" in t.lower() for t in claim_texts)

    def test_empty_text_returns_empty(self):
        extractor = ClaimExtractor()
        assert extractor.extract("", session_id="s1") == []
        assert extractor.extract("   ", session_id="s1") == []

    def test_assigns_unique_ids(self):
        extractor = ClaimExtractor()
        text = "Revenue grew 20% last quarter. Profits declined 10%."
        claims = extractor.extract(text, session_id="s1")
        ids = [c.claim_id for c in claims]
        assert len(ids) == len(set(ids))  # all unique


# ─── EvidenceAssociator ───────────────────────────────────────────────────


class TestEvidenceAssociator:
    def test_associates_evidence_to_claims(self):
        associator = EvidenceAssociator()
        claims = [
            ExtractedClaim(
                claim_id="c1", text="Revenue grew 20% last quarter",
                claim_type="statistical",
            ),
            ExtractedClaim(
                claim_id="c2", text="ChromaDB supports local storage",
                claim_type="factual",
            ),
        ]
        evidence = [
            {
                "quote": "Revenue increased by 20% in Q3, driven by enterprise sales.",
                "url": "https://example.com/report",
                "title": "Annual Report",
                "source_id": "src-1",
            },
            {
                "quote": "ChromaDB is an open-source vector database with local persistence.",
                "url": "https://example.com/chroma",
                "title": "ChromaDB Docs",
                "source_id": "src-2",
            },
        ]
        passages = associator.associate(claims, evidence)
        assert len(passages) == 2
        # Each passage should be matched to a claim
        for p in passages:
            assert p.claim_id in ("c1", "c2")
            assert p.quote
            assert p.source_url
            assert p.stance in {
                EvidenceStance.SUPPORTS.value,
                EvidenceStance.CONTRADICTS.value,
                EvidenceStance.NEUTRAL.value,
            }

    def test_no_overlap_returns_empty(self):
        associator = EvidenceAssociator()
        claims = [
            ExtractedClaim(
                claim_id="c1", text="Quantum computing uses qubits",
                claim_type="factual",
            ),
        ]
        evidence = [
            {
                "quote": "The weather is sunny today in London.",
                "url": "https://example.com/weather",
                "title": "Weather Report",
            },
        ]
        passages = associator.associate(claims, evidence)
        assert passages == []

    def test_empty_inputs_return_empty(self):
        associator = EvidenceAssociator()
        assert associator.associate([], []) == []
        assert associator.associate(
            [ExtractedClaim(claim_id="c1", text="test", claim_type="factual")],
            [],
        ) == []


# ─── ClaimVerifier ───────────────────────────────────────────────────────


class _FakeLLM:
    """Deterministic fake LLM for testing verification statuses."""

    def __init__(self, responses: Dict[str, str]):
        self.responses = responses

    def invoke(self, messages):
        user_content = messages[-1].get("content", "") if messages else ""
        # Match by claim text substring
        for key, response in self.responses.items():
            if key in user_content:
                class _Resp:
                    content = response
                return _Resp()
        class _Resp:
            content = '{"status": "unverified", "confidence": 0.5, "reasoning": "no match"}'
        return _Resp()


class TestClaimVerifier:
    def test_supported_status(self):
        llm = _FakeLLM({
            "Revenue grew 20%": (
                '{"status": "supported", "confidence": 0.9, '
                '"reasoning": "Evidence directly confirms revenue growth.", '
                '"supporting_evidence_ids": ["e1"], '
                '"contradicting_evidence_ids": []}'
            ),
        })
        verifier = ClaimVerifier(llm=llm)
        claim = ExtractedClaim(
            claim_id="c1", text="Revenue grew 20% last quarter",
            claim_type="statistical",
        )
        evidence = [
            EvidencePassage(
                evidence_id="e1", claim_id="c1",
                source_id="src-1", source_url="https://example.com/r",
                source_title="Report", quote="Revenue increased by 20% in Q3.",
                stance=EvidenceStance.SUPPORTS.value, strength=0.9,
            ),
        ]
        result = verifier.verify(claim, evidence)
        assert result.status == ClaimStatus.SUPPORTED.value
        assert result.confidence >= 0.7
        assert "e1" in result.supporting_evidence_ids

    def test_partially_supported_status(self):
        llm = _FakeLLM({
            "Revenue grew 20%": (
                '{"status": "partially_supported", "confidence": 0.6, '
                '"reasoning": "Evidence confirms growth but not the exact figure.", '
                '"supporting_evidence_ids": ["e1"], '
                '"contradicting_evidence_ids": []}'
            ),
        })
        verifier = ClaimVerifier(llm=llm)
        claim = ExtractedClaim(
            claim_id="c1", text="Revenue grew 20% last quarter",
            claim_type="statistical",
        )
        evidence = [
            EvidencePassage(
                evidence_id="e1", claim_id="c1",
                source_id="src-1", source_url="https://example.com/r",
                source_title="Report",
                quote="Revenue increased in Q3 but exact figures unavailable.",
                stance=EvidenceStance.SUPPORTS.value, strength=0.5,
            ),
        ]
        result = verifier.verify(claim, evidence)
        assert result.status == ClaimStatus.PARTIALLY_SUPPORTED.value

    def test_contradicted_status(self):
        llm = _FakeLLM({
            "Revenue grew 20%": (
                '{"status": "contradicted", "confidence": 0.85, '
                '"reasoning": "Evidence shows revenue declined.", '
                '"supporting_evidence_ids": [], '
                '"contradicting_evidence_ids": ["e1"]}'
            ),
        })
        verifier = ClaimVerifier(llm=llm)
        claim = ExtractedClaim(
            claim_id="c1", text="Revenue grew 20% last quarter",
            claim_type="statistical",
        )
        evidence = [
            EvidencePassage(
                evidence_id="e1", claim_id="c1",
                source_id="src-1", source_url="https://example.com/r",
                source_title="Report",
                quote="Revenue declined by 10% in Q3.",
                stance=EvidenceStance.CONTRADICTS.value, strength=0.9,
            ),
        ]
        result = verifier.verify(claim, evidence)
        assert result.status == ClaimStatus.CONTRADICTED.value
        assert "e1" in result.contradicting_evidence_ids

    def test_unverified_status(self):
        llm = _FakeLLM({
            "Revenue grew 20%": (
                '{"status": "unverified", "confidence": 0.4, '
                '"reasoning": "Evidence is ambiguous.", '
                '"supporting_evidence_ids": [], '
                '"contradicting_evidence_ids": []}'
            ),
        })
        verifier = ClaimVerifier(llm=llm)
        claim = ExtractedClaim(
            claim_id="c1", text="Revenue grew 20% last quarter",
            claim_type="statistical",
        )
        evidence = [
            EvidencePassage(
                evidence_id="e1", claim_id="c1",
                source_id="src-1", source_url="https://example.com/r",
                source_title="Report",
                quote="Revenue figures are not disclosed.",
                stance=EvidenceStance.NEUTRAL.value, strength=0.3,
            ),
        ]
        result = verifier.verify(claim, evidence)
        assert result.status == ClaimStatus.UNVERIFIED.value

    def test_insufficient_context_status(self):
        verifier = ClaimVerifier(llm=None)
        claim = ExtractedClaim(
            claim_id="c1", text="Revenue grew 20% last quarter",
            claim_type="statistical",
        )
        result = verifier.verify(claim, [])
        assert result.status == ClaimStatus.INSUFFICIENT_CONTEXT.value
        assert result.confidence == 0.0
        assert "No evidence passages" in result.reasoning

    def test_llm_cannot_override_evidence_absence(self):
        """LLM cannot mark a claim 'supported' when no supporting evidence exists."""
        llm = _FakeLLM({
            "Revenue grew 20%": (
                '{"status": "supported", "confidence": 0.95, '
                '"reasoning": "Evidence confirms the claim.", '
                '"supporting_evidence_ids": ["e1"], '
                '"contradicting_evidence_ids": []}'
            ),
        })
        verifier = ClaimVerifier(llm=llm)
        claim = ExtractedClaim(
            claim_id="c1", text="Revenue grew 20% last quarter",
            claim_type="statistical",
        )
        # Evidence exists but is neutral/contradicting only, no support
        evidence = [
            EvidencePassage(
                evidence_id="e1", claim_id="c1",
                source_id="src-1", source_url="https://example.com/r",
                source_title="Report",
                quote="Revenue figures are not disclosed.",
                stance=EvidenceStance.NEUTRAL.value, strength=0.3,
            ),
        ]
        result = verifier.verify(claim, evidence)
        # Must NOT be supported — downgraded to unverified
        assert result.status != ClaimStatus.SUPPORTED.value
        assert result.status in {
            ClaimStatus.UNVERIFIED.value,
            ClaimStatus.INSUFFICIENT_CONTEXT.value,
        }


# ─── VerifyResult dataclass ──────────────────────────────────────────────


class TestVerificationResult:
    def test_all_statuses_are_valid(self):
        verifier = ClaimVerifier(llm=None)
        claim = ExtractedClaim(
            claim_id="c1", text="Test claim", claim_type="factual",
        )
        # No evidence → insufficient_context
        r = verifier.verify(claim, [])
        assert r.status == ClaimStatus.INSUFFICIENT_CONTEXT.value
        # Valid ClaimStatus values
        valid = {
            ClaimStatus.SUPPORTED.value,
            ClaimStatus.PARTIALLY_SUPPORTED.value,
            ClaimStatus.CONTRADICTED.value,
            ClaimStatus.UNVERIFIED.value,
            ClaimStatus.INSUFFICIENT_CONTEXT.value,
        }
        assert r.status in valid


# ─── Orchestrator claims_node integration ───────────────────────────────


class TestClaimsNodeIntegration:
    def test_claims_node_returns_state_with_claims(self):
        from orchestrator import MultiAgentOrchestrator

        orc = MultiAgentOrchestrator(max_revisions=1, approval_threshold=8)
        state: Dict[str, Any] = {
            "session_id": "s1",
            "user_id": "u1",
            "topic": "Test topic",
            "analysis": (
                "The company reported $50 million in revenue last year. "
                "GPT-4 has approximately 1.76 trillion parameters."
            ),
            "search_results_raw": [
                {
                    "title": "Annual Report",
                    "url": "https://example.com/report",
                    "snippet": "Revenue increased by $50 million last year.",
                    "source_name": "Example Corp",
                },
                {
                    "title": "AI Model Specs",
                    "url": "https://example.com/ai",
                    "snippet": "GPT-4 has approximately 1.76 trillion parameters.",
                    "source_name": "AI Lab",
                },
            ],
            "rag_context": "",
            "status": "running",
            "logs": [],
        }
        result = orc.claims_node(state)
        assert "claims" in result
        assert "claim_verification_results" in result
        assert len(result["claims"]) >= 1
        assert len(result["claim_verification_results"]) >= 1
        # Every claim has a unique ID
        claim_ids = [c["claim_id"] for c in result["claims"]]
        assert len(claim_ids) == len(set(claim_ids))
        # Every verification result references a claim
        for vr in result["claim_verification_results"]:
            assert vr["claim_id"] in claim_ids
            assert vr["status"] in {
                ClaimStatus.SUPPORTED.value,
                ClaimStatus.PARTIALLY_SUPPORTED.value,
                ClaimStatus.CONTRADICTED.value,
                ClaimStatus.UNVERIFIED.value,
                ClaimStatus.INSUFFICIENT_CONTEXT.value,
            }

    def test_claims_node_with_no_text_skips(self):
        from orchestrator import MultiAgentOrchestrator

        orc = MultiAgentOrchestrator(max_revisions=1, approval_threshold=8)
        state: Dict[str, Any] = {
            "session_id": "s1",
            "user_id": "u1",
            "topic": "Test topic",
            "analysis": "",
            "search_results_raw": [],
            "rag_context": "",
            "status": "running",
            "logs": [],
        }
        result = orc.claims_node(state)
        assert result["claims"] == []
        assert result["claim_verification_results"] == []
        assert "skipped" in result["logs"][-1].lower()
