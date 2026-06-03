"""Wall-clock helper.

The contract carries timestamps as epoch milliseconds (`number` in TS).
The live system uses real time so an always-on deployment stays consistent
across restarts. `now_ms()` is the single source of "now" used everywhere a
timestamp is stamped.
"""
from __future__ import annotations

import time


def now_ms() -> int:
    return int(time.time() * 1000)


def day_key(ts_ms: int) -> str:
    """YYYY-MM-DD in UTC, matching the UI's `new Date(ts).toISOString().slice(0,10)`."""
    return time.strftime("%Y-%m-%d", time.gmtime(ts_ms / 1000))


DAY_MS = 86_400_000
