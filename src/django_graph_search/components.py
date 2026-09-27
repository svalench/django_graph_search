from __future__ import annotations

# pylint: disable=duplicate-code

from typing import Optional

from .backends.base import BaseVectorStore
from .embeddings.base import BaseEmbeddingBackend
from .factory import build_components
from .graph_resolver import GraphResolver
from .settings import GraphSearchConfig


class ComponentMixin:
    config: GraphSearchConfig
    vector_store: BaseVectorStore
    embedding_backend: BaseEmbeddingBackend
    resolver: GraphResolver

    def _init_components(
        self,
        *,
        config: Optional[GraphSearchConfig],
        vector_store: Optional[BaseVectorStore],
        embedding_backend: Optional[BaseEmbeddingBackend],
        resolver: Optional[GraphResolver],
        embedding_profile: Optional[str],
    ) -> None:
        (
            self.config,
            self.vector_store,
            self.embedding_backend,
            self.resolver,
        ) = build_components(
            config=config,
            vector_store=vector_store,
            embedding_backend=embedding_backend,
            resolver=resolver,
            embedding_profile=embedding_profile,
        )

