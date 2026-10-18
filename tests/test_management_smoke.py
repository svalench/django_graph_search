"""Exercise the documented management workflow without model downloads."""
# pylint: disable=redefined-outer-name
import io

import pytest
from django.core.management import call_command

from django_graph_search.component_registry import get_shared_components
from .test_conversational_search import graph_search_settings, _base_settings
from .test_app.models import Category, Product


@pytest.mark.django_db
def test_readme_management_workflow(graph_search_settings, tmp_path):
    cfg = graph_search_settings(_base_settings({
        "MODELS": [{"model": "test_app.Product", "fields": ["name", "category__name"]}],
        "VECTOR_STORE": {"BACKEND": "tests.dummy_vector_backend.DummyVectorBackend"},
        "AUTO_INDEX": False,
        "CACHE": {"BACKEND": "file", "OPTIONS": {"path": str(tmp_path)}},
    }))
    category = Category.objects.create(name="Readme category")
    Product.objects.create(name="Readme product", category=category)
    output = io.StringIO()
    call_command("build_search_index", stdout=output)
    assert "test_app.Product: 1" in output.getvalue()
    _, store, _, _ = get_shared_components(cfg)
    assert store.count_documents() == 1
    assert "Readme category" in store._documents[0].text
    output = io.StringIO()
    call_command("search_index_status", stdout=output)
    assert "100.0%" in output.getvalue()
    call_command("purge_search_cache", dry_run=True, stdout=io.StringIO())
    call_command("purge_search_cache", stdout=io.StringIO())
    call_command("clear_search_index", stdout=io.StringIO())
    assert store.count_documents() == 0
    call_command("build_search_index", model="test_app.Product", stdout=io.StringIO())
    assert store.count_documents() == 1
