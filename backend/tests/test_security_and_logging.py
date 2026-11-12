"""
tests/test_security_and_logging.py
=================================
Security primitives (share tokens), log redaction and SSE stream isolation.
"""

from __future__ import annotations

import io
import json
import logging
import threading

from app.observability import JsonFormatter, log_context, redact
from app.security.share_tokens import generate_share_token, verify_share_token
from app.utils.stdout_proxy import capture_stdout


class TestShareTokens:
    def test_token_is_deterministic(self) -> None:
        assert generate_share_token("report_a.md") == generate_share_token("report_a.md")

    def test_token_is_bound_to_filename(self) -> None:
        token = generate_share_token("report_a.md")
        assert verify_share_token("report_a.md", token) is True
        assert verify_share_token("report_b.md", token) is False

    def test_empty_or_foreign_token_is_rejected(self) -> None:
        assert verify_share_token("report_a.md", "") is False
        assert verify_share_token("report_a.md", "0" * 24) is False

    def test_token_length_matches_public_contract(self) -> None:
        assert len(generate_share_token("report_a.md")) == 24


class TestRedaction:
    def test_jwt_is_masked(self) -> None:
        jwt_like = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"
        assert "eyJ" not in redact(f"token={jwt_like}")

    def test_provider_keys_are_masked(self) -> None:
        assert "sk-proj-abcdefghijklmnop" not in redact("key sk-proj-abcdefghijklmnop")
        assert "gsk_abcdefghijklmnop" not in redact("key gsk_abcdefghijklmnop")

    def test_query_credentials_are_masked(self) -> None:
        masked = redact("/reports/a.md?token=abcdef123&share=zzz")
        assert "abcdef123" not in masked
        assert "zzz" not in masked
        assert "token=" in masked

    def test_bearer_header_is_masked(self) -> None:
        assert "abcdef123456" not in redact("Authorization: Bearer abcdef123456")


class TestStructuredLogging:
    def test_json_formatter_emits_correlation_ids(self) -> None:
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="research.started",
            args=(),
            exc_info=None,
        )
        record.request_id = "req123"
        record.research_id = "run456"
        record.agent = "search"
        record.duration_ms = 12.5
        payload = json.loads(JsonFormatter().format(record))
        assert payload["request_id"] == "req123"
        assert payload["research_id"] == "run456"
        assert payload["agent"] == "search"
        assert payload["duration_ms"] == 12.5

    def test_log_context_is_restored(self) -> None:
        from app.observability import current_request_id

        before = current_request_id()
        with log_context(request_id="abc"):
            assert current_request_id() == "abc"
        assert current_request_id() == before

    def test_log_context_rejects_unknown_keys(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            with log_context(not_a_key="x"):
                pass


class TestStreamIsolation:
    def test_captured_stdout_is_thread_local(self) -> None:
        """Two concurrent runs must never see each other's captured output."""
        first, second = io.StringIO(), io.StringIO()
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def run(target: io.StringIO, message: str) -> None:
            try:
                with capture_stdout(target):
                    barrier.wait(timeout=5)
                    print(message)
            except BaseException as exc:  # pragma: no cover - failure reporting
                errors.append(exc)

        threads = [
            threading.Thread(target=run, args=(first, "stream-one")),
            threading.Thread(target=run, args=(second, "stream-two")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert not errors
        assert "stream-one" in first.getvalue()
        assert "stream-two" not in first.getvalue()
        assert "stream-two" in second.getvalue()
        assert "stream-one" not in second.getvalue()
