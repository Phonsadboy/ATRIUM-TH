"""Runtime factory — select backend from settings."""
from __future__ import annotations

from typing import Any

from ..clock import now_ms
from ..config import Settings, get_settings
from .base import AgentRuntime, RuntimeEvent
from .letta_adapter import LettaRuntimeAdapter

_runtime: AgentRuntime | None = None


class EngineRuntimeAdapter:
    """Placeholder: v1 in-process engine remains default until cutover."""

    backend = "engine"
    _registered_tools: dict[str, dict[str, Any]]

    def __init__(self) -> None:
        self._registered_tools = {}

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "backend": self.backend, "degraded": False}

    async def create_agent(self, config) -> dict[str, Any]:
        return {"agentKey": config.agent_key, "runtimeAgentId": None, "backend": self.backend}

    async def get_agent(self, agent_key: str):
        return None

    async def send_message(self, agent_key: str, *, message: str, thread_id=None, metadata=None, **_: Any):
        raise NotImplementedError("use v1 engine chat path when agent_backend=engine")

    async def stream_events(self, agent_key: str, *, message: str, thread_id=None, metadata=None, **_: Any):
        raise NotImplementedError("use v1 engine chat path when agent_backend=engine")
        if False:  # pragma: no cover
            yield RuntimeEvent("done", {})

    async def register_tool(self, name: str, schema: dict[str, Any]):
        tool_name = str(name or "").strip()
        if not tool_name:
            raise ValueError("runtime tool name is required")
        self._registered_tools[tool_name] = dict(schema)
        return {"ok": True, "tool": tool_name, "backend": self.backend, "registered": True, "mode": "in_process"}

    async def update_memory(self, agent_key: str, *, label: str, value: str):
        return {"ok": True, "agentKey": agent_key, "label": label}

    async def recall(self, agent_key: str, *, query: str, limit: int = 8):
        return []

    async def checkpoint(self, agent_key: str, *, reason: str):
        return {
            "ok": True,
            "backend": self.backend,
            "agentKey": agent_key,
            "runtimeAgentId": None,
            "reason": reason,
            "ts": now_ms(),
            "snapshot": None,
            "snapshotAvailable": False,
        }


def get_agent_runtime(settings: Settings | None = None) -> AgentRuntime:
    global _runtime
    if _runtime is not None:
        return _runtime
    settings = settings or get_settings()
    if settings.use_letta_runtime:
        _runtime = LettaRuntimeAdapter(settings)
    else:
        _runtime = EngineRuntimeAdapter()
    return _runtime


def reset_agent_runtime() -> None:
    global _runtime
    _runtime = None


async def agent_runtime_health(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    runtime = get_agent_runtime(settings)
    health = await runtime.health()
    health["configuredBackend"] = settings.agent_backend
    health["useLetta"] = settings.use_letta_runtime
    health["degradedQueue"] = settings.runtime_degraded_queue
    health["degradedRetryS"] = settings.runtime_degraded_retry_s
    return health
