"""ExecutorRouter — route tools to host/sandbox/http/browser/MCP executors."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..config import Settings, get_settings
from .host_bridge import HostBridge
from .registry import ToolRegistry, ToolSpec

ExecutorKind = Literal["host", "sandbox", "http", "browser", "desktop", "mcp", "audit", "scheduler"]


@dataclass
class RoutedExecution:
    tool: str
    executor: ExecutorKind
    risk_class: str
    checkpoint_before: bool
    audit_required: bool = True
    block_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "executor": self.executor,
            "riskClass": self.risk_class,
            "checkpointBefore": self.checkpoint_before,
            "auditRequired": self.audit_required,
            "blockReason": self.block_reason,
        }


class ExecutorRouter:
    def __init__(self, registry: ToolRegistry, settings: Settings | None = None):
        self.registry = registry
        self.settings = settings or get_settings()
        self.host_bridge = HostBridge(self.settings)

    def route(self, tool_name: str, args: dict[str, Any] | None = None) -> RoutedExecution:
        spec: ToolSpec | None = self.registry.get(tool_name)
        if not spec:
            return RoutedExecution(
                tool=tool_name,
                executor="host",
                risk_class="privileged",
                checkpoint_before=True,
                block_reason=f"unknown tool: {tool_name}",
            )
        executor = spec.executor  # type: ignore[assignment]
        checkpoint = spec.supports_checkpoint or spec.mutates_state
        allowed, reason = self.host_bridge.can_run(tool_name, args)
        if not allowed and executor in {"host", "browser", "desktop"}:
            return RoutedExecution(
                tool=tool_name,
                executor=executor,
                risk_class=spec.risk_class,
                checkpoint_before=checkpoint,
                block_reason=reason,
            )
        return RoutedExecution(
            tool=tool_name,
            executor=executor,
            risk_class=spec.risk_class,
            checkpoint_before=checkpoint,
        )
