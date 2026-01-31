from __future__ import annotations

from django.apps import apps
from django.http import JsonResponse
from django.views import View

from .searcher import Searcher
from .settings import get_settings


class SearchAPIView(View):
    def get(self, request, *args, **kwargs):
        query = request.GET.get("q", "").strip()
        if not query:
            return JsonResponse({"error": "Parameter 'q' is required."}, status=400)
        models = request.GET.get("models")
        model_list = [m.strip() for m in models.split(",")] if models else None
        limit = request.GET.get("limit")
        limit_value = int(limit) if limit else None

        searcher = Searcher()
        results = searcher.search(query, models=model_list, limit=limit_value)
        return JsonResponse(
            {"query": query, "results": results, "total": len(results)},
            status=200,
        )


class SimilarAPIView(View):
    def get(self, request, model: str, pk: str, *args, **kwargs):
        if "." not in model:
            return JsonResponse({"error": "Model must be in 'app.Model' format."}, status=400)
        app_label, model_name = model.split(".", 1)
        model_cls = apps.get_model(app_label, model_name)
        if model_cls is None:
            return JsonResponse({"error": "Model not found."}, status=404)
        instance = model_cls.objects.filter(pk=pk).first()
        if instance is None:
            return JsonResponse({"error": "Object not found."}, status=404)
        limit = request.GET.get("limit")
        limit_value = int(limit) if limit else None
        searcher = Searcher()
        results = searcher.find_similar(instance, limit=limit_value)
        return JsonResponse(
            {
                "model": model,
                "pk": pk,
                "results": results,
                "total": len(results),
            },
            status=200,
        )

