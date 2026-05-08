"""Lightweight event hook system used by the LangGraph pipelines.

The hub is intentionally tiny: it stores a list of subscribers and broadcasts
events via plain function calls. It does not try to be a full pub/sub system —
the intent is to give callers (HTTP endpoints, structured logging, future
streaming integrations) a single place to plug in.

Events are dictionaries with a ``type`` key and optional payload. Examples:

* ``{\"type\": \"query_received\", \"query\": \"phone\"}``
* ``{\"type\": \"vector_search_started\", \"queries\": [\"phone\", \"phones\"]}``
* ``{\"type\": \"completed\", \"total\": 12}``
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

EventCallback = Callable[[Dict[str, Any]], None]


class EventHub:
    """Per-instance hub. Use :func:`get_default_hub` for the global one."""

    def __init__(self) -> None:
        self._subscribers: List[EventCallback] = []
        self._lock = threading.Lock()

    def subscribe(self, callback: EventCallback) -> Callable[[], None]:
        """Register ``callback`` and return a function that removes it."""
        with self._lock:
            self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:  # pragma: no cover - already removed.
                    pass

        return _unsubscribe

    def publish(self, event: Dict[str, Any]) -> None:
        """Broadcast ``event`` to subscribers. Errors are logged, never raised."""
        with self._lock:
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(event)
            except Exception as exc:  # noqa: BLE001
                log.warning("Event subscriber raised: %s", exc)


_default_hub: Optional[EventHub] = None
_default_lock = threading.Lock()


def get_default_hub() -> EventHub:
    global _default_hub
    if _default_hub is None:
        with _default_lock:
            if _default_hub is None:
                _default_hub = EventHub()
    return _default_hub


def reset_default_hub() -> None:  # pragma: no cover - testing helper
    global _default_hub
    _default_hub = EventHub()


__all__ = ["EventHub", "EventCallback", "get_default_hub", "reset_default_hub"]
