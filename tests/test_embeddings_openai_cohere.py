"""Юнит-тесты опциональных эмбеддингов OpenAI / Cohere (с моками)."""
from __future__ import annotations

import sys
from types import ModuleType
from unittest import mock

from django_graph_search.embeddings.cohere_backend import CohereEmbeddingBackend
from django_graph_search.embeddings.openai_backend import OpenAIEmbeddingBackend


def test_openai_embed_batch_mocked():
    item0 = mock.Mock(index=0, embedding=[0.25, 0.75])
    item1 = mock.Mock(index=1, embedding=[0.1, 0.9])
    resp = mock.Mock()
    resp.data = [item0, item1]
    client = mock.Mock()
    client.embeddings.create.return_value = resp

    fake_openai = ModuleType("openai")
    fake_openai.OpenAI = mock.Mock(return_value=client)

    with mock.patch.dict(sys.modules, {"openai": fake_openai}):
        backend = OpenAIEmbeddingBackend("text-embedding-3-small", api_key="sk-test")
        out = backend.embed_batch(["hello", "world"], is_query=False)

    assert len(out) == 2
    assert out[0] == [0.25, 0.75]
    assert out[1] == [0.1, 0.9]
    client.embeddings.create.assert_called()


def test_cohere_embed_uses_input_type_query_vs_document():
    emb_obj = mock.Mock()
    emb_obj.float = [[0.1, 0.2]]
    response = mock.Mock()
    response.embeddings = emb_obj
    client = mock.Mock()
    client.embed.return_value = response

    fake_cohere = ModuleType("cohere")
    fake_cohere.Client = mock.Mock(return_value=client)

    with mock.patch.dict(sys.modules, {"cohere": fake_cohere}):
        backend = CohereEmbeddingBackend("embed-multilingual-v3.0", api_key="x")
        backend.embed("q", is_query=True)
        backend.embed("d", is_query=False)

    calls = client.embed.call_args_list
    assert calls[0].kwargs["input_type"] == "search_query"
    assert calls[1].kwargs["input_type"] == "search_document"
