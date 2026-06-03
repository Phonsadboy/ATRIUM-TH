"""In-process event hub: fan out state snapshots + transient pulses to WS clients.

The contract has two channels (mirroring CompanyClient.subscribe / onPulse):
  * `state`  — a full CompanyState snapshot (sent on every meaningful change)
  * `pulse`  — a transient effect signal for canvas animation
  * `notify` — v0.4 notifications (new addressable channel; UI may ignore)

Full snapshots keep the WsClient trivial: it just replaces its state on each
`state` message. For a local single-user company the snapshot is small and
this is plenty efficient.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from .db.base import session_scope
from .db.repo import Repo

_QUEUE_MAX = 64


class Hub:
    def __init__(self) -> None:
        self._clients: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dirty: asyncio.Event | None = None

    def reset_runtime(self) -> None:
        """Clear loop-bound state between ASGI lifespan runs.

        TestClient creates a fresh event loop per context; production usually
        has one. Keeping loop-bound asyncio objects past shutdown makes the next
        lifespan fail with "bound to a different event loop".
        """
        self._clients.clear()
        self._loop = None
        self._dirty = None

    def _dirty_event(self) -> asyncio.Event:
        loop = asyncio.get_running_loop()
        if self._dirty is None or self._loop is not loop:
            self._clients.clear()
            self._loop = loop
            self._dirty = asyncio.Event()
        return self._dirty

    def subscribe(self) -> asyncio.Queue:
        self._dirty_event()
        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def _emit(self, msg: dict[str, Any]) -> None:
        for q in list(self._clients):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # slow client: drop oldest, then enqueue latest
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(msg)

    def pulse(self, event: dict[str, Any]) -> None:
        self._emit({"type": "pulse", "event": event})

    def notify(self, notification: dict[str, Any]) -> None:
        self._emit({"type": "notify", "notification": notification})

    def mark_dirty(self) -> None:
        """Engine ticks call this; the flusher coalesces bursts into one snapshot."""
        self._dirty_event().set()

    async def push_state_now(self) -> None:
        if not self._clients:
            return
        async with session_scope() as s:
            snap = await Repo(s).snapshot()
        self._emit({"type": "state", "state": snap})

    async def run_flusher(self, min_interval: float = 0.4) -> None:
        """Background loop: when marked dirty, broadcast a fresh snapshot (debounced)."""
        while True:
            dirty = self._dirty_event()
            await dirty.wait()
            dirty.clear()
            try:
                await self.push_state_now()
            except Exception:
                # A transient DB/snapshot failure must not permanently stop
                # realtime state delivery. Keep the dirty flag set so the next
                # loop retries after the debounce interval.
                dirty.set()
            await asyncio.sleep(min_interval)


hub = Hub()
