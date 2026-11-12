"""
api/security/jwt_validator.py
===============================
Hardened Supabase JWT validation.

This is the upgraded authentication core for Task 2.4. It validates every
JWT cryptographically via the Supabase JWKS endpoint (RS256) or the shared
HS256 secret, and enforces:

- signature validity (never trusts unverified claims)
- issuer (``iss``) matches the configured Supabase project URL
- audience (``aud``) when configured
- expiration (``exp``)
- an allowlist of signing algorithms
- safe key-rotation handling (refetch JWKS when a signing key is unknown)

No token material is ever logged. On any validation failure the caller gets a
generic 401; details never echo the token or upstream bodies.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import jwt
from fastapi import HTTPException

from app.observability import bind_user

# Allowlist of signing algorithms we are willing to accept. Supabase currently
# issues HS256 tokens; with a JWKS endpoint configured we also accept RS256.
_ALLOWED_ALGORITHMS = frozenset({"HS256", "RS256"})
# Audience enforced when present in the token payload (Supabase uses "authenticated").
DEFAULT_AUDIENCE = "authenticated"


class JWTValidationError(HTTPException):
    """A token failed validation. Kept generic to avoid information disclosure."""

    def __init__(self, status_code: int = 401, detail: str = "Invalid authentication token.") -> None:
        super().__init__(status_code=status_code, detail=detail)


def _now() -> float:
    return time.time()


class SupabaseJWTValidator:
    """
    Validates Supabase access-token JWTs.

    Priority:
      1. JWKS (RS256) when ``jwks_endpoint`` is provided.
      2. Shared secret (HS256) when ``jwt_secret`` is provided.
      3. If neither is available, only structural/expiry checks are possible;
         verification then relies on the Supabase Auth API (remote) which the
         orchestrator performs separately. This class raises 401 for any token
         it cannot verify locally; it never returns unverified claims.

    Key rotation: a ``PyJWKClient`` lazily fetches and caches the JWKS; if a
    token references an unknown ``kid`` it refetches the set once. If the new
    key still does not resolve, validation fails closed.
    """

    def __init__(
        self,
        supabase_url: str = "",
        *,
        jwt_secret: str = "",
        jwks_endpoint: str = "",
        audience: str = "",
    ) -> None:
        self._supabase_url = (supabase_url or "").rstrip("/")
        self._jwt_secret = jwt_secret
        self._audience = audience or DEFAULT_AUDIENCE
        self._jwks_endpoint = jwks_endpoint or (
            f"{self._supabase_url}/auth/v1/.well-known/jwks.json" if self._supabase_url else ""
        )
        self._jwks_client = None

    # ── Initialisation ──────────────────────────────────────────────

    def _get_jwks_client(self):
        if self._jwks_client is None:
            if not self._jwks_endpoint:
                raise JWTValidationError(detail="No JWKS endpoint configured.")
            from jwt import PyJWKClient

            self._jwks_client = PyJWKClient(self._jwks_endpoint, cache_keys=True)
        return self._jwks_client

    # ── Public API ──────────────────────────────────────────────────

    def verify(self, token: str) -> Dict[str, Any]:
        """Verify *token* and return the user identity dict. Raises 401 on failure."""
        token = (token or "").strip()
        if not token:
            raise JWTValidationError(detail="Missing authentication token")

        # 1. Structural + expiry pre-check (no signature trust yet).
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
        except Exception as e:
            raise JWTValidationError() from e

        exp = unverified.get("exp")
        if exp and exp < _now():
            raise JWTValidationError(detail="Token has expired. Please log in again.")

        alg = self._header_alg(token)

        # 2. Cryptographic verification.
        payload: Dict[str, Any]
        if alg == "HS256":
            payload = self._verify_hs256(token)
        elif alg == "RS256":
            payload = self._verify_rs256(token)
        else:
            raise JWTValidationError(detail="Unsupported token signing algorithm.")

        # 3. Issuer validation whenever we know the Supabase URL.
        if self._supabase_url:
            iss = payload.get("iss", "")
            if not iss.startswith(self._supabase_url):
                raise JWTValidationError(detail="Invalid token issuer.")

        # 4. Audience validation when configured.
        if self._audience:
            aud = payload.get("aud")
            if aud != self._audience:
                raise JWTValidationError(detail="Invalid token audience.")

        user_info = {
            "user_id": payload.get("sub"),
            "email": payload.get("email", ""),
            "metadata": payload.get("user_metadata", {}) or {},
            "role": payload.get("role", "authenticated"),
        }
        if not user_info["user_id"]:
            raise JWTValidationError(detail="Could not extract user identity from token")

        bind_user(user_info["user_id"])
        return user_info

    # ── Internals ───────────────────────────────────────────────────

    @staticmethod
    def _header_alg(token: str) -> str:
        try:
            header = jwt.get_unverified_header(token)
        except Exception as e:
            raise JWTValidationError() from e
        return header.get("alg", "")

    def _verify_hs256(self, token: str) -> Dict[str, Any]:
        if not self._jwt_secret:
            raise JWTValidationError(detail="HS256 token but no shared secret configured.")
        try:
            return jwt.decode(
                token,
                self._jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False, "verify_iss": False},
            )
        except jwt.ExpiredSignatureError as e:
            raise JWTValidationError(detail="Token has expired. Please log in again.") from e
        except Exception as e:
            raise JWTValidationError() from e

    def _verify_rs256(self, token: str) -> Dict[str, Any]:
        try:
            signing_key = self._get_jwks_client().get_signing_key_from_jwt(token)
        except Exception as e:
            raise JWTValidationError() from e
        try:
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                options={"verify_aud": False, "verify_iss": False},
            )
        except jwt.ExpiredSignatureError as e:
            raise JWTValidationError(detail="Token has expired. Please log in again.") from e
        except Exception as e:
            raise JWTValidationError() from e


# Module-level default validator bound at import time from environment.
def build_default_validator() -> SupabaseJWTValidator:
    from app.config import (
        SUPABASE_JWKS_ENDPOINT,
        SUPABASE_JWT_SECRET,
        SUPABASE_URL,
    )

    return SupabaseJWTValidator(
        SUPABASE_URL,
        jwt_secret=SUPABASE_JWT_SECRET,
        jwks_endpoint=SUPABASE_JWKS_ENDPOINT,
        audience=DEFAULT_AUDIENCE,
    )


DEFAULT_VALIDATOR = build_default_validator()


def verify_supabase_token(token: str) -> Dict[str, Any]:
    """Backwards-compatible entry point used by routers and the legacy validator.

    Delegates to the hardened validator. Raises ``HTTPException(401)`` on any
    failure; never logs or returns the token.
    """
    return DEFAULT_VALIDATOR.verify(token)
