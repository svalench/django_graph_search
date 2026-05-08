"""LangGraph-powered orchestration layer for the search pipeline.

This module is the optional successor to the linear flow currently
implemented in :class:`~django_graph_search.searcher.Searcher.search`. It
wraps the existing components (embedding backend, vector store, LLM backend)
in a small graph so that query analysis, expansion, vector lookup and
reranking can be composed and individually toggled via settings.

Design goals:

* **No hard dependency on the langgraph package.** The pipeline degrades to
  a tiny in-tree runner when LangGraph is not installed. When LangGraph is
  installed we use its ``StateGraph`` so users get streaming, checkpointing
  and tracing for free.
* **Backwards-compatible defaults.** With ``LANGGRAPH.ENABLED = False``
  nothing in this module runs and ``Searcher.search`` behaves exactly as
  before.
* **Stateless nodes.** Every node receives and returns a plain ``dict``
  state, which makes the pipeline trivial to test and serialize.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, TypedDict

from .events import EventHub
from .llm.base import BaseLLMBackend, RerankCandidate
from .settings import GraphSearchConfig

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State definition
# ---------------------------------------------------------------------------


class SearchState(TypedDict, total=False):
    """Mutable bag of values that flows through the search graph."""

    query: str
    normalized_query: str
    expanded_queries: List[str]
    models: Optional[List[str]]
    limit: int
    rerank_top_k: int

    # Results bookkeeping.
    raw_results: List[Any]            # List of vector store ResultItem objects.
    merged_results: List[Any]
    reranked_results: List[Any]
    final_results: List[Any]

    # Diagnostics.
    errors: List[str]
    debug: Dict[str, Any]


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------


def analyze_query_node(state: SearchState, *, config: GraphSearchConfig) -> SearchState:
    """Normalize the query and seed default values.

    Kept deterministic on purpose: this node never calls an LLM, so it stays
    fast and predictable. It enforces the configured ``MAX_QUERY_LENGTH``.
    """
    query = (state.get("query") or "").strip()
    max_len = config.langgraph.max_query_length
    if max_len and len(query) > max_len:
        query = query[:max_len]
    state["normalized_query"] = query
    state.setdefault("expanded_queries", [query] if query else [])
    state.setdefault("debug", {})["normalized_length"] = len(query)
    return state


def expand_query_node(
    state: SearchState,
    *,
    config: GraphSearchConfig,
    llm: BaseLLMBackend,
) -> SearchState:
    """Generate semantic reformulations of the query via the LLM backend.

    On any failure we fall back to ``[normalized_query]`` so the rest of the
    pipeline keeps working — that is the whole point of having a graph.
    """
    base = state.get("normalized_query") or state.get("query", "")
    if not base:
        state["expanded_queries"] = []
        return state
    max_variants = config.langgraph.max_expanded_queries
    try:
        variants = llm.expand_query(base, models=state.get("models"), max_variants=max_variants)
    except Exception as exc:  # noqa: BLE001 - LLM errors must never poison search.
        log.warning("Query expansion failed, falling back to original query: %s", exc)
        state.setdefault("errors", []).append(f"expand_query: {exc}")
        variants = [base]

    # Make sure the original query is always present and dedup while preserving order.
    seen = set()
    ordered: List[str] = []
    for item in [base, *variants]:
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
        if len(ordered) >= max_variants:
            break
    state["expanded_queries"] = ordered
    state.setdefault("debug", {})["expanded_count"] = len(ordered)
    return state


def vector_search_node(
    state: SearchState,
    *,
    embedding_backend,
    vector_store,
) -> SearchState:
    """Run the vector store query for every expanded query and merge hits.

    Results are deduplicated by ``(model, pk)``; we keep the highest score per
    document because the underlying stores can return slightly different
    scores for related queries.
    """
    queries = state.get("expanded_queries") or [state.get("normalized_query") or ""]
    queries = [q for q in queries if q]
    limit = int(state.get("limit") or 0) or 20

    # Multi-query merge keyed by document id.
    merged: Dict[str, Any] = {}
    for q in queries:
        try:
            vec = embedding_backend.embed(q)
            hits = vector_store.search(vec, limit=limit, filters=None)
        except Exception as exc:  # noqa: BLE001
            log.warning("Vector search failed for query=%r: %s", q, exc)
            state.setdefault("errors", []).append(f"vector_search: {exc}")
            continue
        for hit in hits:
            key = _doc_key(hit)
            existing = merged.get(key)
            if existing is None or _score_value(hit) > _score_value(existing):
                merged[key] = hit

    results = list(merged.values())

    models_filter = state.get("models")
    if models_filter:
        allowed = set(models_filter)
        results = [item for item in results if item.metadata.get("model") in allowed]

    # Stable order: best score first.
    results.sort(key=_score_value, reverse=True)

    state["raw_results"] = results
    state["merged_results"] = results
    state.setdefault("debug", {})["candidate_count"] = len(results)
    return state


def rerank_results_node(
    state: SearchState,
    *,
    config: GraphSearchConfig,
    llm: BaseLLMBackend,
) -> SearchState:
    """Optionally rerank the top-K candidates via the LLM backend."""
    candidates = state.get("merged_results") or []
    if not candidates:
        state["reranked_results"] = []
        return state
    top_k = int(state.get("rerank_top_k") or config.langgraph.rerank_top_k)
    head = candidates[:top_k]
    tail = candidates[top_k:]

    rerank_inputs = [
        RerankCandidate(
            id=_doc_key(item),
            text=getattr(item, "text", "") or "",
            score=_score_value(item),
            metadata=dict(item.metadata or {}),
        )
        for item in head
    ]
    try:
        reranked = llm.rerank(
            state.get("normalized_query") or "",
            rerank_inputs,
            top_k=top_k,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Reranking failed, keeping vector order: %s", exc)
        state.setdefault("errors", []).append(f"rerank: {exc}")
        state["reranked_results"] = candidates
        return state

    by_id = {_doc_key(item): item for item in head}
    ordered_head: List[Any] = []
    for cand in reranked:
        item = by_id.pop(cand.id, None)
        if item is not None:
            ordered_head.append(item)
    # Append any items the reranker dropped, preserving original order.
    for item in head:
        key = _doc_key(item)
        if key in by_id:
            ordered_head.append(item)
            by_id.pop(key, None)

    state["reranked_results"] = ordered_head + tail
    return state


def postprocess_results_node(state: SearchState) -> SearchState:
    """Apply ``limit`` and finalize the result list."""
    results = state.get("reranked_results") or state.get("merged_results") or state.get(
        "raw_results"
    ) or []
    limit = int(state.get("limit") or 0)
    if limit and limit > 0:
        results = results[:limit]
    state["final_results"] = list(results)
    return state


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_search_graph(
    config: GraphSearchConfig,
    *,
    embedding_backend,
    vector_store,
    llm: BaseLLMBackend,
    event_hub: Optional[EventHub] = None,
):
    """Build and compile the search graph.

    When the ``langgraph`` package is available we return a compiled
    LangGraph ``StateGraph``. Otherwise we return :class:`_FallbackGraph` so
    the rest of the code stays identical.

    Pass ``event_hub`` to receive lifecycle events (``query_received``,
    ``query_expanded``, ``vector_search_completed``, ``rerank_completed``,
    ``completed``) — the same hub powers the streaming HTTP endpoint.
    """
    try:
        from langgraph.graph import END, StateGraph  # type: ignore
    except Exception:  # pragma: no cover - exercised when langgraph absent.
        return _FallbackGraph(
            config=config,
            embedding_backend=embedding_backend,
            vector_store=vector_store,
            llm=llm,
            event_hub=event_hub,
        )

    def _wrap(name: str, fn):
        if event_hub is None:
            return fn

        def _wrapped(s):
            event_hub.publish({
                "type": f"{name}_started",
                "query": s.get("normalized_query") or s.get("query"),
            })
            out = fn(s)
            candidates = out.get("merged_results") or out.get("raw_results") or []
            event_hub.publish({
                "type": f"{name}_completed",
                "candidate_count": len(candidates),
            })
            return out

        return _wrapped

    graph: Any = StateGraph(dict)
    graph.add_node(
        "analyze_query",
        _wrap("analyze_query", lambda s: analyze_query_node(s, config=config)),
    )
    graph.add_node(
        "expand_query",
        _wrap("expand_query", lambda s: expand_query_node(s, config=config, llm=llm)),
    )
    graph.add_node(
        "vector_search",
        _wrap(
            "vector_search",
            lambda s: vector_search_node(
                s,
                embedding_backend=embedding_backend,
                vector_store=vector_store,
            ),
        ),
    )
    graph.add_node(
        "rerank_results",
        _wrap("rerank_results", lambda s: rerank_results_node(s, config=config, llm=llm)),
    )
    graph.add_node("postprocess_results", _wrap("postprocess_results", postprocess_results_node))

    graph.set_entry_point("analyze_query")
    graph.add_conditional_edges(
        "analyze_query",
        lambda _s: "expand_query" if config.langgraph.query_expansion else "vector_search",
    )
    graph.add_edge("expand_query", "vector_search")
    graph.add_conditional_edges(
        "vector_search",
        lambda _s: "rerank_results" if config.langgraph.reranking else "postprocess_results",
    )
    graph.add_edge("rerank_results", "postprocess_results")
    graph.add_edge("postprocess_results", END)
    return graph.compile()


# ---------------------------------------------------------------------------
# Fallback runner
# ---------------------------------------------------------------------------


class _FallbackGraph:
    """Tiny sequential runner used when the langgraph package is missing.

    It mirrors the conditional structure of :func:`build_search_graph` so
    behaviour stays identical regardless of LangGraph availability.
    """

    def __init__(
        self,
        *,
        config: GraphSearchConfig,
        embedding_backend,
        vector_store,
        llm: BaseLLMBackend,
        event_hub: Optional[EventHub] = None,
    ) -> None:
        self.config = config
        self.embedding_backend = embedding_backend
        self.vector_store = vector_store
        self.llm = llm
        self.event_hub = event_hub

    def _emit(self, event: Dict[str, Any]) -> None:
        if self.event_hub is not None:
            self.event_hub.publish(event)

    def invoke(self, state: SearchState) -> SearchState:
        self._emit({"type": "query_received", "query": state.get("query") or ""})
        state = analyze_query_node(state, config=self.config)
        if self.config.langgraph.query_expansion:
            state = expand_query_node(state, config=self.config, llm=self.llm)
            self._emit({
                "type": "query_expanded",
                "queries": list(state.get("expanded_queries") or []),
            })
        state = vector_search_node(
            state,
            embedding_backend=self.embedding_backend,
            vector_store=self.vector_store,
        )
        self._emit({
            "type": "vector_search_completed",
            "candidate_count": len(state.get("merged_results") or []),
        })
        if self.config.langgraph.reranking:
            state = rerank_results_node(state, config=self.config, llm=self.llm)
            self._emit({
                "type": "rerank_completed",
                "candidate_count": len(state.get("reranked_results") or []),
            })
        state = postprocess_results_node(state)
        self._emit({
            "type": "completed",
            "total": len(state.get("final_results") or []),
        })
        return state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _doc_key(item: Any) -> str:
    md = getattr(item, "metadata", {}) or {}
    return f"{md.get('model')}::{md.get('pk')}"


def _score_value(item: Any) -> float:
    score = getattr(item, "score", None)
    try:
        return float(score) if score is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def resolve_graph_factory(dotted_path: str) -> Callable[..., Any]:
    """Lazily import a graph factory (used by the searcher)."""
    from django.utils.module_loading import import_string

    return import_string(dotted_path)


__all__ = [
    "SearchState",
    "analyze_query_node",
    "expand_query_node",
    "vector_search_node",
    "rerank_results_node",
    "postprocess_results_node",
    "build_search_graph",
    "resolve_graph_factory",
]
