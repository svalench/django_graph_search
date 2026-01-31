from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class Document:
    id: str
    embedding: List[float]
    metadata: Dict[str, Any]
    text: Optional[str] = None


@dataclass(frozen=True)
class SearchResult:
    id: str
    score: float
    metadata: Dict[str, Any]


class BaseVectorStore(ABC):
    @abstractmethod
    def add_documents(self, documents: Iterable[Document]) -> None:
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        query_vector: List[float],
        limit: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        raise NotImplementedError

    @abstractmethod
    def delete(self, doc_ids: Iterable[str]) -> None:
        raise NotImplementedError

    @abstractmethod
    def clear_collection(self) -> None:
        raise NotImplementedError

