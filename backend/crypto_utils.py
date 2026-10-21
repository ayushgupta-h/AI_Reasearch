"""
ChromaDB Document Encryption Utilities (AES-256-GCM)
=====================================================
Provides authenticated encryption for customer document text stored in ChromaDB.

Requirements:
- AES-256-GCM cipher with 256-bit key and 12-byte random IV (nonce).
- Key stored securely outside ChromaDB (in backend/chroma.key and .env).
- Ciphertext stored in ChromaDB (visible in Chroma dashboard as ciphertext).
- Transparent decryption during semantic search & retrieval.
"""

from __future__ import annotations

import os
import sys
import base64
import secrets
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = lambda *args, **kwargs: None

_KEY_FILE_NAME = "chroma.key"
_DEFAULT_KEY_PATH = Path(__file__).resolve().parent / _KEY_FILE_NAME
_CIPHERTEXT_PREFIX = "ENC:GCM:"

# In-memory cached key
_CACHED_KEY: Optional[bytes] = None


def get_key_file_path() -> Path:
    """Return the absolute path to the persistent key file."""
    custom = os.getenv("CHROMA_KEY_PATH")
    if custom:
        return Path(custom).resolve()
    return _DEFAULT_KEY_PATH


def get_or_create_chroma_key() -> bytes:
    """
    Retrieve the 256-bit AES-GCM encryption key or generate and persist a new one.

    Storage locations (checked in order):
      1. Environment variable: CHROMA_ENCRYPTION_KEY (hex or base64 or 32-byte string)
      2. Key file: backend/chroma.key (outside ChromaDB directory)
      3. Auto-generated 32 cryptographically secure bytes, persisted to
         backend/chroma.key and backend/.env.
    """
    global _CACHED_KEY
    if _CACHED_KEY is not None:
        return _CACHED_KEY

    # 1. Check environment variable
    env_key = os.getenv("CHROMA_ENCRYPTION_KEY", "").strip()
    if env_key:
        parsed = _parse_key_string(env_key)
        if parsed:
            _CACHED_KEY = parsed
            return parsed

    # 2. Check key file
    key_file = get_key_file_path()
    if key_file.is_file():
        try:
            content = key_file.read_text(encoding="utf-8").strip()
            parsed = _parse_key_string(content)
            if parsed:
                _CACHED_KEY = parsed
                return parsed
        except Exception as err:
            sys.stderr.write(f"[WARN] Failed to read chroma key file: {err}\n")

    # 3. Generate a new 256-bit (32 bytes) key
    new_raw_key = os.urandom(32)
    hex_key = new_raw_key.hex()

    # Persist to key file
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(hex_key, encoding="utf-8")
        # Restrict permissions where supported
        try:
            os.chmod(key_file, 0o600)
        except Exception:
            pass
    except Exception as err:
        sys.stderr.write(f"[WARN] Failed to write key file: {err}\n")

    # Persist to backend/.env and root .env if possible
    _persist_key_to_env(hex_key)

    _CACHED_KEY = new_raw_key
    return new_raw_key


def _parse_key_string(val: str) -> Optional[bytes]:
    """Parse hex or base64 representation of a 32-byte key."""
    val = val.strip().strip('"').strip("'")
    # Try hex (64 chars)
    if len(val) == 64:
        try:
            b = bytes.fromhex(val)
            if len(b) == 32:
                return b
        except ValueError:
            pass

    # Try base64 (44 chars)
    if len(val) == 44 or val.endswith("="):
        try:
            b = base64.b64decode(val)
            if len(b) == 32:
                return b
        except Exception:
            pass

    # Raw 32 bytes (rare in env)
    raw = val.encode("utf-8")
    if len(raw) == 32:
        return raw

    return None


def _persist_key_to_env(hex_key: str) -> None:
    """Add CHROMA_ENCRYPTION_KEY to .env files so it persists across restarts."""
    backend_env = Path(__file__).resolve().parent / ".env"
    root_env = Path(__file__).resolve().parent.parent / ".env"

    for env_path in (backend_env, root_env):
        if env_path.is_file():
            try:
                content = env_path.read_text(encoding="utf-8")
                if "CHROMA_ENCRYPTION_KEY" in content:
                    # Update line
                    lines = content.splitlines()
                    new_lines = []
                    for line in lines:
                        if line.startswith("CHROMA_ENCRYPTION_KEY="):
                            new_lines.append(f'CHROMA_ENCRYPTION_KEY="{hex_key}"')
                        else:
                            new_lines.append(line)
                    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                else:
                    with open(env_path, "a", encoding="utf-8") as f:
                        f.write(f'\n# ChromaDB AES-256-GCM Customer Document Encryption Key\nCHROMA_ENCRYPTION_KEY="{hex_key}"\n')
            except Exception as err:
                sys.stderr.write(f"[WARN] Failed to write key to {env_path}: {err}\n")


def is_encrypted(text: Optional[str]) -> bool:
    """Return True if text has the ENC:GCM: header."""
    return bool(text and isinstance(text, str) and text.startswith(_CIPHERTEXT_PREFIX))


def encrypt_text(text: Optional[str], key: Optional[bytes] = None) -> str:
    """
    Encrypt a text string using AES-256-GCM.

    Returns ciphertext string prefixed with 'ENC:GCM:' followed by
    base64(nonce [12 bytes] + ciphertext [variable] + auth_tag [16 bytes]).

    If text is already encrypted, returns as-is (idempotent).
    """
    if text is None or text == "":
        return ""
    if is_encrypted(text):
        return text

    aes_key = key or get_or_create_chroma_key()
    aesgcm = AESGCM(aes_key)

    # 12-byte cryptographically secure random nonce
    nonce = os.urandom(12)
    plaintext_bytes = str(text).encode("utf-8")

    # AESGCM.encrypt appends 16-byte authentication tag to ciphertext
    ciphertext_and_tag = aesgcm.encrypt(nonce, plaintext_bytes, None)

    payload = base64.b64encode(nonce + ciphertext_and_tag).decode("ascii")
    return f"{_CIPHERTEXT_PREFIX}{payload}"


def decrypt_text(ciphertext: Optional[str], key: Optional[bytes] = None) -> str:
    """
    Decrypt an AES-256-GCM encrypted string.

    If text is not encrypted (does not start with 'ENC:GCM:'), it is returned
    as-is to support smooth migrations and graceful fallback.
    """
    if ciphertext is None or ciphertext == "":
        return ""
    if not is_encrypted(ciphertext):
        return ciphertext  # Graceful fallback for unencrypted plaintext

    aes_key = key or get_or_create_chroma_key()
    aesgcm = AESGCM(aes_key)

    encoded = ciphertext[len(_CIPHERTEXT_PREFIX):]
    try:
        raw = base64.b64decode(encoded)
        if len(raw) < 28:  # 12-byte nonce + at least 16-byte tag
            return ciphertext
        nonce = raw[:12]
        ct = raw[12:]
        plaintext_bytes = aesgcm.decrypt(nonce, ct, None)
        return plaintext_bytes.decode("utf-8")
    except Exception as err:
        sys.stderr.write(f"[WARN] Chroma decrypt failed: {err}\n")
        return ciphertext
