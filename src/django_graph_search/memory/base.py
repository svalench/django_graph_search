"""Conversation memory contract.

Memory backends store a small, serialisable trail of recent search events per
conversation. We deliberately avoid storing ORM instances or long blobs so
events can be persisted to Redis / cache backends without surprises.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ConversationEvent:
    """A single turn of the conversation.

    Only metadata about results is kept (model + pk), never the full payload.
    """

    role: str  # "user" | "assistant"
    query: str = ""
    interpreted_query: str = ""
    models: Optional[List[str]] = None
    top_results: List[Dict[str, Any]] = field(default_factory=list)
    clarification_needed: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ConversationEvent":
        return cls(
            role=payload.get("role", "user"),
            query=payload.get("query", "") or "",
            interpreted_query=payload.get("interpreted_query", "") or "",
            models=payload.get("models"),
            top_results=payload.get("top_results") or [],
            clarification_needed=bool(payload.get("clarification_needed", False)),
            timestamp=float(payload.get("timestamp") or time.time()),
        )


class BaseMemoryBackend(ABC):
    """Minimal interface every memory backend implements."""

    def __init__(self, max_history_items: int = 10, **options: Any) -> None:
        if max_history_items < 1:
            raise ValueError("max_history_items must be >= 1")
        self.max_history_items = max_history_items
        self.options = options

    @abstractmethod
    def get_history(self, session_id: str) -> List[ConversationEvent]:
        """Return events in chronological order (oldest first)."""

    @abstractmethod
    def append_event(self, session_id: str, event: ConversationEvent) -> None:
        """Append an event, trimming to ``max_history_items`` from the right."""

    @abstractmethod
    def clear_history(self, session_id: str) -> None:
        """Drop all events for ``session_id``."""
