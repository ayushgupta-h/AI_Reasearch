"""
tests/test_gateway_provider.py
=============================
Gateway routing through the chat workflow, without provider credentials.

A loopback OpenAI-compatible provider fixture and the real in-process Respan
Gateway stand in for a vendor, so the ``ChatService`` path behind ``POST /chat``
is exercised end to end while every request stays on 127.0.0.1.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respan_gateway as rg

import gateway_provider
from app.config import Settings
from app.services.chat_service import ChatService


# ─── Provider fixture ────────────────────────────────────────────────────────


class _FixtureHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible provider; records what the gateway sends."""

    reply = "Gateway routed this reply."
    requests: list = []

    def log_message(self, *args) -> None:  # keep pytest output clean
        pass

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append(body)
        payload = {
            "id": "chatcmpl-fixture",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "fixture"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": self.reply},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        self._send_json(payload)

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        self._send_json(
            {"object": "list", "data": [{"id": "quest-fixture-model", "object": "model"}]}
        )

    def _send_json(self, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def provider_fixture():
    """Start a loopback provider and yield ``(handler class, base_url)``."""
    handler = type("FixtureHandler", (_FixtureHandler,), {"requests": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield handler, f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _reset_gateway():
    gateway_provider.close_gateway()
    yield
    gateway_provider.close_gateway()


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _settings(**env) -> Settings:
    base = {"APP_ENV": "test", "SHARE_SECRET": "x" * 32}
    base.update(env)
    return Settings.from_env(env=base)


def _gateway_config(tmp_path: Path, base_url: str) -> Path:
    """A gateway.toml that routes ``quest-test`` to the fixture provider."""
    path = tmp_path / "gateway.toml"
    path.write_text(
        "\n".join(
            [
                'listen = "127.0.0.1:0"',
                'log = "off"',
                "ui = false",
                "",
                "[providers.fixture]",
                'formats = ["openai-chat"]',
                f'base_url = "{base_url}"',
                "proxy = false",
                "",
                '[routes."quest-test"]',
                'to = "fixture/quest-fixture-model"',
            ]
        ),
        encoding="utf-8",
    )
    return path


# ─── Integrated workflow ────────────────────────────────────────────────────


class TestChatThroughGateway:
    def test_chat_service_routes_through_the_gateway(self, provider_fixture, tmp_path):
        handler, base_url = provider_fixture
        settings = _settings(
            GATEWAY_ENABLED="true",
            GATEWAY_CONFIG=str(_gateway_config(tmp_path, base_url)),
            GATEWAY_MODEL="quest-test",
        )

        reply, provider = ChatService(settings).complete("What is RAG?")

        assert provider == "gateway"
        assert reply == "Gateway routed this reply."
        assert len(handler.requests) == 1
        forwarded = handler.requests[0]
        assert forwarded["model"] == "quest-fixture-model"
        assert forwarded["messages"][-1] == {"role": "user", "content": "What is RAG?"}

    def test_gateway_is_tried_before_direct_providers(self, tmp_path):
        settings = _settings(
            GATEWAY_ENABLED="true",
            GATEWAY_CONFIG=str(tmp_path / "gateway.toml"),
            GROQ_API_KEY="test",
        )

        assert ChatService(settings).provider_chain() == ["gateway", "groq"]

    def test_falls_back_to_direct_providers_when_the_gateway_fails(
        self, monkeypatch, tmp_path
    ):
        settings = _settings(
            GATEWAY_ENABLED="true",
            GATEWAY_CONFIG=str(tmp_path / "gateway.toml"),
            GROQ_API_KEY="test",
        )

        def unavailable(config_path=None):
            raise rg.GatewayError("no provider configured")

        monkeypatch.setattr(gateway_provider, "get_gateway", unavailable)

        original_build = ChatService._build_llm
        direct: list[str] = []

        def build(self, provider):
            if provider == gateway_provider.GATEWAY_PROVIDER:
                return original_build(self, provider)
            direct.append(provider)
            llm = MagicMock()
            llm.invoke.return_value = MagicMock(content=f"answer from {provider}")
            return llm

        monkeypatch.setattr(ChatService, "_build_llm", build)

        reply, provider = ChatService(settings).complete("Hello")

        assert direct == ["groq"]
        assert provider == "groq"
        assert reply == "answer from groq"

    def test_gateway_errors_are_reported_from_the_status_code(self, monkeypatch):
        response = httpx.Response(502, json={"error": {"message": "upstream refused"}})

        class _StubGateway:
            def request(self, method, path, **kwargs):
                self.sent = (method, path, kwargs)
                return response

        stub = _StubGateway()
        monkeypatch.setattr(gateway_provider, "get_gateway", lambda config_path=None: stub)
        model = gateway_provider.GatewayChatModel(model="groq/openai/gpt-oss-120b")

        with pytest.raises(gateway_provider.GatewayRequestError) as excinfo:
            model.invoke([("user", "hi")])

        assert "502" in str(excinfo.value)
        assert "upstream refused" in str(excinfo.value)
        assert stub.sent[1] == "/v1/chat/completions"


# ─── Configuration ──────────────────────────────────────────────────────────


class TestGatewaySettings:
    def test_model_defaults_to_the_provider_model(self):
        settings = _settings(LLM_PROVIDER="gemini", GEMINI_API_KEY="test")

        assert settings.gateway_model_name == "gemini/gemini-3.8-flash"
        assert settings.gateway_ready is False  # not enabled yet

    def test_llm_provider_gateway_enables_the_gateway(self):
        settings = _settings(LLM_PROVIDER="gateway", GROQ_API_KEY="test")

        assert settings.gateway_enabled is True
        assert settings.gateway_ready is True
        assert settings.default_provider == "groq"
        assert settings.gateway_model_name == "groq/openai/gpt-oss-120b"