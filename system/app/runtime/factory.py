"""Runtime factory for ATRIUM's native in-process agent layer."""
from __future__ import annotations

from typing import Any

from ..clock import now_ms
from ..config import Settings, get_settings
from .base import AgentRuntime, RuntimeEvent

_runtime: AgentRuntime | None = None


class NativeRuntimeAdapter:
    """Small compatibility adapter for runtime-facing APIs.

    Chat, autonomous work, tool loops, memory, and retries run through ATRIUM's
    provider-native paths. This adapter only provides a stable health/tool/
    checkpoint surface for code that still talks to the runtime abstraction.
    """

    backend = "native"
    _registered_tools: dict[str, dict[str, Any]]

    def __init__(self) -> None:
        self._registered_tools = {}

    async def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "backend": self.backend,
            "degraded": False,
            "externalRuntime": False,
        }

    async def create_agent(self, config) -> dict[str, Any]:
        return {
            "ok": True,
            "agentKey": config.agent_key,
            "runtimeAgentId": None,
            "backend": self.backend,
            "externalRuntime": False,
            "provisioningRequired": False,
        }

    async def get_agent(self, agent_key: str):
        return {
            "agentKey": agent_key,
            "backend": self.backend,
            "runtimeAgentId": None,
            "externalRuntime": False,
        }

    async def send_message(self, agent_key: str, *, message: str, thread_id=None, metadata=None, **_: Any):
        raise NotImplementedError("native runtime uses ATRIUM provider-native chat/work paths")

    async def stream_events(self, agent_key: str, *, message: str, thread_id=None, metadata=None, **_: Any):
        raise NotImplementedError("native runtime uses ATRIUM provider-native chat/work paths")
        if False:  # pragma: no cover
            yield RuntimeEvent("done", {})

    async def register_tool(self, name: str, schema: dict[str, Any]):
        tool_name = str(name or "").strip()
        if not tool_name:
            raise ValueError("runtime tool name is required")
        self._registered_tools[tool_name] = dict(schema)
        return {
            "ok": True,
            "tool": tool_name,
            "backend": self.backend,
            "registered": True,
            "mode": "in_process",
        }

    async def update_memory(self, agent_key: str, *, label: str, value: str):
        return {
            "ok": True,
            "backend": self.backend,
            "agentKey": agent_key,
            "label": label,
            "externalRuntime": False,
        }

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
            "externalRuntime": False,
        }


def get_agent_runtime(settings: Settings | None = None) -> AgentRuntime:
    del settings
    global _runtime
    if _runtime is None:
        _runtime = NativeRuntimeAdapter()
    return _runtime


def reset_agent_runtime() -> None:
    global _runtime
    _runtime = None


async def agent_runtime_health(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    runtime = get_agent_runtime(settings)
    health = await runtime.health()
    health["configuredBackend"] = settings.agent_backend_mode
    health["externalRuntime"] = False
    health["degradedQueue"] = settings.runtime_degraded_queue
    health["degradedRetryS"] = settings.runtime_degraded_retry_s
    return health
