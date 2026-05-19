"""Раздел django_graph_search в сайдбаре Django Admin."""
from __future__ import annotations

from types import ModuleType
from typing import Any, Dict

import pytest
from django.conf import settings as django_settings
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import Client, override_settings

from django_graph_search.admin import setup_admin_site
from django_graph_search.models import GraphSearch, GraphSearchIndexStatus
from django_graph_search.settings import clear_graph_search_caches, get_settings


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


@pytest.fixture(name="apply_admin_graph_search_settings")
def _apply_admin_graph_search_settings_fixture():
    original = getattr(django_settings, "GRAPH_SEARCH", None)
    clear_graph_search_caches()

    def _apply(payload: Dict[str, Any]):
        django_settings.GRAPH_SEARCH = payload
        clear_graph_search_caches()
        return get_settings()

    yield _apply

    if original is None and hasattr(django_settings, "GRAPH_SEARCH"):
        delattr(django_settings, "GRAPH_SEARCH")
    elif original is not None:
        django_settings.GRAPH_SEARCH = original
    clear_graph_search_caches()


@pytest.fixture
def staff_client(db):
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="admin_sidebar",
        password="secret",
        is_staff=True,
        is_superuser=True,
    )
    client = Client()
    client.force_login(user)
    return client


@override_settings(ROOT_URLCONF="tests.urls_admin")
def test_admin_index_shows_graph_search_section(staff_client, apply_admin_graph_search_settings):
    apply_admin_graph_search_settings(_minimal_graph_search({"ADMIN_SEARCH_ENABLED": True}))
    setup_admin_site()

    response = staff_client.get("/admin/")
    assert response.status_code == 200
    content = response.content.decode()
    assert "Django Graph Search" in content
    assert "Поиск" in content
    assert "Статус индексации" in content
    assert "django_graph_search/graphsearch/" in content
    assert "django_graph_search/graphsearchindexstatus/" in content


@override_settings(ROOT_URLCONF="tests.urls_admin")
def test_graph_search_legacy_url_works(staff_client, apply_admin_graph_search_settings):
    apply_admin_graph_search_settings(_minimal_graph_search({"ADMIN_SEARCH_ENABLED": True}))
    setup_admin_site()

    response = staff_client.get("/admin/graph-search/")
    assert response.status_code == 200
    assert "Graph Search" in response.content.decode()


def test_setup_skips_when_admin_search_disabled(apply_admin_graph_search_settings):
    apply_admin_graph_search_settings(_minimal_graph_search({"ADMIN_SEARCH_ENABLED": False}))
    site = AdminSite(name="disabled_admin_test")

    setup_admin_site(site)

    assert not site.is_registered(GraphSearch)
    assert not site.is_registered(GraphSearchIndexStatus)
    url_names = [p.name for p in site.get_urls() if hasattr(p, "name") and p.name]
    assert "graph-search" not in url_names


def test_graph_search_legacy_url_404_when_disabled(staff_client, apply_admin_graph_search_settings):
    apply_admin_graph_search_settings(_minimal_graph_search({"ADMIN_SEARCH_ENABLED": False}))

    site = AdminSite(name="disabled_graph_search_admin")
    setup_admin_site(site)

    url_module = ModuleType("test_urls_graph_search_disabled")
    from django.urls import path

    url_module.urlpatterns = [path("admin/", site.urls)]

    with override_settings(ROOT_URLCONF=url_module):
        response = staff_client.get("/admin/graph-search/")
    assert response.status_code == 404
