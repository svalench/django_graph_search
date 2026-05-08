"""Pluggable LLM backends used by the optional LangGraph search pipeline.

This subpackage is intentionally self-contained: it has no hard dependency on
LangGraph itself, so the rest of the library keeps working when LangGraph or a
remote LLM SDK is not installed.

Public surface:

* ``BaseLLMBackend`` — the contract every LLM backend implements.
* ``DummyLLMBackend`` — deterministic, dependency-free backend used in tests
  and as the default fallback when no LLM is configured.
* ``build_llm_backend`` — factory that resolves the configured backend from
  :class:`~django_graph_search.settings.LLMConfig`.
"""
from .base import BaseLLMBackend, RerankCandidate
from .dummy import DummyLLMBackend
from .factory import build_llm_backend

__all__ = [
    "BaseLLMBackend",
    "RerankCandidate",
    "DummyLLMBackend",
    "build_llm_backend",
]
