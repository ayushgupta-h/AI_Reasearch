"""
tests/test_document_chat.py
===========================
Unit and integration tests for Conversational Document Analysis and QA.
"""

import dataclasses
import io
import uuid
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.factory import create_app
from app.infrastructure import SQLiteDatabase, StoredObject
from app.repositories.agent_history import AgentHistoryRepository
from app.repositories.documents import DocumentRepository
from app.repositories.projects import ProjectRepository
from app.security.dependencies import get_current_user
from app.services.agent_history_service import AgentHistoryService
from app.services.chat_service import ChatService
from app.services.container import get_document_service, get_document_chat_service
from app.services.document_service import DocumentService
from app.services.document_chat_service import DocumentChatService
from app.services.project_service import ProjectService


class FakeObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: list[dict] = []
        self.deleted: list[str] = []

    def upload(self, key, data, *, content_type="application/octet-stream", metadata=None):
        payload = data if isinstance(data, (bytes, bytearray, memoryview)) else data.read()
        self.objects[key] = bytes(payload)
        return StoredObject(
            key=key,
            size=len(payload),
            content_type=content_type,
            metadata=dict(metadata or {}),
        )

    def download(self, key):
        return self.objects[key]

    def delete(self, key):
        self.deleted.append(key)


class FakeChatService(ChatService):
    def __init__(self, settings):
        super().__init__(settings)

    def provider_chain(self):
        return ["fake_provider"]

    def _execute_chat(self, system_prompt, user_prompt):
        return (
            "According to the document [1], quantum computers operate on qubits which enable exponential parallelism.",
            "fake_provider",
        )

    def _build_llm(self, provider):
        class FakeLLM:
            def invoke(self, messages):
                class FakeRes:
                    content = (
                        "According to the document [1], quantum computers operate on qubits "
                        "which enable exponential parallelism for complex optimization problems [1]."
                    )
                return FakeRes()
        return FakeLLM()


@pytest.fixture
def test_setup(tmp_path: Path):
    base = get_settings()
    settings = dataclasses.replace(
        base,
        reports_dir=tmp_path / "reports",
        exports_dir=tmp_path / "reports" / "exports",
        sessions_db_path=tmp_path / "sessions.db",
        max_document_bytes=1024 * 1024,
        log_file=None,
    )
    database = SQLiteDatabase(settings.sessions_db_path)
    storage = FakeObjectStorage()
    agent_hist_repo = AgentHistoryRepository(database)
    agent_hist = AgentHistoryService(agent_hist_repo)
    proj_repo = ProjectRepository(database)
    proj_service = ProjectService(proj_repo, agent_hist)
    doc_repo = DocumentRepository(database)
    doc_service = DocumentService(doc_repo, storage, proj_service, agent_hist, max_bytes=settings.max_document_bytes)
    fake_chat = FakeChatService(settings)
    doc_chat_service = DocumentChatService(
        repository=doc_repo,
        document_service=doc_service,
        chat_service=fake_chat,
    )

    user_state = {"user_id": "usr_" + uuid.uuid4().hex[:12]}
    user_b = {"user_id": "usr_" + uuid.uuid4().hex[:12]}

    app = create_app(settings)
    app.dependency_overrides[get_current_user] = lambda: dict(user_state)
    app.dependency_overrides[get_document_service] = lambda: doc_service
    app.dependency_overrides[get_document_chat_service] = lambda: doc_chat_service

    with TestClient(app, raise_server_exceptions=False) as client:
        yield {
            "client": client,
            "doc_service": doc_service,
            "doc_chat_service": doc_chat_service,
            "user_state": user_state,
            "user_b": user_b,
            "app": app,
        }


def test_document_preview_and_chat_flow(test_setup):
    client = test_setup["client"]

    # 1. Upload a text document
    content = b"Title: Quantum Computing Overview\n\nQuantum computers leverage quantum superposition and entanglement to execute operations on qubits."
    res = client.post(
        "/documents",
        files={"file": ("quantum.txt", io.BytesIO(content), "text/plain")},
    )
    assert res.status_code == 201
    doc_id = res.json()["document_id"]

    # 2. Preview document
    prev_res = client.get(f"/documents/{doc_id}/preview")
    assert prev_res.status_code == 200
    pdata = prev_res.json()
    assert pdata["document_id"] == doc_id
    assert "Quantum" in pdata["preview_text"]

    # 3. Chat with document
    chat_res = client.post(
        f"/documents/{doc_id}/chat",
        json={"question": "How do quantum computers operate?"},
    )
    assert chat_res.status_code == 200
    cdata = chat_res.json()
    assert "conversation_id" in cdata
    assert "answer" in cdata
    assert len(cdata["citations"]) >= 1
    assert cdata["citations"][0]["document_id"] == doc_id

    conv_id = cdata["conversation_id"]

    # 4. List conversations
    convs_res = client.get(f"/documents/{doc_id}/conversations")
    assert convs_res.status_code == 200
    conv_list = convs_res.json()
    assert len(conv_list) == 1
    assert conv_list[0]["conversation_id"] == conv_id

    # 5. Fetch conversation history
    hist_res = client.get(f"/documents/{doc_id}/conversations/{conv_id}")
    assert hist_res.status_code == 200
    hdata = hist_res.json()
    assert len(hdata["messages"]) == 2  # 1 user + 1 assistant
    assert hdata["messages"][0]["role"] == "user"
    assert hdata["messages"][1]["role"] == "assistant"

    # 6. Delete conversation
    del_res = client.delete(f"/documents/{doc_id}/conversations/{conv_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "deleted"


def test_cross_user_isolation(test_setup):
    client = test_setup["client"]
    app = test_setup["app"]
    user_b = test_setup["user_b"]

    # Upload document as user A
    content = b"Top secret corporate roadmap."
    res = client.post(
        "/documents",
        files={"file": ("roadmap.txt", io.BytesIO(content), "text/plain")},
    )
    doc_id = res.json()["document_id"]

    # Switch auth to user B
    app.dependency_overrides[get_current_user] = lambda: user_b

    # User B cannot preview user A's document
    prev_res = client.get(f"/documents/{doc_id}/preview")
    assert prev_res.status_code == 404

    # User B cannot chat with user A's document
    chat_res = client.post(
        f"/documents/{doc_id}/chat",
        json={"question": "What is the roadmap?"},
    )
    assert chat_res.status_code == 404
