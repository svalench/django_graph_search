"""Пропуск AUTO_INDEX при save(update_fields=...) с «шумными» полями."""
from __future__ import annotations

from typing import Any, Dict
from unittest import mock

import pytest
from django.conf import settings as django_settings

from django_graph_search.settings import clear_graph_search_caches

from .test_app.models import Category, Product


@pytest.fixture(name="graph_search_skip_settings")
def _graph_search_skip_settings_fixture():
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


@pytest.mark.django_db
def test_skip_index_when_only_skip_update_fields_touched(graph_search_skip_settings):
    graph_search_skip_settings(
        {
            "MODELS": [
                {
                    "model": "test_app.Product",
                    "fields": ["name"],
                    "skip_update_fields": ["category"],
                }
            ],
            "VECTOR_STORE": {"BACKEND": "tests.dummy_vector_backend.DummyVectorBackend"},
            "EMBEDDINGS": {
                "default": {
                    "BACKEND": "tests.dummy_embedding_backend.DummyEmbeddingBackend",
                    "MODEL_NAME": "x",
                }
            },
            "AUTO_INDEX": True,
        }
    )
    cat1 = Category.objects.create(name="c1")
    cat2 = Category.objects.create(name="c2")
    product = Product.objects.create(name="widget", category=cat1)

    with mock.patch("django_graph_search.indexer.Indexer._index_batch") as index_batch:
        product.category = cat2
        product.save(update_fields=["category"])
        assert index_batch.call_count == 0

        product.name = "gadget"
        product.save(update_fields=["name"])
        assert index_batch.call_count == 1


@pytest.mark.django_db
def test_global_auto_index_skip_update_fields(graph_search_skip_settings):
    graph_search_skip_settings(
        {
            "MODELS": [{"model": "test_app.Product", "fields": ["name"]}],
            "VECTOR_STORE": {"BACKEND": "tests.dummy_vector_backend.DummyVectorBackend"},
            "EMBEDDINGS": {
                "default": {
                    "BACKEND": "tests.dummy_embedding_backend.DummyEmbeddingBackend",
                    "MODEL_NAME": "x",
                }
            },
            "AUTO_INDEX": True,
            "AUTO_INDEX_SKIP_UPDATE_FIELDS": ["name"],
        }
    )
    cat = Category.objects.create(name="c")
    product = Product.objects.create(name="a", category=cat)

    with mock.patch("django_graph_search.indexer.Indexer._index_batch") as index_batch:
        product.name = "b"
        product.save(update_fields=["name"])
        assert index_batch.call_count == 0
