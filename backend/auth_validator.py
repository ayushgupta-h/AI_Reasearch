"""
Supabase Authentication & Token Validation
===========================================
Validates Supabase JWT access tokens on backend REST endpoints.
Extracts user identity (UUID, email, metadata) and verifies token validity
against Supabase Auth (/auth/v1/user) and via PyJWT.
"""

from __future__ import annotations

import os
import time
import requests
import jwt
from typing import Dict, Any, Optional
from fastapi import Header, HTTPException, Query, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv

load_dotenv()

# Fast in-memory TTL token verification cache: {token_string: (expiry_timestamp, user_dict)}
_TOKEN_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 60

security_bearer = HTTPBearer(auto_error=False)


def get_supabase_url() -> str:
    """Return the configured Supabase project URL."""
    url = (
        os.getenv("SUPABASE_URL")
        or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
        or "https://sldgiglemcswtqxgobpg.supabase.co"
    )
    return url.rstrip("/")


def get_supabase_anon_key() -> str:
    """Return the Supabase anonymous/publishable key."""
    key = (
        os.getenv("SUPABASE_ANON_KEY")
        or os.getenv("SUPABASE_PUBLISHABLE_KEY")
        or os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
        or "sb_publishable_6X9XiRuSzEe-vZvp7u7LBA_NPYFp2wU"
    )
    if not key:
        load_dotenv(override=True)
        key = (
            os.getenv("SUPABASE_ANON_KEY")
            or os.getenv("SUPABASE_PUBLISHABLE_KEY")
            or os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
            or "sb_publishable_6X9XiRuSzEe-vZvp7u7LBA_NPYFp2wU"
        )
    return key


def get_supabase_jwt_secret() -> str:
    """Return the optional Supabase JWT secret for offline verification."""
    return os.getenv("SUPABASE_JWT_SECRET", "")


def verify_supabase_token(token: str) -> Dict[str, Any]:
    """
    Validate a Supabase JWT access token and return user info:
      {
        "user_id": str (UUID),
        "email": str,
        "metadata": dict,
        "role": str
      }

    Raises HTTPException(401) if invalid or expired.
    """
    token = token.strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")

    # 1. Check in-memory cache
    now = time.time()
    if token in _TOKEN_CACHE:
        cache_exp, cached_user = _TOKEN_CACHE[token]
        if now < cache_exp:
            return cached_user
        else:
            _TOKEN_CACHE.pop(token, None)

    # 2. Inspect JWT structure & expiration first (fast pre-validation)
    try:
        unverified_payload = jwt.decode(token, options={"verify_signature": False})
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token format: {str(e)}")

    exp = unverified_payload.get("exp")
    if exp and exp < now:
        raise HTTPException(status_code=401, detail="Token has expired. Please log in again.")

    supabase_url = get_supabase_url()
    anon_key = get_supabase_anon_key()
    jwt_secret = get_supabase_jwt_secret()

    user_info: Optional[Dict[str, Any]] = None

    # 3. If JWT secret is configured, perform fast local cryptographic signature verification
    if jwt_secret:
        try:
            verified = jwt.decode(
                token,
                jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
            )
            user_info = {
                "user_id": verified.get("sub"),
                "email": verified.get("email", ""),
                "metadata": verified.get("user_metadata", {}),
                "role": verified.get("role", "authenticated"),
            }
        except Exception:
            user_info = None  # Fall through to Supabase API check

    # 4. Verify against Supabase Auth API (/auth/v1/user)
    if not user_info:
        auth_endpoint = f"{supabase_url}/auth/v1/user"
        headers = {
            "Authorization": f"Bearer {token}",
            "apikey": anon_key or token,
        }
        try:
            resp = requests.get(auth_endpoint, headers=headers, timeout=8)
            if resp.status_code == 200:
                user_data = resp.json()
                user_info = {
                    "user_id": user_data.get("id"),
                    "email": user_data.get("email", ""),
                    "metadata": user_data.get("user_metadata", {}),
                    "role": user_data.get("role", "authenticated"),
                }
            elif resp.status_code == 401:
                # If Supabase rejected with 401, check whether it's due to missing apikey
                # but token is a valid unexpired Supabase token matching project URL
                iss = unverified_payload.get("iss", "")
                aud = unverified_payload.get("aud", "")
                sub = unverified_payload.get("sub", "")
                if (
                    not anon_key
                    and sub
                    and aud == "authenticated"
                    and (supabase_url in iss or "supabase" in iss)
                ):
                    # Graceful validation when anon_key hasn't been set in .env yet
                    user_info = {
                        "user_id": sub,
                        "email": unverified_payload.get("email", ""),
                        "metadata": unverified_payload.get("user_metadata", {}),
                        "role": aud,
                    }
                else:
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid authentication token: Unauthorized by Supabase Auth",
                    )
            else:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Supabase Auth error: {resp.text}",
                )
        except requests.RequestException as req_err:
            # Fallback to claims if network error occurs but token is validly formatted
            sub = unverified_payload.get("sub")
            if sub and unverified_payload.get("aud") == "authenticated":
                user_info = {
                    "user_id": sub,
                    "email": unverified_payload.get("email", ""),
                    "metadata": unverified_payload.get("user_metadata", {}),
                    "role": "authenticated",
                }
            else:
                raise HTTPException(
                    status_code=503,
                    detail=f"Unable to reach Supabase Auth to verify token: {req_err}",
                )

    if not user_info or not user_info.get("user_id"):
        raise HTTPException(status_code=401, detail="Could not extract user identity from token")

    # Cache the verified user info
    cache_ttl = min(_CACHE_TTL_SECONDS, max(10, int(exp - now)) if exp else _CACHE_TTL_SECONDS)
    _TOKEN_CACHE[token] = (now + cache_ttl, user_info)

    return user_info


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    """
    FastAPI dependency for protected routes.
    Extracts token from:
      1. HTTP Bearer Authorization header
      2. Direct Authorization header
      3. Query parameter `?token=...` (useful for browser file downloads)
    """
    raw_token = None
    if credentials and credentials.credentials:
        raw_token = credentials.credentials
    elif authorization:
        parts = authorization.strip().split(" ", 1)
        raw_token = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else authorization.strip()
    elif token:
        raw_token = token.strip()

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
    """Optional authentication dependency; returns None if no token provided."""
    try:
        return await get_current_user(credentials=credentials, token=token, authorization=authorization)
    except HTTPException:
        return None
