from django_graph_search.searcher import Searcher
from django_graph_search.settings import (
    CacheConfig,
    EmbeddingProfile,
    GraphSearchConfig,
    VectorStoreConfig,
)


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
    config = GraphSearchConfig(
        models=[],
        vector_store=VectorStoreConfig(backend="dummy"),
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
        api_url_prefix="api/search/",
        admin_search_enabled=True,
        auto_index=True,
        default_results_limit=10,
        delta_indexing=False,
        cache=CacheConfig(backend="file"),
    )
    searcher = Searcher(config=config, vector_store=DummyVectorStore())
    assert searcher.embedding_backend.model_name == "model-a"

