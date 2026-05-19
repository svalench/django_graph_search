# django-graph-search 0.3.3

**Release date:** 2026-05-19  
**Type:** Stable (0.3 line — replaces pre-releases `0.3.0a1`, `0.3.1a1`)

```bash
pip install django-graph-search==0.3.3
# optional extras unchanged, e.g.:
pip install django-graph-search[pgvector,openai,all]
```

## Summary

First **stable** 0.3 release: REST scores and `min_score`, smarter indexing weights, async/non-blocking auto-index, pgvector + cloud embeddings, hardened REST API settings, admin index coverage with sidebar navigation, and ChromaDB score fixes aligned with collection metrics.

Upgrading from **0.2.x** is backward-compatible — new settings default to safe/off or sensible production defaults (`AUTO_INDEX_NON_BLOCKING=True` only affects local SentenceTransformer profiles).

## Highlights

### Search & API
- Result objects include **`score`** (0.0–1.0) and indexed **`text`**
- Query param **`?min_score=`** on REST and admin search
- Optional **`GRAPH_SEARCH["API"]`**: DRF-style permission/throttle hooks, `REQUIRE_AUTHENTICATION`
- Invalid **`limit`** → HTTP 400; values above 1000 clamped with warning

### Indexing & signals
- **`weight_fields`** always applied (`fields: "__all__"` supported; `0.0` = exclude field)
- **`ASYNC_INDEXING`**: Celery, `thread`, or django-q via `django_graph_search.tasks`
- **`AUTO_INDEX_NON_BLOCKING`** (default **on**): daemon-thread indexing for local ST without Celery
- **`AUTO_INDEX_SKIP_UPDATE_FIELDS`** / per-model **`skip_update_fields`**: skip re-index on `last_login`-only updates
- **`component_registry`**: one vector store + embedder + resolver per worker config

### Backends & embeddings
- **Pgvector** (`pip install django-graph-search[pgvector]`)
- **OpenAI** / **Cohere** embedding backends
- Normalized **0–1 similarity** across ChromaDB, FAISS, Qdrant; Chroma reads effective HNSW metric

### Admin
- Sidebar: **Поиск**, **Статус индексации**
- **`/admin/graph-search/index-status/`** — DB vs vector store coverage (static snapshot)
- Legacy URLs **`/admin/graph-search/`** preserved

### Fixes
- LangGraph: empty results when `final_results` missing from invoke output
- Chroma cosine / L2 / IP distance mapping
- File delta cache TTL + `purge_search_cache` command
- Conversational in-memory backend warning in production multi-worker setups

## Upgrade notes

| From | Action |
|------|--------|
| `0.2.x` | `pip install -U django-graph-search==0.3.3` — no mandatory settings changes |
| `0.3.0a1` / `0.3.1a1` | Drop `--pre`; pin `==0.3.3`. Behaviour matches pre-releases plus admin sidebar, skip-fields, non-blocking default, component registry |

Re-indexing is **not** required unless you change embedding model or smart-indexing templates.

## Tests

117 passed, 1 skipped (pytest suite in CI).

## Links

- [CHANGELOG.md](CHANGELOG.md) — full categorized list
- [PyPI](https://pypi.org/project/django-graph-search/)
- [Documentation](https://github.com/svalench/django_graph_search#readme)
