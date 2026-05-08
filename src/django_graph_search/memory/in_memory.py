"""Process-local memory backend.

Suitable for tests, single-worker deployments and as a drop-in default. For
multi-worker setups, use the Redis or Django cache backend instead.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Deque, Dict, List

from .base import BaseMemoryBackend, ConversationEvent


class InMemoryBackend(BaseMemoryBackend):
    """Bounded per-session deque of events.

    Thread-safe via a single lock; the critical sections are tiny so
    contention is not a real concern at conversational-search workloads.
    """

    def __init__(self, max_history_items: int = 10, **options) -> None:
        super().__init__(max_history_items=max_history_items, **options)
        self._store: Dict[str, Deque[ConversationEvent]] = {}
        self._lock = threading.Lock()

    def get_history(self, session_id: str) -> List[ConversationEvent]:
        with self._lock:
            return list(self._store.get(session_id, ()))

    def append_event(self, session_id: str, event: ConversationEvent) -> None:
        with self._lock:
            bucket = self._store.get(session_id)
            if bucket is None:
                bucket = deque(maxlen=self.max_history_items)
                self._store[session_id] = bucket
            bucket.append(event)

    def clear_history(self, session_id: str) -> None:
        with self._lock:
            self._store.pop(session_id, None)
