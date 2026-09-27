"""Регрессионные тесты ревизии: утечка полей в индекс, 4xx вместо 500, batch-загрузка."""
from __future__ import annotations

import uuid
from typing import Any, Dict
from unittest import mock

import pytest
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from django_graph_search.backends.base import SearchResult
from django_graph_search.graph_resolver import GraphResolver
from django_graph_search.indexer import serialize_pk
from django_graph_search.langgraph_agent import vector_search_node
from django_graph_search.searcher import Searcher
from django_graph_search.settings import ModelConfig, clear_graph_search_caches
from django_graph_search.views import SearchAPIView, SimilarAPIView

from .dummy_embedding_backend import DummyEmbeddingBackend
from .dummy_vector_backend import DummyVectorBackend
from .test_app.models import Category, Product
from .utils import make_basic_config


def _graph_search_settings(models: list[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "MODELS": models,
        "VECTOR_STORE": {"BACKEND": "tests.dummy_vector_backend.DummyVectorBackend"},
        "EMBEDDINGS": {
            "default": {
                "BACKEND": "tests.dummy_embedding_backend.DummyEmbeddingBackend",
                "MODEL_NAME": "x",
            }
        },
    }


@pytest.fixture(name="apply_settings")
def _apply_settings_fixture():
    original = getattr(django_settings, "GRAPH_SEARCH", None)
    clear_graph_search_caches()

    def _apply(payload: Dict[str, Any]):
        django_settings.GRAPH_SEARCH = payload
        clear_graph_search_caches()

    yield _apply

    if original is None and hasattr(django_settings, "GRAPH_SEARCH"):
        delattr(django_settings, "GRAPH_SEARCH")
    elif original is not None:
        django_settings.GRAPH_SEARCH = original
    clear_graph_search_caches()


# ------------------------------------------------------------ GraphResolver


@pytest.mark.django_db
def test_follow_relations_does_not_leak_unlisted_root_fields():
    """fields=['username'] + follow_relations: password hash не попадает в текст."""
    user = get_user_model().objects.create_user("bob", "bob@example.com", "s3cret")
    resolver = GraphResolver()
    config = ModelConfig(
        model=user._meta.label,
        fields=["username"],
        follow_relations=True,
        relation_depth=2,
    )
    text = resolver.build_searchable_text(user, config)
    assert "bob" in text
    assert "pbkdf2" not in text
    assert user.password not in text
    assert "bob@example.com" not in text


@pytest.mark.django_db
def test_all_fields_skips_password():
    user = get_user_model().objects.create_user("alice", "alice@example.com", "s3cret")
    resolver = GraphResolver()
    config = ModelConfig(
        model=user._meta.label,
        fields=["__all__"],
        follow_relations=False,
        relation_depth=0,
    )
    text = resolver.build_searchable_text(user, config)
    assert "alice" in text
    assert user.password not in text


@pytest.mark.django_db
def test_weight_zero_respected_with_follow_relations():
    """weight=0 исключает поле даже при follow_relations=True (раньше оно возвращалось)."""
    category = Category.objects.create(name="Books")
    product = Product.objects.create(name="Dune", description="Sandworms", category=category)
    resolver = GraphResolver()
    config = ModelConfig(
        model="test_app.Product",
        fields=["name", "description"],
        follow_relations=True,
        relation_depth=2,
        weight_fields={"description": 0.0},
    )
    text = resolver.build_searchable_text(product, config)
    assert "Dune" in text
    assert "Books" in text
    assert "Sandworms" not in text


# ------------------------------------------------------------------- views


@pytest.mark.django_db
def test_similar_unknown_model_returns_404(apply_settings):
    apply_settings(_graph_search_settings([]))
    request = RequestFactory().get("/api/search/similar/nope.Missing/1/")
    response = SimilarAPIView.as_view()(request, model="nope.Missing", pk="1")
    assert response.status_code == 404


@pytest.mark.django_db
def test_similar_invalid_pk_returns_400(apply_settings):
    apply_settings(_graph_search_settings([]))
    request = RequestFactory().get("/api/search/similar/test_app.Product/abc/")
    response = SimilarAPIView.as_view()(request, model="test_app.Product", pk="abc")
    assert response.status_code == 400


@pytest.mark.django_db
def test_search_models_param_ignores_empty_items(apply_settings):
    apply_settings(_graph_search_settings([]))
    request = RequestFactory().get("/api/search/", {"q": "x", "models": "a.B,,c.D,"})
    with mock.patch("django_graph_search.views.Searcher") as sc:
        sc.return_value.search.return_value = []
        SearchAPIView.as_view()(request)
    _args, kwargs = sc.return_value.search.call_args
    assert kwargs["models"] == ["a.B", "c.D"]


# ---------------------------------------------------------------- searcher


class _StoreWithHits(DummyVectorBackend):
    def __init__(self, hits, **options):
        super().__init__(**options)
        self._hits = hits

    def search(self, query_vector, limit, filters=None):
        return self._hits[:limit]


@pytest.mark.django_db
def test_format_results_loads_objects_in_one_query_per_model():
    category = Category.objects.create(name="C")
    products = [
        Product.objects.create(name=f"P{i}", description="", category=category)
        for i in range(5)
    ]
    hits = [
        SearchResult(
            id=f"test_app.Product:{p.pk}",
            score=0.9,
            metadata={"model": "test_app.Product", "pk": p.pk, "text": p.name},
        )
        for p in products
    ]
    config = make_basic_config(
        delta_indexing=False,
        models=[ModelConfig(model="test_app.Product", fields=["name"])],
    )
    searcher = Searcher(
        config=config,
        vector_store=_StoreWithHits(hits),
        embedding_backend=DummyEmbeddingBackend("x"),
    )
    with CaptureQueriesContext(connection) as ctx:
        results = searcher.search("p", limit=10)
    assert len(results) == 5
    assert all("data" in r for r in results)
    assert len(ctx.captured_queries) == 1


@pytest.mark.django_db
def test_format_results_survives_stale_model_label():
    hits = [
        SearchResult(
            id="gone.Model:1",
            score=0.5,
            metadata={"model": "gone.Model", "pk": 1, "text": "stale"},
        )
    ]
    searcher = Searcher(
        config=make_basic_config(delta_indexing=False),
        vector_store=_StoreWithHits(hits),
        embedding_backend=DummyEmbeddingBackend("x"),
    )
    results = searcher.search("q", limit=5)
    assert results[0]["model"] == "gone.Model"
    assert "data" not in results[0]


# ----------------------------------------------------------- vector search


def test_vector_search_node_single_model_uses_store_filter():
    store = mock.Mock()
    store.search.return_value = []
    state: Dict[str, Any] = {"normalized_query": "q", "models": ["a.B"], "limit": 7}
    vector_search_node(state, embedding_backend=DummyEmbeddingBackend("x"), vector_store=store)
    _args, kwargs = store.search.call_args
    assert kwargs["filters"] == {"model": "a.B"}
    assert kwargs["limit"] == 7


def test_vector_search_node_multi_model_overfetches():
    store = mock.Mock()
    store.search.return_value = []
    state: Dict[str, Any] = {"normalized_query": "q", "models": ["a.B", "c.D"], "limit": 7}
    vector_search_node(state, embedding_backend=DummyEmbeddingBackend("x"), vector_store=store)
    _args, kwargs = store.search.call_args
    assert kwargs["filters"] is None
    assert kwargs["limit"] == 70


# ---------------------------------------------------------------- helpers


def test_serialize_pk_keeps_primitives_and_stringifies_uuid():
    pk = uuid.uuid4()
    assert serialize_pk(5) == 5
    assert serialize_pk("abc") == "abc"
    assert serialize_pk(pk) == str(pk)
