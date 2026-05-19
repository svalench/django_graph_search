from __future__ import annotations

from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path

from .index_coverage import get_index_coverage
from .models import GraphSearch, GraphSearchIndexStatus
from .searcher import Searcher
from .settings import get_settings
from .views import _parse_float_param

_admin_site_configured: set[int] = set()


def graph_search_view(request, admin_site=None):
    site = admin_site if admin_site is not None else admin.site
    config = get_settings()
    query = request.GET.get("q", "").strip()
    models = request.GET.get("models")
    model_list = [m.strip() for m in models.split(",")] if models else None
    min_score, min_score_err = _parse_float_param(
        request.GET.get("min_score"),
        "min_score",
        default=None,
        min_value=0.0,
        max_value=1.0,
    )
    min_score_error = None
    if min_score_err is not None:
        min_score = None
        min_score_error = "Параметр min_score: число от 0.0 до 1.0."

    results = []
    if query and min_score_error is None:
        searcher = Searcher(config=config)
        results = searcher.search(query, models=model_list, limit=config.default_results_limit)
        if min_score is not None:
            results = [r for r in results if float(r.get("score") or 0) >= min_score]

    context = dict(
        site.each_context(request),
        title="Graph Search",
        query=query,
        results=results,
        model_list=models or "",
        available_models=[cfg.model for cfg in config.models],
        min_score=request.GET.get("min_score", "").strip(),
        min_score_applied=min_score,
        min_score_error=min_score_error,
    )
    return TemplateResponse(request, "django_graph_search/admin/search.html", context)


def graph_search_index_status_view(request, admin_site=None):
    """Статичный снимок покрытия индекса (без автообновления)."""
    site = admin_site if admin_site is not None else admin.site
    report = get_index_coverage()
    context = dict(
        site.each_context(request),
        title="Статус индексации",
        report=report,
    )
    return TemplateResponse(request, "django_graph_search/admin/index_status.html", context)


class _GraphSearchMenuAdmin(admin.ModelAdmin):
    """Базовый ModelAdmin для пунктов меню без CRUD."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class GraphSearchAdmin(_GraphSearchMenuAdmin):
    def changelist_view(self, request, extra_context=None):
        return graph_search_view(request, admin_site=self.admin_site)


class GraphSearchIndexStatusAdmin(_GraphSearchMenuAdmin):
    def changelist_view(self, request, extra_context=None):
        return graph_search_index_status_view(request, admin_site=self.admin_site)


def _inject_admin_urls(admin_site):
    if getattr(admin_site, "_graph_search_urls_injected", False):
        return

    original_get_urls = admin_site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path(
                "graph-search/index-status/",
                admin_site.admin_view(graph_search_index_status_view),
                name="graph-search-index-status",
            ),
            path(
                "graph-search/",
                admin_site.admin_view(graph_search_view),
                name="graph-search",
            ),
        ]
        return custom + urls

    admin_site.get_urls = get_urls
    admin_site._graph_search_urls_injected = True


def _register_menu_models(admin_site):
    if not admin_site.is_registered(GraphSearch):
        admin_site.register(GraphSearch, GraphSearchAdmin)
    if not admin_site.is_registered(GraphSearchIndexStatus):
        admin_site.register(GraphSearchIndexStatus, GraphSearchIndexStatusAdmin)


def setup_admin_site(admin_site=None):
    """Регистрация раздела админки и legacy-URL (идемпотентно)."""
    site = admin_site if admin_site is not None else admin.site
    site_id = id(site)
    if site_id in _admin_site_configured:
        return

    if not get_settings().admin_search_enabled:
        _admin_site_configured.add(site_id)
        return

    _register_menu_models(site)
    _inject_admin_urls(site)
    _admin_site_configured.add(site_id)
