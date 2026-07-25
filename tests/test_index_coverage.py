"""Тесты count_documents и get_index_coverage."""
from __future__ import annotations

from dataclasses import replace

import pytest

from django_graph_search.backends.base import Document
from django_graph_search.backends.faiss import FaissBackend
from django_graph_search.index_coverage import get_index_coverage
from django_graph_search.settings import ModelConfig, VectorStoreConfig

from tests.utils import make_basic_config
from tests.dummy_vector_backend import DummyVectorBackend
from tests.test_app.models import Category, Product


def test_faiss_count_documents_no_filters():
    pytest.importorskip("faiss", reason="faiss-cpu not installed")
    store = FaissBackend()
    assert store.count_documents() == 0
    assert store.count_documents(None) == 0
    store.add_documents(
        [
            Document(
                id="test_app.Product:1",
                embedding=[1.0, 0.0],
                metadata={"model": "test_app.Product", "pk": 1},
            ),
            Document(
                id="test_app.Tag:1",
                embedding=[0.0, 1.0],
                metadata={"model": "test_app.Tag", "pk": 1},
            ),
        ]
    )
    assert store.count_documents() == 2
    assert store.count_documents({"model": "test_app.Product"}) == 1


def test_dummy_vector_backend_count_documents():
    store = DummyVectorBackend()
    assert store.count_documents() == 0
    store.add_documents(
        [
            Document(
                id="a:1",
                embedding=[0.1],
                metadata={"model": "app.M", "pk": 1},
            )
        ]
    )
    assert store.count_documents({"model": "app.M"}) == 1
    assert store.count_documents({"model": "app.Other"}) == 0


@pytest.mark.django_db
def test_get_index_coverage_uses_orm_and_store():
    pytest.importorskip("faiss", reason="faiss-cpu not installed")
    category = Category.objects.create(name="cat")
    p1 = Product.objects.create(name="one", category=category)
    Product.objects.create(name="two", category=category)

    base = make_basic_config(
        delta_indexing=False,
        models=[
            ModelConfig(
                model="test_app.Product",
                fields=["name"],
                follow_relations=False,
                relation_depth=1,
            )
        ],
    )
    config = replace(
        base,
        vector_store=VectorStoreConfig(
            backend="django_graph_search.backends.faiss.FaissBackend",
            options={},
        ),
    )
    store = FaissBackend()
    store.add_documents(
        [
            Document(
                id=f"test_app.Product:{p1.pk}",
                embedding=[1.0, 0.0],
                metadata={"model": "test_app.Product", "pk": p1.pk, "text": "x"},
            ),
        ]
    )
    report = get_index_coverage(config=config, vector_store=store)
    assert report.total_db == 2
    assert report.total_indexed == 1
    assert len(report.rows) == 1
    assert report.rows[0].db_count == 2
    assert report.rows[0].indexed_count == 1
    assert abs(report.rows[0].percent - 50.0) < 0.01
    assert report.overall_percent == report.rows[0].percent


@pytest.mark.django_db
def test_get_index_coverage_empty_db_is_hundred_percent():
    config = make_basic_config(
        delta_indexing=False,
        models=[
            ModelConfig(
                model="test_app.Product",
                fields=["name"],
                follow_relations=False,
                relation_depth=1,
            )
        ],
    )
    report = get_index_coverage(config=config, vector_store=FaissBackend())
    assert report.total_db == 0
    assert report.overall_percent == 100.0
