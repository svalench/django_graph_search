"""Интеграционные тесты PgvectorBackend (только PostgreSQL)."""
from __future__ import annotations

import pytest
from django.conf import settings

from django_graph_search.backends.base import Document, SearchResult


def _is_postgres() -> bool:
    eng = settings.DATABASES["default"].get("ENGINE", "")
    return "postgresql" in eng


requires_postgres = pytest.mark.skipif(
    not _is_postgres(),
    reason="PgvectorBackend requires PostgreSQL",
)


@requires_postgres
@pytest.mark.django_db
def test_pgvector_add_search_delete_roundtrip():
    pytest.importorskip("django.contrib.postgres")  # noqa: F401 — guard
    from django.db import connection

    from django_graph_search.backends.pgvector import PgvectorBackend

    dim = 8
    store = PgvectorBackend(
        table_name="dgs_pgvector_test_tmp",
        dimension=dim,
        distance="cosine",
        using="default",
    )
    store.clear_collection()
    vec_a = [1.0 / dim**0.5] * dim
    vec_b = [0.0] * dim
    vec_b[0] = 1.0
    store.add_documents(
        [
            Document(
                id="m:1",
                embedding=vec_a,
                metadata={"model": "test_app.Product", "pk": 1, "text": "hello"},
                text="hello",
            ),
            Document(
                id="m:2",
                embedding=vec_b,
                metadata={"model": "test_app.Product", "pk": 2, "text": "other"},
                text="other",
            ),
        ]
    )
    hits = store.search(vec_a, limit=5, filters=None)
    assert len(hits) >= 1
    assert isinstance(hits[0], SearchResult)
    assert hits[0].id == "m:1"
    assert 0.0 <= hits[0].score <= 1.0
    assert store.count_documents() == 2
    assert store.count_documents({"model": "test_app.Product"}) == 2
    assert store.count_documents({"model": "test_app.Other"}) == 0
    store.delete(["m:1"])
    after = store.search(vec_a, limit=5, filters=None)
    ids = {h.id for h in after}
    assert "m:1" not in ids
    store.clear_collection()
    # освободить соединение — таблица может остаться в БД для повторных прогонов
    connection.close()
