"""Pluggable conversation memory backends.

Used by the optional conversational search endpoint to remember the last few
queries / interpreted queries / result references per ``conversation_id``.
The default is the in-process backend, which is enough for single-worker
deployments and tests.

The contract is intentionally tiny so users can plug Redis, the Django cache
framework or even a database-backed table in a few lines.
"""
from .base import BaseMemoryBackend, ConversationEvent
from .factory import build_memory_backend
from .in_memory import InMemoryBackend

__all__ = [
    "BaseMemoryBackend",
    "ConversationEvent",
    "InMemoryBackend",
    "build_memory_backend",
]
