"""Dynamic task router — capability-based handoffs (replaces hardcoded routes)."""
from __future__ import annotations

from typing import Any

from ..config import get_settings
from ..eval.scoring import department_eval_routing_weight
from ..threads import is_exec
from .capabilities import build_capability_registry

# Compatibility fallback when dynamic routing is disabled.
LEGACY_HANDOFF_ROUTES: dict[str, dict[str, str]] = {
    "research": {"to": "strategy", "verb": "ส่งผลวิจัยให้สรุปเชิงกลยุทธ์"},
    "strategy": {"to": "design", "verb": "ส่งแผนให้ออกแบบทำต่อ"},
    "design": {"to": "engineering", "verb": "ส่งดีไซน์ให้พัฒนา"},
    "engineering": {"to": "qa", "verb": "ส่งงานให้ตรวจคุณภาพ"},
    "content": {"to": "qa", "verb": "ส่งคอนเทนต์ให้ตรวจ"},
}


async def route_task_to_department(
    repo: Any,
    *,
    title: str,
    detail: str = "",
    exclude: set[str] | None = None,
) -> tuple[dict[str, Any] | None, float, str]:
    """Pick best department for a new/backlog task."""
    settings = get_settings()
    registry = await build_capability_registry(repo)
    text = f"{title}\n{detail}".strip()
    ranked = registry.rank_for_text(text, limit=8)
    departments = {d["id"]: d for d in await repo.list_departments()}
    exclude = exclude or set()
    adjusted: list[tuple[str, float, float, float, dict[str, Any]]] = []
    for dept_id, capability_score in ranked:
        weight, summary = await department_eval_routing_weight(repo, dept_id)
        adjusted.append((dept_id, capability_score * weight, capability_score, weight, summary))
    adjusted.sort(key=lambda item: item[1], reverse=True)
    for dept_id, score, capability_score, weight, summary in adjusted:
        if dept_id in exclude or is_exec(dept_id):
            continue
        dept = departments.get(dept_id)
        if dept and score >= settings.dynamic_route_min_score:
            return (
                dept,
                score,
                (
                    f"capability match score={capability_score:.2f}, "
                    f"eval weight={weight:.2f}, adjusted={score:.2f}, "
                    f"eval count={summary.get('count', 0)}"
                ),
            )
    return None, 0.0, "no capability match"


async def resolve_handoff_target(
    repo: Any,
    from_dept: dict[str, Any],
    task: dict[str, Any],
    departments: list[dict[str, Any]],
    *,
    review_recommendation: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str, str]:
    """Resolve next department after task completion."""
    settings = get_settings()
    dept_map = {d["id"]: d for d in departments}

    if isinstance(review_recommendation, dict):
        rec_target = review_recommendation.get("toDept")
        target = dept_map.get(str(rec_target)) if rec_target else None
        if target and not is_exec(target["id"]):
            reason = str(review_recommendation.get("reason") or "LLM handoff recommendation")[:800]
            kind = str(review_recommendation.get("kind") or "delegate")
            return target, reason, kind

    if settings.dynamic_routing_enabled:
        deliverable = str(task.get("draftDeliverableMarkdown") or task.get("detail") or task.get("title") or "")
        registry = await build_capability_registry(repo)
        ranked = registry.rank_for_text(deliverable, limit=6)
        adjusted: list[tuple[str, float, float, float, dict[str, Any]]] = []
        for dept_id, capability_score in ranked:
            weight, summary = await department_eval_routing_weight(repo, dept_id)
            adjusted.append((dept_id, capability_score * weight, capability_score, weight, summary))
        adjusted.sort(key=lambda item: item[1], reverse=True)
        for dept_id, score, capability_score, weight, summary in adjusted:
            if dept_id == from_dept["id"] or is_exec(dept_id):
                continue
            if score >= settings.dynamic_route_min_score:
                target = dept_map.get(dept_id)
                if target:
                    return (
                        target,
                        (
                            f"dynamic route ({target['name']}, capability={capability_score:.2f}, "
                            f"eval weight={weight:.2f}, adjusted={score:.2f}, "
                            f"eval count={summary.get('count', 0)})"
                        ),
                        "delegate",
                    )
        return None, "", "delegate"

    legacy = LEGACY_HANDOFF_ROUTES.get(from_dept["id"])
    if legacy:
        target = dept_map.get(legacy["to"])
        if target:
            return target, legacy["verb"], "delegate"

    return None, "", "delegate"
