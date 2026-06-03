"""Eval scoring loop — success-first-try, revisions, accept/reject."""
from __future__ import annotations

from typing import Any

from ..clock import now_ms
from ..ids import uid


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def department_quality_score(summary: dict[str, Any]) -> float:
    """Return a routing/self-improvement score from eval outcomes.

    Neutral departments with no outcomes stay at 1.0 so new teams are not
    punished before evidence exists. Rejections and repeated revisions lower
    the score; accepted first-try outcomes raise it.
    """
    count = int(summary.get("count") or 0)
    if count <= 0:
        return 1.0
    success_first_try_rate = float(summary.get("successFirstTryRate") or 0.0)
    accepted = int(summary.get("accepted") or 0)
    rejected = int(summary.get("rejected") or 0)
    judged = accepted + rejected
    accept_rate = (accepted / judged) if judged else 0.5
    avg_revisions = float(summary.get("avgRevisions") or 0.0)
    raw = 0.75 + (0.35 * success_first_try_rate) + (0.25 * accept_rate) - (0.08 * min(avg_revisions, 5.0))
    return round(_clamp(raw, 0.55, 1.35), 4)


def _is_regression_outcome(
    *,
    outcome: str,
    revision_count: int,
    accepted: bool | None,
) -> bool:
    lowered = outcome.strip().lower()
    if accepted is False:
        return True
    if lowered in {"revising", "rejected", "failed", "blocked", "cancelled", "error"}:
        return True
    return revision_count >= 3 and lowered != "done"


async def _load_checkpoint(repo: Any, checkpoint_id: str) -> tuple[str | None, dict[str, Any] | None]:
    for entity_type in ("org_checkpoint", "checkpoint", "runtime_checkpoint"):
        checkpoint = await repo.get_entity(entity_type, checkpoint_id)
        if checkpoint:
            return entity_type, checkpoint
    return None, None


async def _annotate_checkpoint_regression(
    repo: Any,
    *,
    checkpoint_type: str,
    checkpoint: dict[str, Any],
    event: dict[str, Any],
) -> dict[str, Any]:
    metadata = dict(checkpoint.get("metadata") or {})
    regressions = list(metadata.get("evalRegressions") or [])
    regressions.append(event)
    metadata["evalRegressions"] = regressions[-20:]
    metadata["lastEvalRegression"] = event
    metadata["rollbackRecommended"] = True
    if event.get("autoRollbackRequested"):
        metadata["autoRollbackRequested"] = True
    checkpoint["metadata"] = metadata
    plan = dict(checkpoint.get("rollbackPlan") or {})
    plan["evalRegressionAware"] = True
    if event.get("rollbackRecommended"):
        plan["recommendedByEval"] = True
    checkpoint["rollbackPlan"] = plan
    await repo.put_entity(
        checkpoint_type,
        checkpoint,
        dept=checkpoint.get("departmentId"),
        project=checkpoint.get("projectId"),
        status=checkpoint.get("status"),
        ts=now_ms(),
    )
    return checkpoint


async def _add_regression_activity(
    repo: Any,
    *,
    task_id: str,
    department_id: str,
    checkpoint_id: str,
    checkpoint_type: str | None,
    rollback_status: str,
) -> None:
    await repo.add_activity(
        {
            "id": uid("ev"),
            "ts": now_ms(),
            "type": "system",
            "departmentId": department_id,
            "text": (
                f"eval regression for task {task_id}: checkpoint={checkpoint_id} "
                f"type={checkpoint_type or 'missing'} rollback={rollback_status}"
            ),
            "severity": "warn",
        }
    )


async def record_task_outcome(
    repo: Any,
    *,
    task_id: str,
    department_id: str,
    outcome: str,
    revision_count: int = 0,
    accepted: bool | None = None,
    skill_ids: list[str] | None = None,
    detail: str = "",
    checkpoint_id: str | None = None,
    auto_rollback: bool = False,
) -> dict[str, Any]:
    now = now_ms()
    eval_id = uid("eval")
    regression_detected = _is_regression_outcome(
        outcome=outcome,
        revision_count=revision_count,
        accepted=accepted,
    )
    checkpoint_type: str | None = None
    rollback_recommended = bool(regression_detected and checkpoint_id)
    rollback_status = "not_applicable"
    rollback_result: dict[str, Any] | None = None
    rollback_error: str | None = None
    if regression_detected and checkpoint_id:
        checkpoint_type, checkpoint = await _load_checkpoint(repo, checkpoint_id)
        event = {
            "evalOutcomeId": eval_id,
            "taskId": task_id,
            "departmentId": department_id,
            "outcome": outcome,
            "revisionCount": revision_count,
            "accepted": accepted,
            "detail": detail[:1000],
            "autoRollbackRequested": auto_rollback,
            "rollbackRecommended": True,
            "ts": now,
        }
        if checkpoint_type and checkpoint:
            try:
                await _annotate_checkpoint_regression(
                    repo,
                    checkpoint_type=checkpoint_type,
                    checkpoint=checkpoint,
                    event=event,
                )
                rollback_status = "recommended"
                if auto_rollback:
                    if checkpoint.get("status") == "rolled_back":
                        rollback_status = "already_rolled_back"
                    elif checkpoint_type == "org_checkpoint":
                        from ..org.checkpoints import rollback_org_checkpoint

                        rollback_result = await rollback_org_checkpoint(repo, checkpoint_id)
                        rollback_status = "rolled_back"
                    else:
                        rollback_status = "unsupported_checkpoint_type"
            except Exception as exc:  # pragma: no cover - defensive audit path
                rollback_status = "error"
                rollback_error = str(exc)[:1000]
        else:
            rollback_status = "checkpoint_not_found"
        await _add_regression_activity(
            repo,
            task_id=task_id,
            department_id=department_id,
            checkpoint_id=checkpoint_id,
            checkpoint_type=checkpoint_type,
            rollback_status=rollback_status,
        )
    row = {
        "id": eval_id,
        "taskId": task_id,
        "departmentId": department_id,
        "outcome": outcome,
        "revisionCount": revision_count,
        "accepted": accepted,
        "skillIds": skill_ids or [],
        "detail": detail[:2000],
        "ts": now,
        "successFirstTry": outcome == "done" and revision_count == 0,
        "regressionDetected": regression_detected,
        "checkpointId": checkpoint_id,
        "checkpointType": checkpoint_type,
        "rollbackRecommended": rollback_recommended,
        "rollbackAutoRequested": auto_rollback,
        "rollbackStatus": rollback_status,
    }
    if rollback_result is not None:
        row["rollbackResult"] = rollback_result
    if rollback_error:
        row["rollbackError"] = rollback_error
    await repo.put_entity("eval_outcome", row, dept=department_id, status=outcome, ts=now)
    if skill_ids:
        from ..learning.skills import update_skill_metrics

        for skill_id in skill_ids:
            await update_skill_metrics(
                repo,
                skill_id,
                success=bool(accepted if accepted is not None else outcome == "done"),
                revision_count=revision_count,
            )
    return row


async def department_eval_summary(repo: Any, department_id: str) -> dict[str, Any]:
    rows = [
        row
        for row in await repo.list_entities("eval_outcome", dept=department_id, limit=500)
    ]
    if not rows:
        return {
            "departmentId": department_id,
            "count": 0,
            "successFirstTryRate": None,
            "avgRevisions": None,
            "accepted": 0,
            "rejected": 0,
            "acceptedRate": None,
            "routingWeight": 1.0,
        }
    successes = sum(1 for row in rows if row.get("successFirstTry"))
    revisions = sum(int(row.get("revisionCount") or 0) for row in rows)
    accepted = sum(1 for row in rows if row.get("accepted") is True)
    rejected = sum(1 for row in rows if row.get("accepted") is False)
    judged = accepted + rejected
    summary = {
        "departmentId": department_id,
        "count": len(rows),
        "successFirstTryRate": successes / len(rows),
        "avgRevisions": revisions / len(rows),
        "accepted": accepted,
        "rejected": rejected,
        "acceptedRate": (accepted / judged) if judged else None,
    }
    summary["routingWeight"] = department_quality_score(summary)
    return summary


async def department_eval_routing_weight(repo: Any, department_id: str) -> tuple[float, dict[str, Any]]:
    summary = await department_eval_summary(repo, department_id)
    return float(summary.get("routingWeight") or 1.0), summary
