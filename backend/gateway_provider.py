"""
Respan Gateway Provider
=======================
Routes the Quest chat workflow through the open-source `Respan Gateway
<https://github.com/respanai/respan-gateway>`_, in process: the gateway serves
an OpenAI-compatible ``/v1/chat/completions`` route and forwards each request
to the provider named in the model string (``groq/openai/gpt-oss-120b``,
``gemini/gemini-3.8-flash``, ...), using the provider keys already configured
for the backend. Nothing listens on a socket and no Respan account, hosted
credits or tracing export are involved.

Enable it with ``GATEWAY_ENABLED=true`` in the backend ``.env``; then
:class:`app.services.chat_service.ChatService` tries the gateway before the
direct LangChain providers. ``GATEWAY_MODEL`` picks the routed model (default:
``<LLM_PROVIDER>/<that provider's model>``) and ``GATEWAY_CONFIG`` points at an
optional ``gateway.toml``.

Usage::

    from gateway_provider import GatewayChatModel

    model = GatewayChatModel(model="groq/openai/gpt-oss-120b")
    reply = model.invoke([("system", "..."), ("user", "...")])

    print(reply.content)
"""

import logging
import threading
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple, Union

import httpx
import respan_gateway as rg

logger = logging.getLogger(__name__)

#: Provider name the chat workflow reports when the gateway served the reply.
GATEWAY_PROVIDER = "gateway"

CHAT_COMPLETIONS_PATH = "/v1/chat/completions"

Message = Union[Tuple[str, str], Mapping[str, object]]

# ─── Process-wide gateway ────────────────────────────────────────────────────

_gateway: Optional[rg.Gateway] = None
_gateway_config: Optional[str] = None
_gateway_lock = threading.Lock()


class GatewayRequestError(RuntimeError):
    """The gateway answered with an error status or an unusable body."""


def get_gateway(config_path: Optional[str] = None) -> rg.Gateway:
    """Return the process-wide in-process gateway.

    ``config_path`` is a ``gateway.toml``; without it the gateway configures
    itself from the environment exactly like its CLI does, i.e. every vendor
    whose key is set becomes a provider. A changed path rebuilds the gateway.
    """
    global _gateway, _gateway_config
    with _gateway_lock:
        if _gateway is not None and _gateway_config != config_path:
            _gateway.close()
            _gateway = None
        if _gateway is None:
            _gateway = (
                rg.Gateway.from_file(config_path) if config_path else rg.Gateway()
            )
            _gateway_config = config_path
            logger.info(
                "gateway.ready", extra={"gateway_config": config_path or "environment"}
            )
        return _gateway


def close_gateway() -> None:
    """Release the process-wide gateway (shutdown, tests)."""
    global _gateway, _gateway_config
    with _gateway_lock:
        if _gateway is not None:
            _gateway.close()
        _gateway = None
        _gateway_config = None


# ─── Chat model ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GatewayChatResult:
    """The slice of a LangChain message the chat service reads."""

    content: str


def _to_message_dict(message: Message) -> dict:
    """Normalise a ``(role, content)`` tuple or message dict."""
    if isinstance(message, Mapping):
        return {
            "role": str(message.get("role") or "user"),
            "content": str(message.get("content") or ""),
        }
    role, content = message
    return {"role": str(role), "content": str(content)}


def _error_detail(response: httpx.Response) -> str:
    """Best-effort provider error text, without echoing credentials."""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if isinstance(error, Mapping) and error.get("message"):
            return str(error["message"])[:200]
        if isinstance(error, str):
            return error[:200]
    return (response.text or "no details")[:200]


def _reply_text(response: httpx.Response) -> str:
    """Extract the assistant message from an OpenAI-compatible reply."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise GatewayRequestError("gateway returned a non-JSON body") from exc

    choices = payload.get("choices") if isinstance(payload, Mapping) else None
    if not choices:
        raise GatewayRequestError("gateway response contained no choices")

    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):  # providers that return content blocks
        content = "".join(
            str(block.get("text", "")) if isinstance(block, Mapping) else str(block)
            for block in content
        )
    text = str(content or "").strip()
    if not text:
        raise GatewayRequestError("gateway response contained an empty reply")
    return text


class GatewayChatModel:
    """Chat model fronting the in-process gateway.

    ``invoke`` mirrors the LangChain surface the chat service uses, so the
    gateway slots into the existing provider fallback unchanged.
    """

    def __init__(
        self,
        model: str,
        config_path: Optional[str] = None,
        temperature: float = 0.4,
    ) -> None:
        self.model = model
        self.config_path = config_path
        self.temperature = temperature

    def invoke(self, messages: Sequence[Message], **_: object) -> GatewayChatResult:
        """Send ``messages`` through the gateway and return the reply."""
        return GatewayChatResult(content=self.complete(messages))

    def complete(self, messages: Sequence[Message]) -> str:
        """Return the assistant text for ``messages``."""
        payload = {
            "model": self.model,
            "messages": [_to_message_dict(message) for message in messages],
            "temperature": self.temperature,
        }
        try:
            response = get_gateway(self.config_path).request(
                "POST", CHAT_COMPLETIONS_PATH, json=payload
            )
        except rg.GatewayError as exc:
            raise GatewayRequestError(f"gateway request failed: {exc}") from exc

        if response.status_code != 200:
            raise GatewayRequestError(
                f"gateway returned HTTP {response.status_code}: {_error_detail(response)}"
            )
        return _reply_text(response)