"""
Интеграционные тесты векторных бэкендов на реальных клиентах (без Docker):

* Qdrant — ``QdrantClient(":memory:")``;
* ChromaDB — ``EphemeralClient``;
* FAISS — in-process.

Каждый тест пропускается, если соответствующий пакет не установлен
(``pip install -e ".[chromadb,faiss,qdrant]"``).
"""
from __future__ import annotations

import pytest

from django_graph_search.backends.base import Document
from django_graph_search.indexer import Indexer, make_doc_id, serialize_pk
from django_graph_search.settings import ModelConfig

from .dummy_embedding_backend import DummyEmbeddingBackend
from .test_app.models import UuidNote
from .utils import make_basic_config

pytestmark = pytest.mark.integration


def _docs(n: int, model: str = "app.Model") -> list[Document]:
    return [
        Document(
            id=make_doc_id(model, i),
            embedding=[float(i), 1.0, 0.0],
            metadata={"model": model, "pk": i, "text": f"doc {i}"},
            text=f"doc {i}",
        )
        for i in range(n)
    ]


# ------------------------------------------------------------------ Qdrant


@pytest.fixture(name="qdrant_backend")
def _qdrant_backend_fixture():
    pytest.importorskip("qdrant_client")
    from django_graph_search.backends.qdrant import QdrantBackend

    return QdrantBackend(collection_name="dgs_test", location=":memory:")


def test_qdrant_roundtrip_with_string_doc_ids(qdrant_backend):
    """Строковые doc_id ("app.Model:pk") маппятся в UUID и возвращаются обратно."""
    backend = qdrant_backend
    backend.add_documents(_docs(3))
    assert backend.count_documents() == 3

    hits = backend.search([2.0, 1.0, 0.0], limit=2)
    assert [h.id for h in hits][0] == "app.Model:2"
    # Служебный ключ маппинга не утекает в metadata.
    assert all("_dgs_doc_id" not in h.metadata for h in hits)
    assert hits[0].metadata["pk"] == 2
    assert 0.0 <= hits[0].score <= 1.0


def test_qdrant_upsert_replaces_same_id(qdrant_backend):
    backend = qdrant_backend
    backend.add_documents(_docs(1))
    backend.add_documents(_docs(1))
    assert backend.count_documents() == 1


def test_qdrant_filter_delete_and_clear(qdrant_backend):
    backend = qdrant_backend
    backend.add_documents(_docs(2, model="a.A") + _docs(3, model="b.B"))
    assert backend.count_documents({"model": "b.B"}) == 3
    hits = backend.search([0.0, 1.0, 0.0], limit=10, filters={"model": "a.A"})
    assert {h.metadata["model"] for h in hits} == {"a.A"}

    backend.delete([make_doc_id("b.B", 0)])
    assert backend.count_documents({"model": "b.B"}) == 2

    backend.clear_collection()
    assert backend.count_documents() == 0
    # После clear коллекция пересоздаётся при следующем add.
    backend.add_documents(_docs(1))
    assert backend.count_documents() == 1


@pytest.mark.parametrize("distance", ["Cosine", "cosine", "COSINE", "l2", "Dot"])
def test_qdrant_distance_accepts_any_case_and_aliases(distance):
    """Раньше getattr(Distance, 'Cosine') падал: члены enum — COSINE/EUCLID/DOT."""
    pytest.importorskip("qdrant_client")
    from django_graph_search.backends.qdrant import QdrantBackend

    backend = QdrantBackend(collection_name="dgs_dist", distance=distance, location=":memory:")
    backend.add_documents(_docs(1))
    assert backend.count_documents() == 1


def test_qdrant_unknown_distance_raises():
    pytest.importorskip("qdrant_client")
    from django_graph_search.backends.qdrant import QdrantBackend
    from django_graph_search.exceptions import BackendError

    with pytest.raises(BackendError):
        QdrantBackend(collection_name="dgs_bad", distance="hamming", location=":memory:")


def test_qdrant_search_on_missing_collection_returns_empty(qdrant_backend):
    assert qdrant_backend.search([0.0, 1.0, 0.0], limit=5) == []
    qdrant_backend.delete(["app.Model:404"])  # не падает


# ----------------------------------------------------------------- Chroma


@pytest.fixture(name="chroma_backend")
def _chroma_backend_fixture(tmp_path):
    pytest.importorskip("chromadb")
    from django_graph_search.backends.chromadb import ChromaDBBackend

    return ChromaDBBackend(persist_directory=str(tmp_path), collection_name="dgs_clear")


def test_chroma_clear_collection_removes_everything(chroma_backend):
    """clear_collection удаляет по id батчами (граница батча 500 — берём больше)."""
    backend = chroma_backend
    backend.add_documents(_docs(750))
    assert backend.count_documents() == 750
    backend.clear_collection()
    assert backend.count_documents() == 0
    # Коллекция остаётся рабочей.
    backend.add_documents(_docs(2))
    assert backend.count_documents() == 2


def test_chroma_clear_on_empty_collection_is_noop(chroma_backend):
    chroma_backend.clear_collection()
    assert chroma_backend.count_documents() == 0


# ------------------------------------------------------------------ FAISS


@pytest.mark.django_db
def test_faiss_indexes_uuid_pk_model_end_to_end():
    """UUID pk сериализуется в metadata и объект гидрируется обратно из БД."""
    pytest.importorskip("faiss", reason="faiss-cpu not installed")
    from django_graph_search.backends.faiss import FaissBackend
    from django_graph_search.searcher import Searcher

    note = UuidNote.objects.create(title="uuid note")
    model_cfg = ModelConfig(
        model="test_app.UuidNote",
        fields=["title"],
        follow_relations=False,
        relation_depth=0,
    )
    config = make_basic_config(delta_indexing=False, models=[model_cfg])
    store = FaissBackend()
    embedding = DummyEmbeddingBackend("x")

    Indexer(config=config, vector_store=store, embedding_backend=embedding).index_instance(
        note, model_cfg
    )
    assert store.count_documents({"model": "test_app.UuidNote"}) == 1
    assert store._metas[0]["pk"] == serialize_pk(note.pk) == str(note.pk)

    results = Searcher(
        config=config, vector_store=store, embedding_backend=embedding
    ).search("uuid", limit=5)
    assert len(results) == 1
    assert results[0]["pk"] == str(note.pk)
    assert results[0]["data"]["title"] == "uuid note"
