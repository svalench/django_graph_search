# Django Graph Search

Production-ready vector search for Django models with automatic relation traversal.

- **Search across related models** (FK, M2M, reverse relations)
- **Pluggable vector stores** (ChromaDB, FAISS, Qdrant)
- **Delta indexing** with configurable cache
- **Admin UI** and **REST API** out of the box

---

## Fast Start (5 minutes)

### 1) Install

```bash
pip install django-graph-search[chromadb]
```

Other backends:

```bash
pip install django-graph-search[faiss]
pip install django-graph-search[qdrant]
pip install django-graph-search[all]
```

### 2) Add app

```python
INSTALLED_APPS = [
    ...,
    "django_graph_search",
]
```

### 3) Configure

```python
GRAPH_SEARCH = {
    "MODELS": [
        {
            "model": "shop.Product",
            "fields": ["name", "description", "category__name", "tags__name"],
            "follow_relations": True,
            "relation_depth": 2,
        },
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
            "MODEL_NAME": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "OPTIONS": {},
        },
        "fast": {
            "BACKEND": "django_graph_search.embeddings.SentenceTransformerBackend",
            "MODEL_NAME": "sentence-transformers/all-MiniLM-L6-v2",
            "OPTIONS": {},
        },
    },
    "DEFAULT_EMBEDDING": "default",
    "DEFAULT_RESULTS_LIMIT": 20,
    "DELTA_INDEXING": True,
    "CACHE": {
        "BACKEND": "file",  # file | redis | db
        "OPTIONS": {"path": "graph_search_cache"},
        "KEY_PREFIX": "dgs",
        "TTL": 86400,
    },
}
```

### 4) Add URLs

```python
urlpatterns = [
    ...,
    path("api/search/", include("django_graph_search.urls")),
]
```

### 5) Build index

```bash
python manage.py build_search_index
```

### 6) Search

```bash
GET /api/search/?q=wireless+headphones&models=shop.Product&limit=5
```

---

## API

- `GET /api/search/?q=query&models=app.Model,app.OtherModel&limit=10`
- `GET /api/search/similar/app.Model/123/?limit=10`

---

## Admin UI

After install, open `/admin/graph-search/` for full-text semantic search across your models.

---

## CLI Commands

```bash
python manage.py build_search_index
python manage.py build_search_index --model shop.Product
python manage.py clear_search_index
python manage.py search_index_status
```

---

## Python API

```python
from django_graph_search import search, index, get_similar

results = search("red smartphone", models=["shop.Product"], limit=5)
index(product_instance)
similar = get_similar(product_instance, limit=5)
```

---

## Delta Indexing Cache

Choose a cache backend to skip unchanged objects during reindexing:

- `file`: local files at `CACHE.OPTIONS.path`
- `redis`: Django cache backend (use `CACHE.OPTIONS.alias`)
- `db`: Django DatabaseCache (use `CACHE.OPTIONS.alias`)

---

## Why Django Graph Search

Most search solutions ignore relationships. This package builds rich context by
traversing your model graph and makes it searchable with modern embeddings.
It is fast, modular, and designed for production Django apps.



