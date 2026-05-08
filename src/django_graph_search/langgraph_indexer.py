"""Optional smart-indexing orchestration layer.

The classic :class:`~django_graph_search.indexer.Indexer` glues field text
together with simple whitespace separators. That is fine for many use cases
but loses the field-name signal that helps semantic search distinguish, say,
a category name from a description.

This module adds an opt-in pipeline that builds *structured* documents with
labelled sections such as ``Title:``, ``Description:`` etc. The pipeline is
deterministic and never invents data — it only reformats what the existing
:class:`~django_graph_search.graph_resolver.GraphResolver` returns.

It is implemented as a separate module so the original indexer remains
untouched and continues to be the default. Users opt in by enabling the
``SMART_INDEXING`` flag and (optionally) registering per-model templates.

Pipeline:

```
inspect_model -> collect_fields -> enrich_document -> embed_batch -> persist
```
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from django.db import models

from .backends.base import Document
from .components import ComponentMixin
from .graph_resolver import GraphResolver
from .indexer import make_doc_id
from .settings import GraphSearchConfig, ModelConfig
from .utils import hash_text

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


@dataclass
class FieldSection:
    """A single labelled section in the structured document text."""

    label: str
    field: str  # field path, may contain "__" for relation traversal.
    multiline: bool = False


@dataclass
class DocumentTemplate:
    """How to format an instance of a single model into structured text."""

    title_field: Optional[str] = None
    sections: List[FieldSection] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "DocumentTemplate":
        if not isinstance(payload, dict):
            raise TypeError("DocumentTemplate payload must be a dict.")
        sections = []
        for section in payload.get("sections", []) or []:
            if not isinstance(section, dict):
                continue
            sections.append(
                FieldSection(
                    label=str(section.get("label", "")).strip(),
                    field=str(section.get("field", "")).strip(),
                    multiline=bool(section.get("multiline", False)),
                )
            )
        return cls(
            title_field=payload.get("title_field") or None,
            sections=sections,
        )


def default_template_for(config: ModelConfig) -> DocumentTemplate:
    """Heuristic template when the user has not configured one explicitly.

    We never invent fields — we only label fields that already exist in the
    model config. Common names (``name``, ``title``) become the document
    title, the rest become labelled sections.
    """
    title_candidates = ("title", "name", "headline", "label")
    title_field: Optional[str] = None
    sections: List[FieldSection] = []

    fields = config.fields if config.fields and config.fields != ["__all__"] else []
    for path in fields:
        if title_field is None and path in title_candidates:
            title_field = path
            continue
        sections.append(
            FieldSection(
                label=_humanise(path),
                field=path,
                multiline="description" in path or "body" in path,
            )
        )
    return DocumentTemplate(title_field=title_field, sections=sections)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def inspect_model_node(state: Dict[str, Any], *, config: GraphSearchConfig) -> Dict[str, Any]:
    cfg: ModelConfig = state["model_config"]
    templates = state.get("templates") or {}
    template = templates.get(cfg.model) or default_template_for(cfg)
    state["template"] = template
    return state


def collect_fields_node(
    state: Dict[str, Any], *, resolver: GraphResolver
) -> Dict[str, Any]:
    """Resolve all field paths declared by the template for each instance."""
    template: DocumentTemplate = state["template"]
    instances: List[models.Model] = state["instances"]
    cfg: ModelConfig = state["model_config"]
    rendered: List[Dict[str, Any]] = []
    for instance in instances:
        bag: Dict[str, Any] = {"_instance": instance}
        if template.title_field:
            bag["__title__"] = _resolve_text(resolver, instance, template.title_field)
        for section in template.sections:
            if not section.field:
                continue
            bag[section.field] = _resolve_text(resolver, instance, section.field)
        # Always include the original deterministic text as a safety net so
        # we never index *less* information than the legacy pipeline.
        bag["__legacy__"] = resolver.build_searchable_text(instance, cfg)
        rendered.append(bag)
    state["rendered"] = rendered
    return state


def enrich_document_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Compose labelled multi-section text per instance."""
    template: DocumentTemplate = state["template"]
    rendered: List[Dict[str, Any]] = state["rendered"]
    docs: List[Dict[str, Any]] = []
    for bag in rendered:
        instance: models.Model = bag["_instance"]
        parts: List[str] = []
        title = bag.get("__title__")
        if title:
            parts.append(f"Title: {title}")
        for section in template.sections:
            value = bag.get(section.field)
            if not value:
                continue
            sep = "\n" if section.multiline else " "
            label = section.label or _humanise(section.field)
            parts.append(f"{label}:{sep}{value}")
        legacy = bag.get("__legacy__")
        if legacy:
            parts.append(legacy)
        text = "\n".join(parts).strip()
        docs.append({
            "instance": instance,
            "text": text,
            "text_hash": hash_text(text),
        })
    state["documents"] = docs
    return state


def embed_batch_node(
    state: Dict[str, Any], *, embedding_backend
) -> Dict[str, Any]:
    documents = state["documents"]
    if not documents:
        state["embeddings"] = []
        return state
    state["embeddings"] = embedding_backend.embed_batch([d["text"] for d in documents])
    return state


def persist_node(
    state: Dict[str, Any],
    *,
    vector_store,
    delta_cache,
    cache_ttl: int,
) -> Dict[str, Any]:
    documents = state["documents"]
    embeddings = state["embeddings"]
    payload: List[Document] = []
    for doc, embedding in zip(documents, embeddings):
        instance: models.Model = doc["instance"]
        doc_id = make_doc_id(instance._meta.label, instance.pk)
        if delta_cache is not None:
            cached_hash = delta_cache.get(doc_id)
            if cached_hash == doc["text_hash"]:
                continue
        payload.append(
            Document(
                id=doc_id,
                embedding=embedding,
                metadata={"model": instance._meta.label, "pk": instance.pk},
                text=doc["text"],
            )
        )
    if not payload:
        state["written"] = 0
        return state
    vector_store.add_documents(payload)
    if delta_cache is not None:
        for doc in documents:
            instance = doc["instance"]
            delta_cache.set(
                make_doc_id(instance._meta.label, instance.pk),
                doc["text_hash"],
                ttl=cache_ttl,
            )
    state["written"] = len(payload)
    return state


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------


class SmartIndexer(ComponentMixin):
    """Drop-in alternative to :class:`~django_graph_search.indexer.Indexer`.

    The classic indexer is left in place as the default; ``SmartIndexer`` is
    returned by :func:`~django_graph_search.indexer.get_indexer` only when
    ``SMART_INDEXING.ENABLED`` is true. It produces structured per-section
    text that helps semantic search distinguish the role of each field.
    """

    def __init__(
        self,
        config: Optional[GraphSearchConfig] = None,
        vector_store=None,
        embedding_backend=None,
        resolver: Optional[GraphResolver] = None,
        embedding_profile: Optional[str] = None,
        templates: Optional[Dict[str, DocumentTemplate]] = None,
        delta_cache=None,
    ) -> None:
        self._init_components(
            config=config,
            vector_store=vector_store,
            embedding_backend=embedding_backend,
            resolver=resolver,
            embedding_profile=embedding_profile,
        )
        # Templates may be supplied directly or come from settings.
        if templates is None:
            templates = getattr(self.config.smart_indexing, "templates", {}) or {}
        self.templates = self._normalise_templates(templates)
        self.delta_cache = delta_cache
        if self.delta_cache is None and self.config.delta_indexing:
            from .cache import build_delta_cache

            self.delta_cache = build_delta_cache(self.config)

    @staticmethod
    def _normalise_templates(
        templates: Dict[str, Any]
    ) -> Dict[str, DocumentTemplate]:
        out: Dict[str, DocumentTemplate] = {}
        for key, value in templates.items():
            if isinstance(value, DocumentTemplate):
                out[key] = value
            elif isinstance(value, dict):
                out[key] = DocumentTemplate.from_dict(value)
            else:
                raise TypeError(
                    f"Templates must be DocumentTemplate or dict, got {type(value)!r}"
                )
        return out

    def index_instance(self, instance: models.Model, config: ModelConfig) -> None:
        """Mirror :meth:`Indexer.index_instance` for compatibility."""
        self._index_batch([instance], config)

    def delete_instance(self, model_name: str, pk: object) -> None:
        """Mirror :meth:`Indexer.delete_instance` for compatibility."""
        doc_id = make_doc_id(model_name, pk)
        self.vector_store.delete([doc_id])
        if self.delta_cache is not None:
            self.delta_cache.delete(doc_id)

    def rebuild_all(self) -> dict:
        from django.apps import apps

        result: Dict[str, int] = {}
        for model_cfg in self.config.models:
            app_label, model_name = model_cfg.model.split(".", 1)
            model_cls = apps.get_model(app_label, model_name)
            count = self.index_queryset(model_cls.objects.all(), model_cfg)
            result[model_cfg.model] = count
        return result

    def index_queryset(
        self,
        queryset,
        config: ModelConfig,
        batch_size: int = 100,
    ) -> int:
        total = 0
        batch: List[models.Model] = []
        for instance in queryset.iterator():
            batch.append(instance)
            if len(batch) >= batch_size:
                total += self._index_batch(batch, config)
                batch = []
        if batch:
            total += self._index_batch(batch, config)
        return total

    def _index_batch(
        self, batch: List[models.Model], cfg: ModelConfig
    ) -> int:
        state: Dict[str, Any] = {
            "instances": list(batch),
            "model_config": cfg,
            "templates": self.templates,
        }
        state = inspect_model_node(state, config=self.config)
        state = collect_fields_node(state, resolver=self.resolver)
        state = enrich_document_node(state)
        state = embed_batch_node(state, embedding_backend=self.embedding_backend)
        state = persist_node(
            state,
            vector_store=self.vector_store,
            delta_cache=self.delta_cache,
            cache_ttl=self.config.cache.ttl,
        )
        return int(state.get("written", 0))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _humanise(field_path: str) -> str:
    return field_path.replace("__", " · ").replace("_", " ").title()


def _resolve_text(resolver: GraphResolver, instance, field_path: str) -> str:
    value = resolver._resolve_path(instance, field_path)  # noqa: SLF001
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        flat: List[str] = []
        for item in value:
            if isinstance(item, list):
                flat.extend([str(x) for x in item if x not in (None, "")])
            elif item not in (None, ""):
                flat.append(str(item))
        return ", ".join(flat)
    return str(value)


__all__ = [
    "DocumentTemplate",
    "FieldSection",
    "SmartIndexer",
    "default_template_for",
    "inspect_model_node",
    "collect_fields_node",
    "enrich_document_node",
    "embed_batch_node",
    "persist_node",
]
