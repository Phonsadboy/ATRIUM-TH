"""Native runtime provisioning compatibility.

ATRIUM no longer provisions external runtime agents. Departments are native
records in ATRIUM's database; provider turns, memory, and jobs use app-owned
state directly.
"""
from __future__ import annotations

from typing import Any

from ..clock import now_ms
from ..config import Settings, get_settings
from ..threads import EXEC_ID


def runtime_agent_key(dept: dict[str, Any]) -> str:
    return "executive" if dept.get("id") == EXEC_ID else str(dept.get("id") or "").strip()


async def ensure_department_runtime_agent(
    repo: Any,
    dept: dict[str, Any],
    *,
    runtime: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    del repo, runtime
    settings = settings or get_settings()
    return {
        "backend": "native",
        "agentKey": runtime_agent_key(dept),
        "departmentId": dept.get("id"),
        "status": "ready",
        "runtimeAgentId": None,
        "externalRuntime": False,
        "provisioningRequired": False,
        "model": dept.get("model"),
        "checkedAt": now_ms(),
        "configuredBackend": settings.agent_backend_mode,
    }


async def ensure_department_runtime_agent_safely(
    repo: Any,
    dept: dict[str, Any],
    *,
    runtime: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    return await ensure_department_runtime_agent(repo, dept, runtime=runtime, settings=settings)


async def ensure_all_runtime_agents(
    repo: Any,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    departments = await repo.list_departments()
    return {
        "ok": True,
        "backend": "native",
        "configuredBackend": settings.agent_backend_mode,
        "enabled": True,
        "externalRuntime": False,
        "provisioningRequired": False,
        "departmentCount": len(departments),
        "readyCount": len(departments),
        "created": [],
        "errors": [],
    }


def runtime_agent_provisioning_status(departments: list[dict[str, Any]]) -> dict[str, Any]:
    agents = [
        {
            "departmentId": str(dept.get("id") or ""),
            "agentKey": runtime_agent_key(dept),
            "backend": "native",
            "status": "ready",
            "runtimeAgentId": None,
            "model": dept.get("model"),
            "externalRuntime": False,
            "provisioningRequired": False,
        }
        for dept in departments
    ]
    return {
        "departmentCount": len(departments),
        "readyCount": len(departments),
        "missing": [],
        "errors": [],
        "externalRuntime": False,
        "provisioningRequired": False,
        "agents": agents,
    }
