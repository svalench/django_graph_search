# Django Graph Search

Переиспользуемое Django-приложение для векторного поиска по моделям
с автоматическим разрешением связей.

## Установка

```bash
pip install django-graph-search[chromadb]
```

Дополнительные бэкенды:

```bash
pip install django-graph-search[faiss]
pip install django-graph-search[qdrant]
pip install django-graph-search[all]
```

## Быстрый старт

Добавьте приложение в `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    ...,
    "django_graph_search",
]
```

Настройка в `settings.py`:

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
        "OPTIONS": {
            "path": "graph_search_cache",
        },
        "KEY_PREFIX": "dgs",
        "TTL": 86400,
    },
}
```

Добавьте URL'ы:

```python
urlpatterns = [
    ...,
    path("api/search/", include("django_graph_search.urls")),
]
```

## API

- `GET /api/search/?q=запрос&models=app.Model,app.OtherModel&limit=10`
- `GET /api/search/similar/app.Model/123/?limit=10`

## Админка

После установки появится страница `/admin/graph-search/`.

## Команды

```bash
python manage.py build_search_index
python manage.py build_search_index --model shop.Product
python manage.py clear_search_index
python manage.py search_index_status
```

## Публичный API

```python
from django_graph_search import search, index, get_similar

results = search("красный смартфон", models=["shop.Product"], limit=5)
index(product_instance)
similar = get_similar(product_instance, limit=5)
```

## Кэш для дельта-индексации

- `file`: локальные файлы по пути `CACHE.OPTIONS.path`
- `redis`: через Django cache (указать `CACHE.OPTIONS.alias`)
- `db`: через Django DatabaseCache (указать `CACHE.OPTIONS.alias`)


