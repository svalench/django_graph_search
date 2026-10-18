"""Request-local namespace for shared conversation memory."""
from __future__ import annotations

import hashlib
import json
from typing import List

from .base import BaseMemoryBackend, ConversationEvent


class ScopedMemoryBackend(BaseMemoryBackend):
    """Keep public conversation IDs independent of authenticated user identity.

    Anonymous conversations remain bearer IDs; deployments requiring private
    history must require authentication. The separate anonymous namespace cannot
    address an authenticated user's keys.
    """

    def __init__(self, backend: BaseMemoryBackend, scope: str) -> None:
        super().__init__(max_history_items=backend.max_history_items)
        self.backend = backend
        self.scope = scope

    def _key(self, session_id: str) -> str:
        payload = json.dumps([self.scope, session_id], ensure_ascii=True)
        return "scoped:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get_history(self, session_id: str) -> List[ConversationEvent]:
        return self.backend.get_history(self._key(session_id))

    def append_event(self, session_id: str, event: ConversationEvent) -> None:
        self.backend.append_event(self._key(session_id), event)

    def clear_history(self, session_id: str) -> None:
        self.backend.clear_history(self._key(session_id))
