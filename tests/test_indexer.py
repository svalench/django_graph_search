from django.test import TestCase

from django_graph_search.indexer import Indexer
from django_graph_search.settings import ModelConfig
from django_graph_search.backends.base import Document

from .test_app.models import Category, Product
from .utils import make_basic_config


class DummyVectorStore:
    def __init__(self):
        self.docs = []

    def add_documents(self, documents):
        self.docs.extend(list(documents))

    def search(self, query_vector, limit, filters=None):
        return []

    def delete(self, doc_ids):
        self.docs = [doc for doc in self.docs if doc.id not in set(doc_ids)]

    def clear_collection(self):
        self.docs = []


class DummyEmbeddingBackend:
    def embed(self, text):
        return [1.0, 0.0]

    def embed_batch(self, texts):
        return [[1.0, 0.0] for _ in texts]


class DummyDeltaCache:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ttl):
        self.store[key] = value

    def delete(self, key):
        if key in self.store:
            del self.store[key]


class IndexerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        category = Category.objects.create(name="Phones")
        Product.objects.create(name="Pixel", description="Good camera", category=category)

    def test_index_instance_adds_document(self):
        config = make_basic_config(
            delta_indexing=False,
            models=[
                ModelConfig(
                    model="test_app.Product",
                    fields=["name", "description"],
                    follow_relations=False,
                    relation_depth=1,
                )
            ],
        )
        vector_store = DummyVectorStore()
        embedding_backend = DummyEmbeddingBackend()
        indexer = Indexer(
            config=config,
            vector_store=vector_store,
            embedding_backend=embedding_backend,
        )
        product = Product.objects.first()
        indexer.index_instance(product, config.models[0])

        self.assertEqual(len(vector_store.docs), 1)
        self.assertIsInstance(vector_store.docs[0], Document)

    def test_delta_indexing_skips_unchanged(self):
        config = make_basic_config(
            delta_indexing=True,
            models=[
                ModelConfig(
                    model="test_app.Product",
                    fields=["name", "description"],
                    follow_relations=False,
                    relation_depth=1,
                )
            ],
        )
        vector_store = DummyVectorStore()
        embedding_backend = DummyEmbeddingBackend()
        delta_cache = DummyDeltaCache()
        indexer = Indexer(
            config=config,
            vector_store=vector_store,
            embedding_backend=embedding_backend,
            delta_cache=delta_cache,
        )
        product = Product.objects.first()
        indexer.index_instance(product, config.models[0])
        indexer.index_instance(product, config.models[0])

        self.assertEqual(len(vector_store.docs), 1)

