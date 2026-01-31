from __future__ import annotations

# pylint: disable=duplicate-code

from typing import Iterable, List, Optional

from django.apps import apps
from django.urls import reverse
from .components import ComponentMixin
from .graph_resolver import GraphResolver
from .settings import GraphSearchConfig, ModelConfig, get_settings


class Searcher(ComponentMixin):
    def __init__(
        self,
        config: Optional[GraphSearchConfig] = None,
        vector_store=None,
        embedding_backend=None,
        resolver: Optional[GraphResolver] = None,
        embedding_profile: Optional[str] = None,
    ) -> None:
        self._init_components(
            config=config,
            vector_store=vector_store,
            embedding_backend=embedding_backend,
            resolver=resolver,
            embedding_profile=embedding_profile,
        )

    def search(
        self,
        query: str,
        models: Optional[Iterable[str]] = None,
        limit: Optional[int] = None,
    ) -> List[dict]:
        limit = limit or self.config.default_results_limit
        query_vector = self.embedding_backend.embed(query)
        filters = None
        results = self.vector_store.search(query_vector, limit=limit, filters=filters)
        if models:
            allowed = set(models)
            results = [item for item in results if item.metadata.get("model") in allowed]
        return [self._format_result(item) for item in results]

    def find_similar(
        self,
        instance,
        limit: Optional[int] = None,
    ) -> List[dict]:
        limit = limit or self.config.default_results_limit
        model_cfg = self._find_model_config(instance._meta.label)
        text = self.resolver.build_searchable_text(instance, model_cfg)
        query_vector = self.embedding_backend.embed(text)
        results = self.vector_store.search(
            query_vector,
            limit=limit,
            filters={"model": instance._meta.label},
        )
        return [self._format_result(item) for item in results]

    def _format_result(self, item) -> dict:
        model_label = item.metadata.get("model")
        pk = item.metadata.get("pk")
        data = {"model": model_label, "pk": pk, "score": item.score}
        if model_label and pk is not None:
            model_cls = self._get_model_class(model_label)
            obj = model_cls.objects.filter(pk=pk).first()
            if obj is not None:
                data["data"] = self._model_to_dict(obj)
                data["admin_url"] = self._admin_url(obj)
        return data

    def _model_to_dict(self, instance) -> dict:
        data = {}
        for field in instance._meta.concrete_fields:
            value = getattr(instance, field.name, None)
            if value is None:
                continue
            data[field.name] = str(value)
        return data

    def _admin_url(self, instance) -> str:
        app_label = instance._meta.app_label
        model_name = instance._meta.model_name
        try:
            return reverse(f"admin:{app_label}_{model_name}_change", args=[instance.pk])
        except Exception:
            return ""

    def _find_model_config(self, model_label: str) -> ModelConfig:
        for cfg in self.config.models:
            if cfg.model == model_label:
                return cfg
        # Fallback: minimal config
        return ModelConfig(model=model_label, fields=[], follow_relations=True)

    def _get_model_class(self, model_label: str):
        if "." not in model_label:
            return apps.get_model(model_label)
        app_label, model_name = model_label.split(".", 1)
        return apps.get_model(app_label, model_name)

