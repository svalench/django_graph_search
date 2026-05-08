from __future__ import annotations

# pylint: disable=duplicate-code

import logging
from typing import Iterable, List, Optional

from django.apps import apps
from django.urls import reverse

from .components import ComponentMixin
from .graph_resolver import GraphResolver
from .llm import BaseLLMBackend, build_llm_backend
from .settings import GraphSearchConfig, ModelConfig, get_settings

log = logging.getLogger(__name__)


class Searcher(ComponentMixin):
    """High-level search facade.

    The public API (:meth:`search`, :meth:`find_similar`) is unchanged. When
    ``GRAPH_SEARCH["LANGGRAPH"]["ENABLED"]`` is true the call is routed
    through the LangGraph orchestrator defined in
    :mod:`django_graph_search.langgraph_agent`; otherwise it follows the
    original linear path. Either way the returned shape is identical so
    callers do not need to know which path executed.
    """

    def __init__(
        self,
        config: Optional[GraphSearchConfig] = None,
        vector_store=None,
        embedding_backend=None,
        resolver: Optional[GraphResolver] = None,
        embedding_profile: Optional[str] = None,
        llm_backend: Optional[BaseLLMBackend] = None,
    ) -> None:
        self._init_components(
            config=config,
            vector_store=vector_store,
            embedding_backend=embedding_backend,
            resolver=resolver,
            embedding_profile=embedding_profile,
        )
        self._llm_backend = llm_backend
        self._compiled_graph = None  # Lazy.

    # ------------------------------------------------------------------ public

    def search(
        self,
        query: str,
        models: Optional[Iterable[str]] = None,
        limit: Optional[int] = None,
    ) -> List[dict]:
        limit = limit or self.config.default_results_limit
        model_list = list(models) if models else None
        if self.config.langgraph.enabled:
            try:
                return self._search_via_graph(query, models=model_list, limit=limit)
            except Exception as exc:  # noqa: BLE001
                if not self.config.langgraph.fallback_on_error:
                    raise
                log.warning("LangGraph search failed, falling back to linear path: %s", exc)
        return self._search_linear(query, models=model_list, limit=limit)

    def find_similar(
        self,
        instance,
        limit: Optional[int] = None,
    ) -> List[dict]:
        limit = limit or self.config.default_results_limit
        model_cfg = self._find_model_config(instance._meta.label)
        text = self.resolver.build_searchable_text(instance, model_cfg)
        # Reuse the same graph if requested; otherwise stay on the linear path
        # because instance-level similarity has historically been simpler.
        if self.config.langgraph.enabled and self.config.langgraph.use_for_similar:
            try:
                return self._search_via_graph(
                    text,
                    models=[instance._meta.label],
                    limit=limit,
                )
            except Exception as exc:  # noqa: BLE001
                if not self.config.langgraph.fallback_on_error:
                    raise
                log.warning("LangGraph find_similar failed, falling back: %s", exc)

        query_vector = self.embedding_backend.embed(text)
        results = self.vector_store.search(
            query_vector,
            limit=limit,
            filters={"model": instance._meta.label},
        )
        return [self._format_result(item) for item in results]

    # ----------------------------------------------------------- legacy path

    def _search_linear(
        self,
        query: str,
        *,
        models: Optional[List[str]],
        limit: int,
    ) -> List[dict]:
        """Original deterministic search path. Kept for backwards compatibility."""
        query_vector = self.embedding_backend.embed(query)
        results = self.vector_store.search(query_vector, limit=limit, filters=None)
        if models:
            allowed = set(models)
            results = [item for item in results if item.metadata.get("model") in allowed]
        return [self._format_result(item) for item in results]

    # ---------------------------------------------------------- LangGraph path

    def _search_via_graph(
        self,
        query: str,
        *,
        models: Optional[List[str]],
        limit: int,
    ) -> List[dict]:
        graph = self._get_or_build_graph()
        state = {
            "query": query,
            "models": models,
            "limit": limit,
            "rerank_top_k": self.config.langgraph.rerank_top_k,
        }
        out = graph.invoke(state)
        results = out.get("final_results") or []
        return [self._format_result(item) for item in results]

    def _get_or_build_graph(self):
        if self._compiled_graph is not None:
            return self._compiled_graph
        from .langgraph_agent import resolve_graph_factory

        factory = resolve_graph_factory(self.config.langgraph.search_graph)
        llm = self._llm_backend or build_llm_backend(self.config.langgraph.llm)
        self._compiled_graph = factory(
            self.config,
            embedding_backend=self.embedding_backend,
            vector_store=self.vector_store,
            llm=llm,
        )
        return self._compiled_graph

    # --------------------------------------------------------------- helpers

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
        return ModelConfig(model=model_label, fields=[], follow_relations=True)

    def _get_model_class(self, model_label: str):
        if "." not in model_label:
            return apps.get_model(model_label)
        app_label, model_name = model_label.split(".", 1)
        return apps.get_model(app_label, model_name)
