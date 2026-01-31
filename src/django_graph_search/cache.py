from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from hashlib import sha256
from typing import Optional

from django.core.cache import caches

from .settings import CacheConfig, GraphSearchConfig


class BaseDeltaCache(ABC):
    @abstractmethod
    def get(self, key: str) -> Optional[str]:
        raise NotImplementedError

    @abstractmethod
    def set(self, key: str, value: str, ttl: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete(self, key: str) -> None:
        raise NotImplementedError


class FileDeltaCache(BaseDeltaCache):
    def __init__(self, directory: str) -> None:
        self.directory = directory
        os.makedirs(self.directory, exist_ok=True)

    def get(self, key: str) -> Optional[str]:
        path = self._key_to_path(key)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return payload.get("value")
        except Exception:
            return None

    def set(self, key: str, value: str, ttl: int) -> None:
        path = self._key_to_path(key)
        payload = {"value": value}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    def delete(self, key: str) -> None:
        path = self._key_to_path(key)
        if os.path.exists(path):
            os.remove(path)

    def _key_to_path(self, key: str) -> str:
        digest = sha256(key.encode("utf-8")).hexdigest()
        return os.path.join(self.directory, f"{digest}.json")


class DjangoCacheDeltaCache(BaseDeltaCache):
    def __init__(self, alias: str, key_prefix: str) -> None:
        self.cache = caches[alias]
        self.key_prefix = key_prefix

    def get(self, key: str) -> Optional[str]:
        return self.cache.get(self._build_key(key))

    def set(self, key: str, value: str, ttl: int) -> None:
        self.cache.set(self._build_key(key), value, timeout=ttl)

    def delete(self, key: str) -> None:
        self.cache.delete(self._build_key(key))

    def _build_key(self, key: str) -> str:
        return f"{self.key_prefix}:{key}"


def build_delta_cache(config: GraphSearchConfig) -> BaseDeltaCache:
    cache_cfg: CacheConfig = config.cache
    backend = cache_cfg.backend.lower()
    if backend == "file":
        directory = cache_cfg.options.get("path") or cache_cfg.options.get("directory")
        if not directory:
            directory = ".graph_search_cache"
        return FileDeltaCache(directory)
    if backend in {"redis", "db"}:
        alias = cache_cfg.options.get("alias", "default")
        return DjangoCacheDeltaCache(alias=alias, key_prefix=cache_cfg.key_prefix)
    raise ValueError(f"Unsupported cache backend: {cache_cfg.backend}")

