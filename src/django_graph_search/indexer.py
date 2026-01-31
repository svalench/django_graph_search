from __future__ import annotations

from typing import Iterable, List, Optional

from django.apps import apps
from django.db import models
from django.utils.module_loading import import_string

from .backends.base import Document
from .cache import BaseDeltaCache, build_delta_cache
from .exceptions import ConfigurationError
from .graph_resolver import GraphResolver
from .settings import GraphSearchConfig, ModelConfig, get_settings
from .utils import hash_text


def make_doc_id(model_label: str, pk: object) -> str:
    return f"{model_label}:{pk}"


class Indexer:
    def __init__(
        self,
        config: Optional[GraphSearchConfig] = None,
        vector_store=None,
        embedding_backend=None,
        resolver: Optional[GraphResolver] = None,
        embedding_profile: Optional[str] = None,
        delta_cache: Optional[BaseDeltaCache] = None,
    ) -> None:
        self.config = config or get_settings()
        if vector_store is None:
            backend_cls = import_string(self.config.vector_store.backend)
            vector_store = backend_cls(**self.config.vector_store.options)
        if embedding_backend is None:
            profile_name = embedding_profile or self.config.default_embedding
            profile = self.config.embeddings[profile_name]
            embed_cls = import_string(profile.backend)
            embedding_backend = embed_cls(
                model_name=profile.model_name,
                **profile.options,
            )
        self.vector_store = vector_store
        self.embedding_backend = embedding_backend
        self.resolver = resolver or GraphResolver()
        self.delta_cache = delta_cache
        if self.delta_cache is None and self.config.delta_indexing:
            self.delta_cache = build_delta_cache(self.config)

    def index_queryset(
        self,
        queryset: models.QuerySet,
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

    def index_instance(self, instance: models.Model, config: ModelConfig) -> None:
        self._index_batch([instance], config)

    def delete_instance(self, model_name: str, pk: object) -> None:
        doc_id = make_doc_id(model_name, pk)
        self.vector_store.delete([doc_id])
        if self.delta_cache is not None:
            self.delta_cache.delete(doc_id)

    def rebuild_all(self) -> dict:
        result = {}
        for model_cfg in self.config.models:
            model_cls = self._get_model_class(model_cfg.model)
            count = self.index_queryset(model_cls.objects.all(), model_cfg)
            result[model_cfg.model] = count
        return result

    def _index_batch(self, batch: Iterable[models.Model], config: ModelConfig) -> int:
        prepared = []
        for instance in batch:
            text = self.resolver.build_searchable_text(instance, config)
            text_hash = hash_text(text)
            doc_id = make_doc_id(instance._meta.label, instance.pk)
            if self.delta_cache is not None:
                cached_hash = self.delta_cache.get(doc_id)
                if cached_hash == text_hash:
                    continue
            prepared.append((instance, text, text_hash))

        if not prepared:
            return 0

        texts = [item[1] for item in prepared]
        embeddings = self.embedding_backend.embed_batch(texts)
        documents: List[Document] = []
        for (instance, text, text_hash), embedding in zip(prepared, embeddings):
            model_label = instance._meta.label
            doc_id = make_doc_id(model_label, instance.pk)
            documents.append(
                Document(
                    id=doc_id,
                    embedding=embedding,
                    metadata={"model": model_label, "pk": instance.pk},
                    text=text,
                )
            )
        self.vector_store.add_documents(documents)
        if self.delta_cache is not None:
            ttl = self.config.cache.ttl
            for instance, _text, text_hash in prepared:
                doc_id = make_doc_id(instance._meta.label, instance.pk)
                self.delta_cache.set(doc_id, text_hash, ttl=ttl)
        return len(documents)

    def _get_model_class(self, model_path: str):
        if "." not in model_path:
            raise ConfigurationError("Model path must be in 'app.Model' format.")
        app_label, model_name = model_path.split(".", 1)
        model_cls = apps.get_model(app_label, model_name)
        if model_cls is None:
            raise ConfigurationError(f"Model '{model_path}' not found.")
        return model_cls

