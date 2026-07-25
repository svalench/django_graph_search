from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Literal, Optional, cast

from ..exceptions import BackendError
from .base import BaseVectorStore, Document, SearchResult

log = logging.getLogger(__name__)


_ChromaHnswSpace = Literal["cosine", "l2", "ip"]


def _requested_chroma_space(distance_metric: str) -> _ChromaHnswSpace:
    """Соответствие опции бэкенда ключу ``space`` в конфигурации HNSW Chroma."""
    m = (distance_metric or "cosine").lower()
    if m in ("l2", "euclidean"):
        return "l2"
    if m in ("ip", "inner_product"):
        return "ip"
    return "cosine"


def _space_from_hnsw_block(hnsw: Any) -> Optional[str]:
    """Извлечь ключ space из dict или из объекта конфигурации HNSW (разные версии Chroma)."""
    if hnsw is None:
        return None
    if isinstance(hnsw, dict):
        raw = hnsw.get("space")
    else:
        raw = getattr(hnsw, "space", None)
    if raw is None:
        return None
    space = str(raw).strip().lower()
    if space in ("cosine", "l2", "ip"):
        return space
    return None


def _effective_space_from_collection(collection: Any, fallback: str) -> str:
    """Фактическая метрика индекса (после get_or_create она может отличаться от запрошенной)."""
    cfg = getattr(collection, "configuration", None)
    if cfg is not None:
        if isinstance(cfg, dict):
            hnsw = cfg.get("hnsw") or {}
            space = _space_from_hnsw_block(hnsw)
            if space:
                return space
            top = cfg.get("space")
            if isinstance(top, str) and top.strip().lower() in ("cosine", "l2", "ip"):
                return top.strip().lower()
        else:
            hnsw = getattr(cfg, "hnsw", None)
            space = _space_from_hnsw_block(hnsw)
            if space:
                return space

    meta = getattr(collection, "metadata", None) or {}
    legacy = (meta.get("hnsw:space") or meta.get("hnsw_space") or "").strip().lower()
    if legacy in ("cosine", "l2", "ip"):
        return legacy

    resolved = _requested_chroma_space(fallback)
    log.info(
        "ChromaDB: не удалось определить HNSW space коллекции; "
        "distance→score по fallback из настроек: %s",
        resolved,
    )
    return resolved


def chroma_distance_to_similarity(effective_space: str, distance: Any) -> float:
    """Преобразование raw distance из Chroma в score [0, 1] (юнит-тесты без клиента)."""
    if distance is None:
        return 0.0
    d = float(distance)
    space = (effective_space or "cosine").strip().lower()
    if space in ("cosine", "ip"):
        return max(0.0, min(1.0, 1.0 - d))
    return max(0.0, min(1.0, 1.0 / (1.0 + d)))


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
        self._requested_space = _requested_chroma_space(self.distance_metric)

        if persist_directory:
            client = chromadb.PersistentClient(path=persist_directory, **options)
        else:
            client = chromadb.Client(**options)

        self.collection = self._open_collection(
            client,
            collection_name=collection_name,
            requested_space=self._requested_space,
        )
        # Реальная метрика коллекции (уже существующая L2 не станет cosine).
        self._effective_space = _effective_space_from_collection(
            self.collection,
            fallback=self.distance_metric,
        )
        if self._effective_space != self._requested_space:
            log.info(
                "ChromaDB: фактическая метрика коллекции %s (запрошена %s); "
                "маппинг distance→score использует фактическую.",
                self._effective_space,
                self._requested_space,
            )

    def _open_collection(self, client: Any, *, collection_name: str, requested_space: str) -> Any:
        """get_or_create с configuration (Chroma >= 0.5) и fallback на legacy-metadata."""
        legacy_meta: Optional[Dict[str, Any]] = None
        if requested_space == "cosine":
            legacy_meta = {"hnsw:space": "cosine"}

        coll_cfg: Any = None
        try:
            from chromadb.api.collection_configuration import (
                CreateCollectionConfiguration,
                CreateHNSWConfiguration,
            )

            coll_cfg = CreateCollectionConfiguration(
                hnsw=CreateHNSWConfiguration(
                    space=cast(_ChromaHnswSpace, requested_space),
                )
            )
        except Exception:  # pragma: no cover - старая версия chromadb
            coll_cfg = None

        if coll_cfg is not None:
            try:
                return client.get_or_create_collection(
                    name=collection_name,
                    configuration=coll_cfg,
                    metadata=legacy_meta,
                )
            except TypeError:
                pass

        return client.get_or_create_collection(
            name=collection_name,
            metadata=legacy_meta,
        )

    def add_documents(self, documents: Iterable[Document]) -> None:
        docs = list(documents)
        if not docs:
            return
        # upsert: повторная индексация объекта (AUTO_INDEX при save) не должна
        # падать с DuplicateIDError — документ с тем же id перезаписывается.
        self.collection.upsert(
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
            include=["distances", "metadatas", "documents"],
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
            if distance is not None:
                try:
                    # Сырой distance для tie-break сортировки при равных score.
                    meta["vector_distance"] = float(distance)
                except (TypeError, ValueError):
                    pass
            score = self._distance_to_similarity(distance)
            results.append(SearchResult(id=doc_id, score=score, metadata=meta))
        return results

    def _distance_to_similarity(self, distance: Any) -> float:
        """Привести метрику Chroma к сходству в диапазоне [0, 1]."""
        return chroma_distance_to_similarity(self._effective_space, distance)

    def delete(self, doc_ids: Iterable[str]) -> None:
        ids = list(doc_ids)
        if not ids:
            return
        self.collection.delete(ids=ids)

    def clear_collection(self) -> None:
        self.collection.delete(where={})

    def count_documents(self, filters: Optional[Dict[str, Any]] = None) -> int:
        if filters:
            data = self.collection.get(where=filters, include=[])
            ids = data.get("ids") or []
            return len(ids)
        return int(self.collection.count())
