from __future__ import annotations

from .apps import DjangoGraphSearchConfig
from .indexer import Indexer
from .searcher import Searcher
from .settings import get_settings

default_app_config = "django_graph_search.apps.DjangoGraphSearchConfig"


def search(query: str, models=None, limit: int | None = None):
    return Searcher().search(query, models=models, limit=limit)


def index(instance) -> None:
    config = get_settings()
    model_cfg = next((cfg for cfg in config.models if cfg.model == instance._meta.label), None)
    if model_cfg is None:
        return
    Indexer(config=config).index_instance(instance, model_cfg)


def get_similar(instance, limit: int | None = None):
    return Searcher().find_similar(instance, limit=limit)


__all__ = [
    "DjangoGraphSearchConfig",
    "Indexer",
    "Searcher",
    "search",
    "index",
    "get_similar",
]

