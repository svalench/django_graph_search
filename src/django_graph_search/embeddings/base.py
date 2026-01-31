from abc import ABC, abstractmethod
from typing import Iterable, List


class BaseEmbeddingBackend(ABC):
    @abstractmethod
    def embed(self, text: str) -> List[float]:
        raise NotImplementedError

    @abstractmethod
    def embed_batch(self, texts: Iterable[str]) -> List[List[float]]:
        raise NotImplementedError

