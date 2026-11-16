"""Explainable source-credibility evaluation.

The evaluator deliberately does **not** call an LLM and does not treat a source
as credible merely because it looks academic. It scores only the metadata that
was actually supplied, records missing fields as missing, and exposes every
factor so a reader can audit the result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence
from urllib.parse import urlparse

from app.observability import get_logger
from app.schemas.source_credibility import (
    CredibilityFactor,
    CredibilityFactorStatus,
    CredibilityLabel,
    SourceCredibilityEvaluation,
)

logger = get_logger("app.source_credibility")


# Explicitly configured publisher/venue signals. Membership is a heuristic,
# never a claim that every article from the domain is reliable or peer reviewed.
_DEFAULT_PUBLISHER_SIGNALS: dict[str, tuple[float, str]] = {
    "nature.com": (0.90, "Recognized general-science publisher in the configured signal list."),
    "science.org": (0.90, "Recognized general-science publisher in the configured signal list."),
    "nejm.org": (0.90, "Recognized medical journal publisher in the configured signal list."),
    "thelancet.com": (0.88, "Recognized medical journal publisher in the configured signal list."),
    "jamanetwork.com": (0.88, "Recognized medical journal publisher in the configured signal list."),
    "bmj.com": (0.86, "Recognized medical journal publisher in the configured signal list."),
    "pubmed.ncbi.nlm.nih.gov": (0.85, "Recognized biomedical index in the configured signal list."),
    "nih.gov": (0.82, "Recognized public biomedical research institution in the configured signal list."),
    "who.int": (0.80, "Recognized public-health institution in the configured signal list."),
    "worldbank.org": (0.78, "Recognized public development institution in the configured signal list."),
    "oecd.org": (0.78, "Recognized public policy institution in the configured signal list."),
    "springer.com": (0.78, "Recognized scholarly publisher in the configured signal list."),
    "wiley.com": (0.78, "Recognized scholarly publisher in the configured signal list."),
    "elsevier.com": (0.78, "Recognized scholarly publisher in the configured signal list."),
    "tandfonline.com": (0.76, "Recognized scholarly publisher in the configured signal list."),
    "sagepub.com": (0.76, "Recognized scholarly publisher in the configured signal list."),
    "acm.org": (0.78, "Recognized computing-society publisher in the configured signal list."),
    "ieee.org": (0.78, "Recognized engineering-society publisher in the configured signal list."),
    "plos.org": (0.74, "Recognized open-access publisher in the configured signal list."),
    "arxiv.org": (0.62, "Recognized preprint repository; preprints are not automatically peer reviewed."),
    "wikipedia.org": (0.48, "Community-edited encyclopedia; useful for orientation, not primary evidence."),
}


@dataclass(frozen=True)
class CredibilityConfig:
    """Weights and thresholds for the heuristic evaluator.

    Set a weight to ``0`` to disable a factor. Weights are normalized by the
    total *available* weight, so missing metadata lowers confidence rather than
    being treated as negative evidence.
    """

    publisher_weight: float = 0.18
    author_weight: float = 0.12
    recency_weight: float = 0.10
    source_nature_weight: float = 0.10
    methodology_weight: float = 0.15
    references_weight: float = 0.10
    peer_review_weight: float = 0.15
    relevance_weight: float = 0.10
    recent_days: int = 730
    relevance_positive_threshold: float = 0.35
    minimum_confidence: float = 0.30
    publisher_signals: Mapping[str, tuple[float, str]] = field(
        default_factory=lambda: dict(_DEFAULT_PUBLISHER_SIGNALS)
    )

    @property
    def total_weight(self) -> float:
        return sum(
            max(0.0, float(value))
            for value in (
                self.publisher_weight,
                self.author_weight,
                self.recency_weight,
                self.source_nature_weight,
                self.methodology_weight,
                self.references_weight,
                self.peer_review_weight,
                self.relevance_weight,
            )
        )


DEFAULT_CREDIBILITY_CONFIG = CredibilityConfig()


class SourceCredibilityService:
    """Score sources with deterministic, inspectable heuristics."""

    def __init__(self, config: Optional[CredibilityConfig] = None) -> None:
        self.config = config or DEFAULT_CREDIBILITY_CONFIG

    # ── Public API ────────────────────────────────────────────────────────

    def evaluate(
        self,
        source: Mapping[str, Any] | Any,
        *,
        query: str = "",
    ) -> SourceCredibilityEvaluation:
        """Evaluate one source mapping or Pydantic model-like object."""
        data = self._as_mapping(source)
        # Accept provider metadata either at the top level or under a nested
        # ``metadata`` mapping, without inventing absent values.
        nested = data.get("metadata")
        if isinstance(nested, Mapping):
            for key in (
                "publisher", "authors", "author", "publication_date",
                "published_at", "doi", "venue", "methodology",
                "peer_reviewed", "references_available", "source_nature",
            ):
                if data.get(key) in (None, "", [], {}) and nested.get(key) not in (
                    None, "", [], {}
                ):
                    data[key] = nested[key]
        metadata = self._metadata_snapshot(data)
        factors: list[CredibilityFactor] = []

        publisher, publisher_factor = self._publisher_factor(data)
        factors.append(publisher_factor)
        factors.append(self._author_factor(data))
        factors.append(self._recency_factor(data))
        factors.append(self._source_nature_factor(data))
        factors.append(self._methodology_factor(data))
        factors.append(self._references_factor(data))
        factors.append(self._peer_review_factor(data))
        factors.append(self._relevance_factor(data, query=query))

        available_weight = sum(
            f.weight
            for f in factors
            if f.status is not CredibilityFactorStatus.MISSING and f.weight > 0
        )
        weighted_score = sum(
            f.weight * self._factor_value(f)
            for f in factors
            if f.status is not CredibilityFactorStatus.MISSING and f.weight > 0
        )
        total_weight = self.config.total_weight
        confidence = (
            min(1.0, available_weight / total_weight) if total_weight > 0 else 0.0
        )
        if available_weight <= 0 or confidence < self.config.minimum_confidence:
            score: Optional[float] = None
        else:
            score = round(weighted_score / available_weight, 3)

        missing_fields = [
            f.factor for f in factors if f.status is CredibilityFactorStatus.MISSING
        ]
        label = self._label(score, confidence)
        return SourceCredibilityEvaluation(
            source_id=self._string_or_none(data.get("source_id")),
            source_url=self._string_or_none(data.get("url")),
            title=self._string_or_none(data.get("title")),
            publisher=publisher or self._domain(self._string_or_none(data.get("url"))),
            source_type=self._string_or_none(data.get("source_type")),
            score=score,
            confidence=round(confidence, 3),
            label=label,
            factors=factors,
            missing_fields=missing_fields,
            metadata=metadata,
        )

    def evaluate_many(
        self,
        sources: Iterable[Mapping[str, Any] | Any],
        *,
        query: str = "",
    ) -> list[SourceCredibilityEvaluation]:
        """Evaluate each source independently; one failure never drops a source."""
        evaluations: list[SourceCredibilityEvaluation] = []
        for source in sources:
            try:
                evaluations.append(self.evaluate(source, query=query))
            except Exception:
                logger.exception("source_credibility.evaluation_failed")
        return evaluations

    # ── Factor builders ──────────────────────────────────────────────────

    @staticmethod
    def _as_mapping(source: Mapping[str, Any] | Any) -> dict[str, Any]:
        if isinstance(source, Mapping):
            return dict(source)
        if hasattr(source, "model_dump"):
            return dict(source.model_dump())
        return {
            key: getattr(source, key)
            for key in (
                "source_id",
                "title",
                "url",
                "snippet",
                "source_name",
                "source_type",
                "publisher",
                "authors",
                "author",
                "publication_date",
                "published_at",
                "doi",
                "venue",
                "methodology",
                "peer_reviewed",
                "references_available",
                "source_nature",
                "metadata",
            )
            if hasattr(source, key)
        }

    def _publisher_factor(
        self, data: Mapping[str, Any]
    ) -> tuple[Optional[str], CredibilityFactor]:
        publisher = self._string_or_none(data.get("publisher"))
        domain = self._domain(self._string_or_none(data.get("url")))
        website = publisher or domain
        keys = [
            self._normalize_key(value)
            for value in (publisher, domain)
            if value
        ]
        for key in keys:
            signal = self._publisher_signal(key)
            if signal is not None:
                value, reason = signal
                return website, CredibilityFactor(
                    factor="publisher_reputation",
                    status=(
                        CredibilityFactorStatus.POSITIVE
                        if value >= 0.60
                        else CredibilityFactorStatus.NEUTRAL
                    ),
                    weight=self.config.publisher_weight,
                    value=value,
                    reason=reason,
                    observed=website,
                )
        observed = website
        return website, CredibilityFactor(
            factor="publisher_reputation",
            status=CredibilityFactorStatus.MISSING,
            weight=self.config.publisher_weight,
            reason=(
                "No independently verified publisher reputation signal is available; "
                "an unknown publisher is not treated as a negative signal."
            ),
            observed=observed,
        )

    def _author_factor(self, data: Mapping[str, Any]) -> CredibilityFactor:
        authors = data.get("authors")
        author = data.get("author")
        names: list[str] = []
        if isinstance(authors, str):
            names = [authors]
        elif isinstance(authors, (list, tuple)):
            names = [str(item) for item in authors if item]
        if author:
            names.append(str(author))
        names = [name.strip() for name in names if name and name.strip()]
        names = [
            name
            for name in names
            if name.lower() not in {"unknown", "anonymous", "n/a", "none", "staff"}
        ]
        if names:
            return CredibilityFactor(
                factor="author_information",
                status=CredibilityFactorStatus.POSITIVE,
                weight=self.config.author_weight,
                reason="At least one named author or creator is present.",
                observed=", ".join(names)[:200],
            )
        return CredibilityFactor(
            factor="author_information",
            status=CredibilityFactorStatus.MISSING,
            weight=self.config.author_weight,
            reason=(
                "No author information was supplied. This is missing data, not "
                "evidence that the source is unreliable."
            ),
        )

    def _recency_factor(self, data: Mapping[str, Any]) -> CredibilityFactor:
        raw = data.get("publication_date") or data.get("published_at")
        published = self._parse_date(raw)
        if published is None:
            return CredibilityFactor(
                factor="publication_recency",
                status=CredibilityFactorStatus.MISSING,
                weight=self.config.recency_weight,
                reason=(
                    "Publication date is unavailable or unparseable. Recency is "
                    "not inferred from the current date."
                ),
                observed=self._string_or_none(raw),
            )
        age_days = (datetime.now(timezone.utc).date() - published).days
        if age_days < 0:
            return CredibilityFactor(
                factor="publication_recency",
                status=CredibilityFactorStatus.NEUTRAL,
                weight=self.config.recency_weight,
                reason="Publication date is in the future; recency is not treated as a quality verdict.",
                observed=published.isoformat(),
            )
        if age_days <= self.config.recent_days:
            return CredibilityFactor(
                factor="publication_recency",
                status=CredibilityFactorStatus.POSITIVE,
                weight=self.config.recency_weight,
                reason="Publication is within the configured recent window.",
                observed=f"{published.isoformat()} ({age_days} days old)",
            )
        return CredibilityFactor(
            factor="publication_recency",
            status=CredibilityFactorStatus.NEUTRAL,
            weight=self.config.recency_weight,
            reason=(
                "Publication is older than the configured recent window. Older "
                "sources are not automatically considered unreliable."
            ),
            observed=f"{published.isoformat()} ({age_days} days old)",
        )

    def _source_nature_factor(self, data: Mapping[str, Any]) -> CredibilityFactor:
        value = self._string_or_none(data.get("source_nature"))
        if value:
            normalized = value.lower().strip()
            if normalized in {"primary", "primary_research", "original"}:
                return CredibilityFactor(
                    factor="primary_vs_secondary",
                    status=CredibilityFactorStatus.POSITIVE,
                    weight=self.config.source_nature_weight,
                    reason="Metadata explicitly identifies this as primary or original research.",
                    observed=value,
                )
            if normalized in {"secondary", "secondary_research", "review", "analysis"}:
                return CredibilityFactor(
                    factor="primary_vs_secondary",
                    status=CredibilityFactorStatus.NEUTRAL,
                    weight=self.config.source_nature_weight,
                    reason=(
                        "Metadata explicitly identifies this as secondary research; "
                        "secondary sources can still be valuable and are not penalized."
                    ),
                    observed=value,
                )
        return CredibilityFactor(
            factor="primary_vs_secondary",
            status=CredibilityFactorStatus.MISSING,
            weight=self.config.source_nature_weight,
            reason=(
                "Primary/secondary research status is not explicitly supplied; "
                "source type alone is not used to guess it."
            ),
            observed=value,
        )

    def _methodology_factor(self, data: Mapping[str, Any]) -> CredibilityFactor:
        value = self._string_or_none(data.get("methodology"))
        if value:
            return CredibilityFactor(
                factor="research_methodology",
                status=CredibilityFactorStatus.POSITIVE,
                weight=self.config.methodology_weight,
                reason="A research methodology description is present.",
                observed=value,
            )
        return CredibilityFactor(
            factor="research_methodology",
            status=CredibilityFactorStatus.MISSING,
            weight=self.config.methodology_weight,
            reason=(
                "No methodology description was supplied. This is missing data, "
                "not evidence of weak methodology."
            ),
        )

    def _references_factor(self, data: Mapping[str, Any]) -> CredibilityFactor:
        value = data.get("references_available")
        if value is True:
            return CredibilityFactor(
                factor="supporting_references",
                status=CredibilityFactorStatus.POSITIVE,
                weight=self.config.references_weight,
                reason="Source metadata explicitly reports supporting references.",
                observed="available",
            )
        if value is False:
            return CredibilityFactor(
                factor="supporting_references",
                status=CredibilityFactorStatus.NEGATIVE,
                weight=self.config.references_weight,
                reason=(
                    "Source metadata explicitly reports no supporting references; "
                    "this is a negative quality signal, not proof of unreliability."
                ),
                observed="not available",
            )
        return CredibilityFactor(
            factor="supporting_references",
            status=CredibilityFactorStatus.MISSING,
            weight=self.config.references_weight,
            reason="Supporting-reference availability was not supplied.",
        )

    def _peer_review_factor(self, data: Mapping[str, Any]) -> CredibilityFactor:
        value = data.get("peer_reviewed")
        if value is True:
            return CredibilityFactor(
                factor="peer_review_status",
                status=CredibilityFactorStatus.POSITIVE,
                weight=self.config.peer_review_weight,
                reason="Peer-review status is explicitly recorded as verified in the supplied metadata.",
                observed="peer reviewed",
            )
        if value is False:
            return CredibilityFactor(
                factor="peer_review_status",
                status=CredibilityFactorStatus.NEUTRAL,
                weight=self.config.peer_review_weight,
                reason=(
                    "Metadata reports that the source is not peer reviewed. This "
                    "is useful context but is not treated as automatic unreliability."
                ),
                observed="not peer reviewed",
            )
        return CredibilityFactor(
            factor="peer_review_status",
            status=CredibilityFactorStatus.MISSING,
            weight=self.config.peer_review_weight,
            reason=(
                "Peer-review status was not independently verified, so it is "
                "treated as missing rather than assumed either way."
            ),
        )

    def _relevance_factor(
        self, data: Mapping[str, Any], *, query: str
    ) -> CredibilityFactor:
        query_terms = self._terms(query)
        source_text = " ".join(
            str(data.get(key) or "")
            for key in ("title", "snippet")
        )
        source_terms = self._terms(source_text)
        if not query_terms or not source_terms:
            return CredibilityFactor(
                factor="query_relevance",
                status=CredibilityFactorStatus.MISSING,
                weight=self.config.relevance_weight,
                reason="There is not enough text to estimate relevance to the research question.",
            )
        overlap = len(query_terms & source_terms) / len(query_terms)
        status = (
            CredibilityFactorStatus.POSITIVE
            if overlap >= self.config.relevance_positive_threshold
            else CredibilityFactorStatus.NEUTRAL
        )
        return CredibilityFactor(
            factor="query_relevance",
            status=status,
            weight=self.config.relevance_weight,
            reason=(
                "Lexical overlap with the research question is a relevance signal, "
                "not a measure of factual accuracy."
            ),
            observed=f"{overlap:.0%} query-term overlap",
        )

    # ── Helpers ──────────────────────────────────────────────────────────

    def _publisher_signal(self, key: str) -> Optional[tuple[float, str]]:
        """Match exact domains and their subdomains, never arbitrary suffixes."""
        for domain, signal in self.config.publisher_signals.items():
            if key == domain or key.endswith("." + domain):
                return signal
        return None

    @staticmethod
    def _factor_value(factor: CredibilityFactor) -> float:
        if factor.value is not None:
            return max(0.0, min(1.0, float(factor.value)))
        if factor.status is CredibilityFactorStatus.POSITIVE:
            return 0.85
        if factor.status is CredibilityFactorStatus.NEGATIVE:
            return 0.20
        return 0.50

    @staticmethod
    def _label(
        score: Optional[float], confidence: float
    ) -> CredibilityLabel:
        if score is None:
            return CredibilityLabel.INSUFFICIENT_INFORMATION
        # A high weighted score from only one or two fields is still a narrow
        # evidence base; do not present it as a confident assessment.
        if confidence < 0.50:
            return CredibilityLabel.LIMITED
        if score >= 0.75:
            return CredibilityLabel.HIGH
        if score >= 0.55:
            return CredibilityLabel.MODERATE
        return CredibilityLabel.LIMITED

    @staticmethod
    def _string_or_none(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _normalize_key(value: str) -> str:
        value = value.lower().strip()
        value = re.sub(r"^https?://", "", value)
        value = value.split("/")[0]
        return value[4:] if value.startswith("www.") else value

    @classmethod
    def _domain(cls, url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        try:
            return cls._normalize_key(urlparse(url).netloc)
        except Exception:
            return None

    @staticmethod
    def _parse_date(value: Any) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if not text:
            return None
        # Accept common ISO forms and a bare year, but never guess a full date.
        candidates = [text]
        if text.endswith("Z"):
            candidates.append(text[:-1] + "+00:00")
        for candidate in candidates:
            try:
                return datetime.fromisoformat(candidate).date()
            except ValueError:
                continue
        if re.fullmatch(r"\d{4}", text):
            return date(int(text), 1, 1)
        return None

    @staticmethod
    def _terms(text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9]{3,}", text.lower())
            if token not in {
                "the", "and", "for", "with", "from", "that", "this", "into",
                "are", "was", "were", "has", "have", "about", "over",
            }
        }

    def _metadata_snapshot(self, data: Mapping[str, Any]) -> dict[str, Any]:
        keys = (
            "source_id",
            "title",
            "url",
            "publisher",
            "authors",
            "author",
            "publication_date",
            "published_at",
            "doi",
            "venue",
            "methodology",
            "peer_reviewed",
            "references_available",
            "source_nature",
            "source_type",
            "source_name",
        )
        snapshot = {
            key: data.get(key)
            for key in keys
            if data.get(key) not in (None, "", [], {})
        }
        if "authors" in snapshot and not isinstance(snapshot["authors"], list):
            snapshot["authors"] = [snapshot["authors"]]
        return snapshot


def format_credibility_context(
    evaluations: Sequence[Mapping[str, Any] | Any],
    *,
    limit: int = 12,
) -> str:
    """Render a compact, explicitly caveated context block for the writer."""
    entries = [_evaluation_mapping(item) for item in evaluations][:limit]
    if not entries:
        return ""
    lines = [
        "SOURCE CREDIBILITY (heuristic, not a verification of truth):",
        "Use this as context about source quality. Do not present scores as "
        "objective facts, and do not discard a source solely because its "
        "credibility is uncertain.",
    ]
    for item in entries:
        title = item.get("title") or item.get("source_url") or "Untitled source"
        score = item.get("score")
        score_text = "insufficient information" if score is None else f"{score:.2f}"
        url = item.get("source_url")
        label = item.get("label", "insufficient_information")
        label = getattr(label, "value", label)
        lines.append(
            f"- {title} | {url or 'URL unavailable'} | score={score_text} "
            f"| confidence={item.get('confidence', 0):.2f} | label={label}"
        )
        reasons = [
            str(factor.get("reason"))
            for factor in item.get("factors", [])
            if factor.get("status") in {"positive", "negative"}
        ]
        if reasons:
            lines.append("  " + " ".join(reasons[:3]))
        missing = item.get("missing_fields", [])
        if missing:
            lines.append(f"  Missing metadata: {', '.join(missing[:6])}")
    return "\n".join(lines)


def format_credibility_appendix(
    evaluations: Sequence[Mapping[str, Any] | Any],
) -> str:
    """Render a transparent credibility appendix for a saved report."""
    entries = [_evaluation_mapping(item) for item in evaluations]
    if not entries:
        return ""
    lines = [
        "\n\n---\n",
        "## Source Credibility Assessment\n",
        "> Heuristic assessment of available metadata only. Scores are not "
        "objective truth ratings, and missing information is not treated as "
        "negative evidence. Sources are not rejected on this basis.\n\n",
        "| Source | Score | Confidence | Label | Key caveats |\n",
        "|---|---:|---:|---|---|\n",
    ]
    for item in entries:
        title = _escape_table(str(item.get("title") or item.get("source_url") or "Untitled"))
        score = item.get("score")
        score_text = "—" if score is None else f"{float(score):.2f}"
        missing = ", ".join(item.get("missing_fields", [])[:5]) or "none"
        label = str(item.get("label", "insufficient_information"))
        lines.append(
            f"| {title} | {score_text} | {float(item.get('confidence', 0)):.2f} "
            f"| {label} | Missing: {_escape_table(missing)} |\n"
        )
    return "".join(lines)


def _evaluation_mapping(item: Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return dict(item)
    if hasattr(item, "model_dump"):
        return dict(item.model_dump(mode="json"))
    return dict(item)


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")[:160]


__all__ = [
    "CredibilityConfig",
    "DEFAULT_CREDIBILITY_CONFIG",
    "SourceCredibilityService",
    "format_credibility_appendix",
    "format_credibility_context",
]
