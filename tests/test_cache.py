"""Тесты TTL и purge для FileDeltaCache."""
from __future__ import annotations

import json
import os
import time
from unittest import mock

import pytest

from django_graph_search.cache import DjangoCacheDeltaCache, FileDeltaCache


def test_file_delta_cache_set_get_respects_ttl(tmp_path):
    d = str(tmp_path / "c")
    cache = FileDeltaCache(d)
    cache.set("k1", "v1", ttl=3600)
    assert cache.get("k1") == "v1"


def test_file_delta_cache_expired_removed_on_get(tmp_path):
    d = str(tmp_path / "c2")
    cache = FileDeltaCache(d)
    t0 = 1_000_000.0
    with mock.patch("django_graph_search.cache.time") as mt:
        mt.time.return_value = t0
        cache.set("k2", "gone", ttl=10)
        mt.time.return_value = t0 + 100
        assert cache.get("k2") is None


def test_file_delta_cache_legacy_payload_without_expires_never_expires(tmp_path):
    d = str(tmp_path / "c3")
    cache = FileDeltaCache(d)
    path = cache._key_to_path("legacy")
    os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"value": "old"}, f)
    assert cache.get("legacy") == "old"


def test_purge_expired_deletes_and_dry_run_counts(tmp_path):
    d = str(tmp_path / "c4")
    cache = FileDeltaCache(d)
    past = time.time() - 100
    path = cache._key_to_path("exp")
    os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"value": "x", "expires_at": past}, f)
    n_dry = cache.purge_expired(dry_run=True)
    assert n_dry == 1
    assert os.path.exists(path)
    n = cache.purge_expired(dry_run=False)
    assert n == 1
    assert not os.path.exists(path)


@pytest.mark.django_db
def test_django_cache_delta_purge_is_noop(settings):
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    }
    cache = DjangoCacheDeltaCache(alias="default", key_prefix="dgs")
    assert cache.purge_expired() == 0
    assert cache.purge_expired(dry_run=True) == 0
