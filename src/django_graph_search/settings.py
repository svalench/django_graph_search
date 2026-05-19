from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
    "AUTO_INDEX_SKIP_UPDATE_FIELDS": ["last_login"],
    # Локальный sentence-transformers не блокирует HTTP: индексация в daemon thread.
    "AUTO_INDEX_NON_BLOCKING": True,
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
    "SMART_INDEXING": {
        "ENABLED": False,
        "INDEXER": "django_graph_search.langgraph_indexer.SmartIndexer",
        "TEMPLATES": {},
    },
    "STREAMING": {
        "ENABLED": False,
        "FORMAT": "ndjson",  # "ndjson" or "sse"
        "INCLUDE_INTERNAL_EVENTS": True,
    },
    "API": {
        "PERMISSION_CLASSES": [],
        "THROTTLE_CLASSES": [],
        "THROTTLE_RATES": {
            "search": "60/minute",
            "search_authenticated": "300/minute",
        },
        "REQUIRE_AUTHENTICATION": False,
    },
    "ASYNC_INDEXING": {
        "ENABLED": False,
        "BACKEND": "celery",
        "CELERY_QUEUE": "search_indexing",
        "CELERY_TASK_PATH": "django_graph_search.tasks.index_instance_task",
        "CELERY_DELETE_TASK_PATH": "django_graph_search.tasks.delete_instance_task",
        "THREAD_POOL_SIZE": 4,
    },
}


@dataclass(frozen=True)
class ModelConfig:
    model: str
    fields: List[str]
    follow_relations: bool = True
    relation_depth: int = 2
    weight_fields: Dict[str, float] = field(default_factory=dict)
    # save(update_fields=...): только эти поля — post_save не индексирует (+ глобальный список).
    skip_update_fields: Tuple[str, ...] = field(default_factory=tuple)


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
class SmartIndexingConfig:
    enabled: bool = False
    indexer: str = "django_graph_search.langgraph_indexer.SmartIndexer"
    templates: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StreamingConfig:
    enabled: bool = False
    format: str = "ndjson"
    include_internal_events: bool = True


@dataclass(frozen=True)
class ApiConfig:
    """Настройки доступа и throttling для REST search API."""

    permission_classes: List[str] = field(default_factory=list)
    throttle_classes: List[str] = field(default_factory=list)
    throttle_rates: Dict[str, str] = field(default_factory=dict)
    require_authentication: bool = False


@dataclass(frozen=True)
class AsyncIndexingConfig:
    """Опциональная асинхронная переиндексация по сигналам (Celery / поток / django-q)."""

    enabled: bool = False
    backend: str = "celery"
    celery_queue: str = "search_indexing"
    celery_task_path: str = "django_graph_search.tasks.index_instance_task"
    celery_delete_task_path: str = "django_graph_search.tasks.delete_instance_task"
    thread_pool_size: int = 4


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
    auto_index_skip_update_fields: Tuple[str, ...] = ("last_login",)
    auto_index_non_blocking: bool = True
    langgraph: LangGraphConfig = field(default_factory=LangGraphConfig)
    conversational: ConversationalConfig = field(default_factory=ConversationalConfig)
    smart_indexing: SmartIndexingConfig = field(default_factory=SmartIndexingConfig)
    streaming: StreamingConfig = field(default_factory=StreamingConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    async_indexing: AsyncIndexingConfig = field(default_factory=AsyncIndexingConfig)


def _merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalize_weight_fields(raw: Any) -> Dict[str, float]:
    """
    Нормализует веса полей в float (в т.ч. при fields='__all__').

    Нулевой или отрицательный вес допускается: граф-резолвер исключит поле из текста.
    """
    if raw is None or raw == {}:
        return {}
    if not isinstance(raw, dict):
        raise ConfigurationError("'weight_fields' must be a dict.")
    out: Dict[str, float] = {}
    for key, val in raw.items():
        if not isinstance(key, str):
            key = str(key)
        try:
            out[key] = float(val)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(
                f"weight_fields[{key!r}] must be a number."
            ) from exc
    return out


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
        # Веса парсятся всегда, даже для fields == ["__all__"] (известные имена полей).
        weight_fields = _normalize_weight_fields(item.get("weight_fields", {}))
        skip_raw = item.get("skip_update_fields")
        skip_update_fields: Tuple[str, ...] = ()
        if skip_raw is not None:
            if not isinstance(skip_raw, (list, tuple)):
                raise ConfigurationError("'skip_update_fields' must be a list of field names.")
            skip_update_fields = tuple(str(f) for f in skip_raw)
        normalized.append(
            ModelConfig(
                model=model,
                fields=fields,
                follow_relations=follow_relations,
                relation_depth=relation_depth,
                weight_fields=weight_fields,
                skip_update_fields=skip_update_fields,
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
    smart_indexing_cfg = _build_smart_indexing_config(merged.get("SMART_INDEXING") or {})
    streaming_cfg = _build_streaming_config(merged.get("STREAMING") or {})
    api_cfg = _build_api_config(merged.get("API") or {})
    async_indexing_cfg = _build_async_indexing_config(merged.get("ASYNC_INDEXING") or {})
    skip_update_raw = merged.get("AUTO_INDEX_SKIP_UPDATE_FIELDS")
    if skip_update_raw is None:
        skip_update_fields: Tuple[str, ...] = ("last_login",)
    elif not isinstance(skip_update_raw, (list, tuple)):
        raise ConfigurationError("AUTO_INDEX_SKIP_UPDATE_FIELDS must be a list of field names.")
    else:
        skip_update_fields = tuple(str(f) for f in skip_update_raw)

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
        auto_index_skip_update_fields=skip_update_fields,
        auto_index_non_blocking=bool(merged.get("AUTO_INDEX_NON_BLOCKING", True)),
        default_results_limit=int(merged["DEFAULT_RESULTS_LIMIT"]),
        delta_indexing=bool(merged.get("DELTA_INDEXING", False)),
        cache=cache_cfg,
        langgraph=langgraph_cfg,
        conversational=conversational_cfg,
        smart_indexing=smart_indexing_cfg,
        streaming=streaming_cfg,
        api=api_cfg,
        async_indexing=async_indexing_cfg,
    )


def _build_async_indexing_config(payload: Dict[str, Any]) -> AsyncIndexingConfig:
    """Построить AsyncIndexingConfig из GRAPH_SEARCH['ASYNC_INDEXING']."""
    if not isinstance(payload, dict):
        raise ConfigurationError("ASYNC_INDEXING must be a dict.")
    defaults = DEFAULTS["ASYNC_INDEXING"]
    merged = _merge_dicts(defaults, payload)
    backend = str(merged.get("BACKEND") or "celery").lower()
    if backend not in {"celery", "django_q", "thread"}:
        raise ConfigurationError(
            "ASYNC_INDEXING.BACKEND must be 'celery', 'django_q', or 'thread'."
        )
    task_path = merged.get("CELERY_TASK_PATH") or defaults["CELERY_TASK_PATH"]
    if not isinstance(task_path, str):
        raise ConfigurationError("ASYNC_INDEXING.CELERY_TASK_PATH must be a string.")
    delete_path = merged.get("CELERY_DELETE_TASK_PATH") or defaults["CELERY_DELETE_TASK_PATH"]
    if not isinstance(delete_path, str):
        raise ConfigurationError("ASYNC_INDEXING.CELERY_DELETE_TASK_PATH must be a string.")
    queue = merged.get("CELERY_QUEUE") or defaults["CELERY_QUEUE"]
    if not isinstance(queue, str):
        raise ConfigurationError("ASYNC_INDEXING.CELERY_QUEUE must be a string.")
    pool = int(merged.get("THREAD_POOL_SIZE", 4))
    if pool < 1:
        raise ConfigurationError("ASYNC_INDEXING.THREAD_POOL_SIZE must be >= 1.")
    return AsyncIndexingConfig(
        enabled=bool(merged.get("ENABLED", False)),
        backend=backend,
        celery_queue=queue,
        celery_task_path=task_path,
        celery_delete_task_path=delete_path,
        thread_pool_size=pool,
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


def _build_smart_indexing_config(payload: Dict[str, Any]) -> SmartIndexingConfig:
    if not isinstance(payload, dict):
        raise ConfigurationError("SMART_INDEXING must be a dict.")
    defaults = DEFAULTS["SMART_INDEXING"]
    merged = _merge_dicts(defaults, payload)
    templates = merged.get("TEMPLATES") or {}
    if not isinstance(templates, dict):
        raise ConfigurationError("SMART_INDEXING.TEMPLATES must be a dict.")
    indexer_path = merged.get("INDEXER") or "django_graph_search.langgraph_indexer.SmartIndexer"
    if not isinstance(indexer_path, str):
        raise ConfigurationError("SMART_INDEXING.INDEXER must be a dotted-path string.")
    return SmartIndexingConfig(
        enabled=bool(merged.get("ENABLED", False)),
        indexer=indexer_path,
        templates=templates,
    )


def _build_streaming_config(payload: Dict[str, Any]) -> StreamingConfig:
    if not isinstance(payload, dict):
        raise ConfigurationError("STREAMING must be a dict.")
    defaults = DEFAULTS["STREAMING"]
    merged = _merge_dicts(defaults, payload)
    fmt = str(merged.get("FORMAT") or "ndjson").lower()
    if fmt not in {"ndjson", "sse"}:
        raise ConfigurationError("STREAMING.FORMAT must be 'ndjson' or 'sse'.")
    return StreamingConfig(
        enabled=bool(merged.get("ENABLED", False)),
        format=fmt,
        include_internal_events=bool(merged.get("INCLUDE_INTERNAL_EVENTS", True)),
    )


def _build_api_config(payload: Dict[str, Any]) -> ApiConfig:
    """Построить ApiConfig из пользовательского dict GRAPH_SEARCH['API']."""
    if not isinstance(payload, dict):
        raise ConfigurationError("API must be a dict.")
    defaults = DEFAULTS["API"]
    merged = _merge_dicts(defaults, payload)
    permission_classes = merged.get("PERMISSION_CLASSES") or []
    throttle_classes = merged.get("THROTTLE_CLASSES") or []
    if not isinstance(permission_classes, list):
        raise ConfigurationError("API.PERMISSION_CLASSES must be a list.")
    if not isinstance(throttle_classes, list):
        raise ConfigurationError("API.THROTTLE_CLASSES must be a list.")
    throttle_rates = merged.get("THROTTLE_RATES") or {}
    if not isinstance(throttle_rates, dict):
        raise ConfigurationError("API.THROTTLE_RATES must be a dict.")
    normalized_rates: Dict[str, str] = {}
    for key, val in throttle_rates.items():
        if val is not None:
            normalized_rates[str(key)] = str(val)
    return ApiConfig(
        permission_classes=[str(p) for p in permission_classes],
        throttle_classes=[str(t) for t in throttle_classes],
        throttle_rates=normalized_rates,
        require_authentication=bool(merged.get("REQUIRE_AUTHENTICATION", False)),
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


def clear_graph_search_caches() -> None:
    """Сброс кэша настроек и реестра тяжёлых компонентов (для тестов и reload)."""
    get_settings.cache_clear()
    from .component_registry import clear_component_registry

    clear_component_registry()

