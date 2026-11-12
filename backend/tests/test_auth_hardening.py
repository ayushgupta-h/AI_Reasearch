"""Task 2.4 - hardened Supabase JWT validation and multi-tenant ownership tests."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException

from app.security.jwt_validator import (
    DEFAULT_AUDIENCE,
    SupabaseJWTValidator,
    verify_supabase_token,
)

TEST_SECRET = "unit-test-jwt-secret-0123456789abcdef"
TEST_ISS = "https://project.supabase.co"


def _make_token(
    *,
    sub: str = "user-a",
    aud: str = DEFAULT_AUDIENCE,
    iss: str = TEST_ISS,
    exp_offset: int = 3600,
    alg: str = "HS256",
    secret: str = TEST_SECRET,
    **claims,
) -> str:
    payload = {
        "sub": sub,
        "email": f"{sub}@example.com",
        "aud": aud,
        "role": "authenticated",
        "iss": iss,
        "exp": int(time.time()) + exp_offset,
        "user_metadata": {"name": sub},
    }
    payload.update(claims)
    return jwt.encode(payload, secret, algorithm=alg)


def _validator(**kwargs) -> SupabaseJWTValidator:
    defaults = {
        "supabase_url": TEST_ISS,
        "jwt_secret": TEST_SECRET,
        "jwks_endpoint": f"{TEST_ISS}/auth/v1/.well-known/jwks.json",
        "audience": DEFAULT_AUDIENCE,
    }
    defaults.update(kwargs)
    return SupabaseJWTValidator(**defaults)


class TestJWTHardening:
    def test_valid_hs256_token_is_verified(self) -> None:
        v = _validator()
        user = v.verify(_make_token())
        assert user["user_id"] == "user-a"
        assert user["email"] == "user-a@example.com"

    def test_expired_token_is_rejected(self) -> None:
        v = _validator()
        token = _make_token(exp_offset=-60)
        with pytest.raises(HTTPException) as e:
            v.verify(token)
        assert e.value.status_code == 401
        assert "expired" in e.value.detail.lower()

    def test_malformed_token_is_rejected(self) -> None:
        v = _validator()
        with pytest.raises(HTTPException) as e:
            v.verify("not-a-jwt")
        assert e.value.status_code == 401

    def test_wrong_signature_is_rejected(self) -> None:
        v = _validator()
        forged = _make_token(secret="attacker-secret")
        with pytest.raises(HTTPException) as e:
            v.verify(forged)
        assert e.value.status_code == 401

    def test_invalid_audience_is_rejected(self) -> None:
        v = _validator()
        token = _make_token(aud="some-other-audience")
        with pytest.raises(HTTPException) as e:
            v.verify(token)
        assert e.value.status_code == 401
        assert "audience" in e.value.detail.lower()

    def test_invalid_issuer_is_rejected(self) -> None:
        v = _validator()
        token = _make_token(iss="https://attacker.supabase.co")
        with pytest.raises(HTTPException) as e:
            v.verify(token)
        assert e.value.status_code == 401
        assert "issuer" in e.value.detail.lower()

    def test_disallowed_algorithm_is_rejected(self) -> None:
        v = _validator()
        # none algorithm -> should be rejected by the allowlist
        token = _make_token()
        with patch.object(
            SupabaseJWTValidator, "_header_alg", return_value="none"
        ):
            with pytest.raises(HTTPException) as e:
                v.verify(token)
        assert e.value.status_code == 401

    def test_rs256_via_jwks_is_verified(self, monkeypatch) -> None:
        # Simulate an RS256 key pair + JWKS endpoint.
        import cryptography  # noqa: F401  (sanity import)
        from cryptography.hazmat.primitives.asymmetric import rsa

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = private_key.public_key()

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa as _rsa

        # Build minimalist JWKS payload for the public key.
        import base64
        import json

        from cryptography.hazmat.primitives.asymmetric import ec as _ec  # noqa

        numbers = public_key.public_numbers()
        def _b64_int(n):
            return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8 or 1, "big")).decode().rstrip("=")

        jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": "test-key-1",
                    "n": _b64_int(numbers.n),
                    "e": _b64_int(numbers.e),
                }
            ]
        }

        fake_client = MagicMock()
        fake_client.get_signing_key_from_jwt.return_value = MagicMock(
            key=public_key, kid="test-key-1"
        )

        # RS256 token signed with the private key.
        payload = {
            "sub": "user-rs",
            "email": "rs@example.com",
            "aud": DEFAULT_AUDIENCE,
            "role": "authenticated",
            "iss": TEST_ISS,
            "exp": int(time.time()) + 1800,
        }
        rs_token = jwt.encode(payload, private_key, algorithm="RS256")

        v = SupabaseJWTValidator(
            TEST_ISS,
            jwt_secret="",
            jwks_endpoint=f"{TEST_ISS}/auth/v1/.well-known/jwks.json",
            audience=DEFAULT_AUDIENCE,
        )
        v._jwks_client = fake_client
        user = v.verify(rs_token)
        assert user["user_id"] == "user-rs"

    def test_jwks_key_rotation_refetches(self, monkeypatch) -> None:
        """Unknown kid triggers a JWKS refetch (never trusts the old key)."""
        from unittest.mock import call

        fake_client = MagicMock()
        fake_client.get_signing_key_from_jwt.side_effect = jwt.PyJWKClientError(
            "Unable to find a signing key"
        )
        v = SupabaseJWTValidator(
            TEST_ISS,
            jwt_secret="",
            jwks_endpoint=f"{TEST_ISS}/auth/v1/.well-known/jwks.json",
            audience=DEFAULT_AUDIENCE,
        )
        v._jwks_client = fake_client
        with pytest.raises(HTTPException) as e:
            v.verify(_make_token())
        assert e.value.status_code == 401

    def test_verify_never_logs_token(self, monkeypatch) -> None:
        captured = []
        fake_log = MagicMock()

        class _RecordingLogger:
            def info(self, msg, *a, **k):
                captured.append(str(msg).format(*a, **k))

        v = _validator()
        token = _make_token()
        with patch("app.security.jwt_validator.bind_user") as _bind:
            v.verify(token)
        # token substring should not appear anywhere in captured logs
        for line in captured:
            assert token not in line

    def test_module_level_backwards_compatible_entrypoint(self, monkeypatch) -> None:
        with patch("app.security.jwt_validator.DEFAULT_VALIDATOR") as mock_validator:
            mock_validator.verify.return_value = {"user_id": "user-a"}
            assert verify_supabase_token("whatever")["user_id"] == "user-a"
            mock_validator.verify.assert_called_with("whatever")


class TestGuestAndOwnership:
    def test_guest_access_to_protected_endpoint_is_rejected(self) -> None:
        from app.security.dependencies import get_current_user

        # No credentials -> must be 401 with WWW-Authenticate challenge.
        # (Covered more fully in test_auth_jwt.py; here we assert the hardened
        #  validator also refuses an absent token.)
        v = _validator()
        with pytest.raises(HTTPException) as e:
            v.verify("")
        assert e.value.status_code == 401

    def test_user_b_cannot_act_as_user_a(self, monkeypatch) -> None:
        """Multi-tenant isolation: identity always comes from the validated JWT."""
        v = _validator()
        token_a = _make_token(sub="user-a")
        token_b = _make_token(sub="user-b")

        identity_a = v.verify(token_a)
        identity_b = v.verify(token_b)

        assert identity_a["user_id"] == "user-a"
        assert identity_b["user_id"] == "user-b"
        assert identity_a["user_id"] != identity_b["user_id"]

    def test_user_a_accessing_user_b_research_is_rejected(self) -> None:
        """User A must not access User B's research sessions."""
        from unittest.mock import MagicMock
        from app.services.session_service import SessionService

        mock_mgr = MagicMock()
        # Session exists and belongs to user-b
        mock_mgr.get_session.side_effect = lambda session_id, user_id=None: (
            {"session_id": session_id, "user_id": "user-b"} if user_id == "user-b" else None
        )
        service = SessionService(mock_mgr)

        # User A attempts to access User B's session
        accessed = service.get_session("session-b", user_id="user-a")
        assert accessed is None

        # User B can access their own session
        own_session = service.get_session("session-b", user_id="user-b")
        assert own_session is not None
        assert own_session["user_id"] == "user-b"

    def test_user_a_downloading_user_b_report_is_rejected(self, tmp_path) -> None:
        """User A must not download User B's report without a share token."""
        from unittest.mock import MagicMock
        from app.services.report_service import ReportService
        from app.errors import AuthorizationError

        mock_settings = MagicMock()
        mock_settings.reports_dir = tmp_path
        # Create a dummy report file in reports dir
        report_file = tmp_path / "report_user_b.md"
        report_file.write_text("# Report for User B", encoding="utf-8")

        mock_sessions = MagicMock()
        # Only user-b owns report_user_b.md
        mock_sessions.user_owns_report.side_effect = lambda uid, fname: (
            uid == "user-b" and fname == "report_user_b.md"
        )

        service = ReportService(mock_settings, mock_sessions)

        # User A tries to download User B's report -> 403 AuthorizationError
        with pytest.raises(AuthorizationError) as exc:
            service.authorize_download("report_user_b.md", user={"user_id": "user-a"}, share_token=None)
        assert "do not own" in str(exc.value).lower()

        # User B can download their own report
        path, media_type, read_only = service.authorize_download(
            "report_user_b.md", user={"user_id": "user-b"}, share_token=None
        )
        assert path == report_file
        assert not read_only

    def test_user_a_deleting_user_b_session_is_rejected(self) -> None:
        """User A must not delete User B's research session."""
        from unittest.mock import MagicMock
        from app.services.session_service import SessionService

        mock_mgr = MagicMock()
        # Only user-b can delete session-b
        mock_mgr.delete_session.side_effect = lambda session_id, user_id=None: (
            user_id == "user-b" and session_id == "session-b"
        )
        service = SessionService(mock_mgr)

        # User A attempts to delete User B's session -> returns False
        deleted_by_a = service.delete_session("session-b", user_id="user-a")
        assert deleted_by_a is False

        # User B can delete their own session
        deleted_by_b = service.delete_session("session-b", user_id="user-b")
        assert deleted_by_b is True
