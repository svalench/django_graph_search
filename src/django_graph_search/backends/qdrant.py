from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from ..exceptions import BackendError
from .base import BaseVectorStore, Document, SearchResult


class QdrantBackend(BaseVectorStore):
    def __init__(
        self,
        collection_name: str = "django_graph_search",
        distance: str = "Cosine",
        **options: Any,
    ) -> None:
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.http import models as qmodels
        except Exception as exc:  # pragma: no cover - dependency error
            raise BackendError("qdrant-client is not installed.") from exc

        self.qmodels = qmodels
        self.collection_name = collection_name
        self.client = QdrantClient(**options)
        self.distance = distance

    def _ensure_collection(self, dim: int) -> None:
        if self.client.collection_exists(self.collection_name):
            return
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=self.qmodels.VectorParams(
                size=dim,
                distance=getattr(self.qmodels.Distance, self.distance),
            ),
        )

    def add_documents(self, documents: Iterable[Document]) -> None:
        docs = list(documents)
        if not docs:
            return
        dim = len(docs[0].embedding)
        self._ensure_collection(dim)
        points = [
            self.qmodels.PointStruct(id=doc.id, vector=doc.embedding, payload=doc.metadata)
            for doc in docs
        ]
        self.client.upsert(collection_name=self.collection_name, points=points)

    def search(
        self,
        query_vector: List[float],
        limit: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        query_filter = None
        if filters:
            conditions = [
                self.qmodels.FieldCondition(
                    key=key, match=self.qmodels.MatchValue(value=value)
                )
                for key, value in filters.items()
            ]
            query_filter = self.qmodels.Filter(must=conditions)
        results = self.client.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            limit=limit,
            query_filter=query_filter,
        )
        return [
            SearchResult(id=str(item.id), score=float(item.score), metadata=item.payload or {})
            for item in results
        ]

    def delete(self, doc_ids: Iterable[str]) -> None:
        ids = list(doc_ids)
        if not ids:
            return
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=self.qmodels.PointIdsList(points=ids),
        )

    def clear_collection(self) -> None:
        self.client.delete_collection(collection_name=self.collection_name)

