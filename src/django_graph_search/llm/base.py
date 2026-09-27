"""Base contract for pluggable LLM backends used by the LangGraph pipeline."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence


@dataclass(frozen=True)
class RerankCandidate:
    """Lightweight view of a search hit passed to a reranker.

    The reranker only needs a stable identifier, the textual content and the
    original score. We deliberately avoid leaking ORM objects into the LLM
    layer, which keeps backends serializable and easier to reason about.
    """

    id: str
    text: str
    score: float
    metadata: Dict[str, Any]


class BaseLLMBackend(ABC):
    """Minimal interface every LLM backend implements.

    Backends should be cheap to instantiate; heavy clients (HTTP sessions,
    model warm-up, ...) belong in the constructor so we pay the cost once and
    reuse the instance from the orchestrator.
    """

    def __init__(self, model: Optional[str] = None, **options: Any) -> None:
        self.model = model
        self.options = options
        # Таймаут одного вызова LLM в секундах. Заполняется фабрикой из
        # LANGGRAPH.TIMEOUT_SECONDS, если бэкенд не задал его сам через OPTIONS.
        # Реальные бэкенды должны пробрасывать его в свой HTTP-клиент; узлы графа
        # дополнительно принудительно прерывают ожидание по истечении срока.
        raw_timeout = options.get("timeout")
        self.timeout: Optional[float] = float(raw_timeout) if raw_timeout else None

    @abstractmethod
    def expand_query(
        self,
        query: str,
        models: Optional[Iterable[str]] = None,
        max_variants: int = 3,
    ) -> List[str]:
        """Return up to ``max_variants`` semantic reformulations of ``query``.

        Implementations MUST return at least the original query in the result
        so callers can blindly iterate the returned list and never lose the
        user's intent.
        """

    @abstractmethod
    def rerank(
        self,
        query: str,
        candidates: Sequence[RerankCandidate],
        top_k: Optional[int] = None,
    ) -> List[RerankCandidate]:
        """Reorder ``candidates`` by relevance to ``query``.

        Implementations MUST be tolerant of empty inputs and never raise on
        non-fatal errors — return the input order on failure instead.
        """

    # Optional capability hints. Subclasses can override them so the
    # orchestrator can decide whether it is worth calling a node at all.
    def supports_expansion(self) -> bool:
        return True

    def supports_rerank(self) -> bool:
        return True
