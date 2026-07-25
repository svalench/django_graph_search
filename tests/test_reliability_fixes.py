"""Тесты надёжности: upsert, dedupe, on_commit-семантика, whitelist, лимиты."""
from __future__ import annotations

import json
import os
import sys
import time
import types
from typing import Any, Dict, List, Optional

import pytest
from django.conf import settings as django_settings
from django.test import RequestFactory

from django_graph_search.backends.base import Document, SearchResult
from django_graph_search.graph_resolver import GraphResolver
from django_graph_search.searcher import Searcher
from django_graph_search.settings import clear_graph_search_caches, get_settings

from .dummy_embedding_backend import DummyEmbeddingBackend
from .test_app.models import Category, Product


@pytest.fixture(name="gs_settings")
def _gs_settings_fixture():
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


# --------------------------------------------------------------------------- fakes


class _FakeFaissIndex:
    def __init__(self, dim: int) -> None:
        self.dim = dim
        self._vectors: List[List[float]] = []

    def add(self, vectors) -> None:
        for row in vectors:
            self._vectors.append([float(v) for v in row])

    def search(self, query, k: int):
        import numpy as np

        q = np.array(query[0], dtype="float32")
        if not self._vectors:
            return np.array([[]], dtype="float32"), np.array([[-1]])
        dists = [float(np.sum((np.array(v, dtype="float32") - q) ** 2)) for v in self._vectors]
        order = sorted(range(len(dists)), key=lambda i: dists[i])[:k]
        order += [-1] * (k - len(order))
        dd = [dists[i] if i >= 0 else 0.0 for i in order]
        return np.array([dd], dtype="float32"), np.array([order])


@pytest.fixture(name="fake_faiss")
def _fake_faiss_fixture(monkeypatch):
    module = types.ModuleType("faiss")
    module.IndexFlatL2 = _FakeFaissIndex
    monkeypatch.setitem(sys.modules, "faiss", module)
    return module


class _FakeChromaCollection:
    def __init__(self) -> None:
        self.calls: List[str] = []
        self.docs: Dict[str, Dict[str, Any]] = {}
        self.configuration = None
        self.metadata = {"hnsw:space": "cosine"}

    def upsert(self, ids, embeddings, metadatas, documents) -> None:
        self.calls.append("upsert")
        for doc_id, emb, meta, text in zip(ids, embeddings, metadatas, documents):
            self.docs[doc_id] = {"embedding": emb, "metadata": meta, "text": text}

    def add(self, **kwargs) -> None:  # pragma: no cover - не должен вызываться
        raise AssertionError("collection.add must not be used (upsert expected)")

    def query(self, query_embeddings, n_results, where, include):
        items = list(self.docs.items())[:n_results]
        ids = [doc_id for doc_id, _ in items]
        metas = [payload["metadata"] for _, payload in items]
        docs = [payload["text"] for _, payload in items]
        distances = [0.1] * len(ids)
        return {"ids": [ids], "distances": [distances], "metadatas": [metas], "documents": [docs]}

    def delete(self, ids=None, where=None) -> None:
        if ids:
            for doc_id in ids:
                self.docs.pop(doc_id, None)
        if where is not None:
            self.docs.clear()

    def get(self, where, include):
        return {"ids": list(self.docs)}

    def count(self) -> int:
        return len(self.docs)


@pytest.fixture(name="fake_chromadb")
def _fake_chromadb_fixture(monkeypatch):
    collection = _FakeChromaCollection()
    module = types.ModuleType("chromadb")

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_or_create_collection(self, name, configuration=None, metadata=None):
            return collection

    module.Client = _Client
    module.PersistentClient = _Client
    monkeypatch.setitem(sys.modules, "chromadb", module)
    return collection


class RecordingVectorStore:
    """Vector store, записывающий вызовы и возвращающий подготовленные результаты."""

    def __init__(self, results: Optional[List[SearchResult]] = None) -> None:
        self.results = results or []
        self.last_filters: Optional[Dict[str, Any]] = None
        self.last_limit: Optional[int] = None

    def add_documents(self, documents) -> None:
        pass

    def search(self, query_vector, limit, filters=None):
        self.last_filters = filters
        self.last_limit = limit
        return list(self.results)

    def delete(self, doc_ids) -> None:
        pass

    def clear_collection(self) -> None:
        pass

    def count_documents(self, filters=None) -> int:
        return len(self.results)


# --------------------------------------------------------------------------- backends


def test_chromadb_add_documents_uses_upsert(fake_chromadb):
    from django_graph_search.backends.chromadb import ChromaDBBackend

    backend = ChromaDBBackend()
    doc = Document(id="m:1", embedding=[0.1], metadata={"model": "m", "pk": 1}, text="t")
    backend.add_documents([doc])
    backend.add_documents([doc])  # повторная индексация не должна падать
    assert fake_chromadb.calls == ["upsert", "upsert"]
    assert len(fake_chromadb.docs) == 1


def test_faiss_add_documents_dedupes_ids(fake_faiss):
    from django_graph_search.backends.faiss import FaissBackend

    backend = FaissBackend()
    backend.add_documents(
        [Document(id="m:1", embedding=[0.1], metadata={"model": "m", "pk": 1, "v": 1})]
    )
    backend.add_documents(
        [Document(id="m:1", embedding=[0.2], metadata={"model": "m", "pk": 1, "v": 2})]
    )
    assert backend.count_documents() == 1
    results = backend.search([0.2], limit=5)
    assert len(results) == 1
    assert results[0].metadata["v"] == 2


def test_faiss_add_documents_dedupes_ids_within_batch(fake_faiss):
    from django_graph_search.backends.faiss import FaissBackend

    backend = FaissBackend()
    backend.add_documents(
        [
            Document(id="m:1", embedding=[0.1], metadata={"model": "m", "pk": 1, "v": 1}),
            Document(id="m:1", embedding=[0.9], metadata={"model": "m", "pk": 1, "v": 9}),
        ]
    )
    assert backend.count_documents() == 1
    results = backend.search([0.9], limit=5)
    assert len(results) == 1
    assert results[0].metadata["v"] == 9


def test_faiss_persist_roundtrip(fake_faiss, tmp_path):
    from django_graph_search.backends.faiss import FaissBackend

    path = str(tmp_path / "faiss.pkl")
    backend = FaissBackend(persist_path=path)
    backend.add_documents(
        [Document(id="m:1", embedding=[0.1], metadata={"model": "m", "pk": 1})]
    )
    assert os.path.exists(path)

    reloaded = FaissBackend(persist_path=path)
    assert reloaded.count_documents() == 1
    results = reloaded.search([0.1], limit=5)
    assert len(results) == 1
    assert results[0].id == "m:1"


# --------------------------------------------------------------------------- searcher


def _make_searcher(gs_settings, store, models_fields=None):
    cfg = gs_settings(
        {
            "MODELS": [
                {"model": "test_app.Product", "fields": models_fields or ["name"]},
            ],
            "VECTOR_STORE": {"BACKEND": "tests.dummy_vector_backend.DummyVectorBackend"},
            "EMBEDDINGS": {
                "default": {
                    "BACKEND": "tests.dummy_embedding_backend.DummyEmbeddingBackend",
                    "MODEL_NAME": "x",
                }
            },
        }
    )
    return Searcher(
        config=cfg,
        vector_store=store,
        embedding_backend=DummyEmbeddingBackend(model_name="x"),
        resolver=GraphResolver(),
    )


@pytest.mark.django_db
def test_find_similar_excludes_self(gs_settings):
    product = Product.objects.create(name="Pixel", category=Category.objects.create(name="c"))
    own = SearchResult(
        id=f"test_app.Product:{product.pk}",
        score=1.0,
        metadata={"model": "test_app.Product", "pk": product.pk, "text": "Pixel"},
    )
    others = [
        SearchResult(
            id=f"test_app.Product:{1000 + i}",
            score=0.9 - i * 0.01,
            metadata={"model": "test_app.Product", "pk": 1000 + i, "text": f"Other{i}"},
        )
        for i in range(10)
    ]
    store = RecordingVectorStore(results=[own, *others])
    searcher = _make_searcher(gs_settings, store)
    results = searcher.find_similar(product, limit=5)
    ids = {r["pk"] for r in results}
    assert product.pk not in ids
    assert len(results) == 5
    # max(limit+1, min(limit*3, 100)) при limit=5 → 15; второго прохода нет
    assert store.last_limit == 15


def test_search_linear_pushes_single_model_filter(gs_settings):
    store = RecordingVectorStore()
    searcher = _make_searcher(gs_settings, store)
    searcher.search("q", models=["test_app.Product"], limit=5)
    assert store.last_filters == {"model": "test_app.Product"}
    assert store.last_limit == 5


def test_search_linear_multi_model_overfetch(gs_settings):
    store = RecordingVectorStore()
    searcher = _make_searcher(gs_settings, store)
    searcher.search("q", models=["test_app.Product", "test_app.Category"], limit=5)
    assert store.last_filters is None
    assert store.last_limit == 50  # over-fetch x10


@pytest.mark.django_db
def test_model_to_dict_whitelists_configured_fields(gs_settings):
    product = Product.objects.create(
        name="Pixel", description="secret", category=Category.objects.create(name="c")
    )
    store = RecordingVectorStore()
    searcher = _make_searcher(gs_settings, store, models_fields=["name"])
    item = SearchResult(
        id=f"test_app.Product:{product.pk}",
        score=0.9,
        metadata={"model": "test_app.Product", "pk": product.pk, "text": "Pixel"},
    )
    formatted = searcher._format_result(item)
    assert formatted["data"].get("name") == "Pixel"
    assert "description" not in formatted["data"]
    assert "category" not in formatted["data"]


# --------------------------------------------------------------------------- API permissions


@pytest.mark.django_db
def test_similar_api_enforces_permissions(gs_settings):
    gs_settings(
        {
            "MODELS": [{"model": "test_app.Product", "fields": ["name"]}],
            "VECTOR_STORE": {"BACKEND": "tests.dummy_vector_backend.DummyVectorBackend"},
            "EMBEDDINGS": {
                "default": {
                    "BACKEND": "tests.dummy_embedding_backend.DummyEmbeddingBackend",
                    "MODEL_NAME": "x",
                }
            },
            "API": {"REQUIRE_AUTHENTICATION": True},
        }
    )
    from django.contrib.auth.models import AnonymousUser

    from django_graph_search.views import SimilarAPIView

    request = RequestFactory().get("/api/search/similar/test_app.Product/1/")
    request.user = AnonymousUser()
    response = SimilarAPIView.as_view()(request, model="test_app.Product", pk="1")
    assert response.status_code == 401


# --------------------------------------------------------------------------- delta cache


def test_file_delta_cache_purges_expired_on_init(tmp_path):
    from django_graph_search.cache import FileDeltaCache

    directory = str(tmp_path / "cache")
    cache = FileDeltaCache(directory)
    cache.set("k1", "v1", ttl=3600)
    # Вручную кладём просроченную запись.
    expired_path = cache._key_to_path("old")
    with open(expired_path, "w", encoding="utf-8") as handle:
        json.dump({"value": "stale", "expires_at": time.time() - 10}, handle)

    FileDeltaCache(directory)  # повторная инициализация чистит просроченное
    assert not os.path.exists(expired_path)
    fresh = FileDeltaCache(directory)
    assert fresh.get("k1") == "v1"


# --------------------------------------------------------------------------- resolver limits


@pytest.mark.django_db
def test_resolver_caps_related_items(gs_settings):
    gs_settings(
        {
            "MODELS": [{"model": "test_app.Product", "fields": ["name"]}],
            "VECTOR_STORE": {"BACKEND": "tests.dummy_vector_backend.DummyVectorBackend"},
            "EMBEDDINGS": {
                "default": {
                    "BACKEND": "tests.dummy_embedding_backend.DummyEmbeddingBackend",
                    "MODEL_NAME": "x",
                }
            },
            "MAX_RELATED_ITEMS": 2,
        }
    )
    category = Category.objects.create(name="c")
    product = Product.objects.create(name="p", category=category)
    for i in range(5):
        product.tags.create(name=f"tag{i}")

    resolver = GraphResolver()
    texts = resolver._collect_related_text(product, depth=2)
    tag_texts = [t for t in texts if t.startswith("tag")]
    assert len(tag_texts) == 2


@pytest.mark.django_db
def test_resolver_caps_text_length(gs_settings):
    gs_settings(
        {
            "MODELS": [{"model": "test_app.Product", "fields": ["name"]}],
            "VECTOR_STORE": {"BACKEND": "tests.dummy_vector_backend.DummyVectorBackend"},
            "EMBEDDINGS": {
                "default": {
                    "BACKEND": "tests.dummy_embedding_backend.DummyEmbeddingBackend",
                    "MODEL_NAME": "x",
                }
            },
            "MAX_TEXT_LENGTH": 10,
        }
    )
    from django_graph_search.settings import ModelConfig

    product = Product.objects.create(
        name="a" * 100, category=Category.objects.create(name="c")
    )
    resolver = GraphResolver()
    text = resolver.build_searchable_text(
        product, ModelConfig(model="test_app.Product", fields=["name"])
    )
    assert len(text) <= 10


# --------------------------------------------------------------------------- on_commit / rollback


@pytest.mark.django_db
def test_auto_index_runs_after_commit(gs_settings, django_capture_on_commit_callbacks):
    from unittest import mock

    gs_settings(
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
            "ASYNC_INDEXING": {"ENABLED": False},
            "AUTO_INDEX_NON_BLOCKING": False,
        }
    )
    cat = Category.objects.create(name="c")
    with mock.patch("django_graph_search.signals._dispatch_index") as dispatch:
        # pending заполняется только при выходе из context manager.
        with django_capture_on_commit_callbacks(execute=False) as pending:
            Product.objects.create(name="pending", category=cat)
            dispatch.assert_not_called()
        assert pending
        dispatch.assert_not_called()

        with django_capture_on_commit_callbacks(execute=True):
            Product.objects.create(name="committed", category=cat)
        assert dispatch.call_count == 1


@pytest.mark.django_db(transaction=True)
def test_rollback_does_not_dispatch_index(gs_settings):
    from unittest import mock

    from django.db import transaction

    gs_settings(
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
            "ASYNC_INDEXING": {"ENABLED": False},
            "AUTO_INDEX_NON_BLOCKING": False,
        }
    )
    cat = Category.objects.create(name="c")
    with mock.patch("django_graph_search.signals._dispatch_index") as dispatch:
        with pytest.raises(RuntimeError, match="boom"):
            with transaction.atomic():
                Product.objects.create(name="rolled", category=cat)
                raise RuntimeError("boom")
        dispatch.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_rollback_does_not_dispatch_delete(gs_settings):
    from unittest import mock

    from django.db import transaction

    gs_settings(
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
            "ASYNC_INDEXING": {"ENABLED": False},
            "AUTO_INDEX_NON_BLOCKING": False,
        }
    )
    product = Product.objects.create(name="keep", category=Category.objects.create(name="c"))
    product_pk = product.pk
    with mock.patch("django_graph_search.signals._dispatch_delete") as dispatch:
        with pytest.raises(RuntimeError, match="boom"):
            with transaction.atomic():
                product.delete()
                raise RuntimeError("boom")
        dispatch.assert_not_called()
    # После delete() у инстанса pk=None, даже если транзакция откатилась.
    assert Product.objects.filter(pk=product_pk).exists()


@pytest.mark.django_db
def test_delete_on_commit_passes_real_pk(gs_settings, django_capture_on_commit_callbacks):
    """pk захватывается в post_delete: к on_commit instance.pk уже None."""
    from unittest import mock

    gs_settings(
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
            "ASYNC_INDEXING": {"ENABLED": False},
            "AUTO_INDEX_NON_BLOCKING": False,
        }
    )
    product = Product.objects.create(name="gone", category=Category.objects.create(name="c"))
    product_pk = product.pk
    with mock.patch("django_graph_search.signals._dispatch_delete") as dispatch:
        with django_capture_on_commit_callbacks(execute=True):
            product.delete()
        assert product.pk is None
        dispatch.assert_called_once_with(
            "test_app.Product", "test_app", "product", product_pk
        )


def test_daemon_worker_pool_threads_are_daemon():
    from django_graph_search.signals import _DaemonWorkerPool

    pool = _DaemonWorkerPool(2)
    try:
        assert len(pool._workers) == 2
        assert all(worker.daemon for worker in pool._workers)
    finally:
        pool.shutdown(wait=True)
