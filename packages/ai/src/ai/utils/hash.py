"""Hashing utilities.

Provides convenience wrappers around :mod:`hashlib` for content-addressable
storage, cache keys, and deduplication.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_hex(data: str | bytes) -> str:
    """Return the SHA-256 hex digest of *data*.

    Strings are encoded as UTF-8 before hashing.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def stable_hash(obj: Any) -> str:
    """Return a deterministic SHA-256 hex digest for a JSON-serialisable *obj*.

    The object is serialised with sorted keys and no whitespace to ensure
    the same logical value always produces the same hash.
    """
    serialized = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return sha256_hex(serialized)
