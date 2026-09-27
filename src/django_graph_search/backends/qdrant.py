from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List, Optional

from ..exceptions import BackendError
from .base import BaseVectorStore, Document, SearchResult

# Qdrant принимает id точек только как unsigned int или UUID. Наши id вида
# "app.Model:pk" — строки, поэтому храним детерминированный UUID5 от них,
# а исходный doc_id кладём в payload для обратного маппинга.
_DOC_ID_PAYLOAD_KEY = "_dgs_doc_id"
_QDRANT_NAMESPACE = uuid.UUID("6f1b6c0e-7f7f-4b5a-9a1d-0d1c2b3a4f55")


def qdrant_point_id(doc_id: str) -> str:
    """Детерминированный UUID точки Qdrant для строкового doc_id."""
    return str(uuid.uuid5(_QDRANT_NAMESPACE, doc_id))


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
        self.distance = self._resolve_distance(distance)

    def _resolve_distance(self, distance: str) -> Any:
        """
        Привести строку из настроек к ``qmodels.Distance``.

        Члены enum называются COSINE/EUCLID/DOT/MANHATTAN, а их значения —
        "Cosine"/"Euclid"/"Dot"/"Manhattan"; принимаем любой регистр и синонимы.
        """
        raw = str(distance or "Cosine").strip()
        aliases = {"cos": "Cosine", "l2": "Euclid", "euclidean": "Euclid", "ip": "Dot",
                   "inner_product": "Dot"}
        normalized = aliases.get(raw.lower(), raw)
        enum_cls = self.qmodels.Distance
        for member in enum_cls:
            if member.name.lower() == normalized.lower() or str(
                member.value
            ).lower() == normalized.lower():
                return member
        raise BackendError(
            f"Unknown Qdrant distance {distance!r}; "
            f"expected one of {[m.value for m in enum_cls]}."
        )

    def _ensure_collection(self, dim: int) -> None:
        if self.client.collection_exists(self.collection_name):
            return
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=self.qmodels.VectorParams(size=dim, distance=self.distance),
        )

    def _build_filter(self, filters: Optional[Dict[str, Any]]) -> Any:
        if not filters:
            return None
        conditions = [
            self.qmodels.FieldCondition(key=key, match=self.qmodels.MatchValue(value=value))
            for key, value in filters.items()
        ]
        return self.qmodels.Filter(must=conditions)

    def add_documents(self, documents: Iterable[Document]) -> None:
        docs = list(documents)
        if not docs:
            return
        dim = len(docs[0].embedding)
        self._ensure_collection(dim)
        points = [
            self.qmodels.PointStruct(
                id=qdrant_point_id(doc.id),
                vector=doc.embedding,
                payload={**doc.metadata, _DOC_ID_PAYLOAD_KEY: doc.id},
            )
            for doc in docs
        ]
        self.client.upsert(collection_name=self.collection_name, points=points)

    def search(
        self,
        query_vector: List[float],
        limit: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        if not self.client.collection_exists(self.collection_name):
            return []
        query_filter = self._build_filter(filters)
        # qdrant-client >= 1.10: search() deprecated в пользу query_points().
        if hasattr(self.client, "query_points"):
            hits = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=limit,
                query_filter=query_filter,
                with_payload=True,
            ).points
        else:  # pragma: no cover - старые версии клиента
            hits = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                limit=limit,
                query_filter=query_filter,
            )
        results: List[SearchResult] = []
        for item in hits:
            payload = dict(item.payload or {})
            doc_id = payload.pop(_DOC_ID_PAYLOAD_KEY, None) or str(item.id)
            results.append(
                SearchResult(
                    id=str(doc_id),
                    score=max(0.0, min(1.0, float(item.score))),
                    metadata=payload,
                )
            )
        return results

    def delete(self, doc_ids: Iterable[str]) -> None:
        ids = [qdrant_point_id(doc_id) for doc_id in doc_ids]
        if not ids:
            return
        if not self.client.collection_exists(self.collection_name):
            return
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=self.qmodels.PointIdsList(points=ids),
        )

    def clear_collection(self) -> None:
        if self.client.collection_exists(self.collection_name):
            self.client.delete_collection(collection_name=self.collection_name)

    def count_documents(self, filters: Optional[Dict[str, Any]] = None) -> int:
        if not self.client.collection_exists(self.collection_name):
            return 0
        result = self.client.count(
            collection_name=self.collection_name,
            count_filter=self._build_filter(filters),
        )
        return int(result.count)
