"""
app/security/dependencies.py
============================
FastAPI authentication dependencies (Task 2.4).

The authentication core is :mod:`app.security.jwt_validator` — the hardened
Supabase JWT validator (signature, issuer, audience, expiry, algorithm
allowlist, JWKS with fail-closed key rotation). This module is the seam the
API layer imports, so routers never depend on the legacy ``auth_validator``
path and the insecure unverified-claims fallback is not reachable from any
route.

No token material is ever logged, and identity is derived exclusively from
cryptographically verified JWT claims.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Header, HTTPException, Query, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import SUPABASE_ANON_KEY, SUPABASE_URL
from app.security.jwt_validator import DEFAULT_VALIDATOR

security_bearer = HTTPBearer(auto_error=False)


def get_supabase_url() -> str:
    """Return the configured Supabase project URL (environment only)."""
    return SUPABASE_URL.rstrip("/")


def get_supabase_anon_key() -> str:
    """Return the Supabase anonymous/publishable key from configuration."""
    return SUPABASE_ANON_KEY


def verify_supabase_token(token: str) -> Dict[str, Any]:
    """Verify *token* with the hardened validator.

    Delegates to :class:`SupabaseJWTValidator`. Raises ``HTTPException(401)``
    on any failure and never returns unverified claims.
    """
    return DEFAULT_VALIDATOR.verify(token)


def _extract_raw_token(
    credentials: Optional[HTTPAuthorizationCredentials],
    token: Optional[str],
    authorization: Optional[str],
) -> Optional[str]:
    """Pull the raw JWT from the first available source."""
    if credentials and credentials.credentials:
        return credentials.credentials
    if authorization:
        parts = authorization.strip().split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return authorization.strip()
    if token:
        return token.strip()
    return None


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """FastAPI dependency for protected routes.

    Extracts the token from a Bearer header, a raw ``Authorization`` header, or
    the ``?token=`` query parameter (for browser file downloads), then validates
    it with the hardened JWT validator. Identity comes only from verified claims.
    """
    raw_token = _extract_raw_token(credentials, token, authorization)
    if not raw_token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please sign in with Google.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_supabase_token(raw_token)


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
) -> Optional[Dict[str, Any]]:
    """Optional authentication dependency; returns ``None`` when unauthenticated."""
    try:
        return await get_current_user(
            credentials=credentials, token=token, authorization=authorization
        )
    except HTTPException:
        return None


def user_from_query_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Resolve a user from a ``?token=`` query parameter (file downloads).

    Returns ``None`` for missing or invalid tokens instead of raising, matching
    the existing download flow where the route decides between 401 and 403.
    """
    if not token:
        return None
    try:
        return verify_supabase_token(token)
    except Exception:
        return None


__all__ = [
    "get_current_user",
    "get_optional_user",
    "get_supabase_anon_key",
    "get_supabase_url",
    "user_from_query_token",
    "verify_supabase_token",
]
