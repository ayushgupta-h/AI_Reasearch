"""Citation extraction and URL validation with fully mocked HTTP."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from citation_validator import CitationReport, CitationValidator


@pytest.fixture
def validator(monkeypatch) -> CitationValidator:
    monkeypatch.setattr("citation_validator.console", MagicMock())
    return CitationValidator(timeout=1.0, max_workers=2)


def _response(status_code: int) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    return response


class TestUrlExtraction:
    def test_extracts_bare_and_markdown_urls(self) -> None:
        text = (
            "See https://arxiv.org/abs/2301.00001 and [the docs](https://example.com/docs)."
        )
        assert CitationValidator.extract_urls(text) == [
            "https://arxiv.org/abs/2301.00001",
            "https://example.com/docs",
        ]

    def test_strips_trailing_punctuation(self) -> None:
        urls = CitationValidator.extract_urls("Source: https://example.com/report.")
        assert urls == ["https://example.com/report"]

    def test_deduplicates_preserving_order(self) -> None:
        urls = CitationValidator.extract_urls(
            "https://b.com https://a.com https://b.com"
        )
        assert urls == ["https://b.com", "https://a.com"]

    def test_extracts_no_urls_from_plain_text(self) -> None:
        assert CitationValidator.extract_urls("No links here.") == []

    def test_domain_extraction(self) -> None:
        assert (
            CitationValidator.extract_domain("https://arxiv.org/abs/2301.00001")
            == "arxiv.org"
        )


class TestSingleUrlChecks:
    def test_live_url_is_verified(self, validator, monkeypatch) -> None:
        monkeypatch.setattr(
            "citation_validator.requests.head", lambda *a, **k: _response(200)
        )
        result = validator._check_url("https://example.com/live")
        assert result.is_live is True
        assert result.status_label == "verified"
        assert result.status_code == 200
        assert result.error_message is None

    def test_missing_page_is_broken(self, validator, monkeypatch) -> None:
        monkeypatch.setattr(
            "citation_validator.requests.head", lambda *a, **k: _response(404)
        )
        result = validator._check_url("https://example.com/missing")
        assert result.is_live is False
        assert result.status_label == "broken"
        assert result.error_message == "HTTP 404"

    def test_head_403_falls_back_to_get(self, validator, monkeypatch) -> None:
        head = MagicMock(return_value=_response(403))
        get = MagicMock(return_value=_response(200))
        monkeypatch.setattr("citation_validator.requests.head", head)
        monkeypatch.setattr("citation_validator.requests.get", get)

        result = validator._check_url("https://example.com/blocked")

        assert result.status_label == "verified"
        head.assert_called_once()
        get.assert_called_once()
        assert get.call_args.kwargs.get("stream") is True

    def test_timeout_is_labeled(self, validator, monkeypatch) -> None:
        monkeypatch.setattr(
            "citation_validator.requests.head",
            MagicMock(side_effect=requests.exceptions.Timeout("slow")),
        )
        result = validator._check_url("https://example.com/slow")
        assert result.status_label == "timeout"
        assert result.status_code is None
        assert "Timed out" in result.error_message

    def test_connection_error_is_broken(self, validator, monkeypatch) -> None:
        monkeypatch.setattr(
            "citation_validator.requests.head",
            MagicMock(side_effect=requests.exceptions.ConnectionError("refused")),
        )
        result = validator._check_url("https://unreachable.invalid/x")
        assert result.status_label == "broken"
        assert result.error_message.startswith("Connection error")

    def test_unexpected_error_is_contained(self, validator, monkeypatch) -> None:
        monkeypatch.setattr(
            "citation_validator.requests.head",
            MagicMock(side_effect=RuntimeError("ssl exploded")),
        )
        result = validator._check_url("https://example.com/x")
        assert result.status_label == "error"
        assert "ssl exploded" in result.error_message


class TestBatchValidation:
    def test_counts_and_ordering(self, validator) -> None:
        def fake_check(url: str):
            from citation_validator import CitationResult

            live = "live" in url
            return CitationResult(
                url=url,
                domain=CitationValidator.extract_domain(url),
                status_code=200 if live else 404,
                status_label="verified" if live else "broken",
                response_time_ms=1.0,
                error_message=None if live else "HTTP 404",
            )

        validator._check_url = fake_check  # type: ignore[method-assign]
        report = validator.validate_urls(
            [
                "https://a.com/broken",
                "https://b.com/live",
                "https://c.com/live",
            ]
        )

        assert report.total == 3
        assert report.verified == 2
        assert report.broken == 1
        assert report.results[0].is_live is True
        assert report.results[-1].is_live is False

    def test_empty_input_returns_empty_report(self, validator) -> None:
        report = validator.validate_urls([])
        assert isinstance(report, CitationReport)
        assert report.total == 0
        assert report.pass_rate == 0.0

    def test_validate_report_without_urls(self, validator) -> None:
        report = validator.validate_report("No links in this report.")
        assert report.total == 0

    def test_validate_report_counts_mocked_urls(self, validator, monkeypatch) -> None:
        monkeypatch.setattr(
            "citation_validator.requests.head",
            lambda url, **kwargs: _response(200 if "good" in url else 500),
        )
        report = validator.validate_report(
            "Good: https://good.example/x  Bad: https://bad.example/y"
        )
        assert report.total == 2
        assert report.verified == 1
        assert report.broken == 1


class TestAppendixInjection:
    def test_appendix_contains_summary_and_rows(self, validator) -> None:
        validator._check_url = lambda url: __import__(  # type: ignore[method-assign]
            "citation_validator"
        ).CitationResult(
            url=url,
            domain="example.com",
            status_code=200,
            status_label="verified",
            response_time_ms=2.0,
        )
        report = validator.validate_urls(["https://example.com/a"])
        augmented = validator.inject_appendix("# Report", report)

        assert augmented.startswith("# Report")
        assert "Citation Validation Appendix" in augmented
        assert "1/1 sources live" in augmented
        assert "https://example.com/a" in augmented

    def test_empty_report_is_returned_unchanged(self, validator) -> None:
        text = "# Report"
        assert validator.inject_appendix(text, CitationReport()) == text
