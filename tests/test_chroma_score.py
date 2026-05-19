"""Тесты маппинга distance → score для ChromaDB (метрика коллекции vs формула)."""

from types import SimpleNamespace

import pytest

from django_graph_search.backends.chromadb import (
    ChromaDBBackend,
    _effective_space_from_collection,
    _requested_chroma_space,
    chroma_distance_to_similarity,
)


@pytest.mark.parametrize(
    ("space", "distance", "expected"),
    [
        ("l2", 4.0, 0.2),
        ("l2", 0.0, 1.0),
        ("cosine", 0.1, 0.9),
        ("cosine", 0.0, 1.0),
        ("cosine", 1.5, 0.0),
        ("ip", 0.4, 0.6),
        ("ip", None, 0.0),
    ],
)
def test_chroma_distance_to_similarity(space, distance, expected):
    assert chroma_distance_to_similarity(space, distance) == pytest.approx(expected)


def test_requested_chroma_space_aliases():
    assert _requested_chroma_space("inner_product") == "ip"
    assert _requested_chroma_space("euclidean") == "l2"
    assert _requested_chroma_space("COSINE") == "cosine"


def test_effective_space_from_collection_configuration():
    col = SimpleNamespace(
        configuration={"hnsw": {"space": "l2"}},
        metadata=None,
    )
    assert _effective_space_from_collection(col, fallback="cosine") == "l2"


def test_effective_space_from_collection_legacy_metadata():
    col = SimpleNamespace(
        configuration={},
        metadata={"hnsw:space": "cosine"},
    )
    assert _effective_space_from_collection(col, fallback="l2") == "cosine"


def test_effective_space_from_collection_object_configuration():
    """Chroma >= 0.5 может отдавать configuration как объект с атрибутом hnsw."""
    hnsw = SimpleNamespace(space="ip")
    cfg = SimpleNamespace(hnsw=hnsw)
    col = SimpleNamespace(configuration=cfg, metadata={})
    assert _effective_space_from_collection(col, fallback="cosine") == "ip"


def test_effective_space_from_collection_top_level_space_key():
    col = SimpleNamespace(
        configuration={"space": "l2"},
        metadata={},
    )
    assert _effective_space_from_collection(col, fallback="cosine") == "l2"


def test_effective_space_fallback():
    col = SimpleNamespace(configuration={}, metadata={})
    assert _effective_space_from_collection(col, fallback="inner_product") == "ip"


def test_chroma_backend_l2_collection_nonzero_score_for_large_distance(tmp_path):
    """Раньше при L2-коллекции и формуле (1-d) все score обнулялись; с l2 — нет."""
    pytest.importorskip("chromadb")
    import chromadb

    name = "score_test_col"
    client = chromadb.PersistentClient(path=str(tmp_path))
    client.get_or_create_collection(name)

    backend = ChromaDBBackend(
        persist_directory=str(tmp_path),
        collection_name=name,
        distance_metric="cosine",
    )
    assert backend._effective_space == "l2"

    backend.collection.add(
        ids=["row1"],
        embeddings=[[1.0, 0.0, 0.0]],
        documents=["alpha"],
        metadatas=[{"model": "app.Model", "pk": 1}],
    )
    hits = backend.search([0.0, 1.0, 0.0], limit=1, filters=None)
    assert hits
    assert hits[0].score > 0.0
    assert hits[0].metadata.get("vector_distance") is not None


def test_chroma_backend_search_uses_explicit_include():
    captured = {}

    class _Coll:
        def query(self, **kwargs):
            captured.update(kwargs)
            return {
                "ids": [["id1"]],
                "distances": [[0.0]],
                "metadatas": [[{"model": "m", "pk": 1}]],
                "documents": [["hello"]],
            }

    backend = object.__new__(ChromaDBBackend)
    backend.collection = _Coll()
    backend._effective_space = "cosine"

    hits = ChromaDBBackend.search(backend, [0.1, 0.2], limit=3, filters=None)
    assert captured.get("include") == ["distances", "metadatas", "documents"]
    assert hits[0].score == 1.0
    assert hits[0].metadata.get("vector_distance") == 0.0
