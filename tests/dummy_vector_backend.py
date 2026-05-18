"""Минимальный vector store для тестов настроек (без chromadb/faiss)."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from django_graph_search.backends.base import BaseVectorStore, Document, SearchResult


class DummyVectorBackend(BaseVectorStore):
    """Пустая реализация — только чтобы get_settings() мог импортировать BACKEND."""

    def __init__(self, **options: Any) -> None:
        del options

    def add_documents(self, documents: Iterable[Document]) -> None:
        list(documents)

    def search(
        self,
        query_vector: List[float],
        limit: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        del query_vector, limit, filters
        return []

    def delete(self, doc_ids: Iterable[str]) -> None:
        del doc_ids

    def clear_collection(self) -> None:
        return
