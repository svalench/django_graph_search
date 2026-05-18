# Changelog

All notable changes to **django-graph-search** are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[0.2.0]: https://github.com/svalench/django_graph_search/releases/tag/v0.2.0
[0.1.2]: https://github.com/svalench/django_graph_search/releases/tag/v0.1.2
