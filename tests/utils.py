from typing import List

from django_graph_search.settings import (
    CacheConfig,
    EmbeddingProfile,
    GraphSearchConfig,
    ModelConfig,
    VectorStoreConfig,
)


def make_basic_config(
    *,
    delta_indexing: bool,
    models: List[ModelConfig] | None = None,
    embedding_backend: str = "dummy",
    embedding_model: str = "dummy",
) -> GraphSearchConfig:
    return GraphSearchConfig(
        models=models or [],
        vector_store=VectorStoreConfig(backend="dummy"),
        embeddings={
            "default": EmbeddingProfile(
                backend=embedding_backend,
                model_name=embedding_model,
            ),
        },
        default_embedding="default",
        api_url_prefix="api/search/",
        admin_search_enabled=True,
        auto_index=True,
        default_results_limit=10,
        delta_indexing=delta_indexing,
        cache=CacheConfig(backend="file"),
    )


def with_embeddings(
    config: GraphSearchConfig,
    embeddings: dict,
    default_embedding: str = "default",
) -> GraphSearchConfig:
    return GraphSearchConfig(
        models=config.models,
        vector_store=config.vector_store,
        embeddings=embeddings,
        default_embedding=default_embedding,
        api_url_prefix=config.api_url_prefix,
        admin_search_enabled=config.admin_search_enabled,
        auto_index=config.auto_index,
        default_results_limit=config.default_results_limit,
        delta_indexing=config.delta_indexing,
        cache=config.cache,
    )

