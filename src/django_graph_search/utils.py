from __future__ import annotations

from hashlib import sha256


def hash_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()

