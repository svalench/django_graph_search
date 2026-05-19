"""Singleton тяжёлых компонентов на процесс."""
from __future__ import annotations

from django_graph_search.component_registry import (
    clear_component_registry,
    get_shared_components,
)
from django_graph_search.factory import build_components
from django_graph_search.indexer import get_indexer
from django_graph_search.settings import (
    CacheConfig,
    EmbeddingProfile,
    GraphSearchConfig,
    VectorStoreConfig,
)

from tests.dummy_embedding_backend import DummyEmbeddingBackend
from tests.dummy_vector_backend import DummyVectorBackend


def _minimal_config() -> GraphSearchConfig:
    return GraphSearchConfig(
        models=[],
        vector_store=VectorStoreConfig(
            backend="tests.dummy_vector_backend.DummyVectorBackend",
            options={},
        ),
        embeddings={
            "default": EmbeddingProfile(
                backend="tests.dummy_embedding_backend.DummyEmbeddingBackend",
                model_name="x",
            ),
        },
        default_embedding="default",
        api_url_prefix="api/search/",
        admin_search_enabled=False,
        auto_index=False,
        default_results_limit=10,
        delta_indexing=False,
        cache=CacheConfig(backend="file"),
    )


def test_get_shared_components_returns_same_instances():
    clear_component_registry()
    cfg = _minimal_config()
    _, vs1, emb1, res1 = get_shared_components(cfg)
    _, vs2, emb2, res2 = get_shared_components(cfg)
    assert vs1 is vs2
    assert emb1 is emb2
    assert res1 is res2


def test_build_components_uses_registry_when_all_none():
    clear_component_registry()
    cfg = _minimal_config()
    _, vs1, emb1, _ = build_components(cfg, None, None, None, None)
    _, vs2, emb2, _ = build_components(cfg, None, None, None, None)
    assert vs1 is vs2
    assert emb1 is emb2


def test_get_indexer_reuses_embedding_backend():
    clear_component_registry()
    cfg = _minimal_config()
    idx1 = get_indexer(config=cfg)
    idx2 = get_indexer(config=cfg)
    assert idx1.embedding_backend is idx2.embedding_backend
    assert idx1.vector_store is idx2.vector_store


def test_explicit_backends_bypass_registry():
    clear_component_registry()
    cfg = _minimal_config()
    vs = DummyVectorBackend()
    emb = DummyEmbeddingBackend(model_name="x")
    _, vs1, emb1, _ = build_components(cfg, vs, emb, None, None)
    _, vs2, emb2, _ = build_components(cfg, vs, emb, None, None)
    assert vs1 is vs
    assert emb1 is emb
    assert vs2 is vs
    assert emb2 is emb
