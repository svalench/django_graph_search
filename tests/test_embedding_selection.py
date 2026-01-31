from django_graph_search.searcher import Searcher
from django_graph_search.settings import EmbeddingProfile

from .utils import make_basic_config, with_embeddings


class DummyVectorStore:
    def add_documents(self, documents):
        pass

    def search(self, query_vector, limit, filters=None):
        return []

    def delete(self, doc_ids):
        pass

    def clear_collection(self):
        pass


def test_default_embedding_profile_is_used():
    base_config = make_basic_config(
        delta_indexing=False,
        embedding_backend="tests.dummy_embedding_backend.DummyEmbeddingBackend",
        embedding_model="model-a",
    )
    config = with_embeddings(
        base_config,
        embeddings={
            "default": EmbeddingProfile(
                backend="tests.dummy_embedding_backend.DummyEmbeddingBackend",
                model_name="model-a",
            ),
            "alt": EmbeddingProfile(
                backend="tests.dummy_embedding_backend.DummyEmbeddingBackend",
                model_name="model-b",
            ),
        },
        default_embedding="default",
    )
    searcher = Searcher(config=config, vector_store=DummyVectorStore())
    assert searcher.embedding_backend.model_name == "model-a"

