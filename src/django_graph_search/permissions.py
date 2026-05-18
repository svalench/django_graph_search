"""
Pluggable permission and throttle checking for the search API.

Supports two modes:
1. Standalone (no DRF) — basic allow/deny via callables, simple token check
2. DRF-integrated — delegates to DRF permission_classes and throttle_classes transparently

Usage in settings:
    GRAPH_SEARCH = {
        "API": {
            "REQUIRE_AUTHENTICATION": True,
            # DRF classes (if DRF is installed):
            "PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
            "THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
            # OR standalone callable:
            "PERMISSION_CLASSES": ["myapp.permissions.check_search_permission"],
            # In-process rate limits (per IP) without DRF:
            "THROTTLE_CLASSES": [
                "django_graph_search.permissions.SimpleScopedRateThrottle",
            ],
        }
    }
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Tuple

from django.http import HttpRequest
from django.utils.module_loading import import_string

from .settings import GraphSearchConfig

log = logging.getLogger(__name__)


class PermissionDenied(Exception):
    """Raised when a request does not pass permission checks."""

    status_code: int = 403
    default_detail: str = "Permission denied."

    def __init__(self, detail: str = "", status_code: int = 403) -> None:
        self.detail = detail or self.default_detail
        self.status_code = status_code


class ThrottledError(Exception):
    """Raised when a request exceeds the configured rate limit."""

    status_code: int = 429
    default_detail: str = "Request was throttled."

    def __init__(self, detail: str = "", retry_after: int = 60) -> None:
        self.detail = detail or self.default_detail
        self.status_code = 429
        self.retry_after = retry_after


def check_permissions(request: HttpRequest, config: GraphSearchConfig) -> None:
    """
    Run all configured permission checks.

    Raises ``PermissionDenied`` if any check fails.

    Args:
        request: Current HTTP request.
        config: Resolved graph search configuration.

    Raises:
        PermissionDenied: When authentication is required but missing, an import
            fails, or a configured check denies access.
    """
    api_cfg = config.api

    # Короткий путь: требуется аутентификация Django
    if api_cfg.require_authentication:
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            raise PermissionDenied("Authentication required.", status_code=401)

    for dotted_path in api_cfg.permission_classes:
        try:
            cls_or_fn = import_string(dotted_path)
        except ImportError as exc:
            log.error("Cannot import permission class %r: %s", dotted_path, exc)
            raise PermissionDenied(f"Misconfigured permission class: {dotted_path}") from exc

        # DRF-стиль: класс с has_permission(request, view)
        if isinstance(cls_or_fn, type) and hasattr(cls_or_fn, "has_permission"):
            instance = cls_or_fn()
            if not instance.has_permission(request, None):
                raise PermissionDenied()
        # Обычная функция: check_permission(request) -> bool (не класс)
        elif callable(cls_or_fn) and not isinstance(cls_or_fn, type):
            if not cls_or_fn(request):
                raise PermissionDenied()
        else:
            log.warning(
                "Permission entry %r is neither a class nor a callable — skipped.",
                dotted_path,
            )


def check_throttle(request: HttpRequest, config: GraphSearchConfig) -> None:
    """
    Run all configured throttle checks.

    Raises ``ThrottledError`` if rate limit is exceeded.

    - If the imported object is a class with ``allow_request(request, view)``,
      it is treated like a DRF throttle (including built-in DRF throttles).
    - Callable ``(request, config) -> bool``: ``False`` means throttled.

    When ``THROTTLE_CLASSES`` is empty, throttling is skipped (backward compatible).

    Args:
        request: Current HTTP request.
        config: Resolved graph search configuration.

    Raises:
        ThrottledError: When a throttle denies the request.
    """
    api_cfg = config.api

    if not api_cfg.throttle_classes:
        return

    for dotted_path in api_cfg.throttle_classes:
        try:
            cls = import_string(dotted_path)
        except ImportError as exc:
            log.error("Cannot import throttle class %r: %s", dotted_path, exc)
            continue

        # DRF-стиль с allow_request
        if isinstance(cls, type) and hasattr(cls, "allow_request"):
            instance = cls()
            if not instance.allow_request(request, None):
                wait_fn = getattr(instance, "wait", None)
                wait_val = 60
                if callable(wait_fn):
                    try:
                        wait_val = int(wait_fn() or 60)
                    except (TypeError, ValueError):
                        wait_val = 60
                raise ThrottledError(retry_after=wait_val)
        elif callable(cls) and not isinstance(cls, type):
            # Функция-throttle: True = разрешить
            try:
                allowed = cls(request, config)
            except TypeError:
                allowed = cls(request)
            if not allowed:
                raise ThrottledError(retry_after=60)
        else:
            log.warning(
                "Throttle entry %r is neither a DRF-style throttle class nor a "
                "plain function — skipped.",
                dotted_path,
            )


def _parse_rate(rate: str) -> Tuple[int, float]:
    """
    Parse a rate string such as ``\"60/minute\"`` into (max_hits, window_seconds).

    Args:
        rate: Rate descriptor.

    Returns:
        Tuple of maximum hits allowed and sliding window length in seconds.
        On parse failure returns a very loose limit so misconfiguration does not
        block traffic unexpectedly.
    """
    try:
        left, right = rate.strip().lower().split("/", 1)
        num = int(left.strip())
        unit = right.strip().rstrip("s")
        period = {
            "second": 1.0,
            "minute": 60.0,
            "hour": 3600.0,
        }.get(unit, 60.0)
        if num < 1:
            num = 1
        return num, period
    except (ValueError, AttributeError):
        log.warning("Invalid THROTTLE_RATES entry %r — ignoring.", rate)
        return 10**9, 1.0


class SimpleScopedRateThrottle:
    """
    In-process sliding-window throttle by client IP and auth scope.

    Uses ``GRAPH_SEARCH.API.THROTTLE_RATES`` keys ``search`` (anonymous) and
    ``search_authenticated`` (logged-in). Not safe for multi-process production;
    use DRF throttles backed by cache/Redis there.

    Реестр окон хранится в памяти процесса под глобальной блокировкой.
    """

    _windows: Dict[str, Deque[float]] = {}
    _lock = threading.Lock()

    def allow_request(self, request: HttpRequest, view: Any) -> bool:
        """
        Return whether the request is under the configured rate limit.

        Args:
            request: Django HTTP request.
            view: Optional view (unused; API compatible with DRF throttles).

        Returns:
            True if the request may proceed, False if it should be throttled.
        """
        from .settings import get_settings

        cfg = get_settings()
        rates = cfg.api.throttle_rates or {}
        user = getattr(request, "user", None)
        scope = (
            "search_authenticated"
            if user is not None and user.is_authenticated
            else "search"
        )
        rate = rates.get(scope) or rates.get("search")
        if not rate:
            return True

        max_hits, window = _parse_rate(rate)
        ip = request.META.get("REMOTE_ADDR") or "unknown"
        key = f"{scope}:{ip}"
        now = time.monotonic()

        with self._lock:
            dq = self._windows.setdefault(key, deque())
            while dq and now - dq[0] > window:
                dq.popleft()
            if len(dq) >= max_hits:
                return False
            dq.append(now)
        return True

    def wait(self) -> int:
        """
        Seconds until the client may retry (rough lower bound for Retry-After).

        Returns:
            Retry delay in whole seconds.
        """
        return 60
