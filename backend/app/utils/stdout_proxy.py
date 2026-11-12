"""
app/utils/stdout_proxy.py
=========================
Thread-local ``sys.stdout`` proxy.

The research pipeline reports progress through Rich ``console.print``. To turn
those prints into SSE events we capture stdout — but replacing the process-wide
``sys.stdout`` per request (the original implementation) leaks one user's logs
into another user's stream when two runs overlap.

This proxy is installed once. Each research thread registers its own writer for
the duration of the run; every other thread/process output falls through to the
real stdout.

Rich resolves ``sys.stdout`` at write time, so it transparently picks this up.
"""

from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

_PROXY_ATTR = "_rai_thread_proxy"


class ThreadLocalStream:
    """Dispatch writes to a per-thread writer, else the wrapped stream."""

    def __init__(self, fallback: Any) -> None:
        object.__setattr__(self, "_fallback", fallback)
        object.__setattr__(self, "_local", threading.local())

    # ── writer registration ─────────────────────────────────────────────

    def set_writer(self, writer: Any) -> None:
        self._local.writer = writer

    def clear_writer(self) -> None:
        self._local.__dict__.pop("writer", None)

    @property
    def writer(self) -> Optional[Any]:
        return getattr(self._local, "writer", None)

    # ── file-like protocol ──────────────────────────────────────────────

    def write(self, text: str) -> int:
        writer = self.writer or self._fallback
        return writer.write(text)

    def flush(self) -> None:
        writer = self.writer or self._fallback
        try:
            writer.flush()
        except Exception:  # pragma: no cover
            pass

    def isatty(self) -> bool:
        writer = self.writer or self._fallback
        try:
            return bool(writer.isatty())
        except Exception:
            return False

    def fileno(self) -> int:
        return self._fallback.fileno()

    def reconfigure(self, **kwargs: Any) -> None:
        target = self._fallback
        reconfigure = getattr(target, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_fallback"), name)


def ensure_stdout_proxy() -> ThreadLocalStream:
    """Install the proxy if needed and return it."""
    current = sys.stdout
    if getattr(current, _PROXY_ATTR, False):
        return current  # type: ignore[return-value]
    proxy = ThreadLocalStream(current)
    setattr(proxy, _PROXY_ATTR, True)
    sys.stdout = proxy  # type: ignore[assignment]
    return proxy


def install_stdout_proxy() -> None:
    """Public, idempotent installation hook used at app startup."""
    ensure_stdout_proxy()


@contextmanager
def capture_stdout(writer: Any) -> Iterator[None]:
    """Route stdout writes from the *current thread* to ``writer``."""
    proxy = ensure_stdout_proxy()
    proxy.set_writer(writer)
    try:
        yield
    finally:
        proxy.clear_writer()
