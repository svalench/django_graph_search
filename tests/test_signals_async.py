"""Асинхронная индексация по сигналам (thread backend)."""
from __future__ import annotations

import time
from typing import Any, Dict
from unittest import mock

import pytest
from django.conf import settings as django_settings

from django_graph_search.settings import clear_graph_search_caches, get_settings

from .test_app.models import Category, Product


@pytest.fixture(name="graph_search_signal_settings")
def _graph_search_signal_settings_fixture():
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
def test_thread_async_index_does_not_block_request(graph_search_signal_settings):
    """При ASYNC_INDEXING + thread обработчик сигнала возвращается до долгой работы."""
    graph_search_signal_settings(
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
            "ASYNC_INDEXING": {"ENABLED": True, "BACKEND": "thread"},
        }
    )

    def slow_index(*_a, **_kw):
        time.sleep(1.2)

    cat = Category.objects.create(name="c")
    with mock.patch("django_graph_search.tasks.index_instance_task_fn", side_effect=slow_index):
        t0 = time.monotonic()
        Product.objects.create(name="fast", category=cat)
        elapsed = time.monotonic() - t0
    assert elapsed < 0.35
