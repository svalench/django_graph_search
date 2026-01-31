from unittest.mock import patch

from django.test import RequestFactory, TestCase

from django_graph_search.views import SearchAPIView, SimilarAPIView
from .test_app.models import Category, Product


class ApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        category = Category.objects.create(name="Phones")
        Product.objects.create(name="Pixel", description="Good camera", category=category)

    def test_search_api_returns_results(self):
        factory = RequestFactory()
        request = factory.get("/api/search/", {"q": "pixel"})
        with patch("django_graph_search.views.Searcher") as searcher_cls:
            searcher_cls.return_value.search.return_value = [
                {"model": "test_app.Product", "pk": 1, "score": 0.1}
            ]
            response = SearchAPIView.as_view()(request)
        self.assertEqual(response.status_code, 200)

    def test_similar_api_returns_results(self):
        factory = RequestFactory()
        request = factory.get("/api/search/similar/test_app.Product/1/")
        with patch("django_graph_search.views.Searcher") as searcher_cls:
            searcher_cls.return_value.find_similar.return_value = [
                {"model": "test_app.Product", "pk": 1, "score": 0.1}
            ]
            response = SimilarAPIView.as_view()(request, model="test_app.Product", pk="1")
        self.assertEqual(response.status_code, 200)

