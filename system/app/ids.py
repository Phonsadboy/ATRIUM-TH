"""Short, sortable-ish unique ids that mirror the UI's `uid(prefix)` shape."""
from __future__ import annotations

import secrets
import threading
import time

_lock = threading.Lock()
_last_ms = 0
_counter = 0

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_TS_WIDTH = 9
_SEQ_WIDTH = 3
_SEQ_SPACE = 36 ** _SEQ_WIDTH


def _b36(n: int) -> str:
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, 36)
        out.append(_ALPHABET[r])
    return "".join(reversed(out))


def uid(prefix: str = "id") -> str:
    """Mirror the UI shape while preserving readable creation order.

    Timestamp and sequence segments are fixed-width base36 so IDs sort
    lexicographically by creation time within the same prefix, even for bursts
    inside one millisecond or brief clock regressions.
    """
    global _counter, _last_ms
    with _lock:
        stamp = time.time_ns() // 1_000_000
        if stamp <= _last_ms:
            stamp = _last_ms
            _counter += 1
            if _counter >= _SEQ_SPACE:
                stamp += 1
                _counter = 0
        else:
            _counter = 0
        _last_ms = stamp
        seq = _counter
    rand = secrets.token_hex(3)
    return f"{prefix}_{_b36(stamp).zfill(_TS_WIDTH)}{_b36(seq).zfill(_SEQ_WIDTH)}{rand}"
