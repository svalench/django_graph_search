"""Regression coverage for the 0.3.7 security audit."""
# pylint: disable=redefined-outer-name
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from django_graph_search.graph_resolver import GraphResolver
from django_graph_search.searcher import Searcher
from django_graph_search.settings import ModelConfig
from django_graph_search.views import ConversationalSearchAPIView, StreamingSearchAPIView
from .test_conversational_search import graph_search_settings, _base_settings


@pytest.mark.django_db
@pytest.mark.parametrize("fields", [["__all__"], ["username", "password"]])
def test_password_never_appears_in_index_or_result_data(fields):
    user = get_user_model().objects.create_user("audit-user", password="regression-example")
    config = ModelConfig(model=user._meta.label, fields=fields, follow_relations=False)
    assert user.password not in GraphResolver().build_searchable_text(user, config)
    result = Searcher._model_to_dict(None, user, config)
    assert "password" not in result
    assert result["username"] == "audit-user"


@pytest.mark.parametrize("view", [ConversationalSearchAPIView, StreamingSearchAPIView])
@pytest.mark.parametrize("payload", [
    ["invalid"], 1, True, {"query": ["invalid"]},
    {"query": "hello", "models": 1}, {"query": "hello", "models": [1]},
])
def test_invalid_json_shapes_return_400(graph_search_settings, view, payload):
    graph_search_settings(_base_settings({"STREAMING": {"ENABLED": True}}))
    request = RequestFactory().post("/", json.dumps(payload), content_type="application/json")
    with patch("django_graph_search.views.Searcher"):
        assert view.as_view()(request).status_code == 400


@pytest.mark.parametrize("cid", [["bad"], {"bad": "id"}, 42])
def test_invalid_conversation_id_returns_400(graph_search_settings, cid):
    graph_search_settings(_base_settings())
    request = RequestFactory().post(
        "/", json.dumps({"query": "hello", "conversation_id": cid}),
        content_type="application/json",
    )
    with patch("django_graph_search.views.Searcher"):
        assert ConversationalSearchAPIView.as_view()(request).status_code == 400


def test_streaming_does_not_disclose_backend_exception(graph_search_settings):
    graph_search_settings(_base_settings({"STREAMING": {"ENABLED": True}}))
    request = RequestFactory().get("/", {"q": "hello"})
    with patch("django_graph_search.views.Searcher") as searcher:
        searcher.return_value.search.side_effect = RuntimeError("private backend credential")
        response = StreamingSearchAPIView.as_view()(request)
        content = b"".join(response.streaming_content).decode()
    assert "private backend credential" not in content
    assert '"type": "error"' in content


def test_authenticated_conversation_history_is_isolated(graph_search_settings):
    graph_search_settings(_base_settings())
    factory = RequestFactory()

    def post(user_pk, query):
        request = factory.post(
            "/", json.dumps({"query": query, "conversation_id": "shared-id"}),
            content_type="application/json",
        )
        request.user = SimpleNamespace(is_authenticated=True, pk=user_pk)
        with patch("django_graph_search.views.Searcher") as searcher:
            searcher.return_value.search.return_value = []
            response = ConversationalSearchAPIView.as_view()(request)
        assert response.status_code == 200
        return json.loads(response.content)

    post(1, "private search phrase")
    assert post(2, "more")["interpreted_query"] != "private search phrase"
    # Deleting the same public id must only clear the caller's own history.
    request = factory.delete("/?conversation_id=shared-id")
    request.user = SimpleNamespace(is_authenticated=True, pk=2)
    assert ConversationalSearchAPIView.as_view()(request).status_code == 200
    assert post(1, "more")["interpreted_query"] == "private search phrase"


@pytest.mark.parametrize("filters", [None, {"model": "test_app.Product"}])
def test_pgvector_filter_parameters_follow_sql_placeholder_order(filters):
    from django_graph_search.backends.pgvector import PgvectorBackend
    store = PgvectorBackend(dimension=2)
    store._table_initialized = True
    with patch("django_graph_search.backends.pgvector.connections") as connections:
        cursor = connections.__getitem__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        store.search([1.0, 0.0], limit=5, filters=filters)
        _, params = cursor.execute.call_args.args
    expected = ["[1.0,0.0]"]
    if filters:
        expected.append(json.dumps(filters))
    expected.extend(["[1.0,0.0]", 5])
    assert params == expected


@pytest.mark.parametrize("view", [ConversationalSearchAPIView, StreamingSearchAPIView])
def test_post_views_do_not_bypass_django_csrf_middleware(view):
    from django.middleware.csrf import CsrfViewMiddleware
    request = RequestFactory().post("/", {"query": "hello"})
    response = CsrfViewMiddleware(lambda request: None).process_view(
        request, view.as_view(), (), {}
    )
    assert response.status_code == 403


@pytest.mark.parametrize("backend_type", ["inmemory", "cache"])
def test_scoped_memory_isolates_anonymous_and_authenticated_ids(backend_type):
    from django_graph_search.memory import build_memory_backend, ConversationEvent
    from django_graph_search.memory.scoped import ScopedMemoryBackend
    backend = build_memory_backend(backend_type, max_history_items=3)
    anonymous = ScopedMemoryBackend(backend, "anonymous")
    owner = ScopedMemoryBackend(backend, "user:1")
    other = ScopedMemoryBackend(backend, "user:2")
    owner.clear_history("scope-test")
    owner.append_event("scope-test", ConversationEvent(role="user", query="private query"))
    assert not anonymous.get_history("scope-test")
    assert not other.get_history("scope-test")
    anonymous.clear_history("scope-test")
    other.clear_history("scope-test")
    assert owner.get_history("scope-test")[0].query == "private query"
    owner.clear_history("scope-test")
    assert not owner.get_history("scope-test")


@pytest.mark.parametrize("setting", ["PERMISSION_CLASSES", "THROTTLE_CLASSES"])
@pytest.mark.parametrize("entry", ["django.conf.settings", "builtins.object", "missing.guard"])
def test_misconfigured_access_checks_fail_closed(graph_search_settings, setting, entry):
    from django_graph_search.permissions import check_permissions, check_throttle, PermissionDenied
    cfg = graph_search_settings(_base_settings({"API": {setting: [entry]}}))
    check = check_permissions if setting == "PERMISSION_CLASSES" else check_throttle
    with pytest.raises(PermissionDenied):
        check(RequestFactory().get("/"), cfg)
