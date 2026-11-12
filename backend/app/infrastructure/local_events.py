"""
app/infrastructure/local_events.py
==================================
Synchronous, in-process publish/subscribe bus.

Handlers run on the publishing thread. This adapter is for local development,
tests and future decoupling of side effects; it offers no delivery guarantees
and loses pending work when the process stops.
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, List, Mapping, Optional

from app.infrastructure.ports import Event, EventHandler, Unsubscribe
from app.observability import get_logger

logger = get_logger("app.infrastructure.events")

WILDCARD = "*"


class InProcessEventBus:
    """Implements both ``EventPublisher`` and ``EventSubscriber``."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handlers: Dict[str, List[EventHandler]] = {}

    def publish(self, name: str, payload: Optional[Mapping[str, Any]] = None) -> Event:
        if not isinstance(name, str) or not name:
            raise ValueError("Event name must be a non-empty string")
        event = Event(name=name, payload=dict(payload or {}))

        with self._lock:
            handlers = list(self._handlers.get(name, ())) + list(
                self._handlers.get(WILDCARD, ())
            )

        for handler in handlers:
            try:
                handler(event)
            except Exception:  # one bad subscriber must not stop the others
                logger.exception(
                    "event.handler_failed",
                    extra={"event": name, "error_code": "event_handler_failed"},
                )
        return event

    def subscribe(self, name: str, handler: EventHandler) -> Unsubscribe:
        if not isinstance(name, str) or not name:
            raise ValueError("Event name must be a non-empty string")
        if not callable(handler):
            raise TypeError("Event handler must be callable")

        with self._lock:
            self._handlers.setdefault(name, []).append(handler)

        def unsubscribe() -> None:
            with self._lock:
                listeners = self._handlers.get(name)
                if not listeners:
                    return
                try:
                    listeners.remove(handler)
                except ValueError:
                    return
                if not listeners:
                    self._handlers.pop(name, None)

        return unsubscribe

    def subscriber_count(self, name: str) -> int:
        with self._lock:
            return len(self._handlers.get(name, ()))

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()


__all__ = ["InProcessEventBus", "WILDCARD"]
