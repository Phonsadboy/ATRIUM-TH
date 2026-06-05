"""Persisted runtime-agent checkpoints for regression/rollback evidence."""
from __future__ import annotations

from typing import Any

from ..clock import now_ms
from ..ids import uid
from .factory import get_agent_runtime
from .provisioning import runtime_agent_key


def _activity(text: str, *, department_id: str | None = None, severity: str = "good") -> dict[str, Any]:
    return {
        "id": uid("ev"),
        "ts": now_ms(),
        "type": "system",
        "departmentId": department_id,
        "text": text,
        "severity": severity,
    }


async def create_runtime_checkpoint(
    repo: Any,
    dept: dict[str, Any],
    *,
    reason: str,
    actor: str = "executive",
) -> dict[str, Any]:
    """Create an ATRIUM-owned checkpoint record for a runtime agent.

    ATRIUM is the runtime system of record. This stores a durable checkpoint
    marker for regression/rollback evidence without calling an external agent
    runtime service.
    """
    runtime = get_agent_runtime()
    runtime_meta = dept.get("runtime") if isinstance(dept.get("runtime"), dict) else {}
    agent_key = str((runtime_meta or {}).get("agentKey") or runtime_agent_key(dept))
    if not agent_key:
        raise ValueError("department has no runtime agent key")
    checkpoint_result = await runtime.checkpoint(agent_key, reason=reason)
    checkpoint = {
        "id": uid("rtchk"),
        "type": "runtime_checkpoint",
        "departmentId": dept.get("id"),
        "actor": actor,
        "reason": reason,
        "backend": checkpoint_result.get("backend") or getattr(runtime, "backend", None),
        "agentKey": agent_key,
        "runtimeAgentId": checkpoint_result.get("runtimeAgentId") or runtime_meta.get("runtimeAgentId"),
        "status": "created" if checkpoint_result.get("ok") else "error",
        "snapshotAvailable": bool(checkpoint_result.get("snapshotAvailable")),
        "runtimeResult": checkpoint_result,
        "rollbackPlan": {
            "mode": "atrium_native_checkpoint",
            "note": "Use this record as the pre-change native runtime evidence when evaluating rollback/regression.",
        },
        "ts": now_ms(),
    }
    await repo.put_entity(
        "runtime_checkpoint",
        checkpoint,
        dept=dept.get("id"),
        status=checkpoint["status"],
        ts=checkpoint["ts"],
    )
    await repo.add_activity(_activity(
        f"runtime checkpoint {checkpoint['id']} for {dept.get('id')} — {reason}",
        department_id=dept.get("id"),
        severity="good" if checkpoint["status"] == "created" else "warn",
    ))
    return checkpoint
