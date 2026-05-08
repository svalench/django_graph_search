"""Resolve the configured LLM backend from settings.

The factory keeps the rest of the codebase unaware of concrete implementations
and lets users plug their own backend via a dotted import path.
"""
from __future__ import annotations

from typing import Optional

from django.utils.module_loading import import_string

from ..exceptions import ConfigurationError
from ..settings import LLMConfig
from .base import BaseLLMBackend
from .dummy import DummyLLMBackend


def build_llm_backend(config: Optional[LLMConfig]) -> BaseLLMBackend:
    """Instantiate an LLM backend from ``LLMConfig``.

    When no backend is configured, returns the deterministic
    :class:`DummyLLMBackend` so callers can rely on a non-None object.
    """
    if config is None or not config.backend:
        return DummyLLMBackend()
    try:
        backend_cls = import_string(config.backend)
    except ImportError as exc:  # pragma: no cover - guarded by tests
        raise ConfigurationError(
            f"Cannot import LLM backend '{config.backend}': {exc}"
        ) from exc
    instance = backend_cls(model=config.model, **(config.options or {}))
    if not isinstance(instance, BaseLLMBackend):
        raise ConfigurationError(
            f"LLM backend '{config.backend}' must subclass BaseLLMBackend."
        )
    return instance
