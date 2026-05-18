"""Тесты GraphResolver: веса полей при fields=__all__."""
from __future__ import annotations

from django.conf import settings as django_settings
from django.test import TestCase

from django_graph_search.graph_resolver import GraphResolver
from django_graph_search.settings import ModelConfig, get_settings

from .test_app.models import Category, Product


class GraphResolverWeightAllTests(TestCase):
    """weight_fields с fields=__all__: повтор, исключение (0), значение по умолчанию."""

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Electronics")
        cls.product = Product.objects.create(
            name="Alpha",
            description="Beta line",
            category=cls.category,
        )

    def test_weight_fields_with_all_fields(self):
        resolver = GraphResolver()
        config = ModelConfig(
            model="test_app.Product",
            fields=["__all__"],
            follow_relations=False,
            relation_depth=0,
            weight_fields={
                "name": 3.0,
                "description": 0.0,
            },
        )
        text = resolver.build_searchable_text(self.product, config)
        self.assertEqual(text.count("Alpha"), 3)
        self.assertNotIn("Beta", text)
        self.assertIn("Electronics", text)


def test_weight_fields_parsed_for_all_fields_in_settings():
    """GRAPH_SEARCH: weight_fields нормализуются при fields='__all__'."""
    original = getattr(django_settings, "GRAPH_SEARCH", None)
    get_settings.cache_clear()
    django_settings.GRAPH_SEARCH = {
        "MODELS": [
            {
                "model": "test_app.Product",
                "fields": "__all__",
                "weight_fields": {"name": "2.5"},
            }
        ],
        "VECTOR_STORE": {"BACKEND": "tests.dummy_vector_backend.DummyVectorBackend"},
        "EMBEDDINGS": {
            "default": {
                "BACKEND": "tests.dummy_embedding_backend.DummyEmbeddingBackend",
                "MODEL_NAME": "x",
            }
        },
    }
    try:
        cfg = get_settings()
        mc = cfg.models[0]
        assert mc.fields == ["__all__"]
        assert mc.weight_fields["name"] == 2.5
    finally:
        if original is None:
            delattr(django_settings, "GRAPH_SEARCH")
        else:
            django_settings.GRAPH_SEARCH = original
        get_settings.cache_clear()
