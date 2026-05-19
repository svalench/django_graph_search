from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from django.utils.module_loading import import_string

if TYPE_CHECKING:
    from .settings import GraphSearchConfig

# Один vector store + embedding + resolver на процесс (как memory backend в views).
_registry_lock = threading.Lock()
_component_registry: Dict[Tuple[Any, ...], Tuple[Any, Any, Any]] = {}


def _freeze_options(options: Dict[str, Any]) -> str:
    return json.dumps(options or {}, sort_keys=True, default=str)


def _component_cache_key(
    config: "GraphSearchConfig",
    embedding_profile: Optional[str],
) -> Tuple[Any, ...]:
    profile_name = embedding_profile or config.default_embedding
    profile = config.embeddings[profile_name]
    return (
        config.vector_store.backend,
        _freeze_options(config.vector_store.options),
        profile_name,
        profile.backend,
        profile.model_name,
        _freeze_options(profile.options),
    )


def get_shared_components(
    config: Optional["GraphSearchConfig"] = None,
    embedding_profile: Optional[str] = None,
) -> Tuple["GraphSearchConfig", object, object, Any]:
    """Тяжёлые компоненты поиска/индексации — singleton на воркер."""
    from .graph_resolver import GraphResolver
    from .settings import get_settings

    config = config or get_settings()
    key = _component_cache_key(config, embedding_profile)
    with _registry_lock:
        cached = _component_registry.get(key)
        if cached is not None:
            vector_store, embedding_backend, resolver = cached
            return config, vector_store, embedding_backend, resolver

    backend_cls = import_string(config.vector_store.backend)
    vector_store = backend_cls(**config.vector_store.options)
    profile_name = embedding_profile or config.default_embedding
    profile = config.embeddings[profile_name]
    embed_cls = import_string(profile.backend)
    embedding_backend = embed_cls(
        model_name=profile.model_name,
        **profile.options,
    )
    resolver = GraphResolver()
    entry = (vector_store, embedding_backend, resolver)
    with _registry_lock:
        _component_registry[key] = entry
    return config, vector_store, embedding_backend, resolver


def clear_component_registry() -> None:
    with _registry_lock:
        _component_registry.clear()
