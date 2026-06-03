"""Typed tool catalog for HostBridge / ExecutorRouter (ATRIUM v2 Phase 0)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RiskClass = Literal[
    "safe_read",
    "local_write",
    "host_write",
    "command",
    "network",
    "desktop",
    "credential",
    "external_send",
    "destructive",
    "privileged",
]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    risk_class: RiskClass
    description: str
    mutates_state: bool = False
    external_system: bool = False
    executor: str = "host"
    default_timeout_ms: int = 10_000
    output_limit_bytes: int = 60_000
    supports_checkpoint: bool = False
    can_use_credentials: bool = False
    rollback_capable: bool = False
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    redaction_rules: list[str] = field(default_factory=list)

    def to_catalog_row(self) -> dict[str, Any]:
        return {
            "tool": self.name,
            "riskClass": self.risk_class,
            "mutatesState": self.mutates_state,
            "externalSystem": self.external_system,
            "description": self.description,
            "executor": self.executor,
            "defaultTimeoutMs": self.default_timeout_ms,
            "outputLimitBytes": self.output_limit_bytes,
            "supportsCheckpoint": self.supports_checkpoint,
            "canUseCredentials": self.can_use_credentials,
            "rollbackCapable": self.rollback_capable,
            "inputSchema": self.input_schema,
            "outputSchema": self.output_schema,
            "redactionRules": self.redaction_rules,
        }


class ToolRegistry:
    def __init__(self, tools: list[ToolSpec] | None = None):
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: ToolSpec) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def list(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def catalog(self) -> list[dict[str, Any]]:
        return [tool.to_catalog_row() for tool in self.list()]

    def risk_class(self, name: str) -> RiskClass | None:
        tool = self.get(name)
        return tool.risk_class if tool else None


def tool_spec_from_legacy(row: dict[str, Any]) -> ToolSpec:
    return ToolSpec(
        name=str(row["tool"]),
        risk_class=row.get("riskClass", "safe_read"),
        description=str(row.get("description") or ""),
        mutates_state=bool(row.get("mutatesState")),
        external_system=bool(row.get("externalSystem")),
        executor=str(row.get("executor") or "host"),
        default_timeout_ms=int(row.get("defaultTimeoutMs") or 10_000),
        output_limit_bytes=int(row.get("outputLimitBytes") or 60_000),
        supports_checkpoint=bool(row.get("supportsCheckpoint")),
        can_use_credentials=bool(row.get("canUseCredentials")),
        rollback_capable=bool(row.get("rollbackCapable")),
        input_schema=dict(row.get("inputSchema") or {}),
        output_schema=dict(row.get("outputSchema") or {}),
        redaction_rules=list(row.get("redactionRules") or []),
    )
