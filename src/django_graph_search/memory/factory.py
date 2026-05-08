"""Resolve a memory backend from settings."""
from __future__ import annotations

from typing import Any, Dict, Optional

from django.utils.module_loading import import_string

from ..exceptions import ConfigurationError
from .base import BaseMemoryBackend
from .in_memory import InMemoryBackend


_ALIASES = {
    "inmemory": "django_graph_search.memory.in_memory.InMemoryBackend",
    "memory": "django_graph_search.memory.in_memory.InMemoryBackend",
    "cache": "django_graph_search.memory.django_cache.DjangoCacheBackend",
    "django_cache": "django_graph_search.memory.django_cache.DjangoCacheBackend",
    "redis": "django_graph_search.memory.django_cache.DjangoCacheBackend",
}


def build_memory_backend(
    backend: Optional[str],
    *,
    max_history_items: int = 10,
    options: Optional[Dict[str, Any]] = None,
) -> BaseMemoryBackend:
    """Instantiate a memory backend from a short alias or dotted path.

    Defaults to :class:`InMemoryBackend` when ``backend`` is ``None``.
    """
    options = dict(options or {})
    if not backend:
        return InMemoryBackend(max_history_items=max_history_items, **options)
    path = _ALIASES.get(backend, backend)
    try:
        cls = import_string(path)
    except ImportError as exc:
        raise ConfigurationError(
            f"Cannot import memory backend '{backend}': {exc}"
        ) from exc
    instance = cls(max_history_items=max_history_items, **options)
    if not isinstance(instance, BaseMemoryBackend):
        raise ConfigurationError(
            f"Memory backend '{backend}' must subclass BaseMemoryBackend."
        )
    return instance
