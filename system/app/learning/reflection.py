"""Reflection worker — generate lessons from actual trajectories (Reflexion-style)."""
from __future__ import annotations

import json
import re
from typing import Any

from ..clock import now_ms
from ..config import get_settings
from ..ids import uid
from ..memory.embeddings import embedding_metadata, resolve_embedder
from ..provider.base import LLMMessage
from ..provider.registry import get_provider
from ..schema import Playbook, Preference

_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _activity(
    text: str,
    *,
    type_: str = "system",
    department_id: str | None = None,
    severity: str = "info",
    ts: int | None = None,
) -> dict[str, Any]:
    return {
        "id": uid("ev"),
        "ts": ts or now_ms(),
        "type": type_,
        "departmentId": department_id,
        "text": text,
        "severity": severity,
    }


def format_trajectory(
    *,
    what_went_wrong: str,
    task: dict[str, Any] | None = None,
    artifact: dict[str, Any] | None = None,
    extra_lines: list[str] | None = None,
) -> str:
    parts = [f"What went wrong: {what_went_wrong}"]
    if task:
        parts.append(f"Task: {task.get('title')} ({task.get('status')})")
        log = task.get("log") or []
        if log:
            parts.append("Task log:\n" + "\n".join(f"- {line}" for line in log[-12:]))
        if task.get("detail"):
            parts.append(f"Detail: {_clip(task.get('detail'), 800)}")
    if artifact:
        parts.append(
            f"Artifact: {artifact.get('name')} status={artifact.get('status')} "
            f"version={artifact.get('version')}"
        )
    if extra_lines:
        parts.extend(extra_lines)
    return "\n".join(parts)


def _reflection_lesson_from_text(text: str) -> str | None:
    match = _JSON_BLOCK.search(text or "")
    if not match:
        return None
    data = json.loads(match.group())
    lesson = _clip(data.get("lesson"), 4000)
    next_time = _clip(data.get("nextTime"), 800)
    if lesson and next_time:
        return f"{lesson} ครั้งหน้า: {next_time}"
    return lesson or None


async def _generate_reflection_lesson_via_runtime(
    dept: dict[str, Any],
    *,
    repo: Any | None,
    settings: Any,
    source: str,
    system: str,
    user: str,
) -> str | None:
    if not settings.use_letta_runtime:
        return None
    try:
        from ..runtime.provisioning import ensure_department_runtime_agent_safely
        from ..runtime.turns import complete_agent_via_runtime

        active = dept
        if repo is not None:
            meta = await ensure_department_runtime_agent_safely(repo, dept, settings=settings)
            if meta and meta.get("lettaAgentId"):
                active = {**dept, "runtime": meta}
            session = getattr(repo, "s", None)
            if session is not None:
                await session.commit()

        result = await complete_agent_via_runtime(
            active,
            thread_id=f"reflection:{dept.get('id') or 'exec'}:{source}",
            system_prompt=system,
            messages=[LLMMessage(role="user", content=user)],
            metadata={"category": "reflection", "source": source},
            settings=settings,
        )
        if result is None:
            return None
        return _reflection_lesson_from_text(result.text)
    except Exception:
        return None


async def generate_reflection_lesson(
    dept: dict[str, Any],
    *,
    source: str,
    what_went_wrong: str,
    trajectory: str,
    fallback_lesson: str,
    repo: Any | None = None,
) -> str:
    settings = get_settings()
    if not settings.reflection_enabled:
        return fallback_lesson
    system = (
        "You extract one durable, actionable lesson from a work trajectory. "
        "Respond with JSON only: {\"lesson\": string, \"rootCause\": string, \"nextTime\": string}. "
        "Write lesson and nextTime in Thai. Be specific to the trajectory; do not invent facts."
    )
    user = (
        f"source={source}\n"
        f"department={dept.get('name', dept.get('id'))}\n"
        f"trajectory:\n{_clip(trajectory, 12000)}"
    )
    runtime_lesson = await _generate_reflection_lesson_via_runtime(
        dept,
        repo=repo,
        settings=settings,
        source=source,
        system=system,
        user=user,
    )
    if runtime_lesson:
        return runtime_lesson

    provider = get_provider(dept.get("providerId", "claude_code"), settings)
    if not getattr(provider, "live", False):
        return fallback_lesson
    try:
        result = await provider.complete(
            system=system,
            messages=[LLMMessage(role="user", content=user)],
            model=dept.get("model", "claude-sonnet-4-6"),
            effort="medium",
            speed=dept.get("speed", "standard"),
        )
        return _reflection_lesson_from_text(result.text) or fallback_lesson
    except Exception:
        return fallback_lesson


async def record_learning_signal(
    repo: Any,
    *,
    source: str,
    what_went_wrong: str,
    lesson_text: str,
    task_id: str | None = None,
    artifact_id: str | None = None,
    applied_to: list[str] | None = None,
    trajectory_digest: str | None = None,
) -> dict[str, Any]:
    now = now_ms()
    applied = applied_to or ["knowledge", "playbook", "preference"]
    lesson = {
        "id": uid("lesson"),
        "source": source,
        "taskId": task_id,
        "artifactId": artifact_id,
        "whatWentWrong": _clip(what_went_wrong, 2000),
        "lesson": _clip(lesson_text, 4000),
        "appliedTo": applied,
        "trajectoryDigest": trajectory_digest,
        "ts": now,
    }
    dept_id = None
    if task_id:
        task = await repo.get_task(task_id)
        dept_id = task.get("departmentId") if task else None
    if not dept_id and artifact_id:
        artifact = await repo.get_entity("artifact", artifact_id)
        dept_id = artifact.get("ownerDept") if artifact else None
    if dept_id:
        lesson["departmentId"] = dept_id
    await repo.put_entity("lesson", lesson, dept=dept_id, status=source, ts=now)
    if dept_id and "knowledge" in applied:
        kn = {
            "id": uid("kn"),
            "title": f"บทเรียนจาก {source}",
            "ts": now,
            "score": 0.82,
            "text": lesson["lesson"],
            "tags": ["lesson", source, "reflection"],
            "source": lesson["id"],
        }
        try:
            embedder = await resolve_embedder(get_settings())
            vecs = await embedder.embed([lesson["lesson"]])
            vector = vecs[0] if vecs else None
            await repo.add_knowledge(
                dept_id,
                kn,
                embedding=vector,
                source=f"lesson:{lesson['id']}",
                embedding_meta=embedding_metadata(embedder, vector),
            )
        except Exception:
            await repo.add_knowledge(dept_id, kn, embedding=None, source=f"lesson:{lesson['id']}")
        await repo.refresh_department_memory_stats(dept_id)
    if "preference" in applied:
        pref = Preference(
            id=uid("pref"),
            category="general",
            text=lesson["lesson"],
            confidence=0.7,
            source=lesson["id"],
            ts=now,
        ).dump()
        await repo.put_entity("preference", pref, status="general", ts=now)
    if "playbook" in applied:
        from .skills import promote_playbook_to_skill

        playbook = Playbook(
            id=uid("play"),
            name=f"เรียนรู้จาก {source}",
            when_to_use="ใช้เมื่อพบ feedback หรือการตีกลับลักษณะเดียวกัน",
            steps=["อ่าน trajectory จริง", "ปรับ acceptance criteria", "แนบหลักฐานก่อนส่งซ้ำ"],
            required_skills=[],
            deliverable_spec=lesson["lesson"],
            quality_checklist=["มีหลักฐาน", "มี preview", "ระบุ assumption"],
            examples=[artifact_id] if artifact_id else [],
            version=1,
        ).dump()
        await repo.put_entity("playbook", playbook, dept=dept_id, status=source, ts=now)
        await promote_playbook_to_skill(repo, playbook, dept_id=dept_id)
    await repo.add_activity(
        _activity(
            f"reflection: บันทึกบทเรียนจาก {source}",
            type_="system",
            department_id=dept_id,
            severity="good",
        )
    )
    settings = get_settings()
    if settings.use_letta_runtime and dept_id == "exec":
        try:
            from ..memory.company_memory import append_company_memory_entry, sync_company_memory_to_runtime

            append_company_memory_entry(
                lesson["lesson"][:2000],
                source=f"reflection:{source}",
                confidence=float(lesson.get("confidence") or 0.7),
                settings=settings,
            )
            await sync_company_memory_to_runtime(repo, settings=settings, labels=["company"])
        except Exception:
            pass
    return lesson


async def reflect_and_record(
    repo: Any,
    dept: dict[str, Any],
    *,
    source: str,
    what_went_wrong: str,
    fallback_lesson: str,
    task: dict[str, Any] | None = None,
    artifact: dict[str, Any] | None = None,
    extra_lines: list[str] | None = None,
    task_id: str | None = None,
    artifact_id: str | None = None,
    applied_to: list[str] | None = None,
) -> dict[str, Any]:
    trajectory = format_trajectory(
        what_went_wrong=what_went_wrong,
        task=task,
        artifact=artifact,
        extra_lines=extra_lines,
    )
    from ..memory.archive import transcript_digest

    digest = transcript_digest(trajectory)
    lesson_text = await generate_reflection_lesson(
        dept,
        source=source,
        what_went_wrong=what_went_wrong,
        trajectory=trajectory,
        fallback_lesson=fallback_lesson,
        repo=repo,
    )
    return await record_learning_signal(
        repo,
        source=source,
        what_went_wrong=what_went_wrong,
        lesson_text=lesson_text,
        task_id=task_id or (task.get("id") if task else None),
        artifact_id=artifact_id or (artifact.get("id") if artifact else None),
        applied_to=applied_to,
        trajectory_digest=digest,
    )


async def enqueue_reflection(
    repo: Any,
    *,
    department_id: str,
    source: str,
    what_went_wrong: str,
    fallback_lesson: str,
    task_id: str | None = None,
    artifact_id: str | None = None,
    applied_to: list[str] | None = None,
    run_after: int | None = None,
) -> str:
    job_id = uid("job")
    await repo.enqueue(
        job_id,
        "reflect",
        {
            "departmentId": department_id,
            "source": source,
            "whatWentWrong": what_went_wrong,
            "fallbackLesson": fallback_lesson,
            "taskId": task_id,
            "artifactId": artifact_id,
            "appliedTo": applied_to or ["knowledge", "playbook", "preference"],
        },
        run_after or now_ms(),
        priority=3,
    )
    return job_id


async def process_reflection_job(repo: Any, payload: dict[str, Any]) -> None:
    dept_id = str(payload.get("departmentId") or "exec")
    dept = await repo.get_department(dept_id)
    if not dept:
        return
    task = await repo.get_task(payload["taskId"]) if payload.get("taskId") else None
    artifact = await repo.get_entity("artifact", payload["artifactId"]) if payload.get("artifactId") else None
    await reflect_and_record(
        repo,
        dept,
        source=str(payload.get("source") or "reject"),
        what_went_wrong=str(payload.get("whatWentWrong") or ""),
        fallback_lesson=str(payload.get("fallbackLesson") or ""),
        task=task,
        artifact=artifact,
        task_id=payload.get("taskId"),
        artifact_id=payload.get("artifactId"),
        applied_to=list(payload.get("appliedTo") or []),
    )
