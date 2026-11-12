"""SQLite session persistence: creation, filtering, deletion authorization."""

from __future__ import annotations

import time
import uuid
from unittest.mock import MagicMock

import pytest

from session_manager import PipelineTimer, SessionManager


@pytest.fixture
def manager(tmp_path, monkeypatch) -> SessionManager:
    # The DB lives in a TemporaryDirectory: SessionManager opens a new
    # connection per call, so a shared ``:memory:`` database would not persist.
    monkeypatch.setattr("session_manager.console", MagicMock())
    return SessionManager(db_path=str(tmp_path / "sessions.db"))


def _state(**overrides) -> dict:
    state = {
        "topic": "LLM scaling laws",
        "research_request": {"refined_query": "Empirical scaling laws for LLMs"},
        "provider": "gemini",
        "critic_score": 9,
        "revision_count": 1,
        "status": "completed",
        "saved_report_path": "/reports/report_20260101_llm.md",
        "logs": [],
    }
    state.update(overrides)
    return state


class TestSessionCreation:
    def test_save_creates_a_uuid_record(self, manager: SessionManager) -> None:
        session_id = manager.save_session(_state(), duration_seconds=12.5, user_id="user-a")
        uuid.UUID(session_id)  # valid UUID4 string
        record = manager.get_session(session_id)
        assert record is not None
        assert record["topic"] == "Empirical scaling laws for LLMs"
        assert record["provider"] == "gemini"
        assert record["critic_score"] == 9
        assert record["duration_seconds"] == 12.5
        assert record["user_id"] == "user-a"

    def test_citation_stats_are_parsed_from_logs(self, manager: SessionManager) -> None:
        session_id = manager.save_session(
            _state(logs=["Citations validated: 12/14 live (86%)"]), user_id="user-a"
        )
        record = manager.get_session(session_id)
        assert record["citations_verified"] == 12
        assert record["citations_total"] == 14

    def test_export_paths_are_parsed_from_logs(self, manager: SessionManager) -> None:
        session_id = manager.save_session(
            _state(
                logs=[
                    "DOCX saved to report_20260101_llm.docx",
                    "PDF saved to report_20260101_llm.pdf",
                ]
            ),
            user_id="user-a",
        )
        record = manager.get_session(session_id)
        assert record["docx_path"].endswith(".docx")
        assert record["pdf_path"].endswith(".pdf")

    def test_missing_refined_query_falls_back_to_topic(self, manager: SessionManager) -> None:
        session_id = manager.save_session(
            {"topic": "Fallback topic", "status": "cancelled"}, user_id="user-a"
        )
        assert manager.get_session(session_id)["topic"] == "Fallback topic"


class TestSessionListing:
    def test_user_filter_only_returns_own_sessions(self, manager: SessionManager) -> None:
        first = manager.save_session(_state(topic="A"), user_id="user-a")
        second = manager.save_session(_state(topic="B"), user_id="user-b")

        own = manager.get_all_sessions(user_id="user-a")
        assert [row["session_id"] for row in own] == [first]
        assert second not in [row["session_id"] for row in own]

    def test_unfiltered_listing_returns_all(self, manager: SessionManager) -> None:
        manager.save_session(_state(topic="A"), user_id="user-a")
        manager.save_session(_state(topic="B"), user_id="user-b")
        assert len(manager.get_all_sessions()) == 2

    def test_get_session_hides_other_users_records(self, manager: SessionManager) -> None:
        session_id = manager.save_session(_state(), user_id="user-a")
        assert manager.get_session(session_id, user_id="user-b") is None


class TestDeletionAuthorization:
    def test_delete_requires_the_owning_user(self, manager: SessionManager) -> None:
        session_id = manager.save_session(_state(), user_id="user-a")

        assert manager.delete_session(session_id, user_id="user-b") is False
        assert manager.get_session(session_id) is not None

        assert manager.delete_session(session_id, user_id="user-a") is True
        assert manager.get_session(session_id) is None

    def test_delete_unknown_session_returns_false(self, manager: SessionManager) -> None:
        assert manager.delete_session("does-not-exist", user_id="user-a") is False


class TestReportOwnership:
    def test_owner_is_matched_by_report_filename(self, manager: SessionManager) -> None:
        manager.save_session(_state(), user_id="user-a")
        assert manager.user_owns_report("user-a", "report_20260101_llm.md") is True
        assert manager.user_owns_report("user-b", "report_20260101_llm.md") is False

    def test_unknown_report_is_not_owned(self, manager: SessionManager) -> None:
        assert manager.user_owns_report("user-a", "missing.md") is False

    def test_legacy_unowned_session_is_claimed_once(self, manager: SessionManager) -> None:
        session_id = manager.save_session(_state(), user_id=None)

        assert manager.user_owns_report("user-a", "report_20260101_llm.md") is True
        assert manager.get_session(session_id)["user_id"] == "user-a"

    def test_blank_inputs_are_never_owned(self, manager: SessionManager) -> None:
        assert manager.user_owns_report("", "report.md") is False
        assert manager.user_owns_report("user-a", "") is False


class TestPipelineTimer:
    def test_timer_measures_elapsed_time(self) -> None:
        with PipelineTimer() as timer:
            time.sleep(0.01)
        assert timer.elapsed >= 0.01
