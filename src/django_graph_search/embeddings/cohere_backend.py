"""
Cohere embeddings backend (extra ``[cohere]``).

Для асимметричного поиска Cohere требует ``input_type``:
``search_document`` при индексации, ``search_query`` для запросов.
Параметр ``is_query`` у :meth:`embed` / :meth:`embed_batch` переключает режим.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable, List

from ..exceptions import BackendError
from .base import BaseEmbeddingBackend

log = logging.getLogger(__name__)


class CohereEmbeddingBackend(BaseEmbeddingBackend):
    """Эмбеддинги Cohere (ленивый импорт пакета ``cohere``)."""

    DEFAULT_MODEL = "embed-multilingual-v3.0"

    def __init__(self, model_name: str, **options: Any) -> None:
        self.model_name = model_name or self.DEFAULT_MODEL
        self.api_key = options.get("api_key") or os.environ.get("COHERE_API_KEY")
        self.batch_size = int(options.get("batch_size", 96))
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import cohere
            except ImportError as exc:
                raise BackendError(
                    "cohere package is required for CohereEmbeddingBackend. "
                    "Install: pip install django-graph-search[cohere]"
                ) from exc
            self._client = cohere.Client(api_key=self.api_key)
        return self._client

    def _input_type(self, *, is_query: bool) -> str:
        return "search_query" if is_query else "search_document"

    def embed(self, text: str, *, is_query: bool = False) -> List[float]:
        return self.embed_batch([text], is_query=is_query)[0]

    def embed_batch(self, texts: Iterable[str], *, is_query: bool = False) -> List[List[float]]:
        texts_list = list(texts)
        if not texts_list:
            return []
        client = self._get_client()
        input_type = self._input_type(is_query=is_query)
        results: List[List[float]] = []
        for i in range(0, len(texts_list), self.batch_size):
            batch = texts_list[i : i + self.batch_size]
            try:
                resp = client.embed(
                    texts=batch,
                    model=self.model_name,
                    input_type=input_type,
                    embedding_types=["float"],
                )
                emb = resp.embeddings
                if emb is None:
                    raise BackendError("Cohere embed response missing embeddings.")
                floats = getattr(emb, "float", None)
                if floats is None and isinstance(emb, list):
                    vecs = emb
                elif floats is not None:
                    vecs = floats
                else:
                    vecs = list(emb)
                results.extend([list(row) for row in vecs])
            except BackendError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("Cohere embeddings API failed: %s", exc, exc_info=True)
                raise
        return results
