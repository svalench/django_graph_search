# Changelog

All notable changes to **django-graph-search** are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.4] — 2026-07-25

Reliability and security hardening release: upsert semantics across all vector
backends, transaction-safe signal indexing, and consistent API access control.

```bash
pip install django-graph-search==0.3.4
```

### Fixed
- **ChromaDB upsert:** re-indexing an existing object no longer raises `DuplicateIDError` — `collection.upsert` is used instead of `collection.add`.
- **FAISS duplicates:** `FaissBackend.add_documents` now has upsert semantics — re-added document ids replace previous entries instead of duplicating them (including duplicate ids within a single batch).
- **FAISS filtered search:** when metadata filters are set, the backend escalates to a full-index scan if the first over-fetch pass cannot fill `limit`.
- **Transaction safety:** `AUTO_INDEX` signals dispatch indexing/deletion via `transaction.on_commit`, so background tasks never read uncommitted rows and rollback no longer corrupts the index. Delete handlers capture `pk` before commit (Django clears `instance.pk` after `delete()`).
- **`/api/search/similar/` security:** the endpoint now enforces `GRAPH_SEARCH["API"]` permissions and throttling like the other search endpoints (previously it was always public).
- **Auth-user noise heuristic:** the "only `last_login` changed" check now compares against a `pre_save` snapshot (the old post-save DB comparison could never detect changes and would skip indexing entirely).
- **`find_similar` self-match:** the instance itself is excluded from similar results (previously it was usually the top hit); fetch budget accounts for self and legacy duplicates.
- **Search with `models` filter:** single-model filters are pushed into the vector store and multi-model searches over-fetch, so filtered queries no longer return fewer than `limit` results.
- **Thread pool shutdown:** the `thread` indexing backend uses a bounded **daemon** worker pool (does not block process exit the way `ThreadPoolExecutor` would).

### Added
- **FAISS persistence:** `VECTOR_STORE.OPTIONS: {"persist_path": ...}` saves the index (ids/metadata/embeddings) to disk atomically and reloads it on startup (trusted local path only — pickle).
- **Thread pool:** `ASYNC_INDEXING.THREAD_POOL_SIZE` now actually bounds the `thread` backend via a shared **daemon** worker pool.
- **Graph traversal limits:** `MAX_RELATED_ITEMS` (default `100`) caps related objects per relation and `MAX_TEXT_LENGTH` (default `8000`) caps indexed text length.
- **Prefetch:** bulk indexing applies `select_related`/`prefetch_related` on first-level relations to avoid N+1 queries.
- **Result field whitelist:** REST `data` payloads include only fields listed in the model config (`__all__` keeps previous behavior); unconfigured models return no field data.
- **`reload_settings()`:** public API to re-read `GRAPH_SEARCH` at runtime.
- **Startup warnings:** production warnings for session-auth + `csrf_exempt` endpoints and for the in-memory `SimpleScopedRateThrottle` under multi-process deployments.
- **CI:** pytest workflow (Python 3.10–3.13 × Django 4.2/5.0/5.1), package build + `twine check`; pylint matrix updated to supported Python versions.

### Changed
- **FileDeltaCache:** atomic writes via tmp+rename and automatic purge of expired entries on startup.

## [0.3.3] — 2026-05-19

Stable **0.3** release (replaces pre-releases `0.3.0a1` and `0.3.1a1`).

```bash
pip install django-graph-search==0.3.3
```

### Added
- **REST search:** each hit includes `score` (0.0–1.0) and `text`; optional `min_score` filters weak matches; response may include `min_score_applied`.
- **Model weights:** `weight_fields` is always parsed (including with `fields: "__all__"`); weight `0.0` excludes a field from indexed text.
- **Async indexing:** `ASYNC_INDEXING` (Celery / daemon `thread` / django-q) plus `django_graph_search.tasks` so `AUTO_INDEX` signals can avoid blocking requests.
- **Non-blocking auto-index (default):** with local SentenceTransformer embeddings, `AUTO_INDEX_NON_BLOCKING` runs indexing in a daemon thread without enabling `ASYNC_INDEXING`.
- **Skip noisy saves:** global `AUTO_INDEX_SKIP_UPDATE_FIELDS` (default `last_login`) and per-model `skip_update_fields` skip re-index when only those fields change (`update_fields` or full save with no other diffs).
- **Pgvector backend:** `django_graph_search.backends.PgvectorBackend` (extra `[pgvector]`).
- **Cloud embeddings:** `OpenAIEmbeddingBackend` and `CohereEmbeddingBackend` (extras `[openai]`, `[cohere]`).
- **Admin index coverage:** `/admin/graph-search/index-status/` shows DB row counts vs vector-store document counts per model, overall percentage, and static progress bars. Sidebar entries **Поиск** and **Статус индексации** via unmanaged models `GraphSearch` / `GraphSearchIndexStatus`.
- **`count_documents(filters)`** on ChromaDB, FAISS, Qdrant, and pgvector backends; used by coverage UI and `search_index_status` management command.
- **Admin search:** optional `min_score` query parameter on the Graph Search admin page (same semantics as REST).
- **Component registry:** vector store, embedding backend, and `GraphResolver` are cached per worker configuration (shared by `Searcher`, `Indexer`, signals).

### Changed
- **Vector scores:** ChromaDB / FAISS / Qdrant normalize distances to similarity scores in 0–1; ChromaDB reads the collection’s effective HNSW `space` and maps L2 / cosine / inner-product distances accordingly.
- **Factory / signals:** indexing and search reuse `get_shared_components()` from `component_registry`.

### Security
- **REST API access control:** optional `GRAPH_SEARCH["API"]` (`PERMISSION_CLASSES`, `THROTTLE_CLASSES`, `THROTTLE_RATES`, `REQUIRE_AUTHENTICATION`) via `django_graph_search.permissions`.
- **Safe integer parsing for `limit`:** invalid or negative values return HTTP 400; values above 1000 are clamped with a log warning.

### Fixed
- **LangGraph + `graph.invoke()`:** when the compiled graph omits `final_results`, `Searcher` runs `postprocess_results_node` so results are not empty.
- **ChromaDB:** cosine collections use `hnsw:space=cosine`; query distances mapped to similarity per metric.
- **File delta cache TTL:** `FileDeltaCache` enforces expiry on read; `purge_expired(dry_run=)` and `purge_search_cache` management command.
- **Conversational memory registry:** per-process backends with a lock; `RuntimeWarning` when `inmemory` + conversational enabled + `DEBUG` is false.

### Tests
- **117** tests passing (+59 vs 0.2.0): admin sidebar, Chroma score mapping, component registry, non-blocking signals, `skip_update_fields`.

## [0.3.1a1] — 2026-05-19

**Pre-release** of the **0.3.1** line. Install for smoke tests:

`pip install --pre django-graph-search==0.3.1a1`

### Added
- **Admin index coverage:** page `/admin/graph-search/index-status/` shows DB row counts
  vs vector-store document counts per configured model (metadata `model`), overall
  percentage, and static progress bars (no auto-refresh). Link from the existing
  Graph Search admin page.
- **`count_documents(filters)`** on all built-in vector backends (ChromaDB, FAISS,
  Qdrant, pgvector) plus coverage output in the `search_index_status` management
  command.

### Fixed
- **LangGraph + `graph.invoke()`:** when the compiled graph omits `final_results`
  from the returned dict, `Searcher` runs `postprocess_results_node` so search
  results are not empty.

## [0.3.0a1] — 2026-05-18

**Pre-release** of the upcoming **0.3.0** line. Install for smoke tests:

`pip install --pre django-graph-search==0.3.0a1`

### Added
- **REST search:** each hit includes ``score`` (0.0–1.0) and ``text``; optional query param
  ``min_score`` filters weak matches; response may include ``min_score_applied``.
- **Model weights:** ``weight_fields`` is always parsed (including with ``fields: "__all__"``);
  weight ``0.0`` excludes a field from indexed text.
- **Async indexing:** ``ASYNC_INDEXING`` settings (Celery / daemon thread / django-q) plus
  ``django_graph_search.tasks`` helpers so ``AUTO_INDEX`` signals can avoid blocking requests.
- **pgvector backend:** ``django_graph_search.backends.PgvectorBackend`` (extra ``[pgvector]``).
- **Cloud embeddings:** ``OpenAIEmbeddingBackend`` and ``CohereEmbeddingBackend`` (extras
  ``[openai]``, ``[cohere]``); Cohere distinguishes query vs document embeddings via ``is_query``.

### Changed
- **Vector scores:** ChromaDB / FAISS / Qdrant backends normalize stored distances into
  similarity-style scores in the 0–1 range for consistent API output.

### Security
- **REST API access control:** new optional ``GRAPH_SEARCH["API"]`` settings
  (``PERMISSION_CLASSES``, ``THROTTLE_CLASSES``, ``THROTTLE_RATES``,
  ``REQUIRE_AUTHENTICATION``) with pluggable checks in
  ``django_graph_search.permissions``. Search, streaming, and conversational
  views run these checks before handling requests. Defaults are empty / false so
  behaviour stays open unless you configure restrictions.
- **Safe integer parsing for ``limit``:** invalid or negative ``limit`` values on
  search, streaming, conversational, and similar endpoints return HTTP 400
  instead of raising ``ValueError`` (500). Values above 1000 are clamped with a
  warning in logs.

### Fixed
- **ChromaDB:** cosine collections use ``hnsw:space=cosine`` metadata; query distances mapped to similarity.
- **File delta cache TTL:** ``FileDeltaCache`` now stores ``expires_at``,
  enforces expiry on read (lazy delete), and supports ``purge_expired(dry_run=)``
  plus the ``purge_search_cache`` management command for file backends.
- **Conversational memory registry:** per-process memory backends are cached in a
  module-level registry with a lock (replacing a class attribute). A
  ``RuntimeWarning`` is emitted when ``CONVERSATIONAL.MEMORY_BACKEND="inmemory"``,
  conversational search is enabled, and ``DEBUG`` is false, to highlight
  multi-worker limitations.

## [0.2.0] — 2026-05-08

A large feature release built around an **optional LangGraph orchestration layer**.
Every new capability is opt-in via settings flags, so upgrading from `0.1.x`
requires no code changes — your existing `Searcher`, `Indexer`, REST endpoints
and signal handlers behave exactly as before.

### Added

#### LangGraph search pipeline (`LANGGRAPH.ENABLED`)
- New `langgraph_agent` module with a 5-node graph:
  `analyze_query → expand_query → vector_search → rerank_results → postprocess_results`.
- Works with **or without** the `langgraph` package — when it is missing, an
  in-tree fallback runner mirrors the same conditional structure so behaviour
  stays identical.
- Per-feature toggles: `QUERY_EXPANSION`, `RERANKING`, `MAX_EXPANDED_QUERIES`,
  `RERANK_TOP_K`, `MAX_QUERY_LENGTH`, `TIMEOUT_SECONDS`, `FALLBACK_ON_ERROR`.
- LLM abstraction (`llm/` subpackage) with `BaseLLMBackend`, `RerankCandidate`,
  a deterministic `DummyLLMBackend` fallback, and a dotted-path factory.
- Multi-query merge with `(model, pk)` deduplication keeping the highest score.
- LLM errors never poison search — every node degrades to its deterministic
  baseline and surfaces the error in `state["errors"]`.

#### Conversational search (`CONVERSATIONAL.ENABLED`)
- New REST endpoint `POST /api/search/conversation/` (and `DELETE` to clear a
  session) returning the same shape as `/api/search/` plus
  `conversation_id`, `interpreted_query`, `clarification_needed`.
- Conversation graph with 5 nodes: `load_context → interpret_followup →
  maybe_clarify → execute_search → store_context`.
- Deterministic follow-up handling for "more / similar / filter X" patterns
  via regex — works without an LLM.
- Pluggable memory backends:
  - `inmemory` — `InMemoryBackend`, thread-safe deque (dev / tests).
  - `cache` / `redis` — `DjangoCacheBackend`, runs on top of any Django cache.
  - Bring your own by subclassing `BaseMemoryBackend`.
- `MAX_HISTORY_ITEMS` limit and `MIN_QUERY_LENGTH_FOR_AUTOSEARCH` guardrail.

#### Smart indexing (`SMART_INDEXING.ENABLED`)
- New `SmartIndexer` orchestration that builds **structured** documents with
  labelled sections (`Title:`, `Description:`, `Category:` …) so the embedder
  can distinguish field roles instead of seeing a flat token soup.
- Per-model templates via `SMART_INDEXING.TEMPLATES`, with a deterministic
  heuristic fallback (`default_template_for`) when none is registered.
- Drop-in replacement: new `get_indexer()` factory routes to `SmartIndexer`
  when enabled and back to the classic `Indexer` otherwise. Signals,
  `index()`, and the `build_search_index` management command all use the
  factory now.
- Always appends the legacy whitespace-joined text as a safety net — smart
  indexing never produces *less* searchable content than the classic indexer.

#### Streaming search (`STREAMING.ENABLED`)
- New `events.EventHub` — thread-safe, error-tolerant pub/sub used by the
  LangGraph nodes to publish lifecycle events: `query_received`,
  `query_expanded`, `vector_search_completed`, `rerank_completed`,
  `completed`.
- New `StreamingSearchAPIView` at `/api/search/stream/` supporting two
  transports:
  - `ndjson` (default) — one JSON object per line, ideal for `fetch` +
    `ReadableStream` and CLI tools like `jq`.
  - `sse` — standards-compliant Server-Sent Events for `EventSource`.
- Runs the search in a worker thread, drains events through `queue.Queue`,
  emits a terminal `end` event for reliable client-side stream termination,
  sets `X-Accel-Buffering: no` so reverse proxies don't buffer the response.
- `Searcher` now accepts an optional `event_hub` kwarg and forwards it to the
  graph factory only when supported (`TypeError`-tolerant).

#### Settings
- New frozen dataclasses: `LangGraphConfig`, `LLMConfig`, `ConversationalConfig`,
  `SmartIndexingConfig`, `StreamingConfig`.
- Validation on every numeric / enum field — invalid values raise
  `ConfigurationError` at boot rather than at request time.

### Changed
- `Searcher.search` now routes through the LangGraph pipeline when
  `LANGGRAPH.ENABLED` is true (with linear fallback on error). Public method
  signatures and return shapes are unchanged.
- `Searcher.find_similar` optionally uses the same graph when
  `LANGGRAPH.USE_FOR_SIMILAR` is true.
- Signals, `django_graph_search.index()`, and the `build_search_index`
  management command now go through `get_indexer()` so they pick up
  `SmartIndexer` automatically when enabled.
- README expanded with sections on the LangGraph pipeline, conversational
  search, smart indexing and streaming, plus end-to-end usage snippets.

### Backward compatibility
- All flags default to `False`. Upgrading from `0.1.x` is a no-op behaviourally.
- Public API (`Searcher`, `Indexer`, REST endpoints, signals) is preserved.
- Re-indexing is **not** required to switch smart indexing on or off.
- No new mandatory dependencies. `langgraph`, LLM SDKs, and Redis/Django cache
  remain entirely optional.

### Tests
- Total test count grew from 8 → **58** (+50).
- New suites: `test_langgraph_search.py` (13), `test_conversational_search.py`
  (16), `test_smart_indexing.py` (10), `test_events_streaming.py` (12).

### Packaging
- Added `Development Status :: 4 - Beta` and Django 5.0 / 5.1 trove classifiers.
- Added `keywords` and `project_urls` (Source / Issues / Changelog) so PyPI
  surfaces them in the sidebar.
- Real author / homepage metadata replaces the previous placeholders.

### New optional extras
- `pip install django-graph-search[langgraph]` — pulls in `langgraph>=0.2.0`.
- `pip install django-graph-search[all]` — chromadb + faiss-cpu + qdrant +
  langgraph in one shot.

---

## [0.1.2] — 2026-04-10

- Documentation polish: shields.io badges, “How It Works” architecture
  overview, comparison table with Haystack / `django-elasticsearch-dsl`, and
  better discoverability metadata.

## [0.1.1] — earlier

- Internal cleanups (`__all__` exports, packaging fixes).

## [0.1.0] — initial release

- Vector search for Django models with automatic graph-relation traversal.
- Pluggable vector stores (ChromaDB, FAISS, Qdrant) and embedding backends
  (sentence-transformers).
- Multilingual semantic search out of the box.
- Auto-indexing via `post_save` / `post_delete` signals.
- Optional delta indexing with file / Redis / memory caches.
- Admin UI for searching across registered models.
- REST endpoints `/api/search/` and `/api/search/similar/<model>/<pk>/`.
- `build_search_index` management command.

[0.3.3]: https://github.com/svalench/django_graph_search/releases/tag/v0.3.3
[0.3.1a1]: https://github.com/svalench/django_graph_search/releases/tag/v0.3.1a1
[0.3.0a1]: https://github.com/svalench/django_graph_search/releases/tag/v0.3.0a1
[0.2.0]: https://github.com/svalench/django_graph_search/releases/tag/v0.2.0
[0.1.2]: https://github.com/svalench/django_graph_search/releases/tag/v0.1.2
