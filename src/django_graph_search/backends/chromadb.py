from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from ..exceptions import BackendError
from .base import BaseVectorStore, Document, SearchResult


class ChromaDBBackend(BaseVectorStore):
    def __init__(
        self,
        persist_directory: Optional[str] = None,
        collection_name: str = "django_graph_search",
        distance_metric: str = "cosine",
        **options: Any,
    ) -> None:
        try:
            import chromadb
        except Exception as exc:  # pragma: no cover - dependency error
            raise BackendError("chromadb is not installed.") from exc

        self.distance_metric = (distance_metric or "cosine").lower()
        if persist_directory:
            client = chromadb.PersistentClient(path=persist_directory, **options)
        else:
            client = chromadb.Client(**options)

        collection_metadata = None
        if self.distance_metric == "cosine":
            collection_metadata = {"hnsw:space": "cosine"}

        self.collection = client.get_or_create_collection(
            name=collection_name,
            metadata=collection_metadata,
        )

    def add_documents(self, documents: Iterable[Document]) -> None:
        docs = list(documents)
        if not docs:
            return
        self.collection.add(
            ids=[doc.id for doc in docs],
            embeddings=[doc.embedding for doc in docs],
            metadatas=[doc.metadata for doc in docs],
            documents=[doc.text or "" for doc in docs],
        )

    def search(
        self,
        query_vector: List[float],
        limit: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        response = self.collection.query(
            query_embeddings=[query_vector],
            n_results=limit,
            where=filters,
        )
        ids = response.get("ids", [[]])[0]
        distances = response.get("distances", [[]])[0]
        metadatas = response.get("metadatas", [[]])[0]
        documents = response.get("documents", [[]])[0]
        results = []
        for doc_id, distance, metadata, doc_text in zip(
            ids, distances, metadatas, documents
        ):
            meta = dict(metadata or {})
            if doc_text and "text" not in meta:
                meta["text"] = doc_text
            score = self._distance_to_similarity(distance)
            results.append(SearchResult(id=doc_id, score=score, metadata=meta))
        return results

    def _distance_to_similarity(self, distance: Any) -> float:
        """Привести метрику Chroma к сходству в диапазоне [0, 1]."""
        if distance is None:
            return 0.0
        d = float(distance)
        if self.distance_metric == "cosine":
            return max(0.0, min(1.0, 1.0 - d))
        # l2 и прочее: монотонное сжатие дистанции в (0, 1]
        return max(0.0, min(1.0, 1.0 / (1.0 + d)))

    def delete(self, doc_ids: Iterable[str]) -> None:
        ids = list(doc_ids)
        if not ids:
            return
        self.collection.delete(ids=ids)

    def clear_collection(self) -> None:
        self.collection.delete(where={})

