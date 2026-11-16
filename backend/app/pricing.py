"""
app/pricing.py
==============
Provider-specific LLM pricing loaded from an operational config file.

Prices for per 1,000 tokens (input/output separated) and are stored in
``config/provider_pricing.json``. They are deliberately **not** hardcoded in
this module so they can be updated without a code change. A small built-in
fallback table is used only when no config file is available, so the system
degrades gracefully but operators can supply their own prices.

The returned figures are estimates only; callers should present them as
``estimated_cost_usd``.
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from app.config import BASE_DIR

DEFAULT_PRICING_PATH = BASE_DIR / "config" / "provider_pricing.json"

# Minimal built-in fallback used only when the config file is absent.
_FALLBACK_PROVIDERS: Dict[str, Mapping[str, float]] = {
    "groq": {"input_per_1k": 0.00059, "output_per_1k": 0.00079},
    "gemini": {"input_per_1k": 0.00010, "output_per_1k": 0.00040},
    "mistral": {"input_per_1k": 0.00020, "output_per_1k": 0.00020},
    "huggingface": {"input_per_1k": 0.0, "output_per_1k": 0.0},
}

_DEFAULT_PROVIDER = "default"
_DEFAULT_RATE = 0.0002


class PricingConfig:
    """Loads and resolves per-provider/per-model token pricing."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else DEFAULT_PRICING_PATH
        self._providers: Dict[str, Mapping[str, float]] = {}
        self._models: Dict[str, Mapping[str, float]] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        providers = {k: dict(v) for k, v in _FALLBACK_PROVIDERS.items()}
        models: Dict[str, Mapping[str, float]] = {}
        try:
            if self._path.exists():
                with self._path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                loaded_providers = data.get("providers") or {}
                loaded_models = data.get("models") or {}
                if isinstance(loaded_providers, dict):
                    providers.update(loaded_providers)
                if isinstance(loaded_models, dict):
                    models.update(loaded_models)
        except Exception:  # pragma: no cover - never break the app over pricing
            providers = {k: dict(v) for k, v in _FALLBACK_PROVIDERS.items()}
            models = {}
        self._providers = providers
        self._models = models

    # ── Resolution ───────────────────────────────────────────────────────

    def rates_for(
        self, provider: Optional[str], model: Optional[str]
    ) -> Mapping[str, float]:
        """Return ``input_per_1k`` / ``output_per_1k`` for a provider/model."""
        model_key = f"{provider}/{model}".lower() if provider and model else None
        if model_key and model_key in self._models:
            rates = self._models[model_key]
            return {
                "input_per_1k": float(rates.get("input_per_1k", 0.0)),
                "output_per_1k": float(rates.get("output_per_1k", 0.0)),
            }

        provider_key = (provider or "").lower().strip()
        provider_rates = self._providers.get(provider_key)
        if provider_rates:
            return {
                "input_per_1k": float(provider_rates.get("input_per_1k", 0.0)),
                "output_per_1k": float(provider_rates.get("output_per_1k", 0.0)),
            }

        default_rates = self._providers.get(_DEFAULT_PROVIDER)
        if default_rates:
            return {
                "input_per_1k": float(default_rates.get("input_per_1k", _DEFAULT_RATE)),
                "output_per_1k": float(
                    default_rates.get("output_per_1k", _DEFAULT_RATE)
                ),
            }
        return {"input_per_1k": _DEFAULT_RATE, "output_per_1k": _DEFAULT_RATE}

    def estimate_cost(
        self,
        *,
        provider: Optional[str],
        model: Optional[str],
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> float:
        """Estimate cost in USD for a single invocation.

        Cost is only computed when actual token counts are known; a ``0`` or
        negative token count is treated as "not measured" and yields 0.0.
        """
        input_tokens = max(int(input_tokens or 0), 0)
        output_tokens = max(int(output_tokens or 0), 0)
        if input_tokens == 0 and output_tokens == 0:
            return 0.0
        rates = self.rates_for(provider, model)
        cost = (
            input_tokens * float(rates["input_per_1k"]) / 1000.0
            + output_tokens * float(rates["output_per_1k"]) / 1000.0
        )
        return round(cost, 8)

    def known_providers(self) -> list[str]:
        return sorted(self._providers.keys())

    def known_models(self) -> list[str]:
        return sorted(self._models.keys())


_default_pricing: Optional[PricingConfig] = None
_gest_lock = threading.Lock()


def get_pricing(path: Optional[Path] = None) -> PricingConfig:
    """Return a process-wide cached PricingConfig."""
    global _default_pricing
    if path is not None:
        return PricingConfig(path)
    with _gest_lock:
        if _default_pricing is None:
            _default_pricing = PricingConfig()
        return _default_pricing


__all__ = ["PricingConfig", "get_pricing", "DEFAULT_PRICING_PATH"]
