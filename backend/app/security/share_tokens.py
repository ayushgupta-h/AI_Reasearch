"""
app/security/share_tokens.py
============================
HMAC-SHA256 tokens for read-only report sharing, with expiry.

Token layout (24 characters, unchanged public length)::

    <expiry:8 hex><signature:16 hex>

* ``expiry`` is a UTC epoch second, quantised to the hour so a token is stable
  for its whole TTL (link regeneration is idempotent).
* ``signature`` is the first 16 hex characters of ``HMAC-SHA256(secret,
  "{filename}|{expiry}")``.

The signature therefore binds the file **and** its expiry, and verification
compares the complete token with :func:`hmac.compare_digest` (constant time).
Legacy non-expiring tokens fail closed: their signature covers a different
payload.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.observability import get_logger

logger = get_logger("app.security.share")

_EXPIRY_HEX_LEN = 8
_SIGNATURE_HEX_LEN = 16
TOKEN_LENGTH = _EXPIRY_HEX_LEN + _SIGNATURE_HEX_LEN  # 24 — public contract
_EXPIRY_BUCKET_SECONDS = 3600
_MAX_TTL_HOURS = 8760  # 1 year

_EPHEMERAL_SECRET: Optional[bytes] = None


def share_secret() -> bytes:
    """Return the configured signing secret.

    In development, when neither ``SHARE_SECRET`` nor ``CHROMA_ENCRYPTION_KEY``
    is set, a process-local random secret is generated (with a warning). Share
    links then stop working after a restart, which is preferable to signing with
    a publicly known constant.
    """
    global _EPHEMERAL_SECRET
    configured = get_settings().share_secret
    if configured:
        return configured.encode("utf-8")
    if _EPHEMERAL_SECRET is None:
        _EPHEMERAL_SECRET = secrets.token_bytes(32)
        logger.warning(
            "share_secret_missing_using_ephemeral — set SHARE_SECRET for stable links"
        )
    return _EPHEMERAL_SECRET


def _ttl_hours(ttl_hours: Optional[int] = None) -> int:
    if ttl_hours is None:
        ttl_hours = get_settings().share_token_ttl_hours
    return max(1, min(int(ttl_hours), _MAX_TTL_HOURS))


def _expiry_epoch(ttl_hours: Optional[int] = None) -> int:
    ttl = _ttl_hours(ttl_hours)
    bucket = int(time.time() // _EXPIRY_BUCKET_SECONDS) + 1
    return (bucket + ttl) * _EXPIRY_BUCKET_SECONDS


def _sign(clean_filename: str, expiry: int) -> str:
    payload = f"{clean_filename}|{expiry}".encode("utf-8")
    digest = hmac.new(share_secret(), payload, hashlib.sha256).hexdigest()
    return f"{expiry:0{_EXPIRY_HEX_LEN}x}{digest[:_SIGNATURE_HEX_LEN]}"


def generate_share_token(filename: str, ttl_hours: Optional[int] = None) -> str:
    """Create a signed, expiring token bound to one report file name."""
    clean = Path(filename).name
    return _sign(clean, _expiry_epoch(ttl_hours))


def token_expires_at(token: str) -> Optional[int]:
    """Return the token's UTC expiry epoch, or ``None`` when unparsable."""
    if not token or len(token) != TOKEN_LENGTH:
        return None
    try:
        return int(token[:_EXPIRY_HEX_LEN], 16)
    except ValueError:
        return None


def verify_share_token(
    filename: str, token: str, *, now: Optional[float] = None
) -> bool:
    """Validate signature and expiry in constant time.

    ``now`` is injectable so tests never depend on the wall clock.
    """
    if not token or len(token) != TOKEN_LENGTH:
        return False

    expiry = token_expires_at(token)
    if expiry is None:
        return False

    current = time.time() if now is None else now
    # Cheap rejection first; the signature is still checked in constant time.
    if expiry < current:
        logger.info("share_token_expired", extra={"error_code": "forbidden"})
        return False

    expected = _sign(Path(filename).name, expiry)
    return hmac.compare_digest(expected, token)
