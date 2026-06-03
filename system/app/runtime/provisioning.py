"""Runtime agent provisioning for ATRIUM departments."""
from __future__ import annotations

from typing import Any

from ..catalog import DEFAULT_MODEL
from ..clock import now_ms
from ..config import Settings, get_settings
from ..ids import uid
from ..memory.company_memory import core_memory_blocks, ensure_company_memory_files
from ..threads import EXEC_ID
from .base import AgentRuntimeConfig
from .factory import get_agent_runtime
from .letta_adapter import LettaRuntimeAdapter


def runtime_agent_key(dept: dict[str, Any]) -> str:
    return "executive" if dept.get("id") == EXEC_ID else str(dept.get("id") or "").strip()


def department_runtime_persona(dept: dict[str, Any]) -> str:
    skills = ", ".join(str(item) for item in (dept.get("skills") or [])[:12]) or "none"
    tools = ", ".join(str(item) for item in (dept.get("tools") or [])[:12]) or "none"
    return (
        f"Department: {dept.get('name')}\n"
        f"Role: {dept.get('role')}\n"
        f"Charter: {dept.get('charter')}\n"
        f"Skills: {skills}\n"
        f"Tools: {tools}\n"
        "Operating mode: Full Auto. Use ATRIUM tasks, tools, audit, checkpoints, "
        "and rollback metadata instead of approval gates."
    ).strip()


def _activity(text: str, *, department_id: str | None = None, severity: str = "good") -> dict[str, Any]:
    return {
        "id": uid("act"),
        "ts": now_ms(),
        "type": "system",
        "departmentId": department_id,
        "text": text,
        "severity": severity,
    }


def _ready_runtime(meta: Any) -> bool:
    return isinstance(meta, dict) and meta.get("backend") == "letta" and bool(meta.get("lettaAgentId"))


def _live_model_handle(agent: dict[str, Any] | None) -> str:
    if not isinstance(agent, dict):
        return ""
    handle = agent.get("model")
    if handle:
        return str(handle)
    llm_config = agent.get("llm_config") if isinstance(agent.get("llm_config"), dict) else {}
    return str(llm_config.get("handle") or "")


def _model_handle_compatible(actual: str, expected: str) -> bool:
    if not actual or not expected:
        return False
    if actual == expected:
        return True
    if expected == "anthropic/claude-*" and actual.startswith("anthropic/claude-"):
        return True
    return False


async def ensure_department_runtime_agent(
    repo: Any,
    dept: dict[str, Any],
    *,
    runtime: LettaRuntimeAdapter | None = None,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    settings = settings or get_settings()
    if not settings.use_letta_runtime:
        return None
    runtime = runtime or get_agent_runtime(settings)
    if not isinstance(runtime, LettaRuntimeAdapter):
        return None
    agent_key = runtime_agent_key(dept)
    if not agent_key:
        return None
    runtime.bind_department(agent_key, dept)
    existing = dept.get("runtime") if isinstance(dept.get("runtime"), dict) else {}
    expected_model = str(dept.get("model") or DEFAULT_MODEL)
    expected_model_handle = runtime.model_handle(expected_model)
    if _ready_runtime(existing):
        runtime._agent_ids[agent_key] = str(existing["lettaAgentId"])
        live_handle = str(existing.get("modelHandle") or "")
        with_model_patch = False
        if not _model_handle_compatible(live_handle, expected_model_handle):
            live_handle = _live_model_handle(await runtime.get_agent(agent_key))
        if live_handle and not _model_handle_compatible(live_handle, expected_model_handle):
            updated = await runtime.update_agent_model(agent_key, expected_model)
            live_handle = str(updated.get("modelHandle") or expected_model_handle)
            with_model_patch = True
        if (
            existing.get("agentKey") != agent_key
            or existing.get("status") != "ready"
            or existing.get("model") != expected_model
            or not _model_handle_compatible(str(existing.get("modelHandle") or live_handle), expected_model_handle)
            or with_model_patch
        ):
            existing = {
                **existing,
                "agentKey": agent_key,
                "status": "ready",
                "model": expected_model,
                "modelHandle": live_handle or expected_model_handle,
                "checkedAt": now_ms(),
            }
            await repo.save_department({**dept, "runtime": existing})
        return existing

    ensure_company_memory_files(settings)
    blocks = core_memory_blocks(settings)
    config = AgentRuntimeConfig(
        agent_key=agent_key,
        model=str(dept.get("model") or DEFAULT_MODEL),
        persona=department_runtime_persona(dept),
        owner_profile=blocks.get("human", ""),
        company_memory=blocks.get("company", ""),
        metadata={
            "departmentId": dept.get("id"),
            "departmentName": dept.get("name"),
            "role": dept.get("role"),
        },
    )
    created = await runtime.create_agent(config)
    meta = {
        "backend": "letta",
        "lettaAgentId": created.get("runtimeAgentId"),
        "agentKey": agent_key,
        "departmentId": dept.get("id"),
        "status": "ready",
        "model": expected_model,
        "modelHandle": created.get("modelHandle") or expected_model_handle,
        "provisionedAt": now_ms(),
    }
    await repo.save_department({**dept, "runtime": meta})
    await repo.add_activity(_activity(
        f"provisioned Letta runtime agent for {dept.get('id')}",
        department_id=dept.get("id"),
        severity="good",
    ))
    return meta


async def ensure_department_runtime_agent_safely(
    repo: Any,
    dept: dict[str, Any],
    *,
    runtime: LettaRuntimeAdapter | None = None,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    try:
        return await ensure_department_runtime_agent(repo, dept, runtime=runtime, settings=settings)
    except Exception as exc:
        dept_id = str(dept.get("id") or "")
        error_meta = {
            "backend": "letta",
            "agentKey": runtime_agent_key(dept),
            "departmentId": dept_id,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "checkedAt": now_ms(),
        }
        await repo.save_department({**dept, "runtime": error_meta})
        await repo.add_activity(_activity(
            f"runtime agent provisioning failed for {dept_id}: {error_meta['error']}",
            department_id=dept_id or None,
            severity="warn",
        ))
        return error_meta


async def ensure_all_runtime_agents(
    repo: Any,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    if not settings.use_letta_runtime:
        return {"ok": True, "backend": settings.agent_backend, "enabled": False}
    runtime = get_agent_runtime(settings)
    if not isinstance(runtime, LettaRuntimeAdapter):
        return {"ok": False, "backend": settings.agent_backend, "enabled": True, "error": "runtime is not Letta"}
    health = await runtime.health()
    if not health.get("ok"):
        return {"ok": False, "backend": "letta", "enabled": True, "health": health}
    departments = await repo.list_departments()
    created: list[str] = []
    ready: list[str] = []
    errors: list[dict[str, str]] = []
    for dept in departments:
        dept_id = str(dept.get("id") or "")
        before = dept.get("runtime") if isinstance(dept.get("runtime"), dict) else {}
        meta = await ensure_department_runtime_agent_safely(repo, dept, runtime=runtime, settings=settings)
        if meta and meta.get("lettaAgentId"):
            ready.append(dept_id)
            if not _ready_runtime(before):
                created.append(dept_id)
        elif meta and meta.get("status") == "error":
            errors.append({"departmentId": dept_id, "error": str(meta.get("error") or "")})
    if created or errors:
        await repo.add_activity(_activity(
            f"runtime agent provisioning ready={len(ready)}/{len(departments)} created={len(created)} errors={len(errors)}",
            severity="good" if not errors else "warn",
        ))
    return {
        "ok": not errors,
        "backend": "letta",
        "enabled": True,
        "departmentCount": len(departments),
        "readyCount": len(ready),
        "created": created,
        "errors": errors,
    }


def runtime_agent_provisioning_status(departments: list[dict[str, Any]]) -> dict[str, Any]:
    agents: list[dict[str, Any]] = []
    missing: list[str] = []
    errors: list[dict[str, str]] = []
    for dept in departments:
        dept_id = str(dept.get("id") or "")
        meta = dept.get("runtime") if isinstance(dept.get("runtime"), dict) else None
        if not meta:
            missing.append(dept_id)
            continue
        status = str(meta.get("status") or ("ready" if meta.get("lettaAgentId") else "missing"))
        row = {
            "departmentId": dept_id,
            "agentKey": meta.get("agentKey"),
            "backend": meta.get("backend"),
            "status": status,
            "runtimeAgentId": meta.get("lettaAgentId"),
            "model": meta.get("model"),
        }
        agents.append(row)
        if status == "error":
            errors.append({"departmentId": dept_id, "error": str(meta.get("error") or "")})
        elif not meta.get("lettaAgentId"):
            missing.append(dept_id)
    ready = [item for item in agents if item.get("backend") == "letta" and item.get("runtimeAgentId") and item.get("status") == "ready"]
    return {
        "departmentCount": len(departments),
        "readyCount": len(ready),
        "missing": missing,
        "errors": errors,
        "agents": agents,
    }
