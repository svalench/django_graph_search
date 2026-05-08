"""Conversational search graph.

The graph is a thin shell on top of the existing :class:`Searcher`. It is
search-first, not chat-first: nodes are deterministic by default, never
hallucinate filters, and surface a structured ``clarification_needed`` flag
when the follow-up query is too ambiguous.

Pipeline:

```
load_context \u2192 interpret_followup \u2192 maybe_clarify \u2192 [execute_search] \u2192 store_context
```

* ``load_context`` reads the recent history from the memory backend.
* ``interpret_followup`` rewrites short follow-ups using the previous turn
  (\"show me more\", \"only products\", \"what about cheaper ones\").
* ``maybe_clarify`` decides whether the rewritten query is meaningful enough
  to run a search, otherwise returns ``clarification_needed=True``.
* ``execute_search`` delegates to the existing searcher (which itself can be
  the LangGraph search pipeline if enabled).
* ``store_context`` persists the user query and a compact view of results
  back into the memory backend.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, TypedDict

from .memory.base import BaseMemoryBackend, ConversationEvent
from .settings import GraphSearchConfig

log = logging.getLogger(__name__)


class ConversationState(TypedDict, total=False):
    conversation_id: str
    raw_query: str
    interpreted_query: str
    history: List[ConversationEvent]
    models: Optional[List[str]]
    limit: int
    clarification_needed: bool
    clarification_message: str
    results: List[Dict[str, Any]]
    debug: Dict[str, Any]


# Heuristics for short follow-ups. Patterns are intentionally conservative
# \u2014 the goal is to avoid hallucinating user intent.
_FOLLOWUP_MORE = re.compile(
    r"^(more|еще|ещё|ещё\s+пожалуйста|показать ещё|show me more|next)\b",
    re.IGNORECASE,
)
_FOLLOWUP_SIMILAR = re.compile(
    r"^(similar|похож|аналог|like that|something else)\b", re.IGNORECASE
)
_FOLLOWUP_FILTER = re.compile(
    r"^(only|just|filter|только)\s+(?P<value>[\w\.\- ]+)$", re.IGNORECASE
)


def load_context_node(
    state: ConversationState, *, memory: BaseMemoryBackend
) -> ConversationState:
    cid = state.get("conversation_id") or str(uuid.uuid4())
    state["conversation_id"] = cid
    state["history"] = memory.get_history(cid)
    state.setdefault("debug", {})["history_size"] = len(state["history"])
    return state


def interpret_followup_node(
    state: ConversationState,
    *,
    config: GraphSearchConfig,
) -> ConversationState:
    """Resolve short follow-ups using the previous turn.

    Returns the original query verbatim when no follow-up pattern matches.
    Never invents filters that have no backing in the history.
    """
    raw = (state.get("raw_query") or "").strip()
    history = state.get("history") or []
    last_user = _last_user_event(history)
    interpreted = raw

    if raw and last_user is not None:
        m = _FOLLOWUP_FILTER.match(raw)
        if m:
            # 'only X' - reuse previous query, narrow models if value matches.
            base = last_user.interpreted_query or last_user.query
            interpreted = base
            state["models"] = _filter_models_to_value(
                m.group("value").strip(),
                last_user.models or [],
            )
        elif _FOLLOWUP_MORE.match(raw):
            interpreted = last_user.interpreted_query or last_user.query
        elif _FOLLOWUP_SIMILAR.match(raw):
            interpreted = last_user.interpreted_query or last_user.query
        elif len(raw) <= config.conversational.min_query_length_for_autosearch:
            # Very short query \u2014 lean on previous context.
            base = last_user.interpreted_query or last_user.query
            interpreted = f"{base} {raw}".strip() if base else raw

    if not state.get("models") and last_user is not None and last_user.models:
        state.setdefault("models", last_user.models)

    state["interpreted_query"] = interpreted
    state.setdefault("debug", {})["followup_resolved"] = interpreted != raw
    return state


def maybe_clarify_node(
    state: ConversationState, *, config: GraphSearchConfig
) -> ConversationState:
    interpreted = (state.get("interpreted_query") or "").strip()
    if not config.conversational.allow_clarifications:
        state["clarification_needed"] = False
        return state
    too_short = len(interpreted) < config.conversational.min_query_length_for_autosearch
    has_history = bool(state.get("history"))
    if too_short and not has_history:
        state["clarification_needed"] = True
        state["clarification_message"] = (
            "Could you give a bit more context? The query is too short to search reliably."
        )
        state.setdefault("results", [])
    else:
        state["clarification_needed"] = False
    return state


def execute_search_node(
    state: ConversationState,
    *,
    searcher,
) -> ConversationState:
    if state.get("clarification_needed"):
        state["results"] = []
        return state
    interpreted = state.get("interpreted_query") or ""
    if not interpreted.strip():
        state["results"] = []
        return state
    try:
        results = searcher.search(
            interpreted,
            models=state.get("models"),
            limit=int(state.get("limit") or 0) or None,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Conversational search failed: %s", exc)
        state.setdefault("debug", {})["search_error"] = str(exc)
        results = []
    state["results"] = results
    return state


def store_context_node(
    state: ConversationState, *, memory: BaseMemoryBackend
) -> ConversationState:
    cid = state["conversation_id"]
    user_event = ConversationEvent(
        role="user",
        query=state.get("raw_query", ""),
        interpreted_query=state.get("interpreted_query", ""),
        models=state.get("models"),
        top_results=_compact_results(state.get("results") or []),
        clarification_needed=bool(state.get("clarification_needed", False)),
    )
    memory.append_event(cid, user_event)
    return state


def build_conversation_graph(
    config: GraphSearchConfig, *, searcher, memory: BaseMemoryBackend
):
    """Compile the conversational graph (LangGraph or fallback runner)."""
    try:
        from langgraph.graph import END, StateGraph  # type: ignore
    except Exception:  # pragma: no cover - exercised when langgraph absent.
        return _FallbackConversationGraph(
            config=config, searcher=searcher, memory=memory
        )

    graph: Any = StateGraph(dict)
    graph.add_node("load_context", lambda s: load_context_node(s, memory=memory))
    graph.add_node("interpret_followup", lambda s: interpret_followup_node(s, config=config))
    graph.add_node("maybe_clarify", lambda s: maybe_clarify_node(s, config=config))
    graph.add_node("execute_search", lambda s: execute_search_node(s, searcher=searcher))
    graph.add_node("store_context", lambda s: store_context_node(s, memory=memory))

    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "interpret_followup")
    graph.add_edge("interpret_followup", "maybe_clarify")
    graph.add_conditional_edges(
        "maybe_clarify",
        lambda s: "store_context" if s.get("clarification_needed") else "execute_search",
    )
    graph.add_edge("execute_search", "store_context")
    graph.add_edge("store_context", END)
    return graph.compile()


class _FallbackConversationGraph:
    def __init__(
        self,
        *,
        config: GraphSearchConfig,
        searcher,
        memory: BaseMemoryBackend,
    ) -> None:
        self.config = config
        self.searcher = searcher
        self.memory = memory

    def invoke(self, state: ConversationState) -> ConversationState:
        state = load_context_node(state, memory=self.memory)
        state = interpret_followup_node(state, config=self.config)
        state = maybe_clarify_node(state, config=self.config)
        if not state.get("clarification_needed"):
            state = execute_search_node(state, searcher=self.searcher)
        state = store_context_node(state, memory=self.memory)
        return state


def _last_user_event(
    history: Iterable[ConversationEvent],
) -> Optional[ConversationEvent]:
    last: Optional[ConversationEvent] = None
    for event in history:
        if event.role == "user":
            last = event
    return last


def _filter_models_to_value(value: str, candidates: List[str]) -> List[str]:
    """Return candidates that mention ``value``; otherwise leave unchanged."""
    if not candidates:
        return []
    needle = value.strip().lower()
    matched = [c for c in candidates if needle in c.lower()]
    return matched or candidates


def _compact_results(results: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop heavy fields before persisting results to memory."""
    compact: List[Dict[str, Any]] = []
    for item in list(results)[:5]:
        compact.append({
            "model": item.get("model"),
            "pk": item.get("pk"),
            "score": item.get("score"),
        })
    return compact


__all__ = [
    "ConversationState",
    "load_context_node",
    "interpret_followup_node",
    "maybe_clarify_node",
    "execute_search_node",
    "store_context_node",
    "build_conversation_graph",
]
