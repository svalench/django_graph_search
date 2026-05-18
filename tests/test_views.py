"""Тесты view: лимиты, 403/429 по API-настройкам."""
from __future__ import annotations

import json
from typing import Any, Dict
from unittest import mock

import pytest
from django.conf import settings as django_settings
from django.test import RequestFactory

from django_graph_search.settings import get_settings
from django_graph_search.views import SearchAPIView, StreamingSearchAPIView


def _minimal_graph_search(extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "MODELS": [],
        "VECTOR_STORE": {"BACKEND": "tests.dummy_vector_backend.DummyVectorBackend"},
        "EMBEDDINGS": {
            "default": {
                "BACKEND": "tests.dummy_embedding_backend.DummyEmbeddingBackend",
                "MODEL_NAME": "x",
            }
        },
    }
    if extra:
        base.update(extra)
    return base


@pytest.fixture(name="apply_view_settings")
def _apply_view_settings_fixture():
    original = getattr(django_settings, "GRAPH_SEARCH", None)
    get_settings.cache_clear()

    def _apply(payload: Dict[str, Any]):
        django_settings.GRAPH_SEARCH = payload
        get_settings.cache_clear()

    yield _apply

    if original is None and hasattr(django_settings, "GRAPH_SEARCH"):
        delattr(django_settings, "GRAPH_SEARCH")
    elif original is not None:
        django_settings.GRAPH_SEARCH = original
    get_settings.cache_clear()


@pytest.mark.django_db
def test_search_get_bad_limit_returns_400(apply_view_settings):
    apply_view_settings(_minimal_graph_search())
    factory = RequestFactory()
    request = factory.get("/api/search/", {"q": "x", "limit": "abc"})
    with mock.patch("django_graph_search.views.Searcher") as sc:
        sc.return_value.search.return_value = []
        response = SearchAPIView.as_view()(request)
    assert response.status_code == 400


@pytest.mark.django_db
def test_search_get_negative_limit_returns_400(apply_view_settings):
    apply_view_settings(_minimal_graph_search())
    factory = RequestFactory()
    request = factory.get("/api/search/", {"q": "x", "limit": "-3"})
    response = SearchAPIView.as_view()(request)
    assert response.status_code == 400


@pytest.mark.django_db
def test_search_get_min_score_filters_results(apply_view_settings):
    apply_view_settings(_minimal_graph_search())
    factory = RequestFactory()
    request = factory.get("/api/search/", {"q": "x", "min_score": "0.75"})
    with mock.patch("django_graph_search.views.Searcher") as sc:
        sc.return_value.search.return_value = [
            {"model": "test_app.Product", "pk": 1, "score": 0.9, "text": "a"},
            {"model": "test_app.Product", "pk": 2, "score": 0.5, "text": "b"},
        ]
        response = SearchAPIView.as_view()(request)
    assert response.status_code == 200
    body = json.loads(response.content.decode())
    assert body["total"] == 1
    assert body["results"][0]["pk"] == 1
    assert body["min_score_applied"] == 0.75


@pytest.mark.django_db
def test_search_get_min_score_out_of_range_returns_400(apply_view_settings):
    apply_view_settings(_minimal_graph_search())
    factory = RequestFactory()
    request = factory.get("/api/search/", {"q": "x", "min_score": "1.5"})
    response = SearchAPIView.as_view()(request)
    assert response.status_code == 400


@pytest.mark.django_db
def test_search_get_without_min_score_omits_applied_field(apply_view_settings):
    apply_view_settings(_minimal_graph_search())
    factory = RequestFactory()
    request = factory.get("/api/search/", {"q": "x"})
    with mock.patch("django_graph_search.views.Searcher") as sc:
        sc.return_value.search.return_value = [
            {"model": "test_app.Product", "pk": 1, "score": 0.2, "text": ""},
        ]
        response = SearchAPIView.as_view()(request)
    assert response.status_code == 200
    body = json.loads(response.content.decode())
    assert "min_score_applied" not in body


@pytest.mark.django_db
def test_streaming_returns_429_when_throttled(apply_view_settings):
    from django_graph_search.permissions import SimpleScopedRateThrottle

    SimpleScopedRateThrottle._windows.clear()
    apply_view_settings(
        _minimal_graph_search(
            {
                "STREAMING": {"ENABLED": True},
                "API": {
                    "THROTTLE_CLASSES": [
                        "django_graph_search.permissions.SimpleScopedRateThrottle",
                    ],
                    "THROTTLE_RATES": {"search": "1/minute"},
                },
            }
        )
    )
    factory = RequestFactory()
    req1 = factory.get("/api/search/stream/", {"q": "a"}, REMOTE_ADDR="192.168.1.50")
    req2 = factory.get("/api/search/stream/", {"q": "b"}, REMOTE_ADDR="192.168.1.50")
    with mock.patch("django_graph_search.views.Searcher") as sc:
        sc.return_value.search.return_value = []
        r1 = StreamingSearchAPIView.as_view()(req1)
        r2 = StreamingSearchAPIView.as_view()(req2)
    assert r1.status_code == 200
    assert r2.status_code == 429
    body = json.loads(r2.content.decode())
    assert "error" in body
    assert r2["Retry-After"]
    SimpleScopedRateThrottle._windows.clear()


@pytest.mark.django_db
def test_conversational_bad_limit_json_returns_400(apply_view_settings):
    from django_graph_search.views import ConversationalSearchAPIView

    apply_view_settings(
        _minimal_graph_search({"CONVERSATIONAL": {"ENABLED": True}}),
    )
    factory = RequestFactory()
    request = factory.post(
        "/api/search/conversation/",
        data=json.dumps({"query": "hello", "limit": "nope"}),
        content_type="application/json",
    )
    response = ConversationalSearchAPIView.as_view()(request)
    assert response.status_code == 400
