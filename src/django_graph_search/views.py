from __future__ import annotations

import json
import logging
import math
import queue
import threading
import uuid
import warnings
from typing import Any, Dict, Iterable, Optional, Tuple, Union

from django.apps import apps
from django.conf import settings as django_settings
from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .events import EventHub
from .searcher import Searcher
from .settings import GraphSearchConfig, get_settings

log = logging.getLogger(__name__)

# Реестр бэкендов памяти диалога: один экземпляр на процесс (in-memory не шарится между воркерами).
_memory_backend_lock = threading.Lock()
_memory_backend_registry: Dict[Tuple[Any, ...], Any] = {}
# Флаг однократного production-warning про inmemory-бэкенд (иначе warning на каждый POST).
_inmemory_prod_warning_emitted = False


def _parse_int_param(
    value: Optional[Union[str, int, float]],
    param_name: str,
    default: Optional[int] = None,
    min_value: Optional[int] = 1,
    max_value: Optional[int] = None,
) -> Tuple[Optional[int], Optional[JsonResponse]]:
    """
    Safely parse an integer query or body parameter.

    Args:
        value: Raw string or numeric value from the client.
        param_name: Name for error messages.
        default: Used when value is None or empty string.
        min_value: Minimum inclusive; None disables lower bound check.
        max_value: Maximum inclusive; values above are clamped with a warning.

    Returns:
        A tuple ``(parsed, None)`` on success, or ``(None, JsonResponse)`` on failure.
    """
    coerced, err = _stringify_numeric_param(value, param_name)
    if err is not None:
        return None, err
    if coerced is None:
        return default, None
    try:
        parsed = int(coerced, 10)
    except ValueError:
        return None, JsonResponse(
            {"error": f"'{param_name}' must be a positive integer."},
            status=400,
        )
    if min_value is not None and parsed < min_value:
        return None, JsonResponse(
            {"error": f"'{param_name}' must be a positive integer."},
            status=400,
        )
    if max_value is not None and parsed > max_value:
        log.warning(
            "Parameter %r=%s exceeds max_value=%s — clamping.",
            param_name,
            parsed,
            max_value,
        )
        parsed = max_value
    return parsed, None


def _parse_float_param(
    value: Optional[Union[str, int, float]],
    param_name: str,
    default: Optional[float] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
) -> Tuple[Optional[float], Optional[JsonResponse]]:
    """
    Безопасно распарсить float из query/body (например ``min_score``).

    Returns:
        Кортеж ``(parsed, None)`` при успехе или ``(None, JsonResponse)`` при ошибке.
    """
    bad = JsonResponse(
        {"error": f"'{param_name}' must be a float between 0.0 and 1.0."},
        status=400,
    )
    out: Optional[float] = None
    err: Optional[JsonResponse] = None

    if value is None or value == "":
        out = default
    elif isinstance(value, bool):
        err = bad
    elif isinstance(value, (int, float)):
        out = float(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            out = default
        else:
            try:
                out = float(stripped)
            except ValueError:
                err = bad
    else:
        err = bad

    if err is None and out is not None:
        if math.isnan(out):
            err = bad
            out = None
        elif min_value is not None and out < min_value:
            err = bad
            out = None
        elif max_value is not None and out > max_value:
            err = bad
            out = None

    return out, err


def _stringify_numeric_param(
    value: Optional[Union[str, int, float]],
    param_name: str,
) -> Tuple[Optional[str], Optional[JsonResponse]]:
    """
    Привести limit и подобные поля к строке цифр или вернуть ошибку 400.

    Args:
        value: Сырое значение из GET/JSON.
        param_name: Имя параметра для сообщения об ошибке.

    Returns:
        ``(строка_цифр_или_None, None)`` либо ``(None, JsonResponse)``.
    """
    invalid = JsonResponse(
        {"error": f"'{param_name}' must be a positive integer."},
        status=400,
    )
    out: Optional[str] = None
    err: Optional[JsonResponse] = None

    if value is None or value == "":
        pass
    elif isinstance(value, bool):
        err = invalid
    elif isinstance(value, int):
        out = str(value)
    elif isinstance(value, float):
        if not value.is_integer():
            err = invalid
        else:
            out = str(int(value))
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped:
            out = stripped
    else:
        err = invalid

    return out, err


class SearchPermissionMixin:
    """Mixin that applies GRAPH_SEARCH.API permission and throttle checks."""

    def _check_access(self, request: HttpRequest) -> Optional[JsonResponse]:
        """
        Enforce API permission and throttle configuration.

        Returns:
            ``None`` if the request may proceed, otherwise a ``JsonResponse`` error.
        """
        from .permissions import PermissionDenied, ThrottledError, check_permissions, check_throttle

        cfg = get_settings()
        try:
            check_permissions(request, cfg)
            check_throttle(request, cfg)
        except PermissionDenied as exc:
            return JsonResponse({"error": exc.detail}, status=exc.status_code)
        except ThrottledError as exc:
            return JsonResponse(
                {"error": exc.detail},
                status=429,
                headers={"Retry-After": str(exc.retry_after)},
            )
        return None


def _memory_backend_cache_key(cfg: GraphSearchConfig) -> Tuple[Any, ...]:
    """Ключ реестра бэкендов памяти по настройкам conversational."""
    return (
        cfg.conversational.memory_backend,
        tuple(sorted((cfg.conversational.memory_options or {}).items())),
        cfg.conversational.max_history_items,
    )


def get_conversation_memory_backend(cfg: GraphSearchConfig) -> Any:
    """
    Вернуть singleton бэкенда памяти для конфигурации (один объект на процесс).

    In-memory бэкенд пригоден только для одного процесса; для Gunicorn с несколькими
    воркерами используйте MEMORY_BACKEND='redis' и Django CACHES.
    """
    from .memory import build_memory_backend

    cache_key = _memory_backend_cache_key(cfg)
    with _memory_backend_lock:
        existing = _memory_backend_registry.get(cache_key)
        if existing is not None:
            return existing
        backend = build_memory_backend(
            cfg.conversational.memory_backend,
            max_history_items=cfg.conversational.max_history_items,
            options=cfg.conversational.memory_options,
        )
        _memory_backend_registry[cache_key] = backend
        return backend


class SearchAPIView(SearchPermissionMixin, View):
    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        denied = self._check_access(request)
        if denied is not None:
            return denied
        query = request.GET.get("q", "").strip()
        if not query:
            return JsonResponse({"error": "Parameter 'q' is required."}, status=400)
        models = request.GET.get("models")
        model_list = [m.strip() for m in models.split(",")] if models else None
        limit_value, err = _parse_int_param(
            request.GET.get("limit"),
            "limit",
            default=None,
            min_value=1,
            max_value=1000,
        )
        if err is not None:
            return err
        min_score, err = _parse_float_param(
            request.GET.get("min_score"),
            "min_score",
            default=None,
            min_value=0.0,
            max_value=1.0,
        )
        if err is not None:
            return err

        searcher = Searcher()
        results = searcher.search(query, models=model_list, limit=limit_value)
        if min_score is not None:
            results = [r for r in results if float(r.get("score") or 0) >= min_score]
        payload: Dict[str, Any] = {
            "query": query,
            "results": results,
            "total": len(results),
        }
        if min_score is not None:
            payload["min_score_applied"] = min_score
        return JsonResponse(payload, status=200)


@method_decorator(csrf_exempt, name="dispatch")
class ConversationalSearchAPIView(SearchPermissionMixin, View):
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

    **Production:** при ``MEMORY_BACKEND="inmemory"`` история сессии живёт только
    в памяти текущего процесса — при нескольких воркерах Gunicorn/uWSGI сессии
    «теряются» между воркерами. Рекомендуемая конфигурация::

        "CONVERSATIONAL": {
            "ENABLED": True,
            "MEMORY_BACKEND": "redis",
            "MEMORY_OPTIONS": {
                "alias": "default",
                "key_prefix": "dgs_conv",
                "ttl": 3600,
            },
            "MAX_HISTORY_ITEMS": 10,
        }
    """

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        denied = self._check_access(request)
        if denied is not None:
            return denied
        cfg = get_settings()
        global _inmemory_prod_warning_emitted  # pylint: disable=global-statement
        if (
            cfg.conversational.enabled
            and cfg.conversational.memory_backend == "inmemory"
            and not django_settings.DEBUG
            and not _inmemory_prod_warning_emitted
        ):
            _inmemory_prod_warning_emitted = True
            warnings.warn(
                "GRAPH_SEARCH CONVERSATIONAL.MEMORY_BACKEND='inmemory' is not safe for "
                "multi-process production deployments (Gunicorn, uWSGI). "
                "Switch to MEMORY_BACKEND='redis' for correct session continuity.",
                stacklevel=2,
                category=RuntimeWarning,
            )
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
        limit_value, err = _parse_int_param(
            payload.get("limit"),
            "limit",
            default=None,
            min_value=1,
            max_value=1000,
        )
        if err is not None:
            return err

        memory = get_conversation_memory_backend(cfg)
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

    def delete(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        denied = self._check_access(request)
        if denied is not None:
            return denied
        cfg = get_settings()
        if not cfg.conversational.enabled:
            return JsonResponse({"error": "Conversational search is disabled."}, status=404)
        cid = request.GET.get("conversation_id") or self._parse_body(request).get("conversation_id")
        if not cid:
            return JsonResponse({"error": "conversation_id is required."}, status=400)
        memory = get_conversation_memory_backend(cfg)
        memory.clear_history(cid)
        return JsonResponse({"conversation_id": cid, "cleared": True}, status=200)

    # ----------------------------------------------------------- helpers

    @staticmethod
    def _parse_body(request: HttpRequest) -> Dict[str, Any]:
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

    @staticmethod
    def _build_graph(cfg: GraphSearchConfig, *, searcher: Searcher, memory: Any) -> Any:
        from django.utils.module_loading import import_string

        factory = import_string(cfg.conversational.followup_graph)
        return factory(cfg, searcher=searcher, memory=memory)


@method_decorator(csrf_exempt, name="dispatch")
class StreamingSearchAPIView(SearchPermissionMixin, View):
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

    def get(
        self,
        request: HttpRequest,
        *args: Any,
        **kwargs: Any,
    ) -> StreamingHttpResponse | JsonResponse:
        return self._handle(request)

    def post(
        self,
        request: HttpRequest,
        *args: Any,
        **kwargs: Any,
    ) -> StreamingHttpResponse | JsonResponse:
        return self._handle(request)

    def _handle(self, request: HttpRequest) -> StreamingHttpResponse | JsonResponse:
        denied = self._check_access(request)
        if denied is not None:
            return denied
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
        limit, err = _parse_int_param(
            payload.get("limit"),
            "limit",
            default=None,
            min_value=1,
            max_value=1000,
        )
        if err is not None:
            return err

        hub = EventHub()
        events_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        sentinel = object()
        # Bridge hub -> queue so the request thread can drain it.
        hub.subscribe(events_queue.put)

        searcher = Searcher(event_hub=hub) if cfg.langgraph.enabled else Searcher()

        result_holder: Dict[str, Any] = {}

        def _runner() -> None:
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
    def _extract_payload(request: HttpRequest) -> Dict[str, Any]:
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
        event_type = event.get("type", "message")
        lines = (
            f"event: {event_type}\n"
            f"data: {payload}\n\n"
        )
        return lines.encode("utf-8")
    return (payload + "\n").encode("utf-8")


class SimilarAPIView(SearchPermissionMixin, View):
    def get(
        self,
        request: HttpRequest,
        model: str,
        pk: str,
        *args: Any,
        **kwargs: Any,
    ) -> JsonResponse:
        denied = self._check_access(request)
        if denied is not None:
            return denied
        if "." not in model:
            return JsonResponse({"error": "Model must be in 'app.Model' format."}, status=400)
        app_label, model_name = model.split(".", 1)
        model_cls = apps.get_model(app_label, model_name)
        if model_cls is None:
            return JsonResponse({"error": "Model not found."}, status=404)
        instance = model_cls.objects.filter(pk=pk).first()
        if instance is None:
            return JsonResponse({"error": "Object not found."}, status=404)
        limit_value, err = _parse_int_param(
            request.GET.get("limit"),
            "limit",
            default=None,
            min_value=1,
            max_value=1000,
        )
        if err is not None:
            return err
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
