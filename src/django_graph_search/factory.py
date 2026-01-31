from __future__ import annotations

from typing import Optional, Tuple

from django.utils.module_loading import import_string

from .graph_resolver import GraphResolver
from .settings import GraphSearchConfig, get_settings


def build_components(
    config: Optional[GraphSearchConfig],
    vector_store,
    embedding_backend,
    resolver: Optional[GraphResolver],
    embedding_profile: Optional[str],
) -> Tuple[GraphSearchConfig, object, object, GraphResolver]:
    config = config or get_settings()
    if vector_store is None:
        backend_cls = import_string(config.vector_store.backend)
        vector_store = backend_cls(**config.vector_store.options)
    if embedding_backend is None:
        profile_name = embedding_profile or config.default_embedding
        profile = config.embeddings[profile_name]
        embed_cls = import_string(profile.backend)
        embedding_backend = embed_cls(
            model_name=profile.model_name,
            **profile.options,
        )
    resolver = resolver or GraphResolver()
    return config, vector_store, embedding_backend, resolver

