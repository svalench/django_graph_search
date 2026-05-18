"""
OpenAI embeddings backend (extra ``[openai]``).

Конфигурация::

    "EMBEDDINGS": {
        "default": {
            "BACKEND": "django_graph_search.embeddings.openai_backend.OpenAIEmbeddingBackend",
            "MODEL_NAME": "text-embedding-3-small",
            "OPTIONS": {
                "api_key": "...",
                "dimensions": 1536,
                "batch_size": 100,
                "timeout": 30,
                "max_retries": 3,
            },
        },
    }
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List

from ..exceptions import BackendError
from .base import BaseEmbeddingBackend

log = logging.getLogger(__name__)


class OpenAIEmbeddingBackend(BaseEmbeddingBackend):
    """Эмбеддинги через OpenAI API (ленивый импорт пакета ``openai``)."""

    KNOWN_DIMENSIONS: Dict[str, int] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(self, model_name: str, **options: Any) -> None:
        self.model_name = model_name
        self.api_key = options.get("api_key") or os.environ.get("OPENAI_API_KEY")
        self.dimensions = options.get("dimensions") or self.KNOWN_DIMENSIONS.get(model_name, 1536)
        self.batch_size = int(options.get("batch_size", 100))
        self.timeout = float(options.get("timeout", 30))
        self.max_retries = int(options.get("max_retries", 3))
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise BackendError(
                    "openai package is required for OpenAIEmbeddingBackend. "
                    "Install: pip install django-graph-search[openai]"
                ) from exc
            self._client = OpenAI(
                api_key=self.api_key,
                timeout=self.timeout,
                max_retries=self.max_retries,
            )
        return self._client

    def embed(self, text: str, *, is_query: bool = False) -> List[float]:
        return self.embed_batch([text], is_query=is_query)[0]

    def embed_batch(self, texts: Iterable[str], *, is_query: bool = False) -> List[List[float]]:
        del is_query  # OpenAI — симметричные эмбеддинги
        texts_list = list(texts)
        if not texts_list:
            return []
        client = self._get_client()
        results: List[List[float]] = []
        for i in range(0, len(texts_list), self.batch_size):
            batch = texts_list[i : i + self.batch_size]
            cleaned = [t.replace("\n", " ") for t in batch]
            kwargs: Dict[str, Any] = {
                "input": cleaned,
                "model": self.model_name,
            }
            if "text-embedding-3" in self.model_name and self.dimensions:
                kwargs["dimensions"] = self.dimensions
            try:
                response = client.embeddings.create(**kwargs)
                for item in sorted(response.data, key=lambda x: x.index):
                    results.append(list(item.embedding))
            except Exception as exc:  # noqa: BLE001
                log.error("OpenAI embeddings API failed: %s", exc, exc_info=True)
                raise
        return results
