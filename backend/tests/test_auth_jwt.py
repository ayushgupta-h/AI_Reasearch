"""Hardened JWT validation through the upgraded auth seam (no network)."""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi import HTTPException

from app.security.dependencies import get_current_user, user_from_query_token
from app.security.jwt_validator import SupabaseJWTValidator

TEST_SECRET = "unit-test-jwt-secret-0123456789abcdef"
TEST_URL = "https://project.supabase.co"


@pytest.fixture
def validator() -> SupabaseJWTValidator:
    """A locally-verifying validator bound to an explicit test secret."""
    return SupabaseJWTValidator(
        TEST_URL,
        jwt_secret=TEST_SECRET,
        jwks_endpoint="",
        audience="authenticated",
    )


@pytest.fixture(autouse=True)
def _seam_validator(monkeypatch) -> None:
    """Point the global seam validator at a test validator.

    The ``get_current_user`` / ``user_from_query_token`` helpers use the module-
    level ``DEFAULT_VALIDATOR`` (built from real environment config). For tests
    we override it with a validator bound to the known test secret so the seam
    is exercised without depending on CI secrets.
    """
    test_validator = SupabaseJWTValidator(
        TEST_URL,
        jwt_secret=TEST_SECRET,
        jwks_endpoint="",
        audience="authenticated",
    )
    monkeypatch.setattr(
        "app.security.dependencies.DEFAULT_VALIDATOR", test_validator
    )
    monkeypatch.setattr(
        "app.security.jwt_validator.DEFAULT_VALIDATOR", test_validator
    )


def _token(**overrides) -> str:
    payload = {
        "sub": "user-123",
        "email": "researcher@example.com",
        "aud": "authenticated",
        "role": "authenticated",
        "iss": TEST_URL,
        "exp": int(time.time()) + 3600,
        "user_metadata": {"name": "Researcher"},
    }
    payload.update(overrides)
    return jwt.encode(payload, TEST_SECRET, algorithm="HS256")


class TestLocalSignatureVerification:
    def test_valid_token_returns_identity(self, validator) -> None:
        user = validator.verify(_token())
        assert user["user_id"] == "user-123"
        assert user["email"] == "researcher@example.com"
        assert user["role"] == "authenticated"

    def test_expired_token_is_rejected(self, validator) -> None:
        with pytest.raises(HTTPException) as excinfo:
            validator.verify(_token(exp=int(time.time()) - 60))
        assert excinfo.value.status_code == 401
        assert "expired" in excinfo.value.detail.lower()

    def test_malformed_token_is_rejected(self, validator) -> None:
        with pytest.raises(HTTPException) as excinfo:
            validator.verify("not-a-jwt")
        assert excinfo.value.status_code == 401
        assert excinfo.value.detail == "Invalid authentication token."

    def test_empty_token_is_rejected(self, validator) -> None:
        with pytest.raises(HTTPException) as excinfo:
            validator.verify("   ")
        assert excinfo.value.status_code == 401

    def test_wrong_signature_is_rejected(self, validator) -> None:
        forged = jwt.encode(
            {"sub": "attacker", "aud": "authenticated", "exp": int(time.time()) + 3600},
            "wrong-secret",
            algorithm="HS256",
        )
        with pytest.raises(HTTPException) as excinfo:
            validator.verify(forged)
        assert excinfo.value.status_code == 401

    def test_wrong_issuer_is_rejected(self, validator) -> None:
        with pytest.raises(HTTPException) as excinfo:
            validator.verify(_token(iss="https://attacker.supabase.co"))
        assert excinfo.value.status_code == 401
        assert "issuer" in excinfo.value.detail.lower()

    def test_wrong_audience_is_rejected(self, validator) -> None:
        with pytest.raises(HTTPException) as excinfo:
            validator.verify(_token(aud="other-aud"))
        assert excinfo.value.status_code == 401
        assert "audience" in excinfo.value.detail.lower()

    def test_unsupported_algorithm_is_rejected(self, validator) -> None:
        token = jwt.encode(
            {"sub": "user-123", "aud": "authenticated", "exp": int(time.time()) + 3600},
            TEST_SECRET,
            algorithm="HS384",
        )
        with pytest.raises(HTTPException) as excinfo:
            validator.verify(token)
        assert excinfo.value.status_code == 401

    def test_missing_subject_is_rejected(self, validator) -> None:
        with pytest.raises(HTTPException) as excinfo:
            validator.verify(_token(sub=None))
        assert excinfo.value.status_code == 401

    def test_hs256_without_configured_secret_fails_closed(self) -> None:
        # No secret and no JWKS -> must reject, never fall back to unverified claims.
        bare = SupabaseJWTValidator(TEST_URL, jwt_secret="", jwks_endpoint="")
        with pytest.raises(HTTPException) as excinfo:
            bare.verify(_token())
        assert excinfo.value.status_code == 401


def _run(coro):
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value


class TestDependencyExtraction:
    def test_missing_credentials_returns_401_with_challenge(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            _run(get_current_user(credentials=None, token=None, authorization=None))
        assert excinfo.value.status_code == 401
        assert excinfo.value.headers["WWW-Authenticate"] == "Bearer"

    def test_query_token_is_accepted_for_downloads(self) -> None:
        user = _run(get_current_user(credentials=None, token=_token(), authorization=None))
        assert user["user_id"] == "user-123"

    def test_raw_authorization_header_is_accepted(self) -> None:
        user = _run(get_current_user(
            credentials=None, token=None, authorization=f"Bearer {_token()}"
        ))
        assert user["user_id"] == "user-123"

    def test_invalid_token_returns_401_not_500(self) -> None:
        with pytest.raises(HTTPException) as excinfo:
            _run(get_current_user(credentials=None, token="garbage", authorization=None))
        assert excinfo.value.status_code == 401


class TestQueryTokenHelper:
    def test_missing_token_returns_none(self) -> None:
        assert user_from_query_token(None) is None
        assert user_from_query_token("") is None

    def test_invalid_token_is_swallowed_for_optional_auth(self) -> None:
        assert user_from_query_token("bad-token") is None

    def test_valid_token_resolves_a_user(self) -> None:
        user = user_from_query_token(_token())
        assert user is not None
        assert user["user_id"] == "user-123"
