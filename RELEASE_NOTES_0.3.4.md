# django-graph-search 0.3.4

**Release date:** 2026-07-25  
**Type:** Reliability & security hardening (0.3 line)

```bash
pip install django-graph-search==0.3.4
# optional extras unchanged, e.g.:
pip install django-graph-search[chromadb,faiss,qdrant,all]
```

## Summary

0.3.4 fixes the sharp edges found in production use of 0.3.3: every vector backend
now has **upsert semantics** (re-saving an object can no longer fail or duplicate),
**signal indexing is transaction-safe** (`on_commit`, real `pk` capture on delete),
and **`/api/search/similar/` enforces the same access rules** as the other endpoints.

Upgrading from **0.3.x** is backward-compatible — all new settings have safe defaults.

## Highlights

### Indexing & signals
- **Upsert everywhere**: ChromaDB uses `collection.upsert`; FAISS dedupes by document id (no more `DuplicateIDError` / duplicates on re-save)
- **Transaction safety**: `AUTO_INDEX` dispatches via `transaction.on_commit` — no uncommitted reads, rollback no longer corrupts the index; delete captures `pk` before commit
- **Auth-user noise fix**: the "only `last_login` changed" check now uses a `pre_save` snapshot (the old check silently skipped indexing entirely)
- **Bounded daemon thread pool**: `ASYNC_INDEXING.THREAD_POOL_SIZE` actually limits the `thread` backend
- **Graph traversal limits**: `MAX_RELATED_ITEMS` (default 100) and `MAX_TEXT_LENGTH` (default 8000) protect against reverse-relation blowups
- **Prefetch**: bulk indexing applies `select_related`/`prefetch_related` on first-level relations

### Search & API
- **`/api/search/similar/`** now enforces `GRAPH_SEARCH["API"]` permissions and throttling
- **`find_similar` excludes the object itself** from results
- **Filtered searches fill `limit`**: single-model filters are pushed into the vector store; multi-model queries over-fetch
- **Result `data` whitelist**: only fields listed in the model config are exposed (`__all__` keeps previous behavior)

### FAISS backend
- **Persistence**: `VECTOR_STORE.OPTIONS: {"persist_path": ...}` — atomic save on every mutation, reload on startup
- **Thread-safe** mutations and search; filtered search escalates to a full scan when needed

### Misc
- `FileDeltaCache`: atomic writes + automatic purge of expired entries on startup
- `django_graph_search.settings.reload_settings()` — re-read `GRAPH_SEARCH` at runtime
- Startup warnings for session-auth + `csrf_exempt` and in-memory throttling in production
- CI: pytest matrix (Python 3.10–3.13 × Django 4.2/5.0/5.1), package build + `twine check`
- Project site: https://svalench.github.io/django_graph_search/

## Upgrade notes

- **REST `data` payloads** now include only configured fields. If your clients relied on
  undeclared fields, add them to `MODELS[].fields` or use `fields: "__all__"`.
- **`/api/search/similar/`** is no longer unconditionally public — configure
  `GRAPH_SEARCH["API"]` if it must stay open, or it will follow your existing API rules.
- No database migrations; no changes to indexed document format — existing indexes keep working.
