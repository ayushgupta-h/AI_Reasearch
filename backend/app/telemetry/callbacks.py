"""
app/telemetry/callbacks.py
==========================
LangChain callback handler for OpenTelemetry distributed tracing
and Prometheus AI/LLM metrics (TASK 2.6).

Traces:
- LLM invocations (provider, model, token usage, latency, estimated cost)
- Tool executions (search queries, scraping)

Redaction:
- Prompts, raw documents, and credentials are never placed in span attributes.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Union

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from opentelemetry import trace

from app.observability import redact
from app.telemetry.metrics import get_metrics
from app.telemetry.tracing import get_tracer, sanitize_span_attributes

logger = logging.getLogger("app.telemetry.callbacks")


class TracedLLMCallbackHandler(BaseCallbackHandler):
    """LangChain callback handler recording spans and metrics for AI & Tool execution."""

    def __init__(self, default_provider: str = "groq") -> None:
        super().__init__()
        self.default_provider = default_provider
        self._tracer = get_tracer()
        self._metrics = get_metrics()
        # Track active spans and start times by run_id
        self._spans: Dict[str, trace.Span] = {}
        self._start_times: Dict[str, float] = {}
        self._providers: Dict[str, str] = {}
        self._models: Dict[str, str] = {}

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        *,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        key = str(run_id)
        now = time.perf_counter()
        self._start_times[key] = now

        meta = metadata or {}
        provider = meta.get("ls_provider") or meta.get("provider") or self.default_provider
        model = (
            meta.get("ls_model_name")
            or meta.get("model_name")
            or meta.get("model")
            or (serialized.get("kwargs", {}).get("model") if serialized else None)
            or "default"
        )
        self._providers[key] = str(provider)
        self._models[key] = str(model)

        total_prompt_chars = sum(len(p) for p in prompts)
        attrs = {
            "gen_ai.system": str(provider),
            "gen_ai.request.model": str(model),
            "gen_ai.prompt_char_length": total_prompt_chars,
        }

        try:
            span = self._tracer.start_span(
                f"llm.invoke {provider}/{model}",
                attributes=sanitize_span_attributes(attrs),
                kind=trace.SpanKind.CLIENT,
            )
            self._spans[key] = span
        except Exception as exc:
            logger.debug("Failed to start llm span: %s", exc)

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        key = str(run_id)
        duration = time.perf_counter() - self._start_times.pop(key, time.perf_counter())
        provider = self._providers.pop(key, self.default_provider)
        model = self._models.pop(key, "default")
        span = self._spans.pop(key, None)

        # Extract tokens from standard LangChain response outputs
        prompt_tokens = 0
        completion_tokens = 0

        llm_output = response.llm_output or {}
        token_usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        if isinstance(token_usage, dict):
            prompt_tokens = int(
                token_usage.get("prompt_tokens") or token_usage.get("input_tokens") or 0
            )
            completion_tokens = int(
                token_usage.get("completion_tokens")
                or token_usage.get("output_tokens")
                or 0
            )

        # If zero, try generations info
        if prompt_tokens == 0 and completion_tokens == 0 and response.generations:
            for gen_list in response.generations:
                for gen in gen_list:
                    msg_meta = getattr(gen, "message", None)
                    usage_meta = (
                        getattr(msg_meta, "usage_metadata", None)
                        if msg_meta
                        else None
                    )
                    if isinstance(usage_meta, dict):
                        prompt_tokens += usage_meta.get("input_tokens", 0)
                        completion_tokens += usage_meta.get("output_tokens", 0)

        # Record metrics in Prometheus
        self._metrics.record_llm_usage(
            provider=provider,
            model=model,
            duration_seconds=duration,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            status="success",
        )

        if span:
            try:
                span.set_attribute("gen_ai.usage.prompt_tokens", prompt_tokens)
                span.set_attribute("gen_ai.usage.completion_tokens", completion_tokens)
                span.set_attribute(
                    "gen_ai.usage.total_tokens", prompt_tokens + completion_tokens
                )
                span.set_status(trace.Status(trace.StatusCode.OK))
                span.end()
            except Exception as exc:
                logger.debug("Failed to complete llm span: %s", exc)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        key = str(run_id)
        duration = time.perf_counter() - self._start_times.pop(key, time.perf_counter())
        provider = self._providers.pop(key, self.default_provider)
        model = self._models.pop(key, "default")
        span = self._spans.pop(key, None)

        self._metrics.record_llm_usage(
            provider=provider,
            model=model,
            duration_seconds=duration,
            status="failed",
            error_code=type(error).__name__,
        )

        if span:
            try:
                span.record_exception(error)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(error)[:200]))
                span.end()
            except Exception:
                pass

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        *,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        key = str(run_id)
        self._start_times[key] = time.perf_counter()
        tool_name = (serialized or {}).get("name") or "tool"

        try:
            span = self._tracer.start_span(
                f"tool.{tool_name}",
                attributes={
                    "tool.name": tool_name,
                    "tool.input_length": len(input_str),
                },
                kind=trace.SpanKind.INTERNAL,
            )
            self._spans[key] = span
        except Exception:
            pass

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        key = str(run_id)
        self._start_times.pop(key, None)
        span = self._spans.pop(key, None)
        if span:
            try:
                span.set_attribute("tool.output_length", len(str(output)))
                span.set_status(trace.Status(trace.StatusCode.OK))
                span.end()
            except Exception:
                pass

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        key = str(run_id)
        self._start_times.pop(key, None)
        span = self._spans.pop(key, None)
        if span:
            try:
                span.record_exception(error)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(error)[:200]))
                span.end()
            except Exception:
                pass

