"""
app/services/chat_service.py
============================
Single-turn chat completion with provider fallback.

Providers and models come from :mod:`app.config`; the fallback order and error
semantics match the original ``/chat`` implementation. When the Respan Gateway
is enabled (``GATEWAY_ENABLED``) it is tried first and routes the request to the
provider named in its model, falling back to the direct providers on failure.
"""

from __future__ import annotations

from typing import List, Tuple

from app.config import PROVIDER_ORDER, Settings
from app.errors import LLMProviderError
from app.observability import get_logger, redact
from gateway_provider import GATEWAY_PROVIDER, GatewayChatModel

logger = get_logger("app.services.chat")

SYSTEM_PROMPT = (
    "You are Quest, the assistant inside a research desk. Answer clearly and "
    "concisely (under 180 words) in plain prose. If the user asks for a full "
    "research report, tell them to switch to Research mode and submit their "
    "question there — the 8-agent pipeline will build it."
)


class ChatService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def provider_chain(self) -> List[str]:
        """Providers with configured credentials, preferred order first."""
        configured = [
            provider
            for provider in ("gemini", "groq", "mistral", "huggingface")
            if self._settings.provider_api_key(provider)
        ]
        if self._settings.gateway_ready:
            configured.insert(0, GATEWAY_PROVIDER)
        return configured or [self._settings.default_provider]

    def complete(self, message: str) -> Tuple[str, str]:
        """Return ``(reply, provider)`` or raise :class:`LLMProviderError`."""
        errors: List[str] = []
        for provider in self.provider_chain():
            try:
                reply = self._complete_with(provider, message)
            except Exception as exc:
                logger.warning(
                    "chat.provider_failed",
                    extra={"agent": provider, "error_code": "llm_provider_unavailable"},
                )
                errors.append(f"{provider}: {redact(exc)[:120]}")
                continue
            if reply:
                return reply, provider
            errors.append(f"{provider}: empty reply")

        logger.error("chat.all_providers_failed", extra={"error_code": "llm_provider_unavailable"})
        summary = " | ".join(errors)[:400]
        raise LLMProviderError(
            "All model providers are rate-limited or unavailable right now."
            + (f" ({summary})" if summary else "")
        )

    # ── Provider adapters ────────────────────────────────────────────────

    def _complete_with(self, provider: str, message: str) -> str:
        llm = self._build_llm(provider)
        result = llm.invoke([("system", SYSTEM_PROMPT), ("user", message)])
        content = result.content
        if isinstance(content, list):  # some providers return content blocks
            content = "\n".join(
                (block.get("text") if isinstance(block, dict) and block.get("text") else str(block))
                for block in content
            )
        return str(content).strip()

    def _build_llm(self, provider: str):
        if provider == GATEWAY_PROVIDER:
            return GatewayChatModel(
                model=self._settings.gateway_model_name,
                config_path=self._settings.gateway_config or None,
            )
        if provider == "huggingface":
            from hf_provider import get_hf_llm

            return get_hf_llm()
        if provider == "groq":
            from langchain_groq import ChatGroq

            return ChatGroq(model=self._settings.groq_model, temperature=0.4)
        if provider == "mistral":
            from langchain_mistralai import ChatMistralAI

            return ChatMistralAI(model=self._settings.mistral_model, temperature=0.4)
        if provider == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                model=self._settings.gemini_model,
                google_api_key=self._settings.gemini_api_key,
                temperature=0.4,
                max_retries=2,
                timeout=30,
            )
        raise ValueError(f"Unsupported provider: {provider}")
