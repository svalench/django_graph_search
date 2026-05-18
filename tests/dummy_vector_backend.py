"""Минимальный vector store для тестов настроек (без chromadb/faiss)."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from django_graph_search.backends.base import BaseVectorStore, Document, SearchResult


class DummyVectorBackend(BaseVectorStore):
    """Пустая реализация — только чтобы get_settings() мог импортировать BACKEND."""

    def __init__(self, **options: Any) -> None:
        del options
        self._documents: List[Document] = []

    def add_documents(self, documents: Iterable[Document]) -> None:
        self._documents.extend(list(documents))

    def search(
        self,
        query_vector: List[float],
        limit: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        del query_vector, limit, filters
        return []

    def delete(self, doc_ids: Iterable[str]) -> None:
        drop = set(doc_ids)
        self._documents = [d for d in self._documents if d.id not in drop]

    def clear_collection(self) -> None:
        self._documents.clear()

    def count_documents(self, filters: Optional[Dict[str, Any]] = None) -> int:
        if not filters:
            return len(self._documents)
        return sum(
            1
            for d in self._documents
            if all(d.metadata.get(k) == v for k, v in filters.items())
        )
