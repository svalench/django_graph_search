# Scratchpad

## Ревизия проекта (2026-09-27)

Исправлено (см. CHANGELOG [Unreleased]):
1. [security] GraphResolver: корневой объект больше не добавляет все concrete-поля при follow_relations
   (утечка password hash в индекс/API `text`); `password` исключён и для `__all__`/связанных объектов.
2. apps.ready() не падает без django.contrib.admin.
3. SimilarAPIView: 404 на неизвестную модель, 400 на невалидный pk.
4. Qdrant: UUID5 id точек + query_points.
5. serialize_pk для metadata (UUID pk).
6. Chroma clear_collection по id батчами.
7. vector_search_node: фильтр моделей / over-fetch.
8. Searcher: batch-загрузка объектов (1 запрос на модель), устойчивость к устаревшим label.
9. `_default_manager` вместо `.objects`.
10. Throttle: eviction устаревших окон.
11. clear_search_index / index_coverage используют shared vector store.
12. mypy чист; pylint 10/10; тесты 142 passed / 4 skipped.
13. DEFAULT_RESULTS_LIMIT валидируется; Python 3.13 classifier.

Добавлены регрессионные тесты: tests/test_review_fixes.py.

## Итерация 2 (бэклог из ревизии)
- LANGGRAPH.TIMEOUT_SECONDS реализован: llm.factory → backend.timeout; call_with_timeout в expand/rerank узлах.
- pgvector: валидация идентификатора уже была (_quote_ident) — пункт снят.
- Матрица: setup.cfg classifiers 5.2/6.0; CI Django 4.2/5.1/5.2/6.0 + qdrant extra; README badge 4.2+; docs/index.html.
- .venv с extras (chroma 1.5.9, faiss, qdrant, Django 6.1.1): интеграционные тесты tests/test_backends_integration.py.
  Нашли и починили реальный баг: Qdrant Distance."Cosine" → AttributeError (члены enum COSINE/EUCLID/DOT).
- Тесты: venv 167 passed / 1 skipped; system python (без extras) 151 passed / 17 skipped. pylint 10/10, mypy clean.

DONE
