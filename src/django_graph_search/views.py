from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from typing import Any, Dict, Iterable, List, Optional

from django.apps import apps
from django.http import JsonResponse, StreamingHttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .events import EventHub
from .searcher import Searcher
from .settings import get_settings

log = logging.getLogger(__name__)


class SearchAPIView(View):
    def get(self, request, *args, **kwargs):
        query = request.GET.get("q", "").strip()
        if not query:
            return JsonResponse({"error": "Parameter 'q' is required."}, status=400)
        models = request.GET.get("models")
        model_list = [m.strip() for m in models.split(",")] if models else None
        limit = request.GET.get("limit")
        limit_value = int(limit) if limit else None

        searcher = Searcher()
        results = searcher.search(query, models=model_list, limit=limit_value)
        return JsonResponse(
            {"query": query, "results": results, "total": len(results)},
            status=200,
        )


@method_decorator(csrf_exempt, name="dispatch")
class ConversationalSearchAPIView(View):
    """Session-aware semantic search.

    Accepts ``POST`` with a JSON body or a form payload:

    .. code-block:: json

        {
          "query": "only products",
          "conversation_id": "abc-123",
          "models": ["shop.Product"],
          "limit": 5
        }

    Returns:

    .. code-block:: json

        {
          "conversation_id": "abc-123",
          "query": "only products",
          "interpreted_query": "red phone",
          "clarification_needed": false,
          "results": [...],
          "total": 5
        }

    The endpoint disables itself with HTTP 404 when
    ``CONVERSATIONAL.ENABLED`` is false, so it is safe to leave the URL
    registered globally.
    """

    # Module-level memory cache: keep a single backend per process so the
    # in-memory variant actually persists across requests in tests and dev.
    _memory_cache: Dict[str, Any] = {}

    def post(self, request, *args, **kwargs):
        cfg = get_settings()
        if not cfg.conversational.enabled:
            return JsonResponse({"error": "Conversational search is disabled."}, status=404)
        payload = self._parse_body(request)
        query = (payload.get("query") or "").strip()
        if not query:
            return JsonResponse({"error": "Parameter 'query' is required."}, status=400)
        conversation_id = payload.get("conversation_id") or str(uuid.uuid4())
        models = payload.get("models")
        if isinstance(models, str):
            models = [m.strip() for m in models.split(",") if m.strip()]
        limit = payload.get("limit")
        try:
            limit_value = int(limit) if limit is not None else None
        except (TypeError, ValueError):
            limit_value = None

        memory = self._get_memory_backend(cfg)
        searcher = Searcher()
        graph = self._build_graph(cfg, searcher=searcher, memory=memory)
        state = {
            "conversation_id": conversation_id,
            "raw_query": query,
            "models": list(models) if models else None,
            "limit": limit_value or cfg.default_results_limit,
        }
        try:
            out = graph.invoke(state)
        except Exception as exc:  # noqa: BLE001
            log.exception("Conversational graph failed: %s", exc)
            return JsonResponse({"error": "Internal error in conversational graph."}, status=500)

        body = {
            "conversation_id": out.get("conversation_id", conversation_id),
            "query": query,
            "interpreted_query": out.get("interpreted_query", query),
            "clarification_needed": bool(out.get("clarification_needed", False)),
            "clarification_message": out.get("clarification_message", ""),
            "results": out.get("results") or [],
            "total": len(out.get("results") or []),
        }
        return JsonResponse(body, status=200)

    # GET is handy for clearing the conversation.
    def delete(self, request, *args, **kwargs):
        cfg = get_settings()
        if not cfg.conversational.enabled:
            return JsonResponse({"error": "Conversational search is disabled."}, status=404)
        cid = request.GET.get("conversation_id") or self._parse_body(request).get("conversation_id")
        if not cid:
            return JsonResponse({"error": "conversation_id is required."}, status=400)
        memory = self._get_memory_backend(cfg)
        memory.clear_history(cid)
        return JsonResponse({"conversation_id": cid, "cleared": True}, status=200)

    # ----------------------------------------------------------- helpers

    @staticmethod
    def _parse_body(request) -> Dict[str, Any]:
        if request.body:
            content_type = (request.META.get("CONTENT_TYPE") or "").split(";")[0].strip()
            if content_type == "application/json":
                try:
                    return json.loads(request.body.decode("utf-8")) or {}
                except (ValueError, UnicodeDecodeError):
                    return {}
        # Fall back to form data / query params.
        merged: Dict[str, Any] = {}
        merged.update(request.POST.dict() if hasattr(request.POST, "dict") else {})
        merged.update(request.GET.dict() if hasattr(request.GET, "dict") else {})
        return merged

    @classmethod
    def _get_memory_backend(cls, cfg):
        from .memory import build_memory_backend

        cache_key = (
            cfg.conversational.memory_backend,
            tuple(sorted((cfg.conversational.memory_options or {}).items())),
            cfg.conversational.max_history_items,
        )
        existing = cls._memory_cache.get(cache_key)
        if existing is not None:
            return existing
        backend = build_memory_backend(
            cfg.conversational.memory_backend,
            max_history_items=cfg.conversational.max_history_items,
            options=cfg.conversational.memory_options,
        )
        cls._memory_cache[cache_key] = backend
        return backend

    @staticmethod
    def _build_graph(cfg, *, searcher, memory):
        from django.utils.module_loading import import_string

        factory = import_string(cfg.conversational.followup_graph)
        return factory(cfg, searcher=searcher, memory=memory)


@method_decorator(csrf_exempt, name="dispatch")
class StreamingSearchAPIView(View):
    """Stream pipeline events back to the client as they happen.

    The endpoint is opt-in: it returns HTTP 404 unless
    ``GRAPH_SEARCH["STREAMING"]["ENABLED"]`` is true.

    Two transports are supported:

    * ``ndjson`` (default) — each line is a JSON object, easy to consume from
      browsers via ``fetch`` + ``ReadableStream`` or from CLIs via ``jq``.
    * ``sse`` — standards-compliant Server-Sent Events for ``EventSource``.

    The handler runs the search in a worker thread so the event hub publishes
    events while the request loop drains them. A terminal ``completed`` event
    is always sent (even on error) so clients have a reliable end-of-stream
    marker.
    """

    def get(self, request, *args, **kwargs):
        return self._handle(request)

    def post(self, request, *args, **kwargs):
        return self._handle(request)

    def _handle(self, request):
        cfg = get_settings()
        if not cfg.streaming.enabled:
            return JsonResponse({"error": "Streaming search is disabled."}, status=404)

        payload = self._extract_payload(request)
        query = (payload.get("q") or payload.get("query") or "").strip()
        if not query:
            return JsonResponse({"error": "Parameter 'q' is required."}, status=400)
        models = payload.get("models")
        if isinstance(models, str):
            models = [m.strip() for m in models.split(",") if m.strip()]
        try:
            limit = int(payload.get("limit")) if payload.get("limit") else None
        except (TypeError, ValueError):
            limit = None

        hub = EventHub()
        events_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        sentinel = object()
        # Bridge hub -> queue so the request thread can drain it.
        hub.subscribe(events_queue.put)

        searcher = Searcher(event_hub=hub) if cfg.langgraph.enabled else Searcher()

        result_holder: Dict[str, Any] = {}

        def _runner():
            try:
                result_holder["results"] = searcher.search(
                    query, models=models, limit=limit
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("Streaming search failed: %s", exc)
                result_holder["error"] = str(exc)
                hub.publish({"type": "error", "message": str(exc)})
            finally:
                events_queue.put(sentinel)

        worker = threading.Thread(target=_runner, daemon=True)
        worker.start()

        fmt = cfg.streaming.format

        def _generate() -> Iterable[bytes]:
            yield _format_event(
                {"type": "query_received", "query": query}, fmt
            )
            while True:
                event = events_queue.get()
                if event is sentinel:
                    break
                # Drop internal events when configured to do so (final event
                # is still emitted below).
                if (
                    not cfg.streaming.include_internal_events
                    and event.get("type") not in {"query_received", "completed", "error"}
                ):
                    continue
                yield _format_event(event, fmt)

            # Wait briefly for the worker to publish its return value if it
            # has not already done so.
            worker.join(timeout=0.1)
            final = {
                "type": "results",
                "results": result_holder.get("results") or [],
                "total": len(result_holder.get("results") or []),
            }
            yield _format_event(final, fmt)
            yield _format_event({"type": "end"}, fmt)

        content_type = (
            "text/event-stream"
            if fmt == "sse"
            else "application/x-ndjson"
        )
        response = StreamingHttpResponse(_generate(), content_type=content_type)
        # Disable proxy buffering so events arrive as soon as they are emitted.
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    @staticmethod
    def _extract_payload(request) -> Dict[str, Any]:
        if request.method == "POST" and request.body:
            ctype = (request.META.get("CONTENT_TYPE") or "").split(";")[0].strip()
            if ctype == "application/json":
                try:
                    return json.loads(request.body.decode("utf-8")) or {}
                except (ValueError, UnicodeDecodeError):
                    return {}
        merged: Dict[str, Any] = {}
        merged.update(request.POST.dict() if hasattr(request.POST, "dict") else {})
        merged.update(request.GET.dict() if hasattr(request.GET, "dict") else {})
        return merged


def _format_event(event: Dict[str, Any], fmt: str) -> bytes:
    payload = json.dumps(event, ensure_ascii=False, default=str)
    if fmt == "sse":
        return f"event: {event.get('type', 'message')}\ndata: {payload}\n\n".encode("utf-8")
    return (payload + "\n").encode("utf-8")


class SimilarAPIView(View):
    def get(self, request, model: str, pk: str, *args, **kwargs):
        if "." not in model:
            return JsonResponse({"error": "Model must be in 'app.Model' format."}, status=400)
        app_label, model_name = model.split(".", 1)
        model_cls = apps.get_model(app_label, model_name)
        if model_cls is None:
            return JsonResponse({"error": "Model not found."}, status=404)
        instance = model_cls.objects.filter(pk=pk).first()
        if instance is None:
            return JsonResponse({"error": "Object not found."}, status=404)
        limit = request.GET.get("limit")
        limit_value = int(limit) if limit else None
        searcher = Searcher()
        results = searcher.find_similar(instance, limit=limit_value)
        return JsonResponse(
            {
                "model": model,
                "pk": pk,
                "results": results,
                "total": len(results),
            },
            status=200,
        )

