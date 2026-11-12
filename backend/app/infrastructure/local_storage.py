"""
app/infrastructure/local_storage.py
===================================
Filesystem-backed object storage for local development and tests.

Implements :class:`app.infrastructure.ports.ObjectStorage` with:

* relative-key enforcement (no absolute paths, no ``..`` traversal),
* content metadata persisted in a sidecar directory,
* HMAC-signed, expiring URLs using the ``local://`` scheme.

Signed URLs are only meaningful to this process (or a process given the same
signing key); they are not HTTP URLs and no web server serves them. That is
deliberate — this adapter does not imitate S3.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Callable, Mapping, Optional
from urllib.parse import parse_qs, quote, unquote, urlsplit

from app.infrastructure.ports import SignedUrl, StoredObject


class InvalidSignedUrl(ValueError):
    """Raised when a signed URL is malformed, expired or tampered with."""


class LocalObjectStorage:
    """Directory-backed object store; single-process only."""

    def __init__(
        self,
        root: str | Path,
        *,
        signing_key: Optional[bytes] = None,
        url_scheme: str = "local",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._root = Path(root)
        self._signing_key = signing_key or secrets.token_bytes(32)
        self._url_scheme = url_scheme
        self._clock = clock

    # ── ObjectStorage ─────────────────────────────────────────────────────

    def upload(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        content_type: str = "application/octet-stream",
        metadata: Optional[Mapping[str, str]] = None,
    ) -> StoredObject:
        path = self._path(key)
        payload = self._read_bytes(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

        info = StoredObject(
            key=self._normalise_key(key),
            size=len(payload),
            content_type=content_type,
            metadata=dict(metadata or {}),
        )
        self._write_metadata(info)
        return info

    def download(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(f"No object stored at '{key}'")
        return path.read_bytes()

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.is_file():
            return False
        path.unlink()
        self._meta_path(key).unlink(missing_ok=True)
        self._prune_empty_directories(path.parent)
        return True

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def generate_signed_url(
        self, key: str, *, expires_in: float = 3600, method: str = "GET"
    ) -> SignedUrl:
        if expires_in <= 0:
            raise ValueError("expires_in must be positive")
        normalised = self._normalise_key(key)
        method = method.upper()
        expires_at = int(self._clock() + float(expires_in))
        signature = self._signature(method, normalised, expires_at)
        url = (
            f"{self._url_scheme}://{quote(self._root.name or 'objects')}/"
            f"{quote(normalised, safe='/')}"
            f"?method={method}&expires={expires_at}&signature={signature}"
        )
        return SignedUrl(
            url=url,
            key=normalised,
            expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
        )

    # ── Extras ────────────────────────────────────────────────────────────

    def verify_signed_url(self, url: str, *, method: str = "GET") -> str:
        """Validate *url* and return its object key, or raise ``InvalidSignedUrl``."""
        parts = urlsplit(url)
        if parts.scheme != self._url_scheme:
            raise InvalidSignedUrl("Unexpected signed URL scheme")

        query = parse_qs(parts.query)
        expires_raw = (query.get("expires") or [""])[0]
        signature = (query.get("signature") or [""])[0]
        url_method = (query.get("method") or [""])[0].upper()
        if not expires_raw or not signature:
            raise InvalidSignedUrl("Signed URL is missing expiry or signature")
        if url_method != method.upper():
            raise InvalidSignedUrl("Signed URL was issued for a different method")

        try:
            expires_at = int(expires_raw)
        except ValueError as exc:
            raise InvalidSignedUrl("Signed URL expiry is not a timestamp") from exc

        key = unquote(parts.path).lstrip("/")
        self._normalise_key(key)
        if expires_at <= int(self._clock()):
            raise InvalidSignedUrl("Signed URL has expired")
        expected = self._signature(method.upper(), key, expires_at)
        if not hmac.compare_digest(expected, signature):
            raise InvalidSignedUrl("Signed URL signature is invalid")
        return key

    def stat(self, key: str) -> StoredObject:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(f"No object stored at '{key}'")
        info = self._read_metadata(key)
        if info is not None:
            return info
        return StoredObject(
            key=self._normalise_key(key),
            size=path.stat().st_size,
            content_type="application/octet-stream",
        )

    @property
    def root(self) -> Path:
        return self._root

    # ── Backup / recovery ───────────────────────────────────────────────────

    def backup(self) -> str:
        """Snapshot every stored object (including sidecar metadata).

        Returns a backup directory path inside ``<root>/.backups/<stamp>``.
        No object is read or rewritten: files are copied byte-for-byte.
        """
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = self._root / ".backups" / stamp
        dest.mkdir(parents=True, exist_ok=True)
        _snapshot_directory(self._root, dest, exclude={self._root / ".backups"})
        return str(dest)

    def restore(self, backup_id: str) -> int:
        """Restore objects from a previous backup.

        ``backup_id`` is a stamp (e.g. ``20260101T000000Z``) or a path. Returns
        the number of files restored. Existing object files with identical
        relative paths are overwritten; nothing is deleted from the live root.
        """
        backup_dir = Path(backup_id)
        if not backup_dir.is_absolute():
            backup_dir = self._root / ".backups" / backup_id
        if not backup_dir.is_dir():
            raise FileNotFoundError(f"Backup not found: {backup_id}")
        restored = 0
        for source in backup_dir.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(backup_dir)
            if relative.parts and relative.parts[0] == ".metadata":
                target = self._root / relative
            else:
                # Only restore known object keys (skip stray files).
                try:
                    key = self._normalise_key(str(relative))
                except ValueError:
                    continue
                target = self._path(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            restored += 1
        return restored

    # ── Internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _read_bytes(data: bytes | BinaryIO) -> bytes:
        if isinstance(data, (bytes, bytearray, memoryview)):
            return bytes(data)
        payload = data.read()
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise TypeError("Object data must be bytes or a binary file object")
        return bytes(payload)

    @staticmethod
    def _normalise_key(key: str) -> str:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Object key must be a non-empty string")
        if "\x00" in key:
            raise ValueError("Object key contains a null byte")

        candidate = key.replace("\\", "/")
        if candidate.startswith("/") or re.match(r"^[A-Za-z]:", candidate):
            raise ValueError("Object keys must be relative paths")

        parts: list[str] = []
        for part in candidate.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                raise ValueError("Object keys may not traverse outside the root")
            parts.append(part)
        if not parts:
            raise ValueError("Object key must name a file")
        return "/".join(parts)

    def _path(self, key: str) -> Path:
        normalised = self._normalise_key(key)
        candidate = self._root.joinpath(*normalised.split("/")).resolve()
        root = self._root.resolve()
        if root not in candidate.parents:
            raise ValueError("Object keys may not escape the storage root")
        return candidate

    def _signature(self, method: str, key: str, expires_at: int) -> str:
        message = f"{method}|{key}|{expires_at}".encode("utf-8")
        return hmac.new(self._signing_key, message, hashlib.sha256).hexdigest()

    def _meta_path(self, key: str) -> Path:
        digest = hashlib.sha256(self._normalise_key(key).encode("utf-8")).hexdigest()
        return self._root / ".metadata" / f"{digest}.json"

    def _write_metadata(self, info: StoredObject) -> None:
        meta_path = self._meta_path(info.key)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps(
                {
                    "key": info.key,
                    "size": info.size,
                    "content_type": info.content_type,
                    "metadata": dict(info.metadata),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _read_metadata(self, key: str) -> Optional[StoredObject]:
        meta_path = self._meta_path(key)
        if not meta_path.is_file():
            return None
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return StoredObject(
            key=str(raw.get("key", self._normalise_key(key))),
            size=int(raw.get("size", 0)),
            content_type=str(raw.get("content_type", "application/octet-stream")),
            metadata=dict(raw.get("metadata") or {}),
        )

    def _prune_empty_directories(self, directory: Path) -> None:
        root = self._root.resolve()
        current = directory.resolve()
        while current != root and root in current.parents:
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent


def _snapshot_directory(source: Path, dest: Path, *, exclude: set[Path]) -> None:
    """Copy *source*'s files into *dest*, preserving relative layout."""
    for path in source.rglob("*"):
        if path in exclude:
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


__all__ = ["InvalidSignedUrl", "LocalObjectStorage"]
