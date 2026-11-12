"""Live Supabase JWT verification CLI (Task 2.4, Part 3).

Verifies a token against the configured Supabase JWKS / shared secret using the
hardened ``SupabaseJWTValidator``. It never prints the token, the secret, or
the JWKS URL — only a pass/fail result and the verified identity fields.

Usage (from ``backend/``):

    # verify a token passed as an argument
    python -m scripts.verify_jwt --token eyJ...

    # or via the environment (avoids shell history for the token)
    SUPABASE_TEST_TOKEN=eyJ... python -m scripts.verify_jwt

    # or pipe it on stdin
    echo eyJ... | python -m scripts.verify_jwt --stdin

    # print only what is configured (no secrets)
    python -m scripts.verify_jwt --check-config
"""

from __future__ import annotations

import os
import sys

from app.security.jwt_validator import DEFAULT_VALIDATOR


def _resolve_token(args) -> str:
    if args.token:
        return args.token
    if args.stdin:
        return sys.stdin.read().strip()
    return os.environ.get("SUPABASE_TEST_TOKEN", "").strip()


def _main() -> int:
    args = _parse_args()

    if args.check_config:
        from app.config import SUPABASE_JWKS_ENDPOINT, SUPABASE_JWT_SECRET, SUPABASE_URL

        print("Supabase URL configured :", bool(SUPABASE_URL))
        print("JWKS endpoint configured  :", bool(SUPABASE_JWKS_ENDPOINT))
        print("Shared HS256 secret set   :", bool(SUPABASE_JWT_SECRET))
        # Never print the values themselves.
        return 0

    token = _resolve_token(args)
    if not token:
        print("No token provided. Use --token, --stdin, or SUPABASE_TEST_TOKEN env var.")
        return 2

    try:
        identity = DEFAULT_VALIDATOR.verify(token)
    except Exception as exc:  # validator raises HTTPException(401) on failure
        status = getattr(exc, "status_code", 401)
        detail = getattr(exc, "detail", "invalid token")
        print(f"FAIL (HTTP {status}): {detail}")
        return 1

    print("PASS")
    print("  user_id :", identity.get("user_id"))
    print("  email   :", identity.get("email") or "(not provided)")
    print("  role    :", identity.get("role"))
    print("  audience: verified")
    print("  issuer  : verified")
    print("  expiry  : verified")
    print("  signature: verified")
    return 0


def _parse_args():
    import argparse

    parser = argparse.ArgumentParser(
        description="Verify a Supabase JWT with the hardened validator."
    )
    parser.add_argument("--token", help="Token to verify (avoid in shell history).")
    parser.add_argument(
        "--stdin", action="store_true", help="Read the token from stdin."
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Print which auth config is present (no secret values).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(_main())
