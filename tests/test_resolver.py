from django.test import TestCase

from django_graph_search.graph_resolver import GraphResolver
from django_graph_search.settings import ModelConfig

from .test_app.models import Category, Product, Tag


class GraphResolverTests(TestCase):
    @classmethod
    def setUpTestData(cls):  # pylint: disable=invalid-name
        category = Category.objects.create(name="Phones")
        tag1 = Tag.objects.create(name="Android")
        tag2 = Tag.objects.create(name="Budget")
        product = Product.objects.create(
            name="Pixel",
            description="Good camera",
            category=category,
        )
        product.tags.set([tag1, tag2])

    def test_build_searchable_text_includes_relations(self):
        """Ensure relation fields are included in searchable text."""
        product = Product.objects.first()
        resolver = GraphResolver()
        config = ModelConfig(
            model="test_app.Product",
            fields=["name", "description", "category__name", "tags__name"],
            follow_relations=True,
            relation_depth=2,
        )
        text = resolver.build_searchable_text(product, config)
        self.assertIn("Pixel", text)
        self.assertIn("Good camera", text)
        self.assertIn("Phones", text)
        self.assertIn("Android", text)
        self.assertIn("Budget", text)

