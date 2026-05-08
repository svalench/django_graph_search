from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional

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
    "LANGGRAPH": {
        "ENABLED": False,
        "SEARCH_GRAPH": "django_graph_search.langgraph_agent.build_search_graph",
        "USE_FOR_SIMILAR": False,
        "QUERY_EXPANSION": False,
        "RERANKING": False,
        "MAX_EXPANDED_QUERIES": 3,
        "RERANK_TOP_K": 20,
        "TIMEOUT_SECONDS": 15,
        "MAX_QUERY_LENGTH": 1024,
        "FALLBACK_ON_ERROR": True,
        "LLM": {
            "BACKEND": None,
            "MODEL": None,
            "OPTIONS": {},
        },
    },
    "CONVERSATIONAL": {
        "ENABLED": False,
        "MEMORY_BACKEND": "inmemory",
        "MEMORY_OPTIONS": {},
        "MAX_HISTORY_ITEMS": 10,
        "ALLOW_CLARIFICATIONS": True,
        "MIN_QUERY_LENGTH_FOR_AUTOSEARCH": 2,
        "FOLLOWUP_GRAPH": "django_graph_search.langgraph_conversation.build_conversation_graph",
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
class LLMConfig:
    backend: Optional[str] = None
    model: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LangGraphConfig:
    enabled: bool = False
    search_graph: str = "django_graph_search.langgraph_agent.build_search_graph"
    use_for_similar: bool = False
    query_expansion: bool = False
    reranking: bool = False
    max_expanded_queries: int = 3
    rerank_top_k: int = 20
    timeout_seconds: int = 15
    max_query_length: int = 1024
    fallback_on_error: bool = True
    llm: LLMConfig = field(default_factory=LLMConfig)


@dataclass(frozen=True)
class ConversationalConfig:
    enabled: bool = False
    memory_backend: str = "inmemory"
    memory_options: Dict[str, Any] = field(default_factory=dict)
    max_history_items: int = 10
    allow_clarifications: bool = True
    min_query_length_for_autosearch: int = 2
    followup_graph: str = "django_graph_search.langgraph_conversation.build_conversation_graph"


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
    langgraph: LangGraphConfig = field(default_factory=LangGraphConfig)
    conversational: ConversationalConfig = field(default_factory=ConversationalConfig)


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
        if fields == "__all__" or (isinstance(fields, list) and fields == ["__all__"]):
            fields = ["__all__"]
        elif not fields or not isinstance(fields, list):
            raise ConfigurationError("Model config requires 'fields' list or '__all__'.")
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

    langgraph_cfg = _build_langgraph_config(merged.get("LANGGRAPH") or {})
    conversational_cfg = _build_conversational_config(merged.get("CONVERSATIONAL") or {})

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
        langgraph=langgraph_cfg,
        conversational=conversational_cfg,
    )


def _build_langgraph_config(payload: Dict[str, Any]) -> LangGraphConfig:
    if not isinstance(payload, dict):
        raise ConfigurationError("LANGGRAPH must be a dict.")
    defaults = DEFAULTS["LANGGRAPH"]
    merged = _merge_dicts(defaults, payload)
    llm_payload = merged.get("LLM") or {}
    if not isinstance(llm_payload, dict):
        raise ConfigurationError("LANGGRAPH.LLM must be a dict.")
    llm_cfg = LLMConfig(
        backend=llm_payload.get("BACKEND"),
        model=llm_payload.get("MODEL"),
        options=llm_payload.get("OPTIONS", {}) or {},
    )
    max_expanded = int(merged.get("MAX_EXPANDED_QUERIES", 3))
    if max_expanded < 1:
        raise ConfigurationError("LANGGRAPH.MAX_EXPANDED_QUERIES must be >= 1.")
    rerank_top_k = int(merged.get("RERANK_TOP_K", 20))
    if rerank_top_k < 1:
        raise ConfigurationError("LANGGRAPH.RERANK_TOP_K must be >= 1.")
    timeout_seconds = int(merged.get("TIMEOUT_SECONDS", 15))
    if timeout_seconds < 1:
        raise ConfigurationError("LANGGRAPH.TIMEOUT_SECONDS must be >= 1.")
    max_query_length = int(merged.get("MAX_QUERY_LENGTH", 1024))
    if max_query_length < 1:
        raise ConfigurationError("LANGGRAPH.MAX_QUERY_LENGTH must be >= 1.")
    return LangGraphConfig(
        enabled=bool(merged.get("ENABLED", False)),
        search_graph=str(merged.get("SEARCH_GRAPH")
                         or "django_graph_search.langgraph_agent.build_search_graph"),
        use_for_similar=bool(merged.get("USE_FOR_SIMILAR", False)),
        query_expansion=bool(merged.get("QUERY_EXPANSION", False)),
        reranking=bool(merged.get("RERANKING", False)),
        max_expanded_queries=max_expanded,
        rerank_top_k=rerank_top_k,
        timeout_seconds=timeout_seconds,
        max_query_length=max_query_length,
        fallback_on_error=bool(merged.get("FALLBACK_ON_ERROR", True)),
        llm=llm_cfg,
    )


def _build_conversational_config(payload: Dict[str, Any]) -> ConversationalConfig:
    if not isinstance(payload, dict):
        raise ConfigurationError("CONVERSATIONAL must be a dict.")
    defaults = DEFAULTS["CONVERSATIONAL"]
    merged = _merge_dicts(defaults, payload)
    max_history = int(merged.get("MAX_HISTORY_ITEMS", 10))
    if max_history < 1:
        raise ConfigurationError("CONVERSATIONAL.MAX_HISTORY_ITEMS must be >= 1.")
    min_qlen = int(merged.get("MIN_QUERY_LENGTH_FOR_AUTOSEARCH", 2))
    if min_qlen < 0:
        raise ConfigurationError(
            "CONVERSATIONAL.MIN_QUERY_LENGTH_FOR_AUTOSEARCH must be >= 0."
        )
    options = merged.get("MEMORY_OPTIONS") or {}
    if not isinstance(options, dict):
        raise ConfigurationError("CONVERSATIONAL.MEMORY_OPTIONS must be a dict.")
    return ConversationalConfig(
        enabled=bool(merged.get("ENABLED", False)),
        memory_backend=str(merged.get("MEMORY_BACKEND") or "inmemory"),
        memory_options=options,
        max_history_items=max_history,
        allow_clarifications=bool(merged.get("ALLOW_CLARIFICATIONS", True)),
        min_query_length_for_autosearch=min_qlen,
        followup_graph=str(
            merged.get("FOLLOWUP_GRAPH")
            or "django_graph_search.langgraph_conversation.build_conversation_graph"
        ),
    )

