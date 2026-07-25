"""AUTO_INDEX_NON_BLOCKING: локальный ST не блокирует поток запроса."""
from __future__ import annotations

import time
from typing import Any, Dict
from unittest import mock

import pytest
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model

from django_graph_search.settings import clear_graph_search_caches

from .test_app.models import Category, Product


@pytest.fixture(name="graph_search_nb_settings")
def _graph_search_nb_settings_fixture():
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
def test_non_blocking_auto_index_returns_before_slow_embed(
    graph_search_nb_settings, django_capture_on_commit_callbacks
):
    graph_search_nb_settings(
        {
            "MODELS": [{"model": "test_app.Product", "fields": ["name"]}],
            "VECTOR_STORE": {"BACKEND": "tests.dummy_vector_backend.DummyVectorBackend"},
            "EMBEDDINGS": {
                "default": {
                    "BACKEND": "django_graph_search.embeddings.SentenceTransformerBackend",
                    "MODEL_NAME": "x",
                }
            },
            "AUTO_INDEX": True,
            "AUTO_INDEX_NON_BLOCKING": True,
            "ASYNC_INDEXING": {"ENABLED": False},
        }
    )

    def slow_index(*_a, **_kw):
        time.sleep(1.2)

    cat = Category.objects.create(name="c")
    with mock.patch("django_graph_search.tasks.index_instance_task_fn", side_effect=slow_index):
        t0 = time.monotonic()
        with django_capture_on_commit_callbacks(execute=True):
            Product.objects.create(name="fast", category=cat)
        elapsed = time.monotonic() - t0
    assert elapsed < 0.35


@pytest.mark.django_db
def test_skip_full_save_when_only_last_login_changed(
    graph_search_nb_settings, django_capture_on_commit_callbacks
):
    User = get_user_model()
    label = User._meta.label
    graph_search_nb_settings(
        {
            "MODELS": [{"model": label, "fields": ["username"]}],
            "VECTOR_STORE": {"BACKEND": "tests.dummy_vector_backend.DummyVectorBackend"},
            "EMBEDDINGS": {
                "default": {
                    "BACKEND": "tests.dummy_embedding_backend.DummyEmbeddingBackend",
                    "MODEL_NAME": "x",
                }
            },
            "AUTO_INDEX": True,
            "AUTO_INDEX_SKIP_UPDATE_FIELDS": ["last_login"],
        }
    )
    with django_capture_on_commit_callbacks(execute=True):
        user = User.objects.create_user(username="u1", password="x")
        user.set_password("y")
        user.save()

    with mock.patch("django_graph_search.signals._dispatch_index") as dispatch:
        from django.utils import timezone

        user.last_login = timezone.now()
        with django_capture_on_commit_callbacks(execute=True):
            user.save()
        dispatch.assert_not_called()

    # Реальное изменение (не только skip-поля) должно индексировать.
    with mock.patch("django_graph_search.signals._dispatch_index") as dispatch:
        user.username = "u2"
        with django_capture_on_commit_callbacks(execute=True):
            user.save()
        dispatch.assert_called_once()
