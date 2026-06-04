from __future__ import annotations

from typing import Any

from .ids import uid

TASK_REVIEW_REMINDER_KIND = "task_review_reminder"
TASK_TERMINAL_STATUSES = {"done", "cancelled"}
MIN_REVIEW_INTERVAL_MS = 60_000
MAX_REVIEW_INTERVAL_MS = 7 * 24 * 60 * 60_000
DEFAULT_REVIEW_INTERVAL_MS = 5 * 60_000
RECOMMENDED_REVIEW_INTERVAL_BY_PRIORITY_MS = {
    "urgent": 2 * 60_000,
    "high": 3 * 60_000,
    "normal": DEFAULT_REVIEW_INTERVAL_MS,
    "low": 10 * 60_000,
}


def recommended_review_interval_ms(priority: Any = None) -> int:
    return RECOMMENDED_REVIEW_INTERVAL_BY_PRIORITY_MS.get(
        str(priority or "normal").strip().lower(),
        DEFAULT_REVIEW_INTERVAL_MS,
    )


def normalize_review_interval_ms(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        interval = int(value)
    except (TypeError, ValueError):
        return None
    if interval <= 0:
        return None
    return max(MIN_REVIEW_INTERVAL_MS, min(interval, MAX_REVIEW_INTERVAL_MS))


def review_interval_for_new_task(value: Any, *, priority: Any = None) -> int | None:
    if value is None or value == "":
        return recommended_review_interval_ms(priority)
    return normalize_review_interval_ms(value)


def review_interval_label(interval_ms: int | None) -> str:
    if not interval_ms:
        return "ปิด"
    minutes = max(1, round(interval_ms / 60_000))
    if minutes < 60:
        return f"{minutes} นาที"
    hours = minutes / 60
    if hours < 24:
        return f"{hours:g} ชั่วโมง"
    return f"{hours / 24:g} วัน"


def apply_task_review_schedule(task: dict[str, Any], interval_ms: int | None, now: int) -> None:
    interval = normalize_review_interval_ms(interval_ms)
    task["reviewIntervalMs"] = interval
    if interval:
        task["reviewScheduleToken"] = uid("rev")
        task["nextReviewAt"] = now + interval
    else:
        task["reviewScheduleToken"] = None
        task["nextReviewAt"] = None


async def enqueue_task_review_reminder(repo: Any, task: dict[str, Any], *, now: int | None = None) -> str | None:
    interval = normalize_review_interval_ms(task.get("reviewIntervalMs"))
    if not interval or task.get("status") in TASK_TERMINAL_STATUSES:
        return None
    token = str(task.get("reviewScheduleToken") or "").strip()
    next_review_at = int(task.get("nextReviewAt") or 0)
    if not token or next_review_at <= 0:
        return None
    job_id = uid("job")
    await repo.enqueue(
        job_id,
        TASK_REVIEW_REMINDER_KIND,
        {
            "taskId": task.get("id"),
            "departmentId": task.get("departmentId"),
            "taskTitle": task.get("title"),
            "reviewIntervalMs": interval,
            "reviewScheduleToken": token,
            "queuedAt": now,
            "queueTitle": f"ปลุกตรวจงาน: {task.get('title')}",
        },
        next_review_at,
        priority=2,
    )
    return job_id
