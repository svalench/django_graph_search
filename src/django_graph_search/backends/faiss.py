from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from ..exceptions import BackendError
from .base import BaseVectorStore, Document, SearchResult


class FaissBackend(BaseVectorStore):
    def __init__(self, **options: Any) -> None:
        self.options = options
        self.index = None
        self._ids: List[str] = []
        self._metas: List[Dict[str, Any]] = []
        self._embeddings: List[List[float]] = []

    def _ensure_index(self, dim: int):
        if self.index is not None:
            return
        try:
            import faiss
        except Exception as exc:  # pragma: no cover - dependency error
            raise BackendError("faiss-cpu is not installed.") from exc
        self.index = faiss.IndexFlatL2(dim)

    def add_documents(self, documents: Iterable[Document]) -> None:
        docs = list(documents)
        if not docs:
            return
        dim = len(docs[0].embedding)
        self._ensure_index(dim)
        import numpy as np

        vectors = np.array([doc.embedding for doc in docs], dtype="float32")
        self.index.add(vectors)
        self._ids.extend([doc.id for doc in docs])
        self._metas.extend([doc.metadata for doc in docs])
        self._embeddings.extend([doc.embedding for doc in docs])

    def search(
        self,
        query_vector: List[float],
        limit: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        if self.index is None or not self._ids:
            return []
        import numpy as np

        query = np.array([query_vector], dtype="float32")
        distances, indices = self.index.search(query, limit)
        results: List[SearchResult] = []
        for idx, dist in zip(indices[0], distances[0]):
            if idx < 0 or idx >= len(self._ids):
                continue
            metadata = self._metas[idx]
            if filters and not self._match_filters(metadata, filters):
                continue
            results.append(SearchResult(id=self._ids[idx], score=float(dist), metadata=metadata))
        return results

    def delete(self, doc_ids: Iterable[str]) -> None:
        ids = set(doc_ids)
        if not ids:
            return
        remaining = [
            (doc_id, meta, embedding)
            for doc_id, meta, embedding in zip(self._ids, self._metas, self._embeddings)
            if doc_id not in ids
        ]
        self._ids = [item[0] for item in remaining]
        self._metas = [item[1] for item in remaining]
        self._embeddings = [item[2] for item in remaining]
        self.index = None
        if remaining:
            # Rebuild index
            existing_ids = list(self._ids)
            existing_metas = list(self._metas)
            existing_embeddings = list(self._embeddings)
            self._ids = []
            self._metas = []
            self._embeddings = []
            docs = [
                Document(id=doc_id, embedding=embedding, metadata=meta)
                for doc_id, meta, embedding in zip(
                    existing_ids, existing_metas, existing_embeddings
                )
            ]
            self.add_documents(docs)

    def clear_collection(self) -> None:
        self.index = None
        self._ids = []
        self._metas = []
        self._embeddings = []

    def _match_filters(self, metadata: Dict[str, Any], filters: Dict[str, Any]) -> bool:
        for key, value in filters.items():
            if metadata.get(key) != value:
                return False
        return True

