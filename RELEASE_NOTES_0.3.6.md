# django-graph-search 0.3.6

**Release date:** 2026-09-27  
**Type:** Bug-fix & security release (0.3 line)

```bash
pip install django-graph-search==0.3.6
# optional extras unchanged, e.g.:
pip install "django-graph-search[chromadb,faiss,qdrant,all]==0.3.6"
```

## Summary

0.3.6 closes a data leak in the indexed text, makes the Qdrant backend usable
(it could not create a collection or upsert points before), enforces the
previously inert `LANGGRAPH.TIMEOUT_SECONDS`, turns several `500`s into proper
client errors, and adds integration tests against real vector-store clients.

Upgrading from **0.3.x** is API-compatible. **Rebuild the index** after upgrading
(`python manage.py build_search_index`) — see *Upgrade notes*.

## Security

- **Indexed text leak.** With `follow_relations=True` (the default) the resolver
  appended *all* concrete fields of the root object to the indexed text, not just
  the configured `fields`. For `auth.User` with `fields=["username"]` this stored
  the password hash and e-mail in the vector store and returned them through the
  REST `text` / `text_preview` fields. The root object now contributes only its
  configured fields, and `password` is never indexed — neither for related
  objects nor with `fields="__all__"`. Vectors built by earlier versions still
  contain the leaked text until the index is rebuilt.

## Fixed

### Backends
- **Qdrant:** point ids are UUID5 derived from the document id (Qdrant only accepts
  unsigned integers or UUIDs; the previous `"app.Model:pk"` strings were rejected).
  The original id is kept in the payload and restored on search.
- **Qdrant `distance`:** the default `"Cosine"` never resolved — the `Distance` enum
  members are `COSINE`/`EUCLID`/`DOT` — so collection creation always failed.
  Values are matched case-insensitively; `l2`, `euclidean`, `ip`, `inner_product`
  aliases are accepted. `query_points` is used where available (`search` is deprecated).
- **ChromaDB `clear_collection`:** deletes by ids in batches; `where={}` is rejected
  by recent Chroma versions.
- **Non-primitive primary keys:** UUID (and other) pks are stored as strings in
  vector metadata so Chroma/JSON backends accept them; hydration still works.
- **`clear_search_index` / index coverage** use the shared vector-store instance —
  in-memory backends (FAISS without `persist_path`, ephemeral Chroma) are now
  actually cleared and counted.

### Search & API
- **`LANGGRAPH.TIMEOUT_SECONDS` is enforced.** It is passed to the LLM backend as
  `self.timeout` (unless `LLM.OPTIONS["timeout"]` is set) and independently applied
  as a hard limit around every `expand_query` / `rerank` call; an overrunning call is
  abandoned and the node falls back to the original query / vector order.
- **LangGraph `vector_search`:** the `models` filter is pushed to the store (single
  model) or over-fetched (multiple models) so filtered queries fill `limit` — the
  linear path already did this since 0.3.4.
- **`/api/search/similar/`:** unknown model → `404`, invalid `pk` → `400` (both were `500`).
- **Stale index entries:** hits referencing models no longer installed are returned
  without `data` instead of raising `LookupError`.
- **`weight_fields` = 0 with relations:** zero-weight fields no longer re-enter the
  text through the relation walk.
- `SearchAPIView` ignores empty items in `?models=a.B,,c.D`.

### Startup & misc
- **App startup without `django.contrib.admin`:** `ready()` no longer raises
  `LookupError` when the admin app is not installed.
- **Custom managers:** ORM access goes through `_default_manager` instead of
  assuming `.objects`.
- `DEFAULT_RESULTS_LIMIT` is validated (`>= 1`).

## Changed

- **Result hydration:** ORM objects for search hits are loaded with one `pk__in`
  query per model instead of one query per hit.
- `SimpleScopedRateThrottle` evicts stale per-IP windows to bound memory in
  long-running processes.
- Component attributes (`vector_store`, `embedding_backend`) are typed; `mypy`
  passes cleanly.
- **Supported versions:** classifiers and CI cover Django 4.2 / 5.1 / 5.2 / 6.0
  (5.0 dropped — end of life) on Python 3.10–3.13. README badge corrected from
  "Django 3.2+" to "4.2+" to match `install_requires`.
- **Integration tests** against real clients without Docker: Qdrant (`:memory:`),
  ChromaDB (persistent client in a temp dir), FAISS end-to-end with a UUID-pk model.
  Marked `integration`; skipped when the extra is not installed.

## Upgrade notes

- **Rebuild the index** after upgrading: existing vectors and stored `text` were
  built from the leaky text. `python manage.py clear_search_index && python manage.py build_search_index`.
- **Qdrant users:** ids changed format (UUID5). Since the previous backend could not
  write to Qdrant at all, there is nothing to migrate — just rebuild.
- **Custom LLM backends** receive `self.timeout`; forward it to your HTTP client.
  If you already pass `"timeout"` in `LLM.OPTIONS`, that value wins.
- **UUID primary keys:** the `pk` field in REST hits is now a string for such models.
- No database migrations.
