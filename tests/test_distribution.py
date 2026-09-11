"""Distribution checks also runnable against a wheel with Python's isolated mode."""

from importlib import metadata, resources
import unittest


class DistributionTests(unittest.TestCase):
    def test_public_imports(self):
        from django_graph_search import Indexer, Searcher, get_similar, index, search

        for entry_point in (Indexer, Searcher, get_similar, index, search):
            with self.subTest(entry_point=entry_point):
                self.assertTrue(callable(entry_point))

    def test_metadata_and_extras(self):
        package_metadata = metadata.metadata("django-graph-search")
        self.assertEqual(package_metadata["Requires-Python"], ">=3.10")
        self.assertIn(
            "Documentation, https://svalench.github.io/django_graph_search/",
            package_metadata.get_all("Project-URL", []),
        )
        expected_extras = {
            "chromadb", "faiss", "qdrant", "pgvector", "openai", "cohere",
            "langgraph", "test", "all",
        }
        self.assertTrue(expected_extras <= set(package_metadata.get_all("Provides-Extra", [])))
        self.assertIn(
            'numpy>=1.26; extra == "test"',
            package_metadata.get_all("Requires-Dist", []),
        )

    def test_admin_assets_are_packaged(self):
        package = resources.files("django_graph_search")
        paths = (
            "templates/django_graph_search/admin/search.html",
            "templates/django_graph_search/admin/index_status.html",
            "static/django_graph_search/css/search.css",
            "static/django_graph_search/css/index_status.css",
            "static/django_graph_search/js/search.js",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(package.joinpath(path).read_bytes())


if __name__ == "__main__":
    unittest.main()
