"""Org-level checkpoints for structural changes (spawn/merge/retire)."""
from __future__ import annotations

import copy
from typing import Any

from ..clock import now_ms
from ..ids import uid
from ..threads import EXEC_ID
from .capabilities import deactivate_department_capabilities, sync_department_capabilities


def _clone(value: Any) -> Any:
    return copy.deepcopy(value)


def _restore_department_spec(spec: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    dept_id = spec.get("id")
    now = now_ms()
    dept = _clone(existing or {})
    dept.update(_clone(spec))
    dept.setdefault("id", dept_id)
    dept.setdefault("name", dept_id or "Restored Department")
    dept.setdefault("role", "Restored department")
    dept.setdefault("charter", dept.get("role") or "Restored department")
    dept.setdefault("emoji", "R")
    dept.setdefault("accent", "teal")
    dept.setdefault("providerId", "claude_code")
    dept.setdefault("model", "claude-sonnet-4-6")
    dept.setdefault("thinkingEffort", "high")
    dept.setdefault("speed", "standard")
    dept.setdefault("agentName", f"{dept.get('name') or dept_id} Agent"[:120])
    dept.setdefault("state", "idle")
    dept.setdefault("mood", 0.75)
    dept.setdefault("currentTaskId", None)
    dept.setdefault("autonomy", dept.get("id") == EXEC_ID)
    dept.setdefault("createdAt", now)
    dept.setdefault("room", {"x": 1, "y": 2, "w": 3, "h": 3})
    dept.setdefault(
        "memory",
        {
            "archiveChunks": 0,
            "ragEntries": 0,
            "graphNodes": 0,
            "graphEdges": 0,
            "lastCompactionAt": None,
            "tokensSaved": 0,
        },
    )
    dept.setdefault("skills", [])
    dept.setdefault("tools", [])
    return dept


def rollback_endpoint(checkpoint_id: str) -> str:
    return f"/api/org/checkpoints/{checkpoint_id}/rollback"


def _checkpoint_department_ids(metadata: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("departmentId", "sourceId", "targetId"):
        value = metadata.get(key)
        if value:
            ids.add(str(value))
    for key in ("departmentIds", "affectedDepartmentIds"):
        values = metadata.get(key)
        if isinstance(values, list):
            ids.update(str(value) for value in values if value)
    return ids


def _checkpoint_task_ids(metadata: dict[str, Any]) -> set[str]:
    values = metadata.get("taskIds")
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values if value}


async def create_org_checkpoint(
    repo: Any,
    *,
    reason: str,
    actor: str = "executive",
    action: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = metadata or {}
    departments = await repo.list_departments()
    tasks = await repo.list_tasks(limit=1000, newest_first=True)
    affected_department_ids = _checkpoint_department_ids(metadata)
    affected_task_ids = _checkpoint_task_ids(metadata)
    if affected_department_ids:
        snapshot = [_clone(d) for d in departments if str(d.get("id") or "") in affected_department_ids]
    elif metadata:
        snapshot = []
    else:
        snapshot = [_clone(d) for d in departments]
    task_snapshot = [
        _clone(t)
        for t in tasks
        if str(t.get("id") or "") in affected_task_ids
        or (affected_department_ids and str(t.get("departmentId") or "") in affected_department_ids)
    ]
    before_department_ids = [str(d.get("id")) for d in departments if d.get("id")]
    checkpoint = {
        "id": uid("orgchk"),
        "reason": reason,
        "action": action,
        "actor": actor,
        "departmentCount": len(snapshot),
        "departments": snapshot,
        "taskCount": len(task_snapshot),
        "tasks": task_snapshot,
        "beforeDepartmentIds": before_department_ids,
        "rollbackPlan": {
            "mode": "restore_org_snapshot",
            "restores": ["departments", "department_fields", "task_assignments", "task_status"],
            "removes": ["departments_created_by_this_checkpoint"],
            "endpoint": "",
        },
        "metadata": metadata,
        "ts": now_ms(),
        "status": "active",
    }
    checkpoint["rollbackPlan"]["endpoint"] = rollback_endpoint(checkpoint["id"])
    await repo.put_entity("org_checkpoint", checkpoint, status=action, ts=checkpoint["ts"])
    return checkpoint


async def mark_org_checkpoint_applied(
    repo: Any,
    checkpoint_id: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checkpoint = await repo.get_entity("org_checkpoint", checkpoint_id)
    if not checkpoint:
        raise ValueError(f"org checkpoint not found: {checkpoint_id}")
    now = now_ms()
    checkpoint["status"] = "applied"
    checkpoint["appliedAt"] = now
    checkpoint["afterDepartmentIds"] = [
        str(dept.get("id")) for dept in await repo.list_departments() if dept.get("id")
    ]
    if metadata:
        checkpoint["metadata"] = {**(checkpoint.get("metadata") or {}), **metadata}
    plan = dict(checkpoint.get("rollbackPlan") or {})
    plan["endpoint"] = plan.get("endpoint") or rollback_endpoint(checkpoint_id)
    checkpoint["rollbackPlan"] = plan
    await repo.put_entity("org_checkpoint", checkpoint, status="applied", ts=now)
    return checkpoint


async def rollback_org_checkpoint(repo: Any, checkpoint_id: str) -> dict[str, Any]:
    checkpoint = await repo.get_entity("org_checkpoint", checkpoint_id)
    if not checkpoint:
        raise ValueError(f"org checkpoint not found: {checkpoint_id}")
    metadata = checkpoint.get("metadata") or {}
    raw_before_ids = checkpoint.get("beforeDepartmentIds")
    if isinstance(raw_before_ids, list):
        before_ids = {str(dept_id) for dept_id in raw_before_ids if dept_id}
    else:
        before_ids = {
            str(dept.get("id"))
            for dept in checkpoint.get("departments") or []
            if isinstance(dept, dict) and dept.get("id")
        }
    restored: list[str] = []
    removed: list[str] = []
    for current in await repo.list_departments():
        dept_id = str(current.get("id") or "")
        lifecycle = current.get("lifecycle") if isinstance(current.get("lifecycle"), dict) else {}
        created_by_checkpoint = lifecycle.get("checkpointId") == checkpoint_id
        listed_as_spawned = dept_id in set(metadata.get("spawnedDepartmentIds") or [])
        if dept_id and dept_id not in before_ids and (created_by_checkpoint or listed_as_spawned):
            await deactivate_department_capabilities(repo, dept_id, reason=f"rollback {checkpoint_id}", actor="rollback")
            await repo.delete_department(dept_id)
            removed.append(dept_id)

    for spec in checkpoint.get("departments") or []:
        if not isinstance(spec, dict):
            continue
        dept_id = spec.get("id")
        if not dept_id:
            continue
        existing = await repo.get_department(dept_id)
        restored_dept = _restore_department_spec(spec, existing)
        await repo.save_department(restored_dept)
        await sync_department_capabilities(repo, restored_dept, source="org_checkpoint_rollback")
        restored.append(dept_id)

    restored_tasks: list[str] = []
    for task in checkpoint.get("tasks") or []:
        if not isinstance(task, dict) or not task.get("id"):
            continue
        if await repo.get_task(str(task["id"])):
            await repo.save_task(_clone(task))
            restored_tasks.append(str(task["id"]))

    now = now_ms()
    checkpoint["status"] = "rolled_back"
    checkpoint["restoredDepartmentIds"] = restored
    checkpoint["removedDepartmentIds"] = removed
    checkpoint["restoredTaskIds"] = restored_tasks
    checkpoint["rolledBackAt"] = now
    await repo.put_entity("org_checkpoint", checkpoint, status="rolled_back", ts=now)
    await repo.add_activity(
        {
            "id": uid("ev"),
            "ts": now,
            "type": "system",
            "departmentId": None,
            "text": (
                f"org checkpoint rollback {checkpoint_id}: "
                f"restored departments={len(restored)}, tasks={len(restored_tasks)}, removed={len(removed)}"
            ),
            "severity": "warn",
        }
    )
    return {"checkpointId": checkpoint_id, "restored": restored, "removed": removed, "restoredTasks": restored_tasks}
