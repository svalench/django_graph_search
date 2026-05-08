"""Tests for the optional conversational search endpoint and graph."""
# pylint: disable=redefined-outer-name
from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest import mock

import pytest
from django.conf import settings as django_settings
from django.test import RequestFactory

from django_graph_search.langgraph_conversation import (
    ConversationState,
    build_conversation_graph,
    interpret_followup_node,
    maybe_clarify_node,
)
from django_graph_search.memory import (
    BaseMemoryBackend,
    ConversationEvent,
    InMemoryBackend,
    build_memory_backend,
)
from django_graph_search.settings import get_settings
from django_graph_search.views import ConversationalSearchAPIView


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def graph_search_settings():
    original = getattr(django_settings, "GRAPH_SEARCH", None)
    get_settings.cache_clear()
    # Reset the per-process memory cache between tests so each test starts
    # from a clean slate.
    ConversationalSearchAPIView._memory_cache.clear()

    def _apply(payload):
        django_settings.GRAPH_SEARCH = payload
        get_settings.cache_clear()
        return get_settings()

    yield _apply

    if original is None and hasattr(django_settings, "GRAPH_SEARCH"):
        delattr(django_settings, "GRAPH_SEARCH")
    elif original is not None:
        django_settings.GRAPH_SEARCH = original
    get_settings.cache_clear()
    ConversationalSearchAPIView._memory_cache.clear()


def _base_settings(extra=None):
    payload = {
        "MODELS": [],
        "VECTOR_STORE": {"BACKEND": "django_graph_search.backends.ChromaDBBackend"},
        "EMBEDDINGS": {
            "default": {
                "BACKEND": "tests.dummy_embedding_backend.DummyEmbeddingBackend",
                "MODEL_NAME": "x",
            }
        },
        "CONVERSATIONAL": {"ENABLED": True},
    }
    if extra:
        payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Memory backends
# ---------------------------------------------------------------------------


def test_in_memory_backend_appends_and_truncates():
    backend = InMemoryBackend(max_history_items=2)
    for i in range(3):
        backend.append_event(
            "s1", ConversationEvent(role="user", query=f"q{i}", interpreted_query=f"q{i}")
        )
    history = backend.get_history("s1")
    assert [e.query for e in history] == ["q1", "q2"]


def test_in_memory_backend_clear_history():
    backend = InMemoryBackend(max_history_items=5)
    backend.append_event("s1", ConversationEvent(role="user", query="q"))
    backend.clear_history("s1")
    assert not backend.get_history("s1")


def test_factory_returns_in_memory_for_alias():
    backend = build_memory_backend("inmemory", max_history_items=3)
    assert isinstance(backend, InMemoryBackend)
    assert backend.max_history_items == 3


def test_conversation_event_round_trip():
    ev = ConversationEvent(role="user", query="x", models=["m"], top_results=[{"pk": 1}])
    other = ConversationEvent.from_dict(ev.to_dict())
    assert other.query == "x"
    assert other.models == ["m"]
    assert other.top_results == [{"pk": 1}]


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


class _StubSearcher:
    def __init__(self, results):
        self._results = results
        self.calls: List[Dict[str, Any]] = []

    def search(self, query, models=None, limit=None):
        self.calls.append({"query": query, "models": models, "limit": limit})
        return list(self._results)


def test_interpret_followup_uses_previous_query_for_short_input(graph_search_settings):
    cfg = graph_search_settings(_base_settings())
    history = [
        ConversationEvent(role="user", query="red phone", interpreted_query="red phone"),
    ]
    state: ConversationState = {
        "raw_query": "more",
        "history": history,
    }
    out = interpret_followup_node(state, config=cfg)
    assert out["interpreted_query"] == "red phone"


def test_interpret_followup_only_filter_narrows_models(graph_search_settings):
    cfg = graph_search_settings(_base_settings())
    history = [
        ConversationEvent(
            role="user",
            query="red phone",
            interpreted_query="red phone",
            models=["shop.Product", "blog.Post"],
        ),
    ]
    state: ConversationState = {
        "raw_query": "only Product",
        "history": history,
    }
    out = interpret_followup_node(state, config=cfg)
    assert out["interpreted_query"] == "red phone"
    assert out["models"] == ["shop.Product"]


def test_maybe_clarify_triggers_for_too_short_query_without_history(graph_search_settings):
    cfg = graph_search_settings(_base_settings({
        "CONVERSATIONAL": {
            "ENABLED": True,
            "MIN_QUERY_LENGTH_FOR_AUTOSEARCH": 3,
        },
    }))
    state: ConversationState = {"interpreted_query": "x", "history": []}
    out = maybe_clarify_node(state, config=cfg)
    assert out["clarification_needed"] is True
    assert out.get("clarification_message")


def test_maybe_clarify_skipped_when_disabled(graph_search_settings):
    cfg = graph_search_settings(_base_settings({
        "CONVERSATIONAL": {
            "ENABLED": True,
            "ALLOW_CLARIFICATIONS": False,
        },
    }))
    state: ConversationState = {"interpreted_query": "x", "history": []}
    out = maybe_clarify_node(state, config=cfg)
    assert out["clarification_needed"] is False


# ---------------------------------------------------------------------------
# Graph (fallback runner) end-to-end
# ---------------------------------------------------------------------------


def test_graph_executes_search_and_persists_history(graph_search_settings):
    cfg = graph_search_settings(_base_settings())
    memory = InMemoryBackend(max_history_items=5)
    searcher = _StubSearcher([{"model": "shop.Product", "pk": 1, "score": 0.9}])
    graph = build_conversation_graph(cfg, searcher=searcher, memory=memory)

    out = graph.invoke({"raw_query": "red phone", "limit": 5})
    cid = out["conversation_id"]
    assert out["results"][0]["pk"] == 1
    assert searcher.calls and searcher.calls[0]["query"] == "red phone"
    history = memory.get_history(cid)
    assert len(history) == 1 and history[0].query == "red phone"


def test_graph_followup_uses_previous_context(graph_search_settings):
    cfg = graph_search_settings(_base_settings())
    memory = InMemoryBackend(max_history_items=5)
    searcher = _StubSearcher([{"model": "shop.Product", "pk": 1, "score": 0.9}])
    graph = build_conversation_graph(cfg, searcher=searcher, memory=memory)

    first = graph.invoke({"raw_query": "red phone", "limit": 5})
    cid = first["conversation_id"]
    second = graph.invoke({"raw_query": "more", "conversation_id": cid, "limit": 5})
    # interpret_followup should rewrite "more" using the previous turn.
    assert second["interpreted_query"] == "red phone"
    assert searcher.calls[-1]["query"] == "red phone"


def test_graph_returns_clarification_for_ambiguous_short_input(graph_search_settings):
    cfg = graph_search_settings(_base_settings({
        "CONVERSATIONAL": {
            "ENABLED": True,
            "MIN_QUERY_LENGTH_FOR_AUTOSEARCH": 3,
        },
    }))
    memory = InMemoryBackend(max_history_items=5)
    searcher = _StubSearcher([])
    graph = build_conversation_graph(cfg, searcher=searcher, memory=memory)
    out = graph.invoke({"raw_query": "x", "limit": 5})
    assert out["clarification_needed"] is True
    assert out["results"] == []
    # Search should not have been invoked.
    assert not searcher.calls


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_view_returns_404_when_disabled(graph_search_settings):
    graph_search_settings({
        "MODELS": [],
        "VECTOR_STORE": {"BACKEND": "django_graph_search.backends.ChromaDBBackend"},
        "EMBEDDINGS": {
            "default": {
                "BACKEND": "tests.dummy_embedding_backend.DummyEmbeddingBackend",
                "MODEL_NAME": "x",
            }
        },
    })
    factory = RequestFactory()
    request = factory.post(
        "/api/search/conversation/",
        data=json.dumps({"query": "anything"}),
        content_type="application/json",
    )
    response = ConversationalSearchAPIView.as_view()(request)
    assert response.status_code == 404


@pytest.mark.django_db
def test_view_runs_full_pipeline(graph_search_settings):
    graph_search_settings(_base_settings())
    factory = RequestFactory()
    request = factory.post(
        "/api/search/conversation/",
        data=json.dumps({"query": "red phone"}),
        content_type="application/json",
    )
    with mock.patch("django_graph_search.views.Searcher") as searcher_cls:
        searcher_cls.return_value.search.return_value = [
            {"model": "shop.Product", "pk": 1, "score": 0.5}
        ]
        response = ConversationalSearchAPIView.as_view()(request)
    body = json.loads(response.content.decode())
    assert response.status_code == 200
    assert body["query"] == "red phone"
    assert body["interpreted_query"] == "red phone"
    assert body["clarification_needed"] is False
    assert body["results"][0]["pk"] == 1
    assert body["conversation_id"]


@pytest.mark.django_db
def test_view_followup_uses_history_across_two_calls(graph_search_settings):
    graph_search_settings(_base_settings())
    factory = RequestFactory()

    def post(payload):
        request = factory.post(
            "/api/search/conversation/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        with mock.patch("django_graph_search.views.Searcher") as searcher_cls:
            searcher_cls.return_value.search.return_value = [
                {"model": "shop.Product", "pk": 1, "score": 0.5}
            ]
            return ConversationalSearchAPIView.as_view()(request), searcher_cls

    response, _ = post({"query": "red phone"})
    cid = json.loads(response.content.decode())["conversation_id"]

    response2, searcher_cls2 = post({"query": "more", "conversation_id": cid})
    body2 = json.loads(response2.content.decode())
    assert body2["interpreted_query"] == "red phone"
    # The mocked searcher must have been queried with "red phone".
    args, kwargs = searcher_cls2.return_value.search.call_args
    actual_query = args[0] if args else kwargs.get("query")
    assert actual_query == "red phone"


@pytest.mark.django_db
def test_view_clear_history(graph_search_settings):
    graph_search_settings(_base_settings())
    factory = RequestFactory()
    # Seed memory by issuing a real call.
    request = factory.post(
        "/api/search/conversation/",
        data=json.dumps({"query": "hello"}),
        content_type="application/json",
    )
    with mock.patch("django_graph_search.views.Searcher") as searcher_cls:
        searcher_cls.return_value.search.return_value = []
        response = ConversationalSearchAPIView.as_view()(request)
    cid = json.loads(response.content.decode())["conversation_id"]

    # Now clear it.
    delete_request = factory.delete(
        f"/api/search/conversation/?conversation_id={cid}",
    )
    delete_response = ConversationalSearchAPIView.as_view()(delete_request)
    assert delete_response.status_code == 200
    assert json.loads(delete_response.content.decode())["cleared"] is True
