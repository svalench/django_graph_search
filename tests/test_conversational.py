"""Предупреждения conversational API в production-режиме."""
from __future__ import annotations

import json
import warnings
from typing import Any, Dict
from unittest import mock

import pytest
from django.conf import settings as django_settings
from django.test import RequestFactory

from django_graph_search.settings import get_settings
from django_graph_search.views import ConversationalSearchAPIView, _memory_backend_registry


def _minimal_graph_search(extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "MODELS": [],
        "VECTOR_STORE": {"BACKEND": "django_graph_search.backends.ChromaDBBackend"},
        "EMBEDDINGS": {
            "default": {
                "BACKEND": "tests.dummy_embedding_backend.DummyEmbeddingBackend",
                "MODEL_NAME": "x",
            }
        },
        "CONVERSATIONAL": {
            "ENABLED": True,
            "MEMORY_BACKEND": "inmemory",
        },
    }
    if extra:
        base.update(extra)
    return base


@pytest.fixture
def conv_settings():
    original = getattr(django_settings, "GRAPH_SEARCH", None)
    original_debug = django_settings.DEBUG
    get_settings.cache_clear()
    _memory_backend_registry.clear()

    def _apply(payload: Dict[str, Any], *, debug: bool):
        django_settings.GRAPH_SEARCH = payload
        django_settings.DEBUG = debug
        get_settings.cache_clear()

    yield _apply

    django_settings.DEBUG = original_debug
    if original is None and hasattr(django_settings, "GRAPH_SEARCH"):
        delattr(django_settings, "GRAPH_SEARCH")
    elif original is not None:
        django_settings.GRAPH_SEARCH = original
    get_settings.cache_clear()
    _memory_backend_registry.clear()


@pytest.mark.django_db
def test_inmemory_emits_runtime_warning_when_not_debug(conv_settings):
    conv_settings(_minimal_graph_search(), debug=False)
    factory = RequestFactory()
    request = factory.post(
        "/api/search/conversation/",
        data=json.dumps({"query": "hello world"}),
        content_type="application/json",
    )
    with warnings.catch_warnings(record=True) as wrec:
        warnings.simplefilter("always")
        with mock.patch("django_graph_search.views.Searcher") as sc:
            sc.return_value.search.return_value = []
            ConversationalSearchAPIView.as_view()(request)
    assert any(
        issubclass(w.category, RuntimeWarning) and "inmemory" in str(w.message).lower()
        for w in wrec
    )


@pytest.mark.django_db
def test_inmemory_no_warning_in_debug(conv_settings):
    conv_settings(_minimal_graph_search(), debug=True)
    factory = RequestFactory()
    request = factory.post(
        "/api/search/conversation/",
        data=json.dumps({"query": "hello world"}),
        content_type="application/json",
    )
    with warnings.catch_warnings(record=True) as wrec:
        warnings.simplefilter("always")
        with mock.patch("django_graph_search.views.Searcher") as sc:
            sc.return_value.search.return_value = []
            ConversationalSearchAPIView.as_view()(request)
    runtime = [w for w in wrec if issubclass(w.category, RuntimeWarning) and "inmemory" in str(w.message)]
    assert not runtime
