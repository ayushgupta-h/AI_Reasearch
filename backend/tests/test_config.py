"""
tests/test_config.py
====================
Configuration layer: env parsing, normalisation and startup validation.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.errors import ConfigurationError


def make_env(**overrides: str) -> dict[str, str]:
    env = {
        "GROQ_API_KEY": "test-groq-key",
        "SHARE_SECRET": "unit-test-share-secret-0123456789abcdef",
        "SUPABASE_URL": "https://example.supabase.co/",
        "SUPABASE_ANON_KEY": "test-anon-key",
        "TAVILY_API_KEY": "test-tavily-key",
    }
    env.update(overrides)
    return env


class TestSettingsParsing:
    def test_defaults(self) -> None:
        settings = Settings.from_env(env=make_env())
        assert settings.app_env == "development"
        assert settings.default_provider == "groq"
        assert settings.max_revisions == 2
        assert settings.approval_threshold == 8
        assert settings.supabase_url == "https://example.supabase.co"
        assert settings.configured_providers() == ("groq",)

    def test_invalid_values_are_coerced(self) -> None:
        settings = Settings.from_env(
            env=make_env(
                APP_ENV="definitely-not-an-env",
                LLM_PROVIDER="not-a-provider",
                LOG_FORMAT="xml",
                LOG_LEVEL="shout",
            )
        )
        assert settings.app_env == "development"
        assert settings.default_provider == "groq"
        assert settings.log_format == "console"
        assert settings.log_level == "INFO"

    def test_production_is_never_debug(self) -> None:
        settings = Settings.from_env(env=make_env(APP_ENV="production", DEBUG="true"))
        assert settings.is_production is True
        assert settings.debug is False

    def test_production_alias(self) -> None:
        assert Settings.from_env(env=make_env(ENVIRONMENT="prod")).app_env == "production"

    def test_provider_detection_uses_all_supported_keys(self) -> None:
        settings = Settings.from_env(
            env=make_env(
                GROQ_API_KEY="",
                GEMINI_API_KEY="g",
                MISTRAL_API_KEY="m",
                HF_TOKEN="h",
            )
        )
        assert settings.configured_providers() == ("gemini", "mistral", "huggingface")
        assert settings.provider_api_key("hf") == "h"

    def test_share_secret_falls_back_to_chroma_key(self) -> None:
        settings = Settings.from_env(
            env=make_env(SHARE_SECRET="", CHROMA_ENCRYPTION_KEY="a" * 64)
        )
        assert settings.share_secret == "a" * 64

    def test_numeric_env_is_bounded(self) -> None:
        settings = Settings.from_env(
            env=make_env(DEFAULT_MAX_REVISIONS="99", DEFAULT_APPROVAL_THRESHOLD="0")
        )
        assert settings.max_revisions == 5
        assert settings.approval_threshold == 1


class TestSettingsValidation:
    def test_production_requires_llm_key(self) -> None:
        settings = Settings.from_env(env=make_env(APP_ENV="production", GROQ_API_KEY=""))
        with pytest.raises(ConfigurationError) as excinfo:
            settings.validate()
        assert "LLM provider" in excinfo.value.message

    def test_production_requires_share_secret(self) -> None:
        settings = Settings.from_env(
            env=make_env(APP_ENV="production", SHARE_SECRET="", CHROMA_ENCRYPTION_KEY="")
        )
        with pytest.raises(ConfigurationError):
            settings.validate()

    def test_development_reports_problems_as_warnings(self) -> None:
        settings = Settings.from_env(
            env=make_env(APP_ENV="development", GROQ_API_KEY="", SHARE_SECRET="")
        )
        warnings = settings.validate()
        assert any("LLM provider" in item for item in warnings)
        assert any("SHARE_SECRET" in item for item in warnings)

    def test_missing_supabase_is_a_warning_not_failure(self) -> None:
        settings = Settings.from_env(
            env=make_env(APP_ENV="development", SUPABASE_URL="", SUPABASE_ANON_KEY="")
        )
        warnings = settings.validate()
        assert any("SUPABASE_URL" in item for item in warnings)
        assert any("SUPABASE_ANON_KEY" in item for item in warnings)
