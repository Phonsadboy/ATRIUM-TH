"""Pluggable agent runtime interface (ATRIUM v2).

ATRIUM remains orchestrator + system of record; the runtime owns per-agent LLM
loops and memory paging. Letta/MemGPT is the first adapter.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol

RuntimeEventKind = Literal[
    "assistant_message",
    "tool_call",
    "tool_result",
    "reasoning",
    "status",
    "usage",
    "error",
    "done",
]


@dataclass
class RuntimeEvent:
    kind: RuntimeEventKind
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentRuntimeConfig:
    agent_key: str
    model: str
    persona: str = ""
    charter: str = ""
    owner_profile: str = ""
    company_memory: str = ""
    tools: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentRuntime(Protocol):
    backend: str

    async def health(self) -> dict[str, Any]: ...

    async def create_agent(self, config: AgentRuntimeConfig) -> dict[str, Any]: ...

    async def get_agent(self, agent_key: str) -> dict[str, Any] | None: ...

    async def send_message(
        self,
        agent_key: str,
        *,
        message: str,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        client_tools: list[dict[str, Any]] | None = None,
        messages: list[dict[str, Any]] | None = None,
        max_steps: int | None = None,
        status_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]: ...

    async def stream_events(
        self,
        agent_key: str,
        *,
        message: str,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        client_tools: list[dict[str, Any]] | None = None,
        messages: list[dict[str, Any]] | None = None,
        max_steps: int | None = None,
    ) -> AsyncIterator[RuntimeEvent]: ...

    async def register_tool(self, name: str, schema: dict[str, Any]) -> dict[str, Any]: ...

    async def update_memory(self, agent_key: str, *, label: str, value: str) -> dict[str, Any]: ...

    async def recall(self, agent_key: str, *, query: str, limit: int = 8) -> list[dict[str, Any]]: ...

    async def checkpoint(self, agent_key: str, *, reason: str) -> dict[str, Any]: ...
