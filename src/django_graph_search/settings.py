from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Iterable, List

from django.conf import settings as django_settings
from django.utils.module_loading import import_string

from .exceptions import ConfigurationError


DEFAULTS: Dict[str, Any] = {
    "MODELS": [],
    "VECTOR_STORE": {
        "BACKEND": "django_graph_search.backends.ChromaDBBackend",
        "OPTIONS": {},
    },
    "EMBEDDINGS": {
        "default": {
            "BACKEND": "django_graph_search.embeddings.SentenceTransformerBackend",
            "MODEL_NAME": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "OPTIONS": {},
        }
    },
    "DEFAULT_EMBEDDING": "default",
    "API_URL_PREFIX": "api/search/",
    "ADMIN_SEARCH_ENABLED": True,
    "AUTO_INDEX": True,
    "DEFAULT_RESULTS_LIMIT": 20,
    "RELATION_DEPTH_DEFAULT": 2,
    "DELTA_INDEXING": False,
    "CACHE": {
        "BACKEND": "file",
        "OPTIONS": {},
        "KEY_PREFIX": "dgs",
        "TTL": 86400,
    },
}


@dataclass(frozen=True)
class ModelConfig:
    model: str
    fields: List[str]
    follow_relations: bool = True
    relation_depth: int = 2
    weight_fields: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorStoreConfig:
    backend: str
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingProfile:
    backend: str
    model_name: str
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CacheConfig:
    backend: str
    options: Dict[str, Any] = field(default_factory=dict)
    key_prefix: str = "dgs"
    ttl: int = 86400


@dataclass(frozen=True)
class GraphSearchConfig:
    models: List[ModelConfig]
    vector_store: VectorStoreConfig
    embeddings: Dict[str, EmbeddingProfile]
    default_embedding: str
    api_url_prefix: str
    admin_search_enabled: bool
    auto_index: bool
    default_results_limit: int
    delta_indexing: bool
    cache: CacheConfig


def _merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _validate_models(models: Iterable[Dict[str, Any]], depth_default: int) -> List[ModelConfig]:
    normalized: List[ModelConfig] = []
    for item in models:
        if not isinstance(item, dict):
            raise ConfigurationError("Each model config must be a dict.")
        model = item.get("model")
        if not model or not isinstance(model, str):
            raise ConfigurationError("Model config requires 'model' string.")
        fields = item.get("fields")
        if not fields or not isinstance(fields, list):
            raise ConfigurationError("Model config requires 'fields' list.")
        follow_relations = bool(item.get("follow_relations", True))
        relation_depth = int(item.get("relation_depth", depth_default))
        weight_fields = item.get("weight_fields", {})
        if weight_fields and not isinstance(weight_fields, dict):
            raise ConfigurationError("'weight_fields' must be a dict.")
        normalized.append(
            ModelConfig(
                model=model,
                fields=fields,
                follow_relations=follow_relations,
                relation_depth=relation_depth,
                weight_fields=weight_fields or {},
            )
        )
    return normalized


def _load_backend(path: str):
    if not path or not isinstance(path, str):
        raise ConfigurationError("Backend path must be a non-empty string.")
    return import_string(path)


def _normalize_embeddings(merged: Dict[str, Any]) -> Dict[str, Any]:
    if "EMBEDDINGS" in merged and isinstance(merged.get("EMBEDDINGS"), dict):
        return merged["EMBEDDINGS"]
    legacy = merged.get("EMBEDDING")
    if isinstance(legacy, dict):
        return {"default": legacy}
    return DEFAULTS["EMBEDDINGS"]


@lru_cache(maxsize=1)
def get_settings() -> GraphSearchConfig:
    user_settings = getattr(django_settings, "GRAPH_SEARCH", {})
    if user_settings and not isinstance(user_settings, dict):
        raise ConfigurationError("GRAPH_SEARCH must be a dict.")

    merged = _merge_dicts(DEFAULTS, user_settings or {})
    models = _validate_models(merged["MODELS"], merged["RELATION_DEPTH_DEFAULT"])

    vector_store = VectorStoreConfig(
        backend=merged["VECTOR_STORE"]["BACKEND"],
        options=merged["VECTOR_STORE"].get("OPTIONS", {}),
    )
    embedding_map = _normalize_embeddings(merged)
    embeddings: Dict[str, EmbeddingProfile] = {}
    for name, payload in embedding_map.items():
        if not isinstance(payload, dict):
            raise ConfigurationError("Each embedding profile must be a dict.")
        embeddings[name] = EmbeddingProfile(
            backend=payload["BACKEND"],
            model_name=payload["MODEL_NAME"],
            options=payload.get("OPTIONS", {}),
        )
    default_embedding = merged.get("DEFAULT_EMBEDDING", "default")
    if default_embedding not in embeddings:
        raise ConfigurationError("DEFAULT_EMBEDDING must exist in EMBEDDINGS.")
    cache_cfg = CacheConfig(
        backend=merged["CACHE"]["BACKEND"],
        options=merged["CACHE"].get("OPTIONS", {}),
        key_prefix=merged["CACHE"].get("KEY_PREFIX", "dgs"),
        ttl=int(merged["CACHE"].get("TTL", 86400)),
    )

    # Validate backend paths early
    _load_backend(vector_store.backend)
    for profile in embeddings.values():
        _load_backend(profile.backend)

    return GraphSearchConfig(
        models=models,
        vector_store=vector_store,
        embeddings=embeddings,
        default_embedding=default_embedding,
        api_url_prefix=merged["API_URL_PREFIX"],
        admin_search_enabled=bool(merged["ADMIN_SEARCH_ENABLED"]),
        auto_index=bool(merged["AUTO_INDEX"]),
        default_results_limit=int(merged["DEFAULT_RESULTS_LIMIT"]),
        delta_indexing=bool(merged.get("DELTA_INDEXING", False)),
        cache=cache_cfg,
    )

