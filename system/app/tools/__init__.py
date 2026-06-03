from ..db.repo import TOOL_CATALOG
from .executor_router import ExecutorRouter
from .foundry import ToolDraft, ToolFoundry, custom_tool_catalog_row, execute_custom_tool, load_custom_tools
from .host_bridge import HostBridge, HostBridgeStatus
from .registry import ToolRegistry, tool_spec_from_legacy

__all__ = [
    "ExecutorRouter",
    "HostBridge",
    "HostBridgeStatus",
    "ToolDraft",
    "ToolFoundry",
    "custom_tool_catalog_row",
    "execute_custom_tool",
    "ToolRegistry",
    "load_custom_tools",
    "tool_spec_from_legacy",
    "build_default_tool_registry",
]


def build_default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for row in TOOL_CATALOG:
        registry.register(tool_spec_from_legacy(row))
    return registry
