"""Тесты проверок доступа и throttling (GRAPH_SEARCH.API)."""
from __future__ import annotations

import json
from typing import Any, Dict

import pytest
from django.conf import settings as django_settings
from django.test import RequestFactory

from django_graph_search.permissions import (
    PermissionDenied,
    SimpleScopedRateThrottle,
    ThrottledError,
    check_permissions,
    check_throttle,
)
from django_graph_search.settings import GraphSearchConfig, get_settings


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
    }
    if extra:
        base.update(extra)
    return base


@pytest.fixture(name="apply_api_settings")
def _apply_api_settings_fixture():
    original = getattr(django_settings, "GRAPH_SEARCH", None)
    get_settings.cache_clear()
    SimpleScopedRateThrottle._windows.clear()

    def _apply(payload: Dict[str, Any]) -> GraphSearchConfig:
        django_settings.GRAPH_SEARCH = payload
        get_settings.cache_clear()
        return get_settings()

    yield _apply

    if original is None and hasattr(django_settings, "GRAPH_SEARCH"):
        delattr(django_settings, "GRAPH_SEARCH")
    elif original is not None:
        django_settings.GRAPH_SEARCH = original
    get_settings.cache_clear()
    SimpleScopedRateThrottle._windows.clear()


def deny_permission(request):
    """Тестовый callable: всегда запрещает."""
    return False


def allow_permission(request):
    """Тестовый callable: всегда разрешает."""
    return True


def test_check_permissions_empty_allows(apply_api_settings):
    cfg = apply_api_settings(_minimal_graph_search())
    factory = RequestFactory()
    request = factory.get("/api/search/", {"q": "x"})
    check_permissions(request, cfg)


def test_check_permissions_callable_deny(apply_api_settings):
    cfg = apply_api_settings(
        _minimal_graph_search(
            {"API": {"PERMISSION_CLASSES": ["tests.test_permissions.deny_permission"]}}
        )
    )
    factory = RequestFactory()
    request = factory.get("/api/search/", {"q": "x"})
    with pytest.raises(PermissionDenied):
        check_permissions(request, cfg)


def test_check_permissions_callable_allow(apply_api_settings):
    cfg = apply_api_settings(
        _minimal_graph_search(
            {"API": {"PERMISSION_CLASSES": ["tests.test_permissions.allow_permission"]}}
        )
    )
    factory = RequestFactory()
    request = factory.get("/api/search/", {"q": "x"})
    check_permissions(request, cfg)


def test_check_throttle_skipped_when_no_classes(apply_api_settings):
    cfg = apply_api_settings(_minimal_graph_search())
    factory = RequestFactory()
    request = factory.get("/api/search/", {"q": "x"})
    check_throttle(request, cfg)


def test_simple_scoped_throttle_blocks_second_request(apply_api_settings):
    cfg = apply_api_settings(
        _minimal_graph_search(
            {
                "API": {
                    "THROTTLE_CLASSES": [
                        "django_graph_search.permissions.SimpleScopedRateThrottle",
                    ],
                    "THROTTLE_RATES": {"search": "1/minute"},
                }
            }
        )
    )
    factory = RequestFactory()
    request1 = factory.get("/api/search/", {"q": "a"}, REMOTE_ADDR="10.0.0.1")
    request2 = factory.get("/api/search/", {"q": "b"}, REMOTE_ADDR="10.0.0.1")
    check_throttle(request1, cfg)
    with pytest.raises(ThrottledError):
        check_throttle(request2, cfg)


@pytest.mark.django_db
def test_search_api_view_returns_403_when_permission_denied(apply_api_settings):
    from django_graph_search.views import SearchAPIView

    apply_api_settings(
        _minimal_graph_search(
            {"API": {"PERMISSION_CLASSES": ["tests.test_permissions.deny_permission"]}}
        )
    )
    factory = RequestFactory()
    request = factory.get("/api/search/", {"q": "hello"})
    response = SearchAPIView.as_view()(request)
    assert response.status_code == 403
    body = json.loads(response.content.decode())
    assert "error" in body
