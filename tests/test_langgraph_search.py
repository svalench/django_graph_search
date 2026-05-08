"""Tests for the optional LangGraph search pipeline.

These tests deliberately avoid pulling in the real ``langgraph`` package so
they exercise the in-tree fallback runner. They also verify that switching
``LANGGRAPH.ENABLED`` does not change observable behaviour for the simple
single-query case (Sprint 1 backwards-compat guarantee).
"""
# pylint: disable=redefined-outer-name
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence
from unittest import mock

import pytest

from django.conf import settings as django_settings

from django_graph_search.langgraph_agent import (
    SearchState,
    analyze_query_node,
    expand_query_node,
    postprocess_results_node,
    rerank_results_node,
    vector_search_node,
)
from django_graph_search.llm import DummyLLMBackend
from django_graph_search.llm.base import BaseLLMBackend, RerankCandidate
from django_graph_search.searcher import Searcher
from django_graph_search.settings import (
    CacheConfig,
    EmbeddingProfile,
    GraphSearchConfig,
    LangGraphConfig,
    LLMConfig,
    VectorStoreConfig,
    get_settings,
)


@pytest.fixture
def graph_search_settings():
    """Set GRAPH_SEARCH on Django settings and clear the lru_cache.

    The library reads its configuration via ``get_settings`` which is
    ``lru_cache``-d. Tests that mutate ``GRAPH_SEARCH`` must reset the cache
    before and after to stay isolated.
    """
    original = getattr(django_settings, "GRAPH_SEARCH", None)
    get_settings.cache_clear()

    def _apply(payload):
        django_settings.GRAPH_SEARCH = payload
        get_settings.cache_clear()
        return get_settings()

    yield _apply

    if original is None:
        if hasattr(django_settings, "GRAPH_SEARCH"):
            delattr(django_settings, "GRAPH_SEARCH")
    else:
        django_settings.GRAPH_SEARCH = original
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeHit:
    """Stand-in for ``backends.base.SearchResult`` with optional text."""
    id: str
    score: float
    metadata: Dict[str, Any]
    text: str = ""


class StubVectorStore:
    """Vector store that returns canned hits depending on the query vector."""

    def __init__(self, by_query: Dict[str, List[FakeHit]]):
        self.by_query = by_query
        self.calls: List[List[float]] = []

    def search(self, query_vector, limit, filters=None):
        self.calls.append(list(query_vector))
        # The stub embedding backend below encodes the query as a 1-D vector
        # whose value is the index in the lookup map.
        idx = int(query_vector[0]) if query_vector else 0
        keys = list(self.by_query.keys())
        if 0 <= idx < len(keys):
            return list(self.by_query[keys[idx]])[:limit]
        return []


class StubEmbeddingBackend:
    """Maps each unique query string to a stable index, returned as a vector."""

    def __init__(self, queries: List[str]):
        self.index = {q: i for i, q in enumerate(queries)}

    def embed(self, text: str):
        return [self.index.get(text, 0)]

    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


def _make_config(*, langgraph: LangGraphConfig) -> GraphSearchConfig:
    return GraphSearchConfig(
        models=[],
        vector_store=VectorStoreConfig(backend="x", options={}),
        embeddings={"default": EmbeddingProfile(backend="x", model_name="x")},
        default_embedding="default",
        api_url_prefix="api/search/",
        admin_search_enabled=False,
        auto_index=False,
        default_results_limit=10,
        delta_indexing=False,
        cache=CacheConfig(backend="file"),
        langgraph=langgraph,
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_settings_defaults_have_disabled_langgraph(graph_search_settings):
    cfg = graph_search_settings({"MODELS": []})
    assert cfg.langgraph.enabled is False
    assert cfg.langgraph.query_expansion is False
    assert cfg.langgraph.reranking is False
    assert cfg.langgraph.max_expanded_queries == 3


def test_settings_validate_negative_values(graph_search_settings):
    with pytest.raises(Exception):
        graph_search_settings({
            "MODELS": [],
            "LANGGRAPH": {"MAX_EXPANDED_QUERIES": 0},
        })


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def test_analyze_query_node_truncates_to_max_length():
    cfg = _make_config(langgraph=LangGraphConfig(enabled=True, max_query_length=5))
    state: SearchState = {"query": "  hello world  "}
    out = analyze_query_node(state, config=cfg)
    assert out["normalized_query"] == "hello"  # 5 chars after strip+truncate.
    assert out["debug"]["normalized_length"] == 5


def test_dummy_backend_expands_to_at_most_n_variants():
    backend = DummyLLMBackend()
    out = backend.expand_query("Hello, World!", max_variants=2)
    assert out[0] == "Hello, World!"
    assert len(out) <= 2


def test_expand_query_node_falls_back_on_llm_failure():
    cfg = _make_config(langgraph=LangGraphConfig(query_expansion=True, max_expanded_queries=3))

    class BoomLLM(BaseLLMBackend):
        def expand_query(self, *a, **kw):
            raise RuntimeError("boom")

        def rerank(self, *a, **kw):
            return []

    state: SearchState = {"normalized_query": "phone"}
    out = expand_query_node(state, config=cfg, llm=BoomLLM())
    assert out["expanded_queries"] == ["phone"]
    assert any("expand_query" in e for e in out.get("errors", []))


def test_vector_search_node_merges_and_dedupes():
    embed = StubEmbeddingBackend(["a", "b"])
    store = StubVectorStore({
        "a": [
            FakeHit("test_app.Product:1", 0.4, {"model": "test_app.Product", "pk": 1}),
            FakeHit("test_app.Product:2", 0.2, {"model": "test_app.Product", "pk": 2}),
        ],
        "b": [
            FakeHit("test_app.Product:1", 0.9, {"model": "test_app.Product", "pk": 1}),
            FakeHit("test_app.Product:3", 0.1, {"model": "test_app.Product", "pk": 3}),
        ],
    })
    state: SearchState = {
        "expanded_queries": ["a", "b"],
        "limit": 10,
        "models": None,
    }
    out = vector_search_node(state, embedding_backend=embed, vector_store=store)
    ids = [hit.id for hit in out["raw_results"]]
    # Best score per id wins; ordering is by score desc.
    assert ids[0] == "test_app.Product:1"
    assert {hit.id for hit in out["raw_results"]} == {
        "test_app.Product:1",
        "test_app.Product:2",
        "test_app.Product:3",
    }
    assert pytest.approx([h.score for h in out["raw_results"]][0]) == 0.9


def test_vector_search_node_filters_by_models():
    embed = StubEmbeddingBackend(["q"])
    store = StubVectorStore({
        "q": [
            FakeHit("test_app.Product:1", 0.5, {"model": "test_app.Product", "pk": 1}),
            FakeHit("test_app.Tag:1", 0.6, {"model": "test_app.Tag", "pk": 1}),
        ],
    })
    state: SearchState = {
        "expanded_queries": ["q"],
        "limit": 5,
        "models": ["test_app.Product"],
    }
    out = vector_search_node(state, embedding_backend=embed, vector_store=store)
    assert {hit.metadata["model"] for hit in out["raw_results"]} == {"test_app.Product"}


def test_rerank_node_uses_dummy_backend_and_keeps_tail():
    cfg = _make_config(langgraph=LangGraphConfig(reranking=True, rerank_top_k=2))
    candidates = [
        FakeHit("a", 0.1, {"model": "m", "pk": 1}, text="alpha"),
        FakeHit("b", 0.9, {"model": "m", "pk": 2}, text="beta"),
        FakeHit("c", 0.5, {"model": "m", "pk": 3}, text="gamma"),
    ]
    state: SearchState = {
        "merged_results": candidates,
        "normalized_query": "x",
    }
    out = rerank_results_node(state, config=cfg, llm=DummyLLMBackend())
    # Top-2 reordered by score desc, tail untouched.
    assert [c.id for c in out["reranked_results"][:2]] == ["b", "a"]
    assert out["reranked_results"][2].id == "c"


def test_postprocess_node_applies_limit():
    state: SearchState = {
        "merged_results": [FakeHit(str(i), float(i), {"model": "m", "pk": i}) for i in range(5)],
        "limit": 2,
    }
    out = postprocess_results_node(state)
    assert len(out["final_results"]) == 2


# ---------------------------------------------------------------------------
# Searcher integration (uses the in-tree fallback graph runner)
# ---------------------------------------------------------------------------


def _make_searcher_settings(extra=None):
    payload = {
        "MODELS": [],
        "VECTOR_STORE": {"BACKEND": "django_graph_search.backends.ChromaDBBackend"},
        "EMBEDDINGS": {
            "default": {
                "BACKEND": "tests.dummy_embedding_backend.DummyEmbeddingBackend",
                "MODEL_NAME": "x",
            }
        },
    }
    if extra:
        payload.update(extra)
    return payload


@pytest.mark.django_db
def test_searcher_disabled_uses_legacy_path(graph_search_settings):
    graph_search_settings(_make_searcher_settings())
    embed = StubEmbeddingBackend(["foo"])
    store = StubVectorStore({"foo": [
        FakeHit("test_app.Product:1", 0.5, {"model": "test_app.Product", "pk": 1}),
    ]})
    searcher = Searcher(vector_store=store, embedding_backend=embed)
    results = searcher.search("foo")
    assert results and results[0]["model"] == "test_app.Product"


@pytest.mark.django_db
def test_searcher_enabled_returns_same_shape(graph_search_settings):
    graph_search_settings(_make_searcher_settings({"LANGGRAPH": {"ENABLED": True}}))
    embed = StubEmbeddingBackend(["foo"])
    store = StubVectorStore({"foo": [
        FakeHit("test_app.Product:1", 0.5, {"model": "test_app.Product", "pk": 1}),
    ]})
    searcher = Searcher(vector_store=store, embedding_backend=embed)
    results = searcher.search("foo", limit=5)
    assert results
    assert set(results[0].keys()) >= {"model", "pk", "score"}


@pytest.mark.django_db
def test_searcher_enabled_with_expansion_runs_multi_query(graph_search_settings):
    graph_search_settings(_make_searcher_settings({
        "LANGGRAPH": {
            "ENABLED": True,
            "QUERY_EXPANSION": True,
            "MAX_EXPANDED_QUERIES": 2,
        },
    }))
    embed = StubEmbeddingBackend(["Hello", "hello"])
    store = StubVectorStore({
        "Hello": [FakeHit("test_app.Product:1", 0.5, {"model": "test_app.Product", "pk": 1})],
        "hello": [FakeHit("test_app.Product:2", 0.7, {"model": "test_app.Product", "pk": 2})],
    })
    searcher = Searcher(vector_store=store, embedding_backend=embed)
    results = searcher.search("Hello", limit=5)
    assert {(r["model"], r["pk"]) for r in results} >= {
        ("test_app.Product", 1),
        ("test_app.Product", 2),
    }


@pytest.mark.django_db
def test_searcher_enabled_falls_back_when_graph_factory_raises(graph_search_settings):
    graph_search_settings(_make_searcher_settings({
        "LANGGRAPH": {
            "ENABLED": True,
            "SEARCH_GRAPH": "tests.test_langgraph_search.boom_graph_factory",
            "FALLBACK_ON_ERROR": True,
        },
    }))
    embed = StubEmbeddingBackend(["foo"])
    store = StubVectorStore({"foo": [
        FakeHit("test_app.Product:1", 0.5, {"model": "test_app.Product", "pk": 1}),
    ]})
    searcher = Searcher(vector_store=store, embedding_backend=embed)
    results = searcher.search("foo")
    assert results


def boom_graph_factory(*args, **kwargs):
    raise RuntimeError("boom")
