# Django Graph Search

[![PyPI version](https://badge.fury.io/py/django-graph-search.svg)](https://badge.fury.io/py/django-graph-search)
[![Python Version](https://img.shields.io/pypi/pyversions/django-graph-search.svg)](https://pypi.org/project/django-graph-search/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Django](https://img.shields.io/badge/Django-3.2%2B-092E20?logo=django)](https://djangoproject.com)
[![Vector Search](https://img.shields.io/badge/Vector%20Search-ChromaDB%20%7C%20FAISS%20%7C%20Qdrant-blueviolet)](#supported-backends)

> **Production-ready semantic vector search for Django** — searches across FK, M2M, and reverse relations by traversing your model graph. Pluggable backends: ChromaDB, FAISS, Qdrant.

```bash
pip install django-graph-search[chromadb]
```

## Why Django Graph Search?

Most Django search solutions (Haystack, Elasticsearch, full-text) treat each model in isolation. **Django Graph Search** builds rich search context by traversing the ORM relation graph before indexing:

- A `Product` becomes searchable by its `category__name`, `tags__name`, `brand__description`, etc. — automatically
- Uses **sentence-transformers embeddings** for multilingual semantic similarity
- **Delta indexing** — only re-index what changed
- **Admin UI** — semantic search inside `/admin/` out of the box
- **REST API** — ready-to-use search endpoint

## Installation

```bash
# ChromaDB backend (recommended for local/dev)
pip install django-graph-search[chromadb]

# FAISS backend (fast CPU similarity, no server needed)
pip install django-graph-search[faiss]

# Qdrant backend (production, scalable)
pip install django-graph-search[qdrant]

# pgvector (PostgreSQL extension)
pip install django-graph-search[pgvector]

# OpenAI / Cohere cloud embeddings (no local PyTorch model)
pip install django-graph-search[openai]
pip install django-graph-search[cohere]

# All backends + LangGraph
pip install django-graph-search[all]
```

## What's new in 0.3 (pre-release **0.3.1a1**)

This line is a **pre-release** for smoke-testing packaging and integrations. Install with:

```bash
pip install --pre django-graph-search==0.3.1a1
```

Highlights vs **0.2.0** (full detail in [CHANGELOG.md](CHANGELOG.md)):

| Area | Change |
|------|--------|
| **REST hits** | Each result includes `score` (0.0–1.0) and `text`. Optional `min_score` query parameter filters weak matches; responses may include `min_score_applied`. |
| **Indexing** | `weight_fields` is always honored, including with `fields: "__all__"`; weight `0.0` drops a field from indexed text. |
| **Async signals** | `ASYNC_INDEXING` (Celery, `thread`, or django-q) plus `django_graph_search.tasks` so `AUTO_INDEX` can avoid blocking the request thread. |
| **Backends / embeddings** | **Pgvector** backend (`[pgvector]`). **OpenAI** / **Cohere** embedding backends (`[openai]`, `[cohere]`). |
| **Scores** | ChromaDB / FAISS / Qdrant normalize distances to similarity scores in 0–1 for consistent API output. |
| **Security / API** | Optional `GRAPH_SEARCH["API"]`: `PERMISSION_CLASSES`, `THROTTLE_CLASSES`, `THROTTLE_RATES`, `REQUIRE_AUTHENTICATION` via `django_graph_search.permissions` (defaults keep behaviour open). |
| **Validation** | Invalid or negative `limit` on search, streaming, conversational, and similar endpoints returns **400** (not 500); values above 1000 are clamped with a log warning. |
| **Fixes** | ChromaDB cosine metadata and distance mapping; file delta cache TTL and `purge_search_cache`; conversational in-memory registry + `RuntimeWarning` when `DEBUG` is false. |

## Quick Start (5 minutes)

### 1. Add to INSTALLED_APPS

```python
INSTALLED_APPS = [
    ...,
    "django_graph_search",
]
```

### 2. Configure GRAPH_SEARCH

```python
# settings.py
GRAPH_SEARCH = {
    "MODELS": [
        {
            "model": "shop.Product",
            # Index local fields + traverse relations with __ notation
            "fields": ["name", "description", "category__name", "tags__name"],
            "follow_relations": True,
            "relation_depth": 2,
        },
        # Or index all concrete fields (weight_fields still apply by field name):
        # {"model": "shop.Review", "fields": "__all__",
        #  "weight_fields": {"title": 2.0, "body": 1.0, "internal_note": 0.0}},
    ],
    "VECTOR_STORE": {
        "BACKEND": "django_graph_search.backends.ChromaDBBackend",
        "OPTIONS": {
            "persist_directory": "vector_db",
            "collection_name": "django_search",
        },
    },
    "EMBEDDINGS": {
        "default": {
            "BACKEND": "django_graph_search.embeddings.SentenceTransformerBackend",
            # Multilingual model — works with Russian, English, etc.
            "MODEL_NAME": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        },
        "fast": {
            "BACKEND": "django_graph_search.embeddings.SentenceTransformerBackend",
            "MODEL_NAME": "sentence-transformers/all-MiniLM-L6-v2",
        },
    },
    "DEFAULT_EMBEDDING": "default",
    "DEFAULT_RESULTS_LIMIT": 20,
    "DELTA_INDEXING": True,
    "CACHE": {
        "BACKEND": "file",   # Options: file | redis | db
        "OPTIONS": {"path": "graph_search_cache"},
        "TTL": 86400,
    },
    # Optional REST hardening — permissions / throttling (see "Securing the REST API"):
    # "API": { ... },
}
```

To restrict access to the main search, streaming, and conversational HTTP endpoints,
add an `"API"` block as described [below](#securing-the-rest-api-optional).

### 3. Add URLs

```python
# urls.py
from django.urls import path, include

urlpatterns = [
    ...,
    path("api/search/", include("django_graph_search.urls")),
]
```

### 4. Build the index

```bash
python manage.py build_search_index
```

### 5. Search

```bash
# REST API
GET /api/search/?q=wireless+headphones&models=shop.Product&limit=5&min_score=0.75

# Find similar items
GET /api/search/similar/shop.Product/42/?limit=5
```

## How It Works

```
Django ORM Model Graph
        │
        ▼
  Relation Traversal    <- FK, M2M, reverse relations up to depth N
        │
        ▼
  Text Concatenation    <- fields + related fields merged into one document
        │
        ▼
  Sentence Transformer  <- multilingual embeddings (768-dim vectors)
        │
        ▼
  Vector Store          <- ChromaDB / FAISS / Qdrant
        │
        ▼
  Semantic Search       <- cosine similarity, top-K results
```

## Python API

```python
from django_graph_search import search, index, get_similar

# Semantic search across models
results = search("red smartphone", models=["shop.Product"], limit=5)

# Index a single instance (e.g. in a signal)
index(product_instance)

# Find similar objects
similar = get_similar(product_instance, limit=5)
```

## REST API

| Endpoint | Method | Description |
|---|---|---|
| `/api/search/?q=...&models=...&limit=...&min_score=...` | `GET` | Semantic search; optional `min_score` (0.0–1.0) drops weaker hits |
| `/api/search/similar/{app}.{Model}/{id}/` | `GET` | Find similar objects |
| `/api/search/conversation/` | `POST` | Session-aware conversational search (optional, see below) |
| `/api/search/conversation/?conversation_id=...` | `DELETE` | Clear a conversation history |
| `/api/search/stream/` | `GET`, `POST` | Streaming search events (optional) |

Each result object includes **`model`**, **`pk`**, **`score`** (0.0–1.0 similarity), and **`text`**
(the indexed document string). When `min_score` is used, the response also contains
**`min_score_applied`**.

### Query parameters (`limit`)

The `limit` parameter controls how many results are returned (where supported):

| Where | Parameter |
|---|---|
| `/api/search/` | Query string `limit` |
| `/api/search/` | Query string `min_score` (optional, float 0.0–1.0) |
| `/api/search/similar/.../` | Query string `limit` |
| `/api/search/stream/` | Query string or JSON/form body `limit` |
| `/api/search/conversation/` | JSON/form field `limit` |

Rules:

- Must be a **positive integer** in the range **1–1000**. Values greater than **1000**
  are **clamped to 1000** and a warning is logged.
- Invalid values (non-numeric strings, negative numbers, booleans, etc.) produce
  **HTTP 400** with JSON `{"error": "'limit' must be a positive integer."}`.
- If `min_score` is set on **`/api/search/`**, only results with **`score >= min_score`**
  are returned. The JSON body includes **`min_score_applied`** with the threshold used.
  Invalid values return **HTTP 400**.

### Optional embedding backends (OpenAI / Cohere)

Instead of downloading a **sentence-transformers** model, you can point ``EMBEDDINGS`` at
``django_graph_search.embeddings.OpenAIEmbeddingBackend`` or
``django_graph_search.embeddings.CohereEmbeddingBackend`` (extras ``[openai]`` / ``[cohere]``).
Cohere uses asymmetric ``input_type``: indexing uses document mode and search uses query mode
(``embed_batch(..., is_query=False)`` vs ``embed(..., is_query=True)``).

### Async indexing from signals (optional)

When ``AUTO_INDEX`` is on, saves can block on large graphs. Enable ``ASYNC_INDEXING`` to offload work:

```python
"ASYNC_INDEXING": {
    "ENABLED": True,
    "BACKEND": "celery",  # or "thread" | "django_q"
    "CELERY_QUEUE": "search_indexing",
    "CELERY_TASK_PATH": "django_graph_search.tasks.index_instance_task",
    "CELERY_DELETE_TASK_PATH": "django_graph_search.tasks.delete_instance_task",
},
```

With ``thread``, indexing runs in a daemon thread (no retries). With ``celery``, install Celery
and register tasks; if Celery is missing, the task module falls back to synchronous execution with a warning.

### Securing the REST API (optional)

**Scope:** Settings under `GRAPH_SEARCH["API"]` apply only to **`GET /api/search/`**,
**`/api/search/stream/`**, **`POST`** and **`DELETE /api/search/conversation/`**.
They do **not** apply to **`/api/search/similar/.../`** — protect that route separately
(e.g. Django middleware, URL-level decorators, nginx, or wrapping in your own authenticated view).

By default the search endpoints remain **public** (backward compatible). Configure
``GRAPH_SEARCH["API"]`` to add authentication, permissions, and throttling:

```python
GRAPH_SEARCH = {
    # ... existing keys ...
    "API": {
        "REQUIRE_AUTHENTICATION": True,
        "PERMISSION_CLASSES": [
            # "rest_framework.permissions.IsAuthenticated",  # if DRF is installed
            # or a dotted path to a callable(request) -> bool
        ],
        "THROTTLE_CLASSES": [
            "django_graph_search.permissions.SimpleScopedRateThrottle",
        ],
        "THROTTLE_RATES": {
            "search": "60/minute",
            "search_authenticated": "300/minute",
        },
    },
}
```

``SimpleScopedRateThrottle`` applies **in-process** limits (per Gunicorn worker).
For accurate global limits across workers, use DRF cache-backed throttles or a
reverse-proxy rate limit.

## Management Commands

```bash
python manage.py build_search_index                  # Index all configured models
python manage.py build_search_index --model shop.Product  # Index one model
python manage.py clear_search_index                  # Remove all vectors
python manage.py search_index_status                 # Show index statistics
python manage.py purge_search_cache                  # Remove expired file delta cache (CACHE.BACKEND=file)
python manage.py purge_search_cache --dry-run        # Count expired entries without deleting
```

## Admin UI

After installation, navigate to `/admin/graph-search/` for a semantic search interface directly in Django Admin — useful for content managers and debugging.

## Supported Backends

| Backend | Best for | Server required |
|---|---|---|
| ChromaDB | Development, small-medium datasets | No |
| FAISS | High-speed CPU search, offline | No |
| Qdrant | Production, large datasets, filtering | Yes |
| **pgvector** (`django_graph_search.backends.PgvectorBackend`) | Same PostgreSQL as Django, no separate vector server | PostgreSQL + `vector` extension |

Install: `pip install django-graph-search[pgvector]`. Table is created automatically on first use (see backend docstring for `VECTOR_STORE.OPTIONS`).

## Delta Indexing & Cache

Enable `DELTA_INDEXING: True` to skip objects that haven’t changed since last index run. Choose a cache backend:

| Backend | Config | Use case |
|---|---|---|
| `file` | `OPTIONS.path` | Local dev |
| `redis` | `OPTIONS.alias` | Production |
| `db` | `OPTIONS.alias` | Simple setup |

With **`CACHE.BACKEND: "file"`**, each delta entry stores an **`expires_at`**
timestamp derived from **`CACHE.TTL`**. Expired entries are removed **lazily** when read;
the directory can still grow if keys are never re-read — run
`python manage.py purge_search_cache` periodically (or via cron), or use
`--dry-run` to count stale files without deleting. Redis/db backends use Django’s
cache TTL and do not require this command; `purge_search_cache` only affects the
file backend.

## LangGraph-powered search pipeline (optional)

Starting with this version, `django-graph-search` ships with an **optional**
orchestration layer built on top of [LangGraph](https://langchain-ai.github.io/langgraph/).
It is disabled by default; the public API (`Searcher.search`,
`Searcher.find_similar`, REST endpoints) is fully backwards-compatible.

When enabled, the pipeline runs as a small graph:

```
analyze_query → [expand_query] → vector_search → [rerank] → postprocess
```

Steps in `[brackets]` are toggled via settings, and each one degrades
gracefully: if the LLM backend fails or is not configured, the pipeline keeps
working using the deterministic vector search.

```python
GRAPH_SEARCH = {
    # ... your existing config ...
    "LANGGRAPH": {
        "ENABLED": True,                # Master switch.
        "QUERY_EXPANSION": True,        # Generate semantic reformulations.
        "RERANKING": True,              # Rerank top-K candidates.
        "MAX_EXPANDED_QUERIES": 3,
        "RERANK_TOP_K": 20,
        "TIMEOUT_SECONDS": 15,
        "MAX_QUERY_LENGTH": 1024,
        "FALLBACK_ON_ERROR": True,      # Fall back to legacy search on graph errors.
        "USE_FOR_SIMILAR": False,       # Route find_similar through the graph.
        "LLM": {
            # Leave BACKEND=None to use the deterministic dummy backend.
            "BACKEND": None,
            "MODEL": None,
            "OPTIONS": {},
        },
    },
}
```

### Bring your own LLM backend

Implement `django_graph_search.llm.BaseLLMBackend` and point
`LANGGRAPH.LLM.BACKEND` at the dotted path. The contract is intentionally
tiny — `expand_query(query, models, max_variants)` and
`rerank(query, candidates, top_k)` — so you can wrap any provider
(OpenAI, Ollama, vLLM, your in-house service) in a few lines.

### Why optional?

The library refuses to add hard dependencies on `langgraph` or any LLM SDK.
If `langgraph` is not installed, the pipeline transparently uses an in-tree
sequential runner with the same node structure, so behaviour and tests stay
identical.

## Conversational search (optional)

For session-aware semantic search (follow-ups like "more", "only products",
"similar") enable the conversational endpoint. It is a thin search-first
shell on top of `Searcher` and never invents user intent: ambiguous
follow-ups are surfaced as a structured `clarification_needed` flag instead
of a hallucinated query.

```python
GRAPH_SEARCH = {
    # ... existing config ...
    "CONVERSATIONAL": {
        "ENABLED": True,
        "MEMORY_BACKEND": "redis",
        "MEMORY_OPTIONS": {
            "alias": "default",        # Django CACHES alias
            "key_prefix": "dgs_conv",
            "ttl": 3600,
        },
        "MAX_HISTORY_ITEMS": 10,
        "ALLOW_CLARIFICATIONS": True,
    },
}
```

For local development and tests, ``MEMORY_BACKEND: "inmemory"`` is fine. With
``DEBUG=False`` (typical production), the library emits a ``RuntimeWarning`` if
in-memory mode is left enabled — switch to ``redis`` (Django cache → Redis) so
every Gunicorn worker shares the same conversation state.

Endpoint: `POST /api/search/conversation/`

```json
// Request
{
  "query": "only products",
  "conversation_id": "abc-123",
  "models": ["shop.Product"],
  "limit": 5
}

// Response
{
  "conversation_id": "abc-123",
  "query": "only products",
  "interpreted_query": "red phone",
  "clarification_needed": false,
  "results": [...],
  "total": 5
}
```

Use `DELETE /api/search/conversation/?conversation_id=abc-123` to clear a
conversation.

Built-in memory backends:

| Alias | Class | Best for |
|---|---|---|
| `inmemory` | `InMemoryBackend` | Tests, single-worker dev |
| `cache` / `redis` | `DjangoCacheBackend` | Production via Django cache (Redis, memcached) |

Bring your own by subclassing `BaseMemoryBackend` and pointing
`MEMORY_BACKEND` at the dotted path.

## Smart indexing (optional)

The classic indexer joins selected fields with whitespace. That works, but the
embedding model loses the *role* of each value: a category name and a body
paragraph become indistinguishable tokens. The optional `SmartIndexer` builds
structured documents with labelled sections so the embedder sees something
closer to:

```
Title: Pixel 8
Description:
Camera-first Android phone with Tensor G3.
Category: Phones
```

Enable it from settings — your existing index, signals, and management command
keep working because the resolver and `get_indexer()` factory pick the new
implementation transparently:

```python
GRAPH_SEARCH = {
    # ... your existing config ...
    "SMART_INDEXING": {
        "ENABLED": True,
        # Optional per-model templates; the indexer falls back to a heuristic
        # template based on your MODELS config when one is missing.
        "TEMPLATES": {
            "shop.Product": {
                "title_field": "name",
                "sections": [
                    {"label": "Description", "field": "description", "multiline": True},
                    {"label": "Category", "field": "category__name"},
                ],
            }
        },
    },
}
```

The original deterministic text is always appended as a safety net so smart
indexing never produces *less* searchable content than the legacy pipeline.
Disable the flag to fall back instantly — no reindex required to switch back.

## Streaming search endpoint (optional)

Long-running pipelines (query expansion, vector search, reranking) can stream
lifecycle events to the client so users see progress instead of staring at a
spinner. Two transports are supported:

- `ndjson` (default): one JSON object per line, ideal for `fetch` +
  `ReadableStream` and CLI tools like `jq`.
- `sse`: Server-Sent Events for `EventSource` clients.

Enable from settings:

```python
GRAPH_SEARCH = {
    # ... your existing config ...
    "STREAMING": {
        "ENABLED": True,
        "FORMAT": "ndjson",  # or "sse"
        "INCLUDE_INTERNAL_EVENTS": True,
    },
}
```

The endpoint is registered at `/<API_URL_PREFIX>/stream/` (default
`/api/search/stream/`) and returns HTTP 404 when disabled, so it is safe to
leave the URL config untouched.

Quick test:

```bash
curl -N "http://localhost:8000/api/search/stream/?q=phone"
```

Example event sequence (NDJSON):

```json
{"type": "query_received", "query": "phone"}
{"type": "vector_search_completed", "candidate_count": 12}
{"type": "completed", "total": 5}
{"type": "results", "results": [...], "total": 5}
{"type": "end"}
```

Under the hood the view subscribes a `queue.Queue` to a per-request
`EventHub`, runs the search in a worker thread, and yields events as soon as
the nodes publish them. The hub also powers structured logging and any
custom subscribers you register from your own apps.

## Comparison

| Feature | django-graph-search | Haystack | django-elasticsearch-dsl |
|---|---|---|---|
| Relation traversal | ✅ Auto | ❌ Manual | ❌ Manual |
| Semantic / vector search | ✅ | ❌ | Partial |
| No external server (local) | ✅ ChromaDB/FAISS | ❌ | ❌ |
| Multilingual out of box | ✅ | ❌ | ❌ |
| Admin UI | ✅ | Partial | ❌ |
| Delta indexing | ✅ | ❌ | ❌ |

## Contributing

Pull requests are welcome! Please open an issue first to discuss significant changes.

1. Fork the repo
2. `git checkout -b feature/my-feature`
3. Commit and open a PR

## License

MIT — see [LICENSE](LICENSE)

## Author

**Alexander Valenchits** — [GitHub](https://github.com/svalench)

## Links

- 📦 [PyPI Package](https://pypi.org/project/django-graph-search/)
- 🐛 [Issues](https://github.com/svalench/django_graph_search/issues)
- 🤖 [sentence-transformers](https://www.sbert.net)
- 🕷️ [ChromaDB](https://docs.trychroma.com)
