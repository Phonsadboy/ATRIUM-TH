"""Department lifecycle — spawn / retire / merge (full-auto with checkpoint + audit)."""
from __future__ import annotations

from typing import Any

from ..clock import DAY_MS, now_ms
from ..config import get_settings
from ..ids import uid
from ..runtime.provisioning import ensure_department_runtime_agent_safely
from ..threads import EXEC_ID, is_exec
from .capabilities import build_capability_registry, deactivate_department_capabilities, sync_department_capabilities
from .checkpoints import create_org_checkpoint, mark_org_checkpoint_applied, rollback_endpoint
from .router import route_task_to_department

ACCENTS = ["amber", "teal", "coral", "lavender", "sky", "honey"]


async def enqueue_org_lifecycle(repo: Any, *, run_after: int | None = None) -> str:
    settings = get_settings()
    if not settings.org_lifecycle_enabled:
        return ""
    return await repo.enqueue_once(
        uid("job"),
        "org_lifecycle",
        {"reason": "scheduled"},
        run_after or now_ms(),
        priority=4,
        dedupe_key="scheduled:org_lifecycle",
    )


def _activity(text: str, *, department_id: str | None = None, severity: str = "info") -> dict[str, Any]:
    return {
        "id": uid("ev"),
        "ts": now_ms(),
        "type": "system",
        "departmentId": department_id,
        "text": text,
        "severity": severity,
    }


async def _require_org_mutation_allowed(repo: Any) -> None:
    return None


async def spawn_department(
    repo: Any,
    *,
    name: str,
    role: str,
    charter: str,
    reason: str,
    skills: list[str] | None = None,
    actor: str = "executive",
    related_task_ids: list[str] | None = None,
) -> dict[str, Any]:
    await _require_org_mutation_allowed(repo)
    settings = get_settings()
    checkpoint = await create_org_checkpoint(
        repo,
        reason=reason,
        actor=actor,
        action="spawn_department",
        metadata={"name": name, "taskIds": related_task_ids or []},
    )
    departments = await repo.list_departments()
    dept_id = uid("dept")
    dept = {
        "id": dept_id,
        "name": name[:120],
        "role": role[:240],
        "charter": charter[:2000],
        "emoji": "🟣",
        "accent": ACCENTS[len(departments) % len(ACCENTS)],
        "providerId": "claude_code",
        "model": "claude-sonnet-4-6",
        "thinkingEffort": "high",
        "speed": "standard",
        "agentName": f"{name} Agent"[:120],
        "state": "idle",
        "mood": 0.85,
        "currentTaskId": None,
        "autonomy": True,
        "createdAt": now_ms(),
        "room": {"x": 1 + (len(departments) % 6) * 2, "y": 2, "w": 3, "h": 3},
        "memory": {"archiveChunks": 0, "ragEntries": 0, "graphNodes": 0, "graphEdges": 0, "tokensSaved": 0},
        "skills": skills or [],
        "tools": [],
        "workspacePath": str(settings.workspace_dir / dept_id),
        "visibilityPolicy": {"dept": dept_id, "archive": "private", "knowledge": "on_request", "tasks": "company", "artifacts": "company"},
        "lifecycle": {
            "spawnReason": reason,
            "spawnedBy": actor,
            "checkpointId": checkpoint["id"],
            "rollbackEndpoint": rollback_endpoint(checkpoint["id"]),
        },
    }
    (settings.workspace_dir / dept_id).mkdir(parents=True, exist_ok=True)
    await repo.save_department(dept)
    await sync_department_capabilities(repo, dept, source="lifecycle_spawn")
    await ensure_department_runtime_agent_safely(repo, dept, settings=settings)
    dept = await repo.get_department(dept_id) or dept
    decision = {
        "id": uid("dec"),
        "title": f"spawn department {name}",
        "proposedBy": actor,
        "approvedBy": actor,
        "rationale": reason,
        "alternatives": [],
        "impact": f"department={dept_id}; checkpoint={checkpoint['id']}; rollback={rollback_endpoint(checkpoint['id'])}",
        "status": "approved",
        "ts": now_ms(),
    }
    await repo.put_entity("decision", decision, dept=dept_id, status="approved", ts=decision["ts"])
    await mark_org_checkpoint_applied(
        repo,
        checkpoint["id"],
        metadata={
            "spawnedDepartmentIds": [dept_id],
            "rollbackEndpoint": rollback_endpoint(checkpoint["id"]),
        },
    )
    await repo.add_activity(_activity(f"org lifecycle: spawn แผนก {name} — {reason}", department_id=dept_id, severity="good"))
    return dept


async def retire_department(
    repo: Any,
    dept_id: str,
    *,
    reason: str,
    actor: str = "executive",
) -> dict[str, Any]:
    await _require_org_mutation_allowed(repo)
    if is_exec(dept_id):
        raise ValueError("cannot retire executive")
    dept = await repo.get_department(dept_id)
    if not dept:
        raise ValueError("department not found")
    checkpoint = await create_org_checkpoint(
        repo,
        reason=reason,
        actor=actor,
        action="retire_department",
        metadata={"departmentId": dept_id},
    )
    for task in await repo.tasks_for_dept(dept_id):
        if task.get("status") in {"done", "cancelled"}:
            continue
        task["departmentId"] = None
        task["status"] = "backlog"
        task["updatedAt"] = now_ms()
        task.setdefault("log", []).append(f"department {dept_id} retired → backlog")
        await repo.save_task(task)
    await deactivate_department_capabilities(repo, dept_id, reason=reason, actor=actor)
    await repo.delete_department(dept_id)
    await mark_org_checkpoint_applied(
        repo,
        checkpoint["id"],
        metadata={
            "retiredDepartmentIds": [dept_id],
            "rollbackEndpoint": rollback_endpoint(checkpoint["id"]),
        },
    )
    await repo.add_activity(_activity(f"org lifecycle: retire แผนก {dept.get('name')} — {reason}", severity="warn"))
    return {
        "departmentId": dept_id,
        "status": "retired",
        "reason": reason,
        "checkpointId": checkpoint["id"],
        "rollbackEndpoint": rollback_endpoint(checkpoint["id"]),
    }


async def merge_departments(
    repo: Any,
    source_id: str,
    target_id: str,
    *,
    reason: str,
    actor: str = "executive",
) -> dict[str, Any]:
    await _require_org_mutation_allowed(repo)
    if source_id == target_id or is_exec(source_id) or is_exec(target_id):
        raise ValueError("invalid merge targets")
    source = await repo.get_department(source_id)
    target = await repo.get_department(target_id)
    if not source or not target:
        raise ValueError("department not found")
    checkpoint = await create_org_checkpoint(
        repo,
        reason=reason,
        actor=actor,
        action="merge_departments",
        metadata={"sourceId": source_id, "targetId": target_id},
    )
    moved = 0
    for task in await repo.tasks_for_dept(source_id):
        task["departmentId"] = target_id
        task["updatedAt"] = now_ms()
        task.setdefault("log", []).append(f"merged from {source_id} into {target_id}")
        await repo.save_task(task)
        moved += 1
    merged_skills = list(dict.fromkeys([*(target.get("skills") or []), *(source.get("skills") or [])]))
    target["skills"] = merged_skills
    target_lifecycle = target.setdefault("lifecycle", {})
    target_lifecycle["mergedFrom"] = source_id
    target_lifecycle["mergeCheckpointId"] = checkpoint["id"]
    target_lifecycle["rollbackEndpoint"] = rollback_endpoint(checkpoint["id"])
    await repo.save_department(target)
    await sync_department_capabilities(repo, target, source="lifecycle_merge")
    await deactivate_department_capabilities(repo, source_id, reason=reason, actor=actor)
    await repo.delete_department(source_id)
    await mark_org_checkpoint_applied(
        repo,
        checkpoint["id"],
        metadata={
            "sourceId": source_id,
            "targetId": target_id,
            "tasksMoved": moved,
            "rollbackEndpoint": rollback_endpoint(checkpoint["id"]),
        },
    )
    await repo.add_activity(
        _activity(
            f"org lifecycle: merge {source.get('name')} → {target.get('name')} ({moved} tasks) — {reason}",
            department_id=target_id,
            severity="good",
        )
    )
    return {
        "sourceId": source_id,
        "targetId": target_id,
        "tasksMoved": moved,
        "reason": reason,
        "checkpointId": checkpoint["id"],
        "rollbackEndpoint": rollback_endpoint(checkpoint["id"]),
    }


async def _department_idle_ms(dept: dict[str, Any], tasks: list[dict[str, Any]], now: int) -> int:
    active = [
        t for t in tasks
        if t.get("departmentId") == dept["id"] and t.get("status") in {"assigned", "in_progress", "review", "revising", "waiting"}
    ]
    if active or dept.get("state") not in {"idle", "handoff"}:
        return 0
    last = int(dept.get("createdAt") or 0)
    for task in tasks:
        if task.get("departmentId") == dept["id"]:
            last = max(last, int(task.get("updatedAt") or 0))
    return max(0, now - last)


async def process_org_lifecycle(repo: Any, now: int) -> dict[str, Any]:
    """Analyze backlog/skill-gap → spawn; idle/redundant → retire/merge."""
    settings = get_settings()
    if not settings.org_lifecycle_enabled:
        return {"skipped": True}
    company = await repo.get_company()
    if company and not company.running:
        return {"skipped": True, "reason": "global_kill_switch"}

    result: dict[str, Any] = {"spawned": [], "retired": [], "merged": [], "routed": []}
    departments = await repo.list_departments()
    tasks = await repo.list_active_tasks(limit=1000)

    # Route orphan backlog tasks
    for task in tasks:
        if task.get("status") != "backlog" or task.get("departmentId"):
            continue
        dept, score, note = await route_task_to_department(repo, title=task.get("title", ""), detail=task.get("detail", ""))
        if dept:
            task["departmentId"] = dept["id"]
            task["status"] = "assigned"
            task["updatedAt"] = now
            task.setdefault("log", []).append(f"dynamic router assigned ({note})")
            await repo.save_task(task)
            result["routed"].append({"taskId": task["id"], "departmentId": dept["id"], "score": score})

    # Spawn on skill gap
    if settings.org_autospawn_enabled:
        for task in tasks:
            if task.get("status") != "backlog" or task.get("departmentId"):
                continue
            dept, score, _ = await route_task_to_department(repo, title=task.get("title", ""), detail=task.get("detail", ""))
            if dept:
                continue
            title = str(task.get("title") or "งานใหม่")[:80]
            spawned = await spawn_department(
                repo,
                name=f"แผนก{title[:24]}",
                role="งานเฉพาะทาง",
                charter=f"รับผิดชอบงานประเภท: {title}",
                reason=f"skill-gap จาก backlog task {task['id']}",
                skills=[title],
                related_task_ids=[task["id"]],
            )
            task["departmentId"] = spawned["id"]
            task["status"] = "assigned"
            task["updatedAt"] = now
            task.setdefault("log", []).append("spawned department for skill gap")
            await repo.save_task(task)
            result["spawned"].append(spawned["id"])
            break  # one spawn per lifecycle pass

    registry = await build_capability_registry(repo)
    idle_threshold = int(settings.org_idle_retire_hours * 3600 * 1000)
    non_exec = [d for d in departments if not is_exec(d["id"])]

    # Retire idle departments
    for dept in non_exec:
        idle_ms = await _department_idle_ms(dept, tasks, now)
        if idle_ms >= idle_threshold and len(non_exec) > 2:
            await retire_department(repo, dept["id"], reason=f"idle {idle_ms // DAY_MS}d+ with no active work")
            result["retired"].append(dept["id"])
            non_exec = [d for d in non_exec if d["id"] != dept["id"]]
            break

    # Merge redundant pairs (similar capabilities, both idle)
    if settings.org_automerge_enabled and len(non_exec) >= 3:
        caps = registry.list()
        for i, a in enumerate(caps):
            for b in caps[i + 1 :]:
                overlap = len(a.keywords & b.keywords)
                if overlap < 3:
                    continue
                da = next((d for d in non_exec if d["id"] == a.department_id), None)
                db = next((d for d in non_exec if d["id"] == b.department_id), None)
                if not da or not db:
                    continue
                if await _department_idle_ms(da, tasks, now) < idle_threshold:
                    continue
                if await _department_idle_ms(db, tasks, now) < idle_threshold:
                    continue
                merged = await merge_departments(
                    repo,
                    b.department_id,
                    a.department_id,
                    reason=f"redundant capabilities overlap={overlap}",
                )
                result["merged"].append(merged)
                break
            if result["merged"]:
                break

    return result
