"""Tests for the event hub and the streaming search endpoint (Sprint 4)."""
# pylint: disable=redefined-outer-name
from __future__ import annotations

import json
from unittest import mock

import pytest

from django.conf import settings as django_settings
from django.test import RequestFactory, TestCase

from django_graph_search.events import EventHub, get_default_hub, reset_default_hub
from django_graph_search.langgraph_agent import _FallbackGraph
from django_graph_search.llm import DummyLLMBackend
from django_graph_search.settings import (
    CacheConfig,
    EmbeddingProfile,
    GraphSearchConfig,
    LangGraphConfig,
    StreamingConfig,
    SmartIndexingConfig,
    ConversationalConfig,
    VectorStoreConfig,
    get_settings,
)
from django_graph_search.views import StreamingSearchAPIView


# ---------------------------------------------------------------------------
# Event hub
# ---------------------------------------------------------------------------


class EventHubTests(TestCase):
    def test_subscribe_publish_unsubscribe(self):
        hub = EventHub()
        events = []
        unsubscribe = hub.subscribe(events.append)
        hub.publish({"type": "a"})
        hub.publish({"type": "b"})
        unsubscribe()
        hub.publish({"type": "c"})
        self.assertEqual([e["type"] for e in events], ["a", "b"])

    def test_subscriber_errors_are_swallowed(self):
        hub = EventHub()
        good = []

        def bad(_evt):
            raise RuntimeError("boom")

        hub.subscribe(bad)
        hub.subscribe(good.append)
        hub.publish({"type": "ok"})
        # Bad subscriber must not stop the good one.
        self.assertEqual(good, [{"type": "ok"}])

    def test_default_hub_is_singleton(self):
        reset_default_hub()
        hub_a = get_default_hub()
        hub_b = get_default_hub()
        self.assertIs(hub_a, hub_b)


# ---------------------------------------------------------------------------
# Fallback graph emits events
# ---------------------------------------------------------------------------


class _StubVS:
    def search(self, vector, limit, filters=None):
        return []


class _StubEmb:
    def embed(self, text, *, is_query: bool = False):
        return [0.0]

    def embed_batch(self, texts, *, is_query: bool = False):
        return [[0.0] for _ in texts]


def _make_lg_config(*, query_expansion=False, reranking=False) -> GraphSearchConfig:
    return GraphSearchConfig(
        models=[],
        vector_store=VectorStoreConfig(backend="x"),
        embeddings={"default": EmbeddingProfile(backend="x", model_name="x")},
        default_embedding="default",
        api_url_prefix="api/search/",
        admin_search_enabled=False,
        auto_index=False,
        default_results_limit=10,
        delta_indexing=False,
        cache=CacheConfig(backend="file"),
        langgraph=LangGraphConfig(
            enabled=True,
            query_expansion=query_expansion,
            reranking=reranking,
        ),
    )


class FallbackGraphEventsTests(TestCase):
    def test_fallback_graph_emits_lifecycle_events(self):
        hub = EventHub()
        seen = []
        hub.subscribe(seen.append)
        cfg = _make_lg_config(query_expansion=True)
        graph = _FallbackGraph(
            config=cfg,
            embedding_backend=_StubEmb(),
            vector_store=_StubVS(),
            llm=DummyLLMBackend(),
            event_hub=hub,
        )
        graph.invoke({"query": "phone"})
        types = [e["type"] for e in seen]
        self.assertIn("query_received", types)
        self.assertIn("query_expanded", types)
        self.assertIn("vector_search_completed", types)
        self.assertEqual(types[-1], "completed")

    def test_fallback_graph_no_hub_runs_silently(self):
        cfg = _make_lg_config()
        graph = _FallbackGraph(
            config=cfg,
            embedding_backend=_StubEmb(),
            vector_store=_StubVS(),
            llm=DummyLLMBackend(),
        )
        out = graph.invoke({"query": "phone"})
        self.assertEqual(out.get("final_results"), [])


# ---------------------------------------------------------------------------
# Streaming endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def graph_search_settings():
    original = getattr(django_settings, "GRAPH_SEARCH", None)
    get_settings.cache_clear()

    def _apply(payload):
        django_settings.GRAPH_SEARCH = payload
        get_settings.cache_clear()
        return get_settings()

    yield _apply

    if original is None:
        if hasattr(django_settings, "GRAPH_SEARCH"):
            delattr(django_settings, "GRAPH_SEARCH")
    else:
        django_settings.GRAPH_SEARCH = original
    get_settings.cache_clear()


def _drain(response) -> list:
    """Collect NDJSON lines from a StreamingHttpResponse."""
    chunks = []
    for chunk in response.streaming_content:
        text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        for line in text.splitlines():
            line = line.strip()
            if line:
                chunks.append(line)
    return chunks


def test_streaming_disabled_by_default_returns_404(graph_search_settings):
    graph_search_settings({"MODELS": []})
    factory = RequestFactory()
    request = factory.get("/api/search/stream/", {"q": "phone"})
    response = StreamingSearchAPIView.as_view()(request)
    assert response.status_code == 404


def test_streaming_requires_query(graph_search_settings):
    graph_search_settings({"MODELS": [], "STREAMING": {"ENABLED": True}})
    factory = RequestFactory()
    request = factory.get("/api/search/stream/")
    response = StreamingSearchAPIView.as_view()(request)
    assert response.status_code == 400


def test_streaming_returns_ndjson_with_results(graph_search_settings):
    graph_search_settings({"MODELS": [], "STREAMING": {"ENABLED": True}})
    factory = RequestFactory()
    request = factory.get("/api/search/stream/", {"q": "phone"})
    with mock.patch("django_graph_search.views.Searcher") as searcher_cls:
        searcher_cls.return_value.search.return_value = [
            {"model": "test_app.Product", "pk": 1, "score": 0.5}
        ]
        response = StreamingSearchAPIView.as_view()(request)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/x-ndjson"
        lines = _drain(response)
    parsed = [json.loads(line) for line in lines]
    types = [evt["type"] for evt in parsed]
    assert "query_received" in types
    assert "results" in types
    assert types[-1] == "end"
    results_evt = next(evt for evt in parsed if evt["type"] == "results")
    assert results_evt["total"] == 1


def test_streaming_sse_format(graph_search_settings):
    graph_search_settings({
        "MODELS": [],
        "STREAMING": {"ENABLED": True, "FORMAT": "sse"},
    })
    factory = RequestFactory()
    request = factory.get("/api/search/stream/", {"q": "phone"})
    with mock.patch("django_graph_search.views.Searcher") as searcher_cls:
        searcher_cls.return_value.search.return_value = []
        response = StreamingSearchAPIView.as_view()(request)
    assert response["Content-Type"] == "text/event-stream"
    body = b"".join(response.streaming_content).decode("utf-8")
    assert "event: query_received" in body
    assert "event: end" in body
    assert "data: " in body


def test_streaming_handles_searcher_error(graph_search_settings):
    graph_search_settings({"MODELS": [], "STREAMING": {"ENABLED": True}})
    factory = RequestFactory()
    request = factory.get("/api/search/stream/", {"q": "phone"})
    with mock.patch("django_graph_search.views.Searcher") as searcher_cls:
        searcher_cls.return_value.search.side_effect = RuntimeError("boom")
        response = StreamingSearchAPIView.as_view()(request)
        lines = _drain(response)
    parsed = [json.loads(line) for line in lines]
    types = [evt["type"] for evt in parsed]
    # The runner publishes an "error" and the stream still ends cleanly.
    assert "error" in types
    assert types[-1] == "end"


def test_streaming_format_validation(graph_search_settings):
    with pytest.raises(Exception):
        graph_search_settings({
            "MODELS": [],
            "STREAMING": {"ENABLED": True, "FORMAT": "weird-format"},
        })


def test_streaming_post_with_json_body(graph_search_settings):
    graph_search_settings({"MODELS": [], "STREAMING": {"ENABLED": True}})
    factory = RequestFactory()
    request = factory.post(
        "/api/search/stream/",
        data=json.dumps({"query": "phone", "limit": 3}),
        content_type="application/json",
    )
    with mock.patch("django_graph_search.views.Searcher") as searcher_cls:
        searcher_cls.return_value.search.return_value = []
        response = StreamingSearchAPIView.as_view()(request)
    assert response.status_code == 200
    args, kwargs = searcher_cls.return_value.search.call_args
    assert args[0] == "phone"
    assert kwargs["limit"] == 3
