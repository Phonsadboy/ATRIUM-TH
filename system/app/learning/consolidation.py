"""Episodic → semantic → procedural consolidation (builds on compact_dept)."""
from __future__ import annotations

from typing import Any

from ..clock import now_ms
from ..config import get_settings
from ..ids import uid


async def enqueue_consolidation(
    repo: Any,
    *,
    run_after: int | None = None,
    reason: str = "scheduled",
    department_ids: list[str] | None = None,
    priority: int = 4,
    dedupe_key: str | None = None,
) -> str:
    settings = get_settings()
    if not settings.consolidation_enabled:
        return ""
    payload: dict[str, Any] = {"reason": reason}
    if department_ids:
        payload["departmentIds"] = list(dict.fromkeys(department_ids))
    job_id = uid("job")
    return await repo.enqueue_once(
        job_id,
        "consolidate_memory",
        payload,
        run_after or now_ms(),
        priority=priority,
        dedupe_key=dedupe_key or "scheduled:consolidate_memory",
    )


async def run_consolidation(
    repo: Any,
    now: int,
    *,
    department_ids: list[str] | None = None,
    reason: str = "scheduled",
) -> dict[str, Any]:
    """Roll up recent archives + lessons into procedural skill hints."""
    if department_ids:
        departments = [
            dept
            for dept_id in dict.fromkeys(department_ids)
            if (dept := await repo.get_department(dept_id))
        ]
    else:
        departments = await repo.list_departments()
    promoted = 0
    for dept in departments:
        dept_id = dept["id"]
        archives = await repo.list_archive(dept_id, limit=5)
        lessons = [
            row
            for row in await repo.list_entities("lesson", dept=dept_id, limit=20)
        ]
        if not archives and not lessons:
            continue
        summary_bits = []
        for archive in archives[:3]:
            summary_bits.append(str(archive.get("summary") or "")[:400])
        for lesson in lessons[:5]:
            summary_bits.append(str(lesson.get("lesson") or "")[:240])
        if not summary_bits:
            continue
        procedural = {
            "id": uid("proc"),
            "departmentId": dept_id,
            "title": f"Procedural rollup {dept.get('name', dept_id)}",
            "text": "\n".join(f"- {bit}" for bit in summary_bits if bit),
            "sourceArchiveIds": [a.get("id") for a in archives[:3]],
            "sourceLessonIds": [l.get("id") for l in lessons[:5]],
            "reason": reason,
            "ts": now,
        }
        await repo.put_entity("procedural_memory", procedural, dept=dept_id, ts=now)
        old = dept.get("memory") or {}
        dept["memory"] = {**old, "lastConsolidationAt": now, "proceduralRollups": int(old.get("proceduralRollups") or 0) + 1}
        await repo.save_department(dept)
        promoted += 1
    return {"departments": promoted, "ts": now, "reason": reason}


async def process_consolidation_job(repo: Any, now: int, payload: dict[str, Any] | None = None) -> None:
    payload = payload or {}
    department_ids = [
        str(item)
        for item in payload.get("departmentIds", [])
        if str(item).strip()
    ]
    reason = str(payload.get("reason") or "scheduled")
    await run_consolidation(repo, now, department_ids=department_ids or None, reason=reason)
    settings = get_settings()
    if settings.consolidation_enabled:
        delay_ms = int(settings.consolidation_interval_hours * 3600 * 1000)
        await enqueue_consolidation(repo, run_after=now + delay_ms)
