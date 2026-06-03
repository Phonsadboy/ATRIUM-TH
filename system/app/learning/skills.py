"""Skill library — versioned playbooks with success metrics (Voyager pattern)."""
from __future__ import annotations

import asyncio
from typing import Any

from ..clock import now_ms
from ..config import get_settings
from ..ids import uid
from ..memory.embeddings import resolve_embedder


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


async def promote_playbook_to_skill(
    repo: Any,
    playbook: dict[str, Any],
    *,
    dept_id: str | None = None,
) -> dict[str, Any]:
    """Create or bump a skill indexed from a playbook."""
    name = str(playbook.get("name") or "skill")
    description = _clip(
        f"{playbook.get('whenToUse', playbook.get('when_to_use', ''))} — {playbook.get('deliverableSpec', playbook.get('deliverable_spec', ''))}",
        2000,
    )
    existing = []
    for row in await repo.list_entities("skill", limit=500):
        if row.get("playbookId") == playbook.get("id"):
            existing.append(row)
            continue
        same_department = row.get("departmentId") == dept_id if dept_id else not row.get("departmentId")
        if same_department and row.get("name") == name:
            existing.append(row)
    now = now_ms()
    if existing:
        skill = dict(existing[0])
        skill["version"] = int(skill.get("version") or 1) + 1
        skill["description"] = description
        skill["departmentId"] = dept_id
        skill["updatedAt"] = now
    else:
        skill = {
            "id": uid("skill"),
            "name": name,
            "description": description,
            "tools": list(playbook.get("requiredSkills") or playbook.get("required_skills") or []),
            "playbookId": playbook.get("id"),
            "departmentId": dept_id,
            "version": 1,
            "successCount": 0,
            "failureCount": 0,
            "avgRevisions": 0.0,
            "createdAt": now,
            "updatedAt": now,
        }
    embedder = await resolve_embedder(get_settings())
    try:
        vecs = await embedder.embed([description])
        skill["embedding"] = vecs[0] if vecs else None
    except Exception:
        skill["embedding"] = None
    await repo.put_entity("skill", skill, dept=dept_id, ts=now)
    return skill


async def update_skill_metrics(
    repo: Any,
    skill_id: str,
    *,
    success: bool,
    revision_count: int = 0,
) -> None:
    skill = await repo.get_entity("skill", skill_id)
    if not skill:
        return
    if success:
        skill["successCount"] = int(skill.get("successCount") or 0) + 1
    else:
        skill["failureCount"] = int(skill.get("failureCount") or 0) + 1
    total = int(skill.get("successCount") or 0) + int(skill.get("failureCount") or 0)
    prev_avg = float(skill.get("avgRevisions") or 0)
    skill["avgRevisions"] = round((prev_avg * max(total - 1, 0) + revision_count) / max(total, 1), 2)
    skill["updatedAt"] = now_ms()
    await repo.put_entity("skill", skill, dept=skill.get("departmentId"), ts=skill["updatedAt"])


async def retrieve_relevant_skills(
    repo: Any,
    dept: dict[str, Any],
    task: dict[str, Any] | None,
    *,
    limit: int = 3,
) -> str:
    """Return formatted skill context to inject before similar tasks."""
    context, _skill_ids = await retrieve_relevant_skill_matches(repo, dept, task, limit=limit)
    return context


async def retrieve_relevant_skill_matches(
    repo: Any,
    dept: dict[str, Any],
    task: dict[str, Any] | None,
    *,
    limit: int = 3,
) -> tuple[str, list[str]]:
    """Return formatted skill context plus the skill ids used for metrics."""
    settings = get_settings()
    query = " ".join(
        part
        for part in [
            task.get("title") if task else "",
            task.get("detail") if task else "",
            dept.get("charter", ""),
        ]
        if part
    ).strip()
    if not query:
        return "", []
    skills = await repo.list_entities("skill", dept=dept.get("id"), limit=100)
    if not skills:
        skills = await repo.list_entities("skill", limit=100)
    if not skills:
        return "", []

    query_vec: list[float] = []
    try:
        embedder = await resolve_embedder(settings)
        query_vec = (await asyncio.wait_for(embedder.embed([query]), timeout=8.0))[0]
    except Exception:
        query_vec = []

    query_terms = [tok.lower() for tok in query.split()[:12] if tok.strip()]

    def _lexical_score(skill: dict[str, Any]) -> float:
        text = f"{skill.get('name') or ''} {skill.get('description') or ''}".lower()
        if not text or not query_terms:
            return 0.0
        hits = sum(1 for tok in query_terms if tok in text)
        return min(0.5, hits / max(len(query_terms), 1))

    def _score(skill: dict[str, Any]) -> float:
        emb = skill.get("embedding")
        if not query_vec or not isinstance(emb, list) or not emb:
            return _lexical_score(skill)
        dot = sum(float(a) * float(b) for a, b in zip(query_vec, emb[: len(query_vec)]))
        success = int(skill.get("successCount") or 0)
        failure = int(skill.get("failureCount") or 0)
        rate = success / max(success + failure, 1)
        return max(_lexical_score(skill), dot * (0.5 + 0.5 * rate))

    scored = [(_score(skill), skill) for skill in skills]
    ranked = [skill for score, skill in sorted(scored, key=lambda item: item[0], reverse=True)[:limit] if score > 0]
    if not ranked:
        return "", []
    lines = []
    skill_ids: list[str] = []
    for skill in ranked:
        if skill.get("id"):
            skill_ids.append(str(skill["id"]))
        success = int(skill.get("successCount") or 0)
        failure = int(skill.get("failureCount") or 0)
        lines.append(
            f"- {skill.get('name')} (v{skill.get('version', 1)}, success {success}/{success + failure}): "
            f"{_clip(skill.get('description'), 320)}"
        )
    return "Relevant skills/playbooks:\n" + "\n".join(lines), skill_ids
