"""LANGGRAPH.TIMEOUT_SECONDS: проброс в LLM-бэкенд и жёсткий лимит вызова в узлах графа."""
from __future__ import annotations

import threading
import time
from typing import Iterable, List, Optional, Sequence

import pytest

from django_graph_search.langgraph_agent import (
    LLMTimeoutError,
    SearchState,
    call_with_timeout,
    expand_query_node,
    rerank_results_node,
)
from django_graph_search.llm import DummyLLMBackend, build_llm_backend
from django_graph_search.llm.base import BaseLLMBackend, RerankCandidate
from django_graph_search.settings import LangGraphConfig, LLMConfig

from .utils import make_basic_config


class SlowLLM(BaseLLMBackend):
    """Бэкенд, который «зависает» дольше таймаута."""

    def __init__(self, delay: float, **options) -> None:
        super().__init__(**options)
        self.delay = delay
        self.finished = threading.Event()

    def expand_query(
        self,
        query: str,
        models: Optional[Iterable[str]] = None,
        max_variants: int = 3,
    ) -> List[str]:
        time.sleep(self.delay)
        self.finished.set()
        return [query, f"{query} slow"]

    def rerank(
        self,
        query: str,
        candidates: Sequence[RerankCandidate],
        top_k: Optional[int] = None,
    ) -> List[RerankCandidate]:
        time.sleep(self.delay)
        self.finished.set()
        return list(reversed(candidates))


def _cfg(timeout_seconds: int = 1) -> object:
    from dataclasses import replace

    base = make_basic_config(delta_indexing=False)
    return replace(
        base,
        langgraph=LangGraphConfig(
            enabled=True,
            query_expansion=True,
            reranking=True,
            timeout_seconds=timeout_seconds,
        ),
    )


# ------------------------------------------------------------- фабрика


def test_factory_sets_timeout_from_langgraph_config():
    backend = build_llm_backend(None, timeout=15)
    assert isinstance(backend, DummyLLMBackend)
    assert backend.timeout == 15.0


def test_factory_keeps_explicit_backend_timeout():
    cfg = LLMConfig(
        backend="tests.test_llm_timeout.SlowLLM",
        model=None,
        options={"delay": 0.0, "timeout": 3},
    )
    backend = build_llm_backend(cfg, timeout=15)
    assert backend.timeout == 3.0


# ----------------------------------------------------- call_with_timeout


def test_call_with_timeout_returns_value():
    assert call_with_timeout(lambda: 42, timeout=1.0) == 42


def test_call_with_timeout_propagates_exception():
    def _boom():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        call_with_timeout(_boom, timeout=1.0)


def test_call_with_timeout_raises_on_overrun():
    started = time.monotonic()
    with pytest.raises(LLMTimeoutError):
        call_with_timeout(lambda: time.sleep(2.0), timeout=0.1)
    # Ждали примерно timeout, а не полный sleep.
    assert time.monotonic() - started < 1.0


def test_call_with_timeout_without_limit_runs_inline():
    ident = call_with_timeout(threading.get_ident, timeout=None)
    assert ident == threading.get_ident()


# ------------------------------------------------------------------ узлы


def test_expand_query_node_falls_back_on_timeout():
    llm = SlowLLM(delay=2.0)
    cfg = _cfg(timeout_seconds=1)
    llm.timeout = 0.1  # ускоряем тест: явный таймаут бэкенда приоритетнее
    state: SearchState = {"normalized_query": "phone", "models": None}
    started = time.monotonic()
    out = expand_query_node(state, config=cfg, llm=llm)
    assert time.monotonic() - started < 1.0
    assert out["expanded_queries"] == ["phone"]
    assert any("expand_query" in e for e in out.get("errors", []))


def test_rerank_node_keeps_vector_order_on_timeout():
    from django_graph_search.backends.base import SearchResult

    llm = SlowLLM(delay=2.0)
    llm.timeout = 0.1
    cfg = _cfg(timeout_seconds=1)
    candidates = [
        SearchResult(id="m:1", score=0.9, metadata={"model": "m", "pk": 1, "text": "a"}),
        SearchResult(id="m:2", score=0.8, metadata={"model": "m", "pk": 2, "text": "b"}),
    ]
    state: SearchState = {"merged_results": candidates, "normalized_query": "q"}
    out = rerank_results_node(state, config=cfg, llm=llm)
    assert [c.id for c in out["reranked_results"]] == ["m:1", "m:2"]
    assert any("rerank" in e for e in out.get("errors", []))


def test_expand_query_node_uses_config_timeout_when_backend_has_none():
    """Без явного timeout у бэкенда действует LANGGRAPH.TIMEOUT_SECONDS."""
    llm = SlowLLM(delay=0.0)
    assert llm.timeout is None
    cfg = _cfg(timeout_seconds=5)
    state: SearchState = {"normalized_query": "phone"}
    out = expand_query_node(state, config=cfg, llm=llm)
    assert out["expanded_queries"] == ["phone", "phone slow"]
    assert llm.finished.is_set()
