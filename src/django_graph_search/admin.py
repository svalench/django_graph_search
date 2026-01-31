from __future__ import annotations

from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path

from .searcher import Searcher
from .settings import get_settings


def graph_search_view(request):
    config = get_settings()
    query = request.GET.get("q", "").strip()
    models = request.GET.get("models")
    model_list = [m.strip() for m in models.split(",")] if models else None
    results = []
    if query:
        searcher = Searcher(config=config)
        results = searcher.search(query, models=model_list, limit=config.default_results_limit)

    context = dict(
        admin.site.each_context(request),
        title="Graph Search",
        query=query,
        results=results,
        model_list=models or "",
        available_models=[cfg.model for cfg in config.models],
    )
    return TemplateResponse(request, "django_graph_search/admin/search.html", context)


def _inject_admin_urls(admin_site):
    original_get_urls = admin_site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path(
                "graph-search/",
                admin_site.admin_view(graph_search_view),
                name="graph-search",
            )
        ]
        return custom + urls

    admin_site.get_urls = get_urls


_inject_admin_urls(admin.site)

