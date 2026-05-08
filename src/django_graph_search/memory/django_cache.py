"""Memory backend that piggy-backs on the Django cache framework.

Works with any Django cache (locmem, Redis via django-redis, memcached, ...),
which means we get a Redis-capable backend without taking a hard dependency
on a Redis client. A wholly Redis-specific backend can be added later if
needed.
"""
from __future__ import annotations

from typing import List

from django.core.cache import caches

from .base import BaseMemoryBackend, ConversationEvent


class DjangoCacheBackend(BaseMemoryBackend):
    def __init__(
        self,
        max_history_items: int = 10,
        alias: str = "default",
        key_prefix: str = "dgs:conv:",
        ttl: int = 86400,
        **options,
    ) -> None:
        super().__init__(max_history_items=max_history_items, **options)
        self.alias = alias
        self.key_prefix = key_prefix
        self.ttl = ttl

    @property
    def _cache(self):
        return caches[self.alias]

    def _key(self, session_id: str) -> str:
        return f"{self.key_prefix}{session_id}"

    def get_history(self, session_id: str) -> List[ConversationEvent]:
        payload = self._cache.get(self._key(session_id)) or []
        return [ConversationEvent.from_dict(p) for p in payload]

    def append_event(self, session_id: str, event: ConversationEvent) -> None:
        history = self.get_history(session_id)
        history.append(event)
        if len(history) > self.max_history_items:
            history = history[-self.max_history_items:]
        self._cache.set(
            self._key(session_id),
            [e.to_dict() for e in history],
            timeout=self.ttl,
        )

    def clear_history(self, session_id: str) -> None:
        self._cache.delete(self._key(session_id))
