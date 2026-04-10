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

# All backends
pip install django-graph-search[all]
```

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
        # Or index all concrete fields:
        # {"model": "shop.Review", "fields": "__all__"}
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
}
```

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
GET /api/search/?q=wireless+headphones&models=shop.Product&limit=5

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
| `/api/search/?q=...&models=...&limit=...` | `GET` | Semantic full-text search |
| `/api/search/similar/{app}.{Model}/{id}/` | `GET` | Find similar objects |

## Management Commands

```bash
python manage.py build_search_index                  # Index all configured models
python manage.py build_search_index --model shop.Product  # Index one model
python manage.py clear_search_index                  # Remove all vectors
python manage.py search_index_status                 # Show index statistics
```

## Admin UI

After installation, navigate to `/admin/graph-search/` for a semantic search interface directly in Django Admin — useful for content managers and debugging.

## Supported Backends

| Backend | Best for | Server required |
|---|---|---|
| ChromaDB | Development, small-medium datasets | No |
| FAISS | High-speed CPU search, offline | No |
| Qdrant | Production, large datasets, filtering | Yes |

## Delta Indexing & Cache

Enable `DELTA_INDEXING: True` to skip objects that haven’t changed since last index run. Choose a cache backend:

| Backend | Config | Use case |
|---|---|---|
| `file` | `OPTIONS.path` | Local dev |
| `redis` | `OPTIONS.alias` | Production |
| `db` | `OPTIONS.alias` | Simple setup |

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
