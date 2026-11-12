"""Chat provider fallback without any real LLM or credentials."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.errors import LLMProviderError
from app.services.chat_service import ChatService


def _settings(**env) -> Settings:
    base = {"APP_ENV": "test", "SHARE_SECRET": "x" * 32}
    base.update(env)
    return Settings.from_env(env=base)


def _llm(reply: str = "Grounded answer") -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content=reply)
    return llm


class TestChatFallback:
    def test_uses_the_first_available_provider(self) -> None:
        service = ChatService(_settings(GEMINI_API_KEY="g", GROQ_API_KEY="r"))
        service._build_llm = lambda provider: _llm(f"answer from {provider}")

        reply, provider = service.complete("What is RAG?")

        assert provider == "gemini"
        assert reply == "answer from gemini"

    def test_falls_back_when_the_first_provider_fails(self) -> None:
        service = ChatService(_settings(GEMINI_API_KEY="g", GROQ_API_KEY="r"))
        calls: list[str] = []

        def build(provider: str):
            calls.append(provider)
            if provider == "gemini":
                raise RuntimeError("429 quota exceeded")
            return _llm("fallback answer")

        service._build_llm = build
        reply, provider = service.complete("What is RAG?")

        assert calls == ["gemini", "groq"]
        assert provider == "groq"
        assert reply == "fallback answer"

    def test_empty_reply_is_treated_as_a_failure(self) -> None:
        service = ChatService(_settings(GEMINI_API_KEY="g", GROQ_API_KEY="r"))
        service._build_llm = lambda provider: _llm("" if provider == "gemini" else "ok")

        reply, provider = service.complete("Hello")

        assert provider == "groq"
        assert reply == "ok"

    def test_all_providers_failing_raises_a_sanitised_error(self) -> None:
        service = ChatService(_settings(GEMINI_API_KEY="g", GROQ_API_KEY="r"))
        secret = "sk-" + "a" * 24

        def build(provider: str):
            raise RuntimeError(f"401 unauthorized for api key {secret}")

        service._build_llm = build
        with pytest.raises(LLMProviderError) as excinfo:
            service.complete("Hello")

        assert "rate-limited" in str(excinfo.value)
        assert secret not in str(excinfo.value)

    def test_provider_chain_defaults_without_credentials(self) -> None:
        service = ChatService(_settings())
        assert service.provider_chain() == ["groq"]

    def test_provider_chain_orders_configured_providers(self) -> None:
        service = ChatService(_settings(GROQ_API_KEY="r", MISTRAL_API_KEY="m"))
        assert service.provider_chain() == ["groq", "mistral"]

    def test_content_blocks_are_joined(self) -> None:
        service = ChatService(_settings(GEMINI_API_KEY="g"))
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(
            content=[{"text": "first"}, {"text": "second"}]
        )
        service._build_llm = lambda provider: llm

        reply, _ = service.complete("Hello")
        assert reply == "first\nsecond"
