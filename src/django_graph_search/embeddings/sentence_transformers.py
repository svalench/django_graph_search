from __future__ import annotations

from typing import Iterable, List, Optional

from ..exceptions import BackendError
from .base import BaseEmbeddingBackend


class SentenceTransformerBackend(BaseEmbeddingBackend):
    def __init__(self, model_name: str, **options: object) -> None:
        self.model_name = model_name
        self.options = options
        self._model = None

    def _get_model(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:  # pragma: no cover - dependency error
            raise BackendError("sentence-transformers is not installed.") from exc
        self._model = SentenceTransformer(self.model_name, **self.options)
        return self._model

    def embed(self, text: str) -> List[float]:
        model = self._get_model()
        return model.encode(text, convert_to_numpy=True).tolist()

    def embed_batch(self, texts: Iterable[str]) -> List[List[float]]:
        model = self._get_model()
        return model.encode(list(texts), convert_to_numpy=True).tolist()

