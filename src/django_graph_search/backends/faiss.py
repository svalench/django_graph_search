from __future__ import annotations

import logging
import os
import pickle
import threading
from typing import Any, Dict, Iterable, List, Optional

from ..exceptions import BackendError
from .base import BaseVectorStore, Document, SearchResult

log = logging.getLogger(__name__)


class FaissBackend(BaseVectorStore):
    """In-process FAISS flat index with optional disk persistence.

    Options:
        persist_path: путь к pickle-файлу, куда сохраняются (ids, metadata,
            embeddings) после каждой мутации. Без него индекс живёт только в
            памяти процесса и теряется при рестарте.

    Security:
        ``persist_path`` загружается через ``pickle.load`` — указывайте только
        доверенный локальный путь, недоступный для записи посторонним
        (подмена файла = remote code execution).
    """

    def __init__(self, persist_path: Optional[str] = None, **options: Any) -> None:
        self.options = options
        self.persist_path = persist_path
        self.index = None
        self._ids: List[str] = []
        self._metas: List[Dict[str, Any]] = []
        self._embeddings: List[List[float]] = []
        self._lock = threading.Lock()
        if self.persist_path:
            self._load()

    # ------------------------------------------------------------- persistence

    def _load(self) -> None:
        path = self.persist_path
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, "rb") as handle:
                payload = pickle.load(handle)
            self._ids = list(payload.get("ids") or [])
            self._metas = list(payload.get("metas") or [])
            self._embeddings = list(payload.get("embeddings") or [])
            if self._embeddings:
                self._ensure_index(len(self._embeddings[0]))
                self._rebuild_index_locked()
            log.info("FAISS: loaded %d documents from %s", len(self._ids), path)
        except Exception as exc:  # noqa: BLE001
            log.warning("FAISS: failed to load %s (%s); starting empty", path, exc)
            self._ids = []
            self._metas = []
            self._embeddings = []
            self.index = None

    def _persist(self) -> None:
        path = self.persist_path
        if not path:
            return
        payload = {
            "ids": self._ids,
            "metas": self._metas,
            "embeddings": self._embeddings,
        }
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "wb") as handle:
            pickle.dump(payload, handle)
        os.replace(tmp_path, path)

    # ----------------------------------------------------------------- helpers

    def _ensure_index(self, dim: int):
        if self.index is not None:
            return
        try:
            import faiss
        except Exception as exc:  # pragma: no cover - dependency error
            raise BackendError("faiss-cpu is not installed.") from exc
        self.index = faiss.IndexFlatL2(dim)

    def _rebuild_index_locked(self) -> None:
        """Пересоздать FAISS-индекс из текущих embeddings (вызывать под lock)."""
        if not self._embeddings:
            self.index = None
            return
        import numpy as np

        dim = len(self._embeddings[0])
        self.index = None
        self._ensure_index(dim)
        if self.index is None:  # pragma: no cover - _ensure_index raises otherwise
            raise BackendError("FAISS index was not initialized.")
        self.index.add(np.array(self._embeddings, dtype="float32"))

    # ------------------------------------------------------------------ CRUD

    def add_documents(self, documents: Iterable[Document]) -> None:
        docs = list(documents)
        if not docs:
            return
        # Внутри батча last-wins: два документа с одним id не должны
        # оба попасть в индекс.
        last_index: Dict[str, int] = {}
        for idx, doc in enumerate(docs):
            last_index[doc.id] = idx
        docs = [doc for idx, doc in enumerate(docs) if last_index[doc.id] == idx]
        with self._lock:
            # Upsert-семантика: документы с существующими id сначала удаляются,
            # иначе повторная индексация создавала бы дубликаты.
            incoming = {doc.id for doc in docs}
            removed = bool(incoming) and bool(incoming.intersection(self._ids))
            if removed:
                remaining = [
                    (doc_id, meta, embedding)
                    for doc_id, meta, embedding in zip(self._ids, self._metas, self._embeddings)
                    if doc_id not in incoming
                ]
                self._ids = [item[0] for item in remaining]
                self._metas = [item[1] for item in remaining]
                self._embeddings = [item[2] for item in remaining]
            self._ids.extend([doc.id for doc in docs])
            self._metas.extend([doc.metadata for doc in docs])
            self._embeddings.extend([doc.embedding for doc in docs])
            import numpy as np

            new_vectors = np.array([doc.embedding for doc in docs], dtype="float32")
            if removed or self.index is None:
                # После удаления позиции в плоском индексе съехали — пересборка.
                self._rebuild_index_locked()
            else:
                self.index.add(new_vectors)
            self._persist()

    def search(
        self,
        query_vector: List[float],
        limit: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        with self._lock:
            if self.index is None or not self._ids:
                return []
            import numpy as np

            query = np.array([query_vector], dtype="float32")
            # При фильтрах over-fetch: FAISS не умеет where-условия.
            # Если после первого прохода не набрали limit — полный scan.
            n_total = len(self._ids)
            if not filters:
                fetch_plan = [limit]
            else:
                first = min(n_total, max(limit * 10, limit))
                fetch_plan = [first]
                if first < n_total:
                    fetch_plan.append(n_total)

            results: List[SearchResult] = []
            for fetch in fetch_plan:
                distances, indices = self.index.search(query, fetch)
                results = []
                for idx, dist in zip(indices[0], distances[0]):
                    if idx < 0 or idx >= n_total:
                        continue
                    metadata = dict(self._metas[idx])
                    if filters and not self._match_filters(metadata, filters):
                        continue
                    fdist = float(dist)
                    metadata["vector_distance"] = fdist
                    results.append(
                        SearchResult(
                            id=self._ids[idx],
                            score=max(0.0, min(1.0, 1.0 / (1.0 + fdist))),
                            metadata=metadata,
                        )
                    )
                    if len(results) >= limit:
                        return results
                if len(results) >= limit or fetch >= n_total:
                    break
            return results

    def delete(self, doc_ids: Iterable[str]) -> None:
        ids = set(doc_ids)
        if not ids:
            return
        with self._lock:
            remaining = [
                (doc_id, meta, embedding)
                for doc_id, meta, embedding in zip(self._ids, self._metas, self._embeddings)
                if doc_id not in ids
            ]
            if len(remaining) == len(self._ids):
                return
            self._ids = [item[0] for item in remaining]
            self._metas = [item[1] for item in remaining]
            self._embeddings = [item[2] for item in remaining]
            self._rebuild_index_locked()
            self._persist()

    def clear_collection(self) -> None:
        with self._lock:
            self.index = None
            self._ids = []
            self._metas = []
            self._embeddings = []
            self._persist()

    def count_documents(self, filters: Optional[Dict[str, Any]] = None) -> int:
        with self._lock:
            if not self._metas:
                return 0
            if filters is None:
                return len(self._metas)
            return sum(1 for m in self._metas if self._match_filters(m, filters))

    def _match_filters(self, metadata: Dict[str, Any], filters: Dict[str, Any]) -> bool:
        for key, value in filters.items():
            if metadata.get(key) != value:
                return False
        return True
