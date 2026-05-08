"""Deterministic, dependency-free LLM backend used as a safe default.

Real LLM backends bring heavy dependencies and network calls. The dummy
backend lets the rest of the pipeline be exercised in unit tests, in CI and
in early-stage projects without any external service. It is deliberately
boring: query expansion produces simple morphological variants, and reranking
is a stable sort by score.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence

from .base import BaseLLMBackend, RerankCandidate


class DummyLLMBackend(BaseLLMBackend):
    """Tiny, predictable LLM stub.

    The backend never raises and never makes a network call. It is suitable
    as a fallback when ``LANGGRAPH.LLM.BACKEND`` is unset and as a
    deterministic baseline in tests.
    """

    _WORD_RE = re.compile(r"\w+", re.UNICODE)

    def expand_query(
        self,
        query: str,
        models: Optional[Iterable[str]] = None,
        max_variants: int = 3,
    ) -> List[str]:
        cleaned = (query or "").strip()
        if not cleaned:
            return [""]
        variants: List[str] = [cleaned]
        lowered = cleaned.lower()
        if lowered != cleaned:
            variants.append(lowered)
        # Strip punctuation as a tiny normalisation variant.
        words = self._WORD_RE.findall(cleaned)
        if words:
            joined = " ".join(words)
            if joined and joined not in variants:
                variants.append(joined)
        # Deduplicate while preserving order.
        seen = set()
        ordered: List[str] = []
        for item in variants:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
            if len(ordered) >= max(1, max_variants):
                break
        return ordered

    def rerank(
        self,
        query: str,
        candidates: Sequence[RerankCandidate],
        top_k: Optional[int] = None,
    ) -> List[RerankCandidate]:
        if not candidates:
            return []
        # Stable sort: highest score first. ``score`` is similarity-like; for
        # distance-based stores it is up to the vector store to flip the sign.
        ordered = sorted(candidates, key=lambda c: c.score, reverse=True)
        if top_k is not None and top_k > 0:
            ordered = ordered[:top_k]
        return ordered
