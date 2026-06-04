"""Always-on company engine.

This is the headless part the UI cannot own: scheduler ticks, durable jobs,
task progress/review, handoffs, autonomous work, budget telemetry, and visible
memory compaction. Background work goes through the same Claude-compatible live
provider layer as explicit chat turns. Provider failures surface as real
provider errors; there is no fabricated LLM fallback.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import random
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .atrium_domain import (
    agent_message_metadata,
    handoff_flow,
    is_war_room_thread,
    meeting_context,
    meeting_id_from_thread,
    meeting_participants,
    persona_prompt,
    system_chat_message,
    thread_cost_summary,
    update_agent_state,
    war_room_context,
    war_room_id_from_thread,
    war_room_participants,
)
from .catalog import (
    DEFAULT_MODEL,
    coerce_model_speed,
    coerce_thinking_effort,
    model_pricing,
    provider_bypasses_agent_runtime,
    provider_has_native_chat_stream,
)
from .chat_input import (
    attachment_context,
    message_content_with_attachment_images,
    message_text_with_attachment_refs,
    suggested_followups_for_response,
)
from .chat_rendering import citation_chips, ensure_rendering_metadata
from .chat_tools import (
    apply_result_totals,
    assistant_tool_message,
    chat_tool_definitions,
    chat_tool_system_instructions,
    deferred_wake_tool_run_context,
    fallback_text_for_tool_runs,
    image_generation_wake_context,
    likely_needs_chat_tools,
    recent_tool_run_context,
    run_chat_tool,
    should_enable_chat_tools,
    tool_result_message,
    video_job_wake_context,
)
from .chat_streaming import (
    ChatMessageStreamSink,
    ChatStreamCancelled,
    chat_streams,
    provider_exception_detail,
    runtime_event_to_hub_pulse,
    stream_llm,
)
from .clock import DAY_MS, day_key, now_ms
from .config import Settings, get_settings
from .context_budget import estimate_llm_context_tokens, model_auto_compact_context_tokens
from .db.base import commit_and_release, session_scope
from .db.repo import Repo
from .events import hub
from .file_intake import attachments_from_tool_runs
from .handoffs import (
    append_handoff_message,
    handoff_chat_message,
    handoff_status_for_act,
    make_handoff_message,
)
from .image_generation import RetryableImageGenerationError, mark_image_generation_job_failed, process_image_generation_job
from .ids import uid
from .memory.archive import attach_archive_audit
from .memory.debt import compute_department_knowledge_debt
from .memory.embeddings import embedding_metadata, resolve_embedder
from .memory.extraction import normalize_compaction_extraction
from .memory.retrieval import build_retrieval_context
from .provider.base import LLMMessage, LLMResult, LLMStreamEvent, LLMToolCall
from .provider.registry import get_provider
from .scheduling import cadence_interval_ms, repair_trigger_schedule
from .seed import EXEC_ID
from .task_review import (
    TASK_REVIEW_REMINDER_KIND,
    TASK_TERMINAL_STATUSES,
    enqueue_task_review_reminder,
    normalize_review_interval_ms,
    review_interval_label,
)
from .threads import EXEC_THREAD, is_exec, thread_id_for

MAX_ACTIVITY = 80
MAX_TRIGGER_CATCH_UP_RUNS = 8
_TRIGGER_ENQUEUE_LOCK = asyncio.Lock()
DEDICATED_WORKER_JOB_KINDS = {"image_generation", "trigger_run"}
CHAT_REPLY_DEFER_COLLISION_MS = 750
BUDGET_NEAR_CAP_RATIO = 0.8
EXECUTIVE_SUMMARY_INTERVAL_MS = 60 * 60_000
EXECUTIVE_SUMMARY_JITTER_MS = 15 * 60_000
EXECUTIVE_SUMMARY_NEXT_RUN_KEY = "nextAutoSummaryAt"
EXECUTIVE_SUMMARY_LAST_ATTEMPT_KEY = "lastAutoSummaryAttemptAt"
EXECUTIVE_WAKE_EVENTS = {"dept_done", "blocked", "budget", "escalate"}
ENGINE_ERROR_NOTIFY_INTERVAL_MS = 5 * 60_000
KNOWLEDGE_DEBT_NOTIFY_MIN_DEBT = 5
DAILY_DIGEST_SAMPLE_LIMIT = 5
NOTIFICATION_PREFS_ID = "global"
NOTIFICATION_TYPES = {
    "approval",
    "budget",
    "blocked",
    "task_done",
    "digest",
    "crash",
    "knowledge_debt",
    "security",
}
DEFAULT_NOTIFICATION_DELIVERY = {
    "approval": "push",
    "budget": "push",
    "blocked": "push",
    "task_done": "inbox",
    "digest": "inbox",
    "crash": "push",
    "knowledge_debt": "inbox",
    "security": "push",
}


def _attach_tool_artifacts_to_message(message: dict[str, Any], tool_runs: list[dict[str, Any]] | None) -> dict[str, Any]:
    generated = attachments_from_tool_runs(tool_runs or [])
    if not generated:
        return message
    existing = list(message.get("attachments") or [])
    seen = {str(item.get("artifactId") or item.get("artifact_id") or "") for item in existing if isinstance(item, dict)}
    for item in generated:
        artifact_id = str(item.get("artifactId") or "")
        if artifact_id and artifact_id not in seen:
            existing.append(item)
            seen.add(artifact_id)
    return {**message, "attachments": existing}


class _RetryJobLater(Exception):
    def __init__(self, reason: str, *, delay_ms: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.delay_ms = max(1_000, int(delay_ms))


_ENGINE_RUNTIME: dict[str, Any] = {
    "enabled": True,
    "state": "starting",
    "currentPhase": None,
    "currentDepartmentId": None,
    "currentDepartmentName": None,
    "currentTaskId": None,
    "currentJobId": None,
    "lastTickStartedAt": None,
    "lastTickFinishedAt": None,
    "lastTickDurationMs": None,
    "lastTickStats": None,
    "lastError": None,
    "lastErrorAt": None,
}
_CHAT_REPLY_WORKER_RUNTIME: dict[str, Any] = {
    "concurrency": 0,
    "inFlight": 0,
    "lastStartedAt": None,
    "lastFinishedAt": None,
    "lastBatchStarted": 0,
}
_DEPARTMENT_WORKER_RUNTIME: dict[str, Any] = {
    "concurrency": 0,
    "inFlight": 0,
    "lastStartedAt": None,
    "lastFinishedAt": None,
    "lastBatchStarted": 0,
}


def _runtime_degraded_reason(health: dict[str, Any]) -> str:
    backend = str(health.get("backend") or health.get("configuredBackend") or "agent runtime")
    status = health.get("statusCode")
    error = str(health.get("error") or "").strip()
    if error:
        return f"{backend} degraded: {error}"
    if status is not None:
        return f"{backend} degraded: statusCode={status}"
    return f"{backend} degraded"


def _uses_agent_runtime(settings: Settings, dept: dict[str, Any]) -> bool:
    return bool(settings.use_letta_runtime and not provider_bypasses_agent_runtime(dept.get("providerId")))


def _uses_agent_runtime_for_chat(settings: Settings, dept: dict[str, Any]) -> bool:
    return bool(settings.use_letta_runtime and not provider_has_native_chat_stream(dept.get("providerId")))


async def _runtime_degraded_retry_reason(settings: Settings | None = None, dept: dict[str, Any] | None = None) -> str | None:
    settings = settings or get_settings()
    if not settings.use_letta_runtime or not settings.runtime_degraded_queue:
        return None
    if dept is not None and not _uses_agent_runtime(settings, dept):
        return None
    from .runtime import agent_runtime_health

    health = await agent_runtime_health(settings)
    if health.get("ok") is True and health.get("degraded") is not True:
        return None
    return _runtime_degraded_reason(health)
_JOB_CLAIM_LOCK = asyncio.Lock()


def _set_engine_runtime(**patch: Any) -> None:
    _ENGINE_RUNTIME.update(patch)


def _set_engine_phase(
    phase: str | None,
    *,
    dept: dict[str, Any] | None = None,
    task: dict[str, Any] | None = None,
    job_id: str | None = None,
) -> None:
    _set_engine_runtime(
        currentPhase=phase,
        currentDepartmentId=dept.get("id") if dept else None,
        currentDepartmentName=dept.get("name") if dept else None,
        currentTaskId=(task or {}).get("id") if task else None,
        currentJobId=job_id,
    )


def engine_runtime_snapshot(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    now = now_ms()
    started = _ENGINE_RUNTIME.get("lastTickStartedAt")
    finished = _ENGINE_RUNTIME.get("lastTickFinishedAt")
    in_tick = bool(started and (not finished or int(started) > int(finished)))
    reference = started if in_tick else finished
    age_ms = None if reference is None else max(0, now - int(reference))
    stale_after_ms = int(max(1.0, settings.engine_stale_after_s) * 1000)
    return {
        **_ENGINE_RUNTIME,
        "inTick": in_tick,
        "tickAgeMs": age_ms,
        "staleAfterMs": stale_after_ms,
        "stale": bool(age_ms is not None and age_ms > stale_after_ms),
        "tickSeconds": settings.tick_seconds,
        "jobTimeoutS": settings.engine_job_timeout_s,
        "tickTimeoutS": settings.engine_tick_timeout_s,
        "chatReplyWorker": {
            **_CHAT_REPLY_WORKER_RUNTIME,
            "configuredConcurrency": _bounded_worker_concurrency(
                getattr(settings, "chat_reply_worker_concurrency", 1),
                default=1,
                ceiling=20,
            ),
        },
        "departmentWorker": {
            **_DEPARTMENT_WORKER_RUNTIME,
            "configuredConcurrency": _bounded_worker_concurrency(
                getattr(settings, "department_worker_concurrency", 1),
                default=1,
                ceiling=20,
            ),
        },
    }

AUTONOMY_IDEAS: dict[str, list[str]] = {
    "strategy": ["ทบทวนความเสี่ยงของแผนเปิดตัว", "หาโอกาสตลาดใหม่"],
    "research": ["สแกนข่าวคู่แข่งรอบล่าสุด", "รวบรวมงานวิจัยใหม่ประจำวัน"],
    "design": ["ปรับปรุงคอนทราสต์ธีมมืด", "ทำชุดไอคอนเพิ่ม"],
    "engineering": ["รีแฟกเตอร์โมดูลที่ซ้ำซ้อน", "เพิ่มเทสต์ให้สัญญา API"],
    "content": ["เสนอหัวข้อคอนเทนต์ใหม่", "ร่างแคปชันโซเชียลประจำสัปดาห์"],
    "qa": ["เพิ่มเคสทดสอบ edge case", "ทบทวนรายงานบั๊กเก่า"],
}

EXEC_LINES = [
    "อัปเดตครับ: ทีมกำลังเดินงานตามแผน เดี๋ยวมีสรุปให้อีกรอบ",
    "ผมเร่งงานสายเปิดตัวให้อยู่ในไทม์ไลน์ 6 สัปดาห์ครับ",
    "มี action เสี่ยงหนึ่งรายการถูกบันทึกพร้อม audit/checkpoint แล้วครับ",
    "กลยุทธ์ใกล้ปิดโรดแมปแล้ว วิจัยส่งข้อมูลตลาดเข้ามาเสริม",
    "ผมให้คอนเทนต์เริ่มร่างบทความเปิดตัวคู่ขนานไปก่อนครับ",
]


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


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


def _notification_preferences(raw: dict[str, Any] | None) -> dict[str, Any]:
    prefs = {
        "byType": dict(DEFAULT_NOTIFICATION_DELIVERY),
        "quietHours": {
            "enabled": False,
            "start": "22:00",
            "end": "07:00",
            "timezone": "Asia/Bangkok",
        },
    }
    if not raw:
        return prefs
    raw_by_type = raw.get("byType") or {}
    if isinstance(raw_by_type, dict):
        for notif_type, mode in raw_by_type.items():
            if notif_type in NOTIFICATION_TYPES and mode in {"off", "inbox", "push"}:
                prefs["byType"][notif_type] = mode
    raw_quiet = raw.get("quietHours") or {}
    if isinstance(raw_quiet, dict):
        prefs["quietHours"].update({
            key: value
            for key, value in raw_quiet.items()
            if key in {"enabled", "start", "end", "timezone"} and value is not None
        })
    return prefs


def _quiet_time_minutes(value: Any) -> int | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value or ""))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def _is_quiet_hour(now: int, quiet_hours: dict[str, Any]) -> bool:
    if not quiet_hours.get("enabled"):
        return False
    start = _quiet_time_minutes(quiet_hours.get("start"))
    end = _quiet_time_minutes(quiet_hours.get("end"))
    if start is None or end is None:
        return False
    try:
        tz = ZoneInfo(str(quiet_hours.get("timezone") or "Asia/Bangkok"))
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    current_time = datetime.fromtimestamp(now / 1000, tz)
    current = current_time.hour * 60 + current_time.minute
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


async def _notification_delivery(repo: Repo, type_: str, now: int) -> tuple[str, bool]:
    raw = await repo.get_entity("notification_preferences", NOTIFICATION_PREFS_ID)
    prefs = _notification_preferences(raw)
    mode = prefs["byType"].get(type_, DEFAULT_NOTIFICATION_DELIVERY.get(type_, "inbox"))
    quiet_applied = False
    if mode == "push" and _is_quiet_hour(now, prefs["quietHours"]):
        mode = "inbox"
        quiet_applied = True
    return mode, quiet_applied


async def _notify(
    repo: Repo,
    *,
    type_: str,
    severity: str,
    title: str,
    body: str,
    now: int,
    links: list[str] | None = None,
) -> dict[str, Any]:
    delivery_mode, quiet_applied = await _notification_delivery(repo, type_, now)
    notification = {
        "id": uid("notif"),
        "type": type_,
        "severity": severity,
        "title": title,
        "body": body,
        "ts": now,
        "read": False,
        "links": links or [],
        "deliveryMode": delivery_mode,
        "quietHoursApplied": quiet_applied,
    }
    if delivery_mode == "off":
        notification["suppressed"] = True
        return notification
    await repo.put_entity("notification", notification, status="unread", ts=now)
    hub.notify(notification)
    return notification


def _thinking_tokens(effort: str) -> int:
    return {"off": 0, "low": 40, "medium": 120, "high": 300, "xhigh": 600, "max": 1000}.get(effort, 200)


def _estimated_cost(model: str, effort: str = "high", speed: str = "standard", scale: float = 1.0) -> float:
    in_rate, out_rate = model_pricing(model, speed)
    tokens_in = 1500 + random.random() * 3000
    tokens_out = 300 + random.random() * 900 + _thinking_tokens(effort)
    return round(((tokens_in * in_rate + tokens_out * out_rate) / 1_000_000) * scale, 6)


def _engine_cost_estimate(
    dept: dict[str, Any],
    *,
    input_tokens: int = 3500,
    output_tokens_estimate: int = 2048,
) -> float:
    in_rate, out_rate = model_pricing(dept.get("model", DEFAULT_MODEL), dept.get("speed", "standard"))
    tokens_out = output_tokens_estimate + _thinking_tokens(dept.get("thinkingEffort", "high"))
    return round((input_tokens * in_rate + tokens_out * out_rate) / 1_000_000, 6)


def _clip_text(text: Any, limit: int = 4000) -> str:
    s = str(text or "").replace("\r\n", "\n").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    return value if isinstance(value, list) else []


def _choice(value: Any, allowed: set[str], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def _number(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = default
    if hi <= 1 and n > 1:
        n = n / 100
    return _clamp(n, lo, hi)


def _working_memory_context(dept: dict[str, Any]) -> str:
    memory = dept.get("memory") or {}
    summary = _clip_text(memory.get("workingSummary"), 900)
    if not summary:
        return ""
    provenance = []
    if memory.get("workingArchiveId"):
        provenance.append(f"archive={memory['workingArchiveId']}")
    if memory.get("workingThreadId"):
        provenance.append(f"thread={memory['workingThreadId']}")
    label = "Working memory summary"
    if provenance:
        label = f"{label} ({', '.join(provenance)})"
    return f"{label}:\n{summary}"


def _engine_memory_context(knowledge: list[dict[str, Any]], graph: dict[str, Any], working_memory: str = "") -> str:
    return build_retrieval_context(knowledge, graph, working_memory=working_memory)


async def _task_memory_context(repo: Repo, dept: dict[str, Any], task: dict[str, Any] | None) -> str:
    if is_exec(dept["id"]):
        from .memory.executive_retrieval import executive_retrieval_context

        query = " ".join(
            _clip_text(part, 1200)
            for part in [
                task.get("title") if task else "",
                task.get("detail") if task else "",
                dept.get("role", ""),
                dept.get("charter", ""),
            ]
            if part
        )
        context, _hits = await executive_retrieval_context(repo, query or dept.get("charter", ""))
        return context
    query = " ".join(
        _clip_text(part, 1200)
        for part in [
            task.get("title") if task else "",
            task.get("detail") if task else "",
            dept.get("role", ""),
            dept.get("charter", ""),
        ]
        if part
    )
    knowledge: list[dict[str, Any]] = []
    if query:
        vecs = await (await resolve_embedder()).embed([query])
        knowledge = await repo.search_knowledge(dept["id"], vecs[0], k=get_settings().rag_top_k) if vecs else []
    if not knowledge:
        knowledge = await repo.list_knowledge(dept["id"], limit=min(get_settings().rag_top_k, 5))
    return _engine_memory_context(knowledge, await repo.graph(dept["id"]), _working_memory_context(dept))


def _department_system(dept: dict[str, Any], purpose: str, memory_context: str = "") -> str:
    if is_exec(dept["id"]):
        base = (
            f"คุณคือ {dept['agentName']} ผู้บริหารของบริษัท AI ATRIUM. "
            "คุณขับเคลื่อนบริษัทแบบ always-on: แตกงาน มอบหมาย ตรวจคุณภาพ จัดลำดับความเสี่ยง "
            "และสรุปความคืบหน้าอย่างตรวจสอบย้อนหลังได้. "
            "ในการคุยจริงครั้งแรกกับเจ้าของ ให้ถามว่าอยากให้ผู้บริหารคนนี้ชื่ออะไร; "
            "เมื่อเจ้าของบอกชื่อ ให้ใช้ tool rename_self เพื่อบันทึกชื่อนั้นเป็นชื่อของตัวเองก่อนทำงานต่อ."
        )
    else:
        base = (
            f"คุณคือ {dept['agentName']} จากฝ่าย{dept['name']} ({dept.get('role', '')}). "
            f"charter: {dept.get('charter', '')}. "
            "คุณทำงานใน background loop จริงของ ATRIUM; ห้ามอ้างว่าทำ external action, publish, ใช้เงิน, "
            "ลบข้อมูล หรือรันคำสั่งภายนอกแล้ว เว้นแต่มี tool/audit record ให้ทำจริง. "
            "งานเสี่ยงต้องวิ่งผ่าน tool/checkpoint/audit/rollback surface ไม่ใช่ approval gate."
        )
    if memory_context:
        base += "\n\nบริบทความจำที่เกี่ยวข้อง:\n" + memory_context
    return f"{base}\n\nภารกิจรอบนี้: {purpose}"


async def _budget_block_reason(repo: Repo, dept: dict[str, Any], estimated_usd: float) -> str | None:
    """Budget is telemetry/alert/forecast only; it never blocks execution."""
    return None


async def _record_budget_exhaustion(repo: Repo, dept: dict[str, Any], now: int) -> None:
    company = await repo.get_company()
    if not company:
        return
    spent = await repo.spent_today()
    if company.daily_cap_usd > 0 and spent >= company.daily_cap_usd:
        marker = f"atrium://budget/company/over-cap/{day_key(now)}"
        existing = await repo.list_entities("notification", limit=1000)
        if any(marker in (notification.get("links") or []) for notification in existing):
            return
        await repo.add_activity(_activity(
            "งบรวมรายวันถึงหรือเกินเพดานแล้ว — บันทึกเป็น telemetry เท่านั้น ระบบยังไม่หยุดเอง",
            type_="budget",
            severity="alert",
            ts=now,
        ))
        await _notify(
            repo,
            type_="budget",
            severity="alert",
            title="งบรวมรายวันถึงเพดานแล้ว",
            body="Full Auto ยังทำงานต่อ; budget เป็น telemetry/alert/forecast ไม่ใช่ execution gate",
            now=now,
            links=["atrium://budget/company", marker, f"atrium://department/{dept['id']}"],
        )
    else:
        await _record_budget_near_cap(repo, dept, now, spent, company.daily_cap_usd)


def _budget_near_cap_marker(now: int) -> str:
    return f"atrium://budget/company/near-cap/{day_key(now)}"


async def _budget_threshold_reached(repo: Repo) -> bool:
    company = await repo.get_company()
    if not company or company.daily_cap_usd <= 0:
        return False
    return await repo.spent_today() >= company.daily_cap_usd * BUDGET_NEAR_CAP_RATIO


async def _record_budget_near_cap(
    repo: Repo,
    dept: dict[str, Any],
    now: int,
    spent: float,
    cap: float,
) -> bool:
    if cap <= 0 or spent < cap * BUDGET_NEAR_CAP_RATIO or spent >= cap:
        return False
    marker = _budget_near_cap_marker(now)
    for notification in await repo.list_entities("notification", limit=1000):
        if marker in (notification.get("links") or []):
            return False
    percent = round((spent / cap) * 100)
    await _notify(
        repo,
        type_="budget",
        severity="warn",
        title="งบรวมรายวันใกล้ถึงเพดาน",
        body=(
            f"ใช้ไป ${spent:.4f} จากเพดานรายวัน ${cap:.4f} ({percent}%). "
            "ระบบยังทำงานต่อ แต่ควรติดตามค่าใช้จ่าย"
        ),
        now=now,
        links=[
            "atrium://budget/company",
            marker,
            f"atrium://department/{dept['id']}",
        ],
    )
    return True


def _knowledge_debt_marker(dept_id: str, now: int) -> str:
    return f"atrium://knowledge-debt/{day_key(now)}/{dept_id}"


def _daily_digest_marker(now: int) -> str:
    return f"atrium://digest/daily/{day_key(now)}"


def _budget_forecast_marker(now: int) -> str:
    return f"atrium://budget/company/forecast/{day_key(now)}"


async def _notification_link_exists(repo: Repo, marker: str, *, limit: int = 2000) -> bool:
    return any(marker in (notification.get("links") or []) for notification in await repo.list_entities("notification", limit=limit))


async def _record_daily_digest_notification(repo: Repo, departments: list[dict[str, Any]], now: int) -> bool:
    marker = _daily_digest_marker(now)
    if await _notification_link_exists(repo, marker):
        return False
    tasks = await repo.list_tasks(limit=1000, newest_first=True)
    budget = await repo.get_budget()
    open_tasks = [task for task in tasks if task.get("status") not in {"done", "cancelled"}]
    blocked_tasks = [task for task in open_tasks if task.get("status") == "blocked"]
    done_today = [
        task
        for task in tasks
        if task.get("status") == "done" and int(task.get("updatedAt") or 0) >= now - DAY_MS
    ]
    busy_departments = [
        dept
        for dept in departments
        if not is_exec(dept["id"]) and (dept.get("state") not in {None, "idle"} or dept.get("currentTaskId"))
    ]
    sample = "; ".join(str(task.get("title") or task.get("id"))[:70] for task in open_tasks[:DAILY_DIGEST_SAMPLE_LIMIT])
    body = (
        f"Open tasks: {len(open_tasks)}; done last 24h: {len(done_today)}; "
        f"blocked: {len(blocked_tasks)}; active departments: {len(busy_departments)}; "
        f"spent today: ${float(budget.get('spentTodayUsd') or 0.0):.4f} / ${float(budget.get('dailyCapUsd') or 0.0):.2f}."
    )
    if sample:
        body = f"{body} Top open work: {sample}"
    await _notify(
        repo,
        type_="digest",
        severity="warn" if blocked_tasks else "info",
        title=f"Daily ATRIUM digest — {day_key(now)}",
        body=body,
        now=now,
        links=[
            "atrium://digest/daily",
            marker,
            "atrium://tasks/open",
            "atrium://budget/company",
        ],
    )
    return True


async def _record_budget_forecast_notification(repo: Repo, now: int) -> bool:
    company = await repo.get_company()
    if not company or company.daily_cap_usd <= 0:
        return False
    report = await repo.cost_report("day")
    forecast = float(report.get("forecastUsd") or 0.0)
    ratio = min(max(float(get_settings().chat_budget_warning_ratio), 0.0), 1.0)
    threshold = float(company.daily_cap_usd) * ratio
    if threshold <= 0 or forecast < threshold:
        return False
    marker = _budget_forecast_marker(now)
    if await _notification_link_exists(repo, marker):
        return False
    severity = "alert" if forecast >= float(company.daily_cap_usd) else "warn"
    await _notify(
        repo,
        type_="budget",
        severity=severity,
        title="Budget forecast alert",
        body=(
            f"Forecast today is ${forecast:.2f} against daily cap ${company.daily_cap_usd:.2f} "
            f"(warning threshold {int(ratio * 100)}%). Telemetry only; Full Auto continues unless a real runtime/quota failure occurs."
        ),
        now=now,
        links=[
            "atrium://budget/company",
            marker,
        ],
    )
    return True


async def _record_daily_operational_notifications(repo: Repo, departments: list[dict[str, Any]], now: int) -> int:
    created = 0
    if await _record_daily_digest_notification(repo, departments, now):
        created += 1
    if await _record_budget_forecast_notification(repo, now):
        created += 1
    return created


def _knowledge_debt_total(report: dict[str, Any]) -> int:
    return sum(
        int(report.get(key) or 0)
        for key in ("stale", "conflicting", "unsourced", "orphaned", "duplicate")
    )


async def _record_knowledge_debt_notifications(
    repo: Repo,
    departments: list[dict[str, Any]],
    now: int,
) -> int:
    created = 0
    scheduled_consolidation_depts: list[str] = []
    existing_notifications = await repo.list_entities("notification", limit=2000)
    for dept in departments:
        dept_id = dept["id"]
        if is_exec(dept_id):
            continue
        knowledge = await repo.list_knowledge(dept_id, limit=1000)
        graph = await repo.graph(dept_id)
        report = compute_department_knowledge_debt(dept_id, knowledge, graph, now)
        debt = _knowledge_debt_total(report)
        if debt < KNOWLEDGE_DEBT_NOTIFY_MIN_DEBT:
            continue
        marker = _knowledge_debt_marker(dept_id, now)
        if any(marker in (notification.get("links") or []) for notification in existing_notifications):
            continue
        scheduled_consolidation_depts.append(dept_id)
        notification = await _notify(
            repo,
            type_="knowledge_debt",
            severity="warn",
            title=f"Knowledge debt สูง: {dept.get('name', dept_id)}",
            body=(
                f"พบ knowledge-debt {debt} จุด "
                f"(unsourced={report['unsourced']}, stale={report['stale']}, "
                f"duplicate={report['duplicate']}, orphaned={report['orphaned']}, "
                f"conflicting={report['conflicting']})."
            ),
            now=now,
            links=[
                "atrium://knowledge-debt",
                marker,
                f"atrium://department/{dept_id}",
            ],
        )
        if not notification.get("suppressed"):
            existing_notifications.append(notification)
            created += 1
    if scheduled_consolidation_depts:
        from .learning.consolidation import enqueue_consolidation

        await enqueue_consolidation(
            repo,
            run_after=now + 30_000,
            reason="knowledge_debt",
            department_ids=scheduled_consolidation_depts,
            priority=3,
            dedupe_key=f"knowledge_debt:{day_key(now)}",
        )
        await repo.add_activity(_activity(
            f"scheduled knowledge-debt consolidation for {len(scheduled_consolidation_depts)} departments",
            type_="compaction",
            severity="warn",
            ts=now,
        ))
    return created


async def _complete_engine_turn(
    repo: Repo,
    dept: dict[str, Any],
    *,
    category: str,
    system: str,
    messages: list[LLMMessage],
    now: int,
    input_tokens: int = 3500,
    on_stream_event: Callable[[LLMStreamEvent], Awaitable[None]] | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> LLMResult | None:
    estimated = _engine_cost_estimate(dept, input_tokens=input_tokens)
    blocked = await _budget_block_reason(repo, dept, estimated)
    if blocked:
        await repo.add_activity(_activity(
            f"Budget telemetry warning for {dept['agentName']}: {blocked}",
            type_="budget",
            department_id=dept["id"],
            severity="warn",
            ts=now,
        ))
        await _notify(
            repo,
            type_="budget",
            severity="warn",
            title=f"Budget telemetry warning: {dept.get('name', dept['id'])}",
            body=f"{blocked}. Full Auto ยังทำงานต่อ",
            now=now,
            links=[f"atrium://department/{dept['id']}"],
        )
        hub.pulse({"kind": "spend", "departmentId": dept["id"]})

    # Engine phases update state before asking the model. Return the DB
    # connection before the long provider wait so live API requests keep moving.
    await commit_and_release(repo.s)

    provider = get_provider(dept.get("providerId", "claude_code"), get_settings())
    model = dept.get("model", DEFAULT_MODEL)
    effort = coerce_thinking_effort(model, dept.get("thinkingEffort", "high"))
    speed = coerce_model_speed(model, dept.get("speed", "standard"))
    try:
        if on_stream_event is not None:
            result = await stream_llm(
                provider,
                system=system,
                messages=messages,
                model=model,
                effort=effort,
                speed=speed,
                on_event=on_stream_event,
                tools=tools,
            )
        else:
            result = await provider.complete(
                system=system,
                messages=messages,
                model=model,
                effort=effort,
                speed=speed,
                tools=tools,
            )
        result.meta["engineProviderLive"] = bool(getattr(provider, "live", False))
    except ChatStreamCancelled:
        raise
    except Exception:
        raise

    if not result.meta.get("cancelled"):
        await repo.add_cost(
            uid("cost"),
            now,
            dept["id"],
            category,
            result.usd,
            detail=f"engine:{category}:{result.provider_id}:{result.model}:{effort}:{result.speed}",
            provider_id=result.provider_id,
            model=result.model,
            speed=result.speed,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
        )
    await _record_budget_exhaustion(repo, dept, now)
    hub.pulse({"kind": "spend", "departmentId": dept["id"]})
    await commit_and_release(repo.s)
    return result


async def _complete_runtime_turn(
    repo: Repo,
    dept: dict[str, Any],
    *,
    category: str,
    system: str,
    messages: list[LLMMessage],
    now: int,
    thread_id: str | None = None,
    input_tokens: int = 3500,
    on_stream_event: Callable[[LLMStreamEvent], Awaitable[None]] | None = None,
    client_tools: list[dict[str, Any]] | None = None,
    stream_msg_id: str | None = None,
    requested_by: str | None = None,
) -> LLMResult | None:
    """Complete an agent turn through the v2 stateful runtime when enabled."""
    settings = get_settings()
    if not _uses_agent_runtime(settings, dept):
        return None
    estimated = _engine_cost_estimate(dept, input_tokens=input_tokens)
    blocked = await _budget_block_reason(repo, dept, estimated)
    if blocked:
        await repo.add_activity(_activity(
            f"Budget telemetry warning for {dept['agentName']}: {blocked}",
            type_="budget",
            department_id=dept["id"],
            severity="warn",
            ts=now,
        ))
        hub.pulse({"kind": "spend", "departmentId": dept["id"]})

    from .runtime.provisioning import ensure_department_runtime_agent_safely
    from .runtime.turns import (
        RuntimeTurnUnavailable,
        complete_agent_via_runtime,
        runtime_dependency_result,
    )

    meta = await ensure_department_runtime_agent_safely(repo, dept, settings=settings)
    if not meta or not meta.get("lettaAgentId"):
        detail = f"runtime agent is not provisioned for department {dept.get('id') or 'unknown'}"
        if category == "chat":
            return runtime_dependency_result(dept, detail, category=category, source="engine", settings=settings)
        return None
    runtime_dept = {**dept, "runtime": meta}
    # Release any pending write transaction before the runtime HTTP turn.
    await commit_and_release(repo.s)

    async def emit_runtime_event(event: Any) -> None:
        if thread_id and stream_msg_id:
            pulse = runtime_event_to_hub_pulse(event, thread_id=thread_id, msg_id=stream_msg_id)
            if pulse:
                hub.pulse(pulse)
        if thread_id:
            with contextlib.suppress(Exception):
                from .memory.ledger import record_runtime_event_ledger

                async with session_scope() as s:
                    await record_runtime_event_ledger(
                        Repo(s),
                        event,
                        thread_id=thread_id,
                        department_id=dept["id"],
                        message_id=stream_msg_id,
                        source="engine",
                        category=category,
                    )

    async def execute_runtime_tool(call: LLMToolCall) -> dict[str, Any]:
        async with session_scope() as s:
            return await run_chat_tool(
                Repo(s),
                call,
                active_dept=dept,
                thread_id=thread_id or thread_id_for(dept["id"]),
                requested_by=requested_by or dept.get("agentName", dept["id"]),
            )

    try:
        result = await complete_agent_via_runtime(
            runtime_dept,
            thread_id=thread_id,
            system_prompt=system,
            messages=messages,
            metadata={"category": category, "source": "engine"},
            on_stream_event=on_stream_event,
            client_tools=client_tools,
            tool_executor=execute_runtime_tool if client_tools else None,
            on_runtime_event=emit_runtime_event if thread_id else None,
            settings=settings,
            allow_provider_fallback=False,
        )
    except RuntimeTurnUnavailable as exc:
        detail = str(exc) or "runtime turn unavailable"
        if category == "chat":
            return runtime_dependency_result(runtime_dept, detail, category=category, source="engine", settings=settings)
        await repo.add_activity(_activity(
            f"runtime dependency ระหว่าง {category} ของ{dept.get('agentName', dept.get('id'))}: {detail}",
            type_="system",
            department_id=dept["id"],
            severity="warn",
            ts=now,
        ))
        return None
    if result is None:
        return None
    if not result.meta.get("cancelled"):
        await repo.add_cost(
            uid("cost"),
            now,
            dept["id"],
            category,
            result.usd,
            detail=f"runtime:{category}:{result.provider_id}:{result.model}:{dept.get('thinkingEffort', 'high')}:{result.speed}",
            provider_id=result.provider_id,
            model=result.model,
            speed=result.speed,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
        )
    await _record_budget_exhaustion(repo, dept, now)
    hub.pulse({"kind": "spend", "departmentId": dept["id"]})
    await commit_and_release(repo.s)
    return result


def _partial_chat_result(
    dept: dict[str, Any],
    text: str,
    *,
    stop_reason: str,
    error_type: str | None = None,
    error_detail: str | None = None,
) -> LLMResult:
    meta: dict[str, Any] = {}
    if stop_reason == "cancelled":
        meta["cancelled"] = True
    if error_type:
        meta["streamErrorType"] = error_type
    if error_detail:
        meta["streamErrorDetail"] = error_detail
    provider_error = error_detail or error_type or "unknown"
    return LLMResult(
        text=text
        or (
            "หยุดการตอบแล้วก่อนมีข้อความตอบกลับ"
            if stop_reason == "cancelled"
            else f"AI จริงตอบไม่ได้เพราะ provider error: {provider_error}"
        ),
        tokens_in=0,
        tokens_out=0,
        model=dept.get("model", DEFAULT_MODEL),
        provider_id=dept.get("providerId", "claude_code"),
        speed=coerce_model_speed(dept.get("model", DEFAULT_MODEL), dept.get("speed", "standard")),
        stop_reason=stop_reason,
        meta=meta,
    )


def _ensure_result_text_from_tools(result: LLMResult | None) -> None:
    if not result or str(result.text or "").strip():
        return
    fallback = fallback_text_for_tool_runs(result.meta.get("toolRuns"))
    if not fallback:
        return
    result.text = fallback
    result.tokens_out = max(int(result.tokens_out or 0), max(1, len(fallback) // 4))
    result.meta["emptyTextFallback"] = "tool_runs"


def _chat_cost_estimate(dept: dict[str, Any]) -> float:
    in_rate, out_rate = model_pricing(dept.get("model", DEFAULT_MODEL), dept.get("speed", "standard"))
    tokens_in = 4500
    tokens_out = 2048 + _thinking_tokens(dept.get("thinkingEffort", "high"))
    return round((tokens_in * in_rate + tokens_out * out_rate) / 1_000_000, 6)


def _with_turn_thinking_effort(dept: dict[str, Any], thinking_effort: str | None) -> dict[str, Any]:
    if not thinking_effort:
        return dept
    model = dept.get("model", DEFAULT_MODEL)
    return {**dept, "thinkingEffort": coerce_thinking_effort(model, thinking_effort)}


def _with_turn_speed(dept: dict[str, Any], speed: str | None) -> dict[str, Any]:
    model = dept.get("model", DEFAULT_MODEL)
    if speed is None:
        speed = dept.get("speed", "standard")
    return {**dept, "speed": coerce_model_speed(model, speed)}


def _chat_system_prompt(dept: dict[str, Any], memory_context: str = "") -> str:
    if is_exec(dept["id"]):
        base = (
            f"คุณคือ {dept['agentName']} ผู้บริหารของบริษัท AI ATRIUM. "
            "หน้าที่คือรับโจทย์จากผู้ใช้ แตกงาน มอบหมายงาน ตรวจคุณภาพ และสรุปกลับเป็นภาษาไทยที่ชัดเจน. "
            "ตอบให้กระชับ มีเหตุผล และพร้อมนำไปปฏิบัติ. "
            "ในการคุยจริงครั้งแรกกับเจ้าของ ให้ถามว่าอยากให้ผู้บริหารคนนี้ชื่ออะไร; "
            "เมื่อเจ้าของบอกชื่อ ให้ใช้ tool rename_self เพื่อบันทึกชื่อนั้นเป็นชื่อของตัวเองก่อนทำงานต่อ."
        )
    else:
        base = (
            f"คุณคือ {dept['agentName']} จากฝ่าย{dept['name']} ({dept['role']}). "
            f"ขอบเขตงาน: {dept['charter']} "
            "ตอบเป็นภาษาไทยแบบมืออาชีพ ระบุสิ่งที่ทำได้จริง ข้อจำกัด และขั้นถัดไปเมื่อจำเป็น."
        )
    base = f"{base}{persona_prompt(dept)}"
    if not memory_context:
        return base
    return (
        f"{base}\n\n"
        "ใช้ความจำของแผนกต่อไปนี้เป็นบริบทสำคัญก่อนตอบ ถ้าความจำไม่เกี่ยวข้องให้ละไว้โดยไม่ฝืนอ้าง:\n"
        f"{memory_context}"
    )


def _llm_chat_history(history: list[dict[str, Any]], user_msg: dict[str, Any]) -> list[LLMMessage]:
    out: list[LLMMessage] = []
    settings = get_settings()
    limit = settings.chat_history_message_limit
    recent_history = history if limit <= 0 else history[-limit:]
    for msg in recent_history:
        if msg.get("pending"):
            continue
        role = "user" if msg.get("role") == "user" else "assistant"
        prefix = "" if role == "user" else f"{msg.get('authorName', 'agent')}: "
        text = message_text_with_attachment_refs(str(msg.get("text") or ""), msg.get("attachments") or [])
        out.append(LLMMessage(role=role, content=f"{prefix}{text}"))
    content = message_content_with_attachment_images(
        str(user_msg.get("text") or ""),
        user_msg.get("attachments") or [],
        max_images=int(settings.image_context_max_images),
        max_bytes=int(settings.image_context_max_bytes),
    )
    out.append(LLMMessage(role="user", content=content))
    return out


async def _thread_messages_for_live_prompt(repo: Repo, thread_id: str, *, minimum_limit: int = 500) -> list[dict[str, Any]]:
    limit = get_settings().chat_history_message_limit
    if limit <= 0:
        return await repo.all_thread_messages(thread_id)
    return await repo.thread_messages(thread_id, limit=max(minimum_limit, limit))


async def _chat_context_tokens_for_turn(
    dept: dict[str, Any],
    system: str,
    history: list[dict[str, Any]],
    user_msg: dict[str, Any],
    *,
    tools: list[dict[str, Any]] | None = None,
) -> tuple[int, str]:
    messages = _llm_chat_history(history, user_msg)
    fallback = estimate_llm_context_tokens(system, messages)
    try:
        provider = get_provider(str(dept.get("providerId") or "claude_code"), get_settings())
        model = str(dept.get("model") or DEFAULT_MODEL)
        tokens = await asyncio.wait_for(
            provider.count_context_tokens(
                system=system,
                messages=messages,
                model=model,
                effort=coerce_thinking_effort(model, str(dept.get("thinkingEffort") or "high")),
                speed=coerce_model_speed(model, str(dept.get("speed") or "standard")),
                tools=tools,
            ),
            timeout=8.0,
        )
        if tokens > 0:
            return tokens, "provider"
    except Exception:
        pass
    return fallback, "estimate"


def _replace_message(messages: list[dict[str, Any]], message: dict[str, Any]) -> list[dict[str, Any]]:
    replaced = False
    out: list[dict[str, Any]] = []
    for item in messages:
        if item.get("id") == message.get("id"):
            out.append(message)
            replaced = True
        else:
            out.append(item)
    if not replaced:
        out.append(message)
    return out


async def _apply_thread_cost(repo: Repo, thread_id: str, message: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    messages = await repo.thread_messages(thread_id, limit=500)
    summary = thread_cost_summary(thread_id, _replace_message(messages, message))
    return {**message, "threadCost": summary}, summary


async def _add_chat_system_line(
    repo: Repo,
    thread_id: str,
    text: str,
    *,
    activity: dict[str, Any] | None = None,
    department_id: str | None = None,
    flow: dict[str, Any] | None = None,
    war_room_id: str | None = None,
    meeting_id: str | None = None,
    severity: str = "info",
) -> dict[str, Any]:
    summary = thread_cost_summary(thread_id, await repo.thread_messages(thread_id, limit=500))
    msg = system_chat_message(
        thread_id,
        text,
        activity=activity,
        department_id=department_id,
        flow=flow,
        thread_cost=summary,
        war_room_id=war_room_id,
        meeting_id=meeting_id,
        severity=severity,
    )
    await repo.add_message(msg)
    hub.pulse({
        "kind": "chat_activity",
        "threadId": thread_id,
        "msgId": msg["id"],
        "departmentId": msg.get("departmentId"),
        "message": msg,
    })
    return msg


def _task_watch_flow(dept: dict[str, Any], task: dict[str, Any], event: str) -> dict[str, Any]:
    return {
        "kind": "department_work",
        "title": f"ฝ่าย{dept.get('name', dept.get('id'))}: {task.get('title')}",
        "steps": [
            {
                "kind": event,
                "label": event.replace("_", " "),
                "departmentId": dept.get("id"),
                "taskId": task.get("id"),
            }
        ],
        "refs": {
            "departmentId": dept.get("id"),
            "threadId": thread_id_for(str(dept.get("id") or "")),
            "taskId": task.get("id"),
            "status": task.get("status"),
            "progress": task.get("progress"),
        },
    }


async def _add_executive_watch_line(
    repo: Repo,
    dept: dict[str, Any],
    task: dict[str, Any] | None,
    text: str,
    *,
    event: str,
    severity: str = "info",
    now: int | None = None,
) -> None:
    if is_exec(str(dept.get("id") or "")):
        return
    flow = _task_watch_flow(dept, task, event) if task else {
        "kind": "department_work",
        "title": f"ฝ่าย{dept.get('name', dept.get('id'))}",
        "steps": [{"kind": event, "label": event.replace("_", " "), "departmentId": dept.get("id")}],
        "refs": {"departmentId": dept.get("id"), "threadId": thread_id_for(str(dept.get("id") or ""))},
    }
    await _add_chat_system_line(
        repo,
        EXEC_THREAD,
        text,
        department_id=str(dept.get("id") or ""),
        flow=flow,
        severity=severity,
    )


async def _add_executive_department_reply_line(
    repo: Repo,
    dept: dict[str, Any],
    reply: dict[str, Any],
    *,
    task: dict[str, Any] | None = None,
    now: int | None = None,
) -> None:
    status = str(reply.get("status") or "sent")
    if status == "sent":
        label = "ตอบกลับแล้ว"
        severity = "good"
    elif status in {"failed", "blocked", "cancelled"}:
        label = "ตอบกลับไม่สำเร็จ"
        severity = "warn"
    else:
        label = f"อัปเดตสถานะ {status}"
        severity = "info"
    text = _clip_text(str(reply.get("text") or ""), 360) or "-"
    await _add_executive_watch_line(
        repo,
        dept,
        task,
        f"ฝ่าย{dept.get('name', dept.get('id'))} {label}ในห้องแผนก: {text}",
        event="department_reply",
        severity=severity,
        now=now,
    )


async def _tool_activity_lines(
    repo: Repo,
    thread_id: str,
    active_dept: dict[str, Any],
    tool_runs: list[dict[str, Any]],
    *,
    war_room_id: str | None = None,
) -> None:
    for run in tool_runs:
        tool = str(run.get("tool") or "tool")
        status = str(run.get("status") or "unknown")
        ok = status == "succeeded"
        result = run.get("result") if isinstance(run.get("result"), dict) else {}
        result = result if isinstance(result, dict) else {}
        activity_type = "system"
        activity_dept = run.get("departmentId") or active_dept.get("id")
        flow = None
        text = f"{active_dept.get('agentName', active_dept.get('id'))} ใช้ tool {tool}: {status}"
        task = result.get("task") if isinstance(result.get("task"), dict) else None
        if tool == "create_task" and task:
            target = await repo.get_department(str(task.get("departmentId") or ""))
            if target:
                activity_type = "task_assigned"
                activity_dept = target["id"]
                flow = handoff_flow(active_dept, target, task)
                text = f"ผู้บริหาร -> มอบ -> ฝ่าย{target['name']}: “{task['title']}”"
                hub.pulse({
                    "kind": "handoff",
                    "departmentId": active_dept.get("id"),
                    "toDepartmentId": target["id"],
                    "taskId": task.get("id"),
                    "threadId": thread_id,
                })
        elif tool == "propose_org_plan" and result.get("orgPlan"):
            org_plan = result["orgPlan"]
            activity_type = "system"
            text = f"{active_dept.get('agentName')} apply org chart {len(org_plan.get('departments', []))} แผนกแบบ Full Auto"
        elif tool == "run_owner_tool" and result.get("run"):
            owner_run = result["run"]
            activity_type = "approval" if owner_run.get("status") == "pending_approval" else "system"
            activity_dept = owner_run.get("departmentId") or activity_dept
            text = f"{active_dept.get('agentName')} เรียก Owner Mode tool {owner_run.get('tool')}: {owner_run.get('status')}"
        elif result.get("summary"):
            text = f"{active_dept.get('agentName', active_dept.get('id'))} ใช้ tool {tool}: {result['summary']}"
        activity = _activity(
            text,
            type_=activity_type,
            department_id=activity_dept,
            severity="good" if ok else "warn",
        )
        activity["chatVisible"] = False
        await repo.add_activity(activity)
        await _add_chat_system_line(
            repo,
            thread_id,
            text,
            activity=activity,
            department_id=activity_dept,
            flow=flow,
            war_room_id=war_room_id,
            severity="good" if ok else "warn",
        )


async def _chat_memory_context(repo: Repo, dept: dict[str, Any], text: str) -> tuple[str, list[dict[str, Any]]]:
    from .memory.executive_retrieval import retrieval_context_for_department
    from .runtime.turns import runtime_recall_snippet

    context, hits = await retrieval_context_for_department(repo, dept, text)
    recall = await runtime_recall_snippet(dept, text)
    if recall:
        context = (context + "\n\n" + recall) if context else recall
    return context, hits


async def _complete_engine_chat_with_tools(
    repo: Repo,
    dept: dict[str, Any],
    *,
    system: str,
    history: list[dict[str, Any]],
    user_msg: dict[str, Any],
    thread_id: str,
    now: int,
    on_stream_event: Callable[[LLMStreamEvent], Awaitable[None]] | None = None,
    stream_msg_id: str | None = None,
) -> LLMResult | None:
    departments = await repo.list_departments()
    tools = (
        chat_tool_definitions(departments, dept)
        if should_enable_chat_tools(str(user_msg.get("text") or ""), dept)
        else []
    )
    if tools:
        system = f"{system}\n\n{chat_tool_system_instructions(departments, dept)}"
    messages = _llm_chat_history(history, user_msg)
    partials: list[LLMResult] = []
    tool_runs: list[dict[str, Any]] = []
    # งานจริงมีโอกาสวน tool loop มากกว่า 50 ครั้ง จึงไม่ควรใส่ max tool loop
    prev_had_text = False
    while True:
        if prev_had_text and on_stream_event is not None:
            # visually separate consecutive assistant turns in the bubble
            await on_stream_event(LLMStreamEvent(kind="text_delta", text="\n\n"))
        result = await _complete_engine_turn(
            repo,
            dept,
            category="chat",
            system=system,
            messages=messages,
            now=now,
            input_tokens=4500,
            on_stream_event=on_stream_event,
            tools=tools,
        )
        if not result:
            return None
        prev_had_text = bool(result.text and result.text.strip())
        partials.append(result)
        if not result.tool_calls:
            final = apply_result_totals(result, partials)
            final.meta["toolRuns"] = tool_runs
            return final

        messages.append(assistant_tool_message(result))
        round_records: list[dict[str, Any]] = []
        for call in result.tool_calls:
            if on_stream_event is not None and stream_msg_id:
                hub.pulse({
                    "kind": "tool_call",
                    "threadId": thread_id,
                    "msgId": stream_msg_id,
                    "run": {
                        "id": call.id,
                        "toolUseId": call.id,
                        "tool": call.name,
                        "departmentId": dept["id"],
                        "args": call.input or {},
                        "status": "running",
                        "startedAt": now_ms(),
                    },
                })
            async with session_scope() as s:
                record = await run_chat_tool(
                    Repo(s),
                    call,
                    active_dept=dept,
                    thread_id=thread_id,
                    requested_by=str(user_msg.get("authorName") or user_msg.get("author") or dept.get("agentName") or dept["id"]),
                )
            round_records.append(record)
            if on_stream_event is not None and stream_msg_id:
                hub.pulse({
                    "kind": "tool_result",
                    "threadId": thread_id,
                    "msgId": stream_msg_id,
                    "run": record,
                })
        tool_runs.extend(round_records)
        messages.append(tool_result_message(round_records))


async def _maybe_enqueue_chat_compaction(
    repo: Repo,
    *,
    thread_id: str,
    dept: dict[str, Any],
    message_count: int | None = None,
    estimated_context_tokens: int | None = None,
    context_token_source: str = "estimate",
    now: int,
) -> bool:
    settings = get_settings()
    model = str(dept.get("model") or DEFAULT_MODEL)
    compact_context_threshold = model_auto_compact_context_tokens(
        model,
        claude_threshold=int(settings.compact_claude_context_tokens),
        gpt_threshold=int(settings.compact_gpt_context_tokens),
        small_window_ratio=float(settings.compact_context_window_ratio),
    )
    context_tokens = int(estimated_context_tokens or 0)
    reason = ""
    if context_tokens > compact_context_threshold:
        reason = "context_threshold"
    else:
        threshold = int(settings.compact_message_threshold)
        count = int(message_count or 0)
        if threshold > 0 and count >= threshold and count % threshold == 0:
            reason = "message_threshold"
    if not reason:
        return False
    await repo.enqueue(
        uid("job"),
        "compact_dept",
        {
            "departmentId": dept["id"],
            "threadId": thread_id,
            "reason": reason,
            "estimatedContextTokens": context_tokens or None,
            "contextTokenSource": context_token_source,
            "compactContextThreshold": compact_context_threshold,
            "model": model,
        },
        now,
        priority=2,
    )
    metric = (
        f" context {context_tokens:,}/{compact_context_threshold:,} tokens"
        if context_tokens
        else ""
    )
    await repo.add_activity(_activity(
        f"คิว compact ความจำของฝ่าย{dept['name']}จาก thread ที่ยาวขึ้น{metric}",
        type_="compaction",
        department_id=dept["id"],
        severity="info",
        ts=now,
    ))
    return True


def _chat_reply_finished_state(dept: dict[str, Any]) -> str:
    return "working" if dept.get("currentTaskId") else "idle"


async def _process_chat_reply_job(repo: Repo, payload: dict[str, Any], now: int) -> None:
    thread_id = str(payload.get("threadId") or "")
    dept_id = str(payload.get("departmentId") or EXEC_ID)
    user_message_id = str(payload.get("userMessageId") or "")
    reply_message_id = str(payload.get("replyMessageId") or "")
    if not thread_id or not user_message_id or not reply_message_id:
        raise ValueError("chat_reply job missing thread/message ids")
    deferred_wake = payload.get("deferredWake") if isinstance(payload.get("deferredWake"), dict) else None
    image_generation_wake = payload.get("imageGenerationWake") if isinstance(payload.get("imageGenerationWake"), dict) else None
    video_job_wake = payload.get("videoJobWake") if isinstance(payload.get("videoJobWake"), dict) else None

    dept = await repo.get_department(dept_id) or await repo.get_department(EXEC_ID)
    if not dept:
        raise ValueError("chat_reply job has no available department")
    turn_thinking_effort = str(payload.get("thinkingEffort") or "") or None
    turn_speed = str(payload.get("speed") or "") or None
    war_room_id = str(payload.get("warRoomId") or war_room_id_from_thread(thread_id) or "")
    war_room: dict[str, Any] | None = None
    war_participants: list[dict[str, Any]] = []
    if war_room_id:
        war_room = await repo.get_entity("war_room", war_room_id)
        if war_room:
            war_participants = war_room_participants(war_room, await repo.list_departments())
    meeting_id = str(payload.get("meetingId") or meeting_id_from_thread(thread_id) or "")
    meeting: dict[str, Any] | None = None
    meeting_participant_rows: list[dict[str, Any]] = []
    if meeting_id:
        meeting = await repo.get_entity("meeting", meeting_id)
        if meeting:
            meeting_participant_rows = meeting_participants(meeting, await repo.list_departments())
    dept = await update_agent_state(repo, dept, "thinking", mood_delta=-0.01)
    turn_dept = _with_turn_speed(_with_turn_thinking_effort(dept, turn_thinking_effort), turn_speed)

    messages = await _thread_messages_for_live_prompt(repo, thread_id)
    existing_reply = next((m for m in messages if m.get("id") == reply_message_id), None)
    if existing_reply and not existing_reply.get("pending"):
        return

    user_msg = next((m for m in messages if m.get("id") == user_message_id), None)
    if not user_msg:
        user_msg = {
            "id": user_message_id,
            "threadId": thread_id,
            "role": "user",
            "authorName": "คุณ",
            "text": str(payload.get("text") or ""),
            "ts": int(payload.get("userTs") or now),
            "status": "sent",
        }
        await repo.add_message(user_msg)
        messages = [*messages, user_msg]

    history: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("id") == user_message_id:
            break
        history.append(msg)

    if not existing_reply:
        wake_status = str(payload.get("statusMessage") or "").strip()
        existing_reply = {
            "id": reply_message_id,
            "threadId": thread_id,
            "role": "executive" if is_exec(dept["id"]) else "agent",
            "authorName": dept["agentName"],
            "text": wake_status or (
                "กลับมาตรวจงานที่ตั้งเวลาไว้และกำลังทำต่อ..."
                if deferred_wake
                else "กำลังคิดและทำงานต่อในคิวเบื้องหลัง..."
            ),
            "ts": int(payload.get("replyTs") or now),
            "pending": True,
            "status": "queued",
            "replyToMessageId": user_message_id,
            **({"deferredWake": deferred_wake} if deferred_wake else {}),
            **({"imageGenerationWake": image_generation_wake} if image_generation_wake else {}),
            **({"videoJobWake": video_job_wake} if video_job_wake else {}),
            **agent_message_metadata(dept, war_room_id=war_room_id or None, meeting_id=meeting_id or None),
        }
        await repo.add_message(existing_reply)
    else:
        existing_reply = {
            **existing_reply,
            **agent_message_metadata(
                dept,
                war_room_id=war_room_id or existing_reply.get("warRoomId"),
                meeting_id=meeting_id or existing_reply.get("meetingId"),
            ),
        }

    settings = get_settings()
    runtime_retry_reason = (
        await _runtime_degraded_retry_reason(settings, turn_dept)
        if _uses_agent_runtime_for_chat(settings, turn_dept)
        else None
    )
    if runtime_retry_reason:
        retry_delay_ms = int(max(1.0, settings.runtime_degraded_retry_s) * 1000)
        retry_after = now + retry_delay_ms
        usage = {
            "usd": 0.0,
            "compactEnqueued": False,
            "thinkingEffort": turn_dept.get("thinkingEffort", "high"),
            "speed": turn_dept.get("speed", "standard"),
            "warnings": [
                {
                    "code": "runtime_degraded",
                    "message": runtime_retry_reason,
                    "severity": "warn",
                }
            ],
        }
        reply = {
            **existing_reply,
            "text": "Agent runtime ยังไม่พร้อม ระบบจะลองตอบให้อัตโนมัติอีกครั้งเมื่อ runtime กลับมาพร้อม",
            "pending": True,
            "status": "queued",
            "replyToMessageId": user_message_id,
            "runtime": {
                "backend": settings.agent_backend,
                "degraded": True,
                "retryAfter": retry_after,
            },
            "error": {
                "code": "runtime_degraded",
                "detail": runtime_retry_reason,
                "retryable": True,
                "retryAfter": retry_after,
            },
        }
        reply = ensure_rendering_metadata(reply, usage=usage, notices=["runtime_dependency"], severity="warn")
        await repo.update_message(reply)
        await update_agent_state(repo, dept, _chat_reply_finished_state(dept), mood_delta=0.0)
        hub.pulse({"kind": "msg_queued", "threadId": thread_id, "msgId": reply_message_id, "message": reply})
        raise _RetryJobLater(runtime_retry_reason, delay_ms=retry_delay_ms)

    safety_warnings = list(payload.get("safetyWarnings") or [])
    rate_limit = payload.get("rateLimit")
    budget = payload.get("budget")
    estimated_usd = _chat_cost_estimate(turn_dept)
    blocked = await _budget_block_reason(repo, turn_dept, estimated_usd)
    if blocked:
        usage = {
            "usd": 0.0,
            "compactEnqueued": False,
            "thinkingEffort": turn_dept.get("thinkingEffort", "high"),
            "speed": turn_dept.get("speed", "standard"),
            "warnings": [*safety_warnings, {"code": "budget_guardrail", "message": blocked, "severity": "alert"}],
            "rateLimit": rate_limit,
            "budget": budget,
        }
        suggestions = suggested_followups_for_response(
            dept,
            str(user_msg.get("text") or ""),
            blocked,
            mentions=user_msg.get("mentions") or [],
        )
        reply = {
            **existing_reply,
            "text": f"ยังไม่เรียกโมเดลเพราะ runtime dependency: {blocked}",
            "pending": False,
            "status": "blocked",
            "replyToMessageId": user_message_id,
            "error": {"code": "runtime_dependency", "detail": blocked, "retryable": True},
            "suggestedFollowUps": suggestions,
        }
        reply = ensure_rendering_metadata(reply, usage=usage, notices=["runtime_dependency"], severity="warn")
        reply, summary = await _apply_thread_cost(repo, thread_id, reply)
        usage["threadUsd"] = summary["totalUsd"]
        await repo.update_message(reply)
        await update_agent_state(repo, dept, _chat_reply_finished_state(dept), mood_delta=0.01)
        await repo.add_activity(_activity(
            f"runtime dependency บล็อกแชตของ{dept['agentName']}: {blocked}",
            type_="budget",
            department_id=dept["id"],
            severity="alert",
            ts=now,
        ))
        hub.pulse({"kind": "spend", "departmentId": dept["id"]})
        return

    departments = await repo.list_departments()
    memory_context, memory_hits = await _chat_memory_context(repo, dept, str(user_msg.get("text") or ""))
    tool_memory_context = await recent_tool_run_context(repo, dept, thread_id)
    if tool_memory_context:
        memory_context = (memory_context + "\n\n" if memory_context else "") + tool_memory_context
    if deferred_wake:
        wake_lines = [
            "Deferred continuation context:",
            f"- waitedSeconds={deferred_wake.get('delaySeconds')}",
            f"- reason={deferred_wake.get('reason') or '-'}",
            f"- instruction={deferred_wake.get('continueInstruction') or '-'}",
        ]
        tool_run_ids = deferred_wake.get("toolRunIds") if isinstance(deferred_wake.get("toolRunIds"), list) else []
        if tool_run_ids:
            wake_lines.append(f"- relatedToolRunIds={', '.join(str(item) for item in tool_run_ids)}")
        tool_run_context = await deferred_wake_tool_run_context(repo, deferred_wake)
        if tool_run_context:
            wake_lines.append(tool_run_context)
        wake_lines.append("Use available tools if you need to inspect listed paths or gather additional current status before answering.")
        memory_context = (memory_context + "\n\n" if memory_context else "") + "\n".join(wake_lines)
    if image_generation_wake:
        image_context = await image_generation_wake_context(repo, image_generation_wake)
        if image_context:
            memory_context = (memory_context + "\n\n" if memory_context else "") + image_context
    if video_job_wake:
        video_context = await video_job_wake_context(repo, video_job_wake)
        if video_context:
            memory_context = (memory_context + "\n\n" if memory_context else "") + video_context
    attached_context = await attachment_context(repo, user_msg.get("attachments") or payload.get("attachments") or [])
    if attached_context:
        memory_context = (memory_context + "\n\n" if memory_context else "") + "Attached user context:\n" + attached_context
    system = _chat_system_prompt(dept, memory_context)
    if war_room:
        system = f"{system}\n\n{war_room_context(war_room, war_participants)}"
    if meeting:
        system = f"{system}\n\n{meeting_context(meeting, meeting_participant_rows)}"
    runtime_tools = chat_tool_definitions(departments, turn_dept)
    tool_system = chat_tool_system_instructions(departments, turn_dept) if runtime_tools else ""
    estimated_context_tokens, context_token_source = await _chat_context_tokens_for_turn(
        turn_dept,
        f"{system}\n\n{tool_system}" if tool_system else system,
        history,
        user_msg,
        tools=runtime_tools,
    )
    cancel_event = chat_streams.start(thread_id, reply_message_id)
    sink = ChatMessageStreamSink(
        thread_id=thread_id,
        msg_id=reply_message_id,
        message=existing_reply,
        cancel_event=cancel_event,
        repo=repo,
    )
    await sink.start()
    stopped = False
    stream_error: str | None = None
    try:
        result = None
        runtime_system = (
            f"{system}\n\n{chat_tool_system_instructions(departments, turn_dept)}"
            if runtime_tools
            else system
        )
        use_agent_runtime = _uses_agent_runtime_for_chat(settings, turn_dept)
        if use_agent_runtime:
            result = await _complete_runtime_turn(
                repo,
                turn_dept,
                category="chat",
                system=runtime_system,
                messages=_llm_chat_history(history, user_msg),
                now=now,
                thread_id=thread_id,
                on_stream_event=sink.handle,
                input_tokens=4500,
                client_tools=runtime_tools,
                stream_msg_id=reply_message_id,
                requested_by=str(user_msg.get("authorName") or user_msg.get("author") or turn_dept.get("agentName") or turn_dept["id"]),
            )
        if result is None and not use_agent_runtime:
            result = await _complete_engine_chat_with_tools(
                repo,
                turn_dept,
                system=system,
                history=history,
                user_msg=user_msg,
                thread_id=thread_id,
                now=now,
                on_stream_event=sink.handle,
                stream_msg_id=reply_message_id,
            )
    except ChatStreamCancelled:
        stopped = True
        result = _partial_chat_result(turn_dept, sink.text, stop_reason="cancelled")
    except Exception as exc:
        error_type, stream_error = provider_exception_detail(exc)
        result = _partial_chat_result(
            turn_dept,
            sink.text,
            stop_reason="error",
            error_type=error_type,
            error_detail=stream_error,
        )
    finally:
        chat_streams.finish(thread_id, reply_message_id)

    if not result:
        usage = {"usd": 0.0, "compactEnqueued": False}
        reply = {**existing_reply, "text": "ยังไม่เรียกโมเดลเพราะ runtime guardrail"}
        result = _partial_chat_result(turn_dept, reply["text"], stop_reason="blocked")
        reply = await sink.finish(result=result, error="runtime_guardrail")
        reply["suggestedFollowUps"] = suggested_followups_for_response(
            dept,
            str(user_msg.get("text") or ""),
            reply.get("text") or "",
            mentions=user_msg.get("mentions") or [],
        )
        reply = ensure_rendering_metadata(reply, usage=usage, notices=["budget_guardrail"], severity="warn")
        reply, summary = await _apply_thread_cost(repo, thread_id, reply)
        usage["threadUsd"] = summary["totalUsd"]
        await repo.update_message(reply)
        await update_agent_state(repo, dept, _chat_reply_finished_state(dept), mood_delta=0.01)
        return

    _ensure_result_text_from_tools(result)
    reply = {
        **existing_reply,
        "role": "executive" if is_exec(dept["id"]) else "agent",
        "authorName": dept["agentName"],
        "text": result.text,
        "pending": False,
    }
    reply = await sink.finish(result=result, stopped=stopped, error=stream_error)
    runtime_dependency = bool(result.meta.get("runtimeDependency"))
    reply["status"] = "cancelled" if stopped else "failed" if stream_error else "blocked" if runtime_dependency else "sent"
    if runtime_dependency:
        reply["error"] = {
            "code": "runtime_dependency",
            "detail": str(result.meta.get("runtimeError") or "agent runtime unavailable"),
            "retryable": True,
        }
    reply["suggestedFollowUps"] = suggested_followups_for_response(
        dept,
        str(user_msg.get("text") or ""),
        result.text,
        mentions=user_msg.get("mentions") or [],
    )
    compact_enqueued = await _maybe_enqueue_chat_compaction(
        repo,
        thread_id=thread_id,
        dept=turn_dept,
        message_count=len(history) + 2,
        estimated_context_tokens=estimated_context_tokens,
        context_token_source=context_token_source,
        now=now,
    )
    result.meta["ragHitIds"] = [hit["id"] for hit in memory_hits]
    result.meta["compactEnqueued"] = compact_enqueued
    if result.meta.get("toolRuns"):
        reply["toolRuns"] = result.meta["toolRuns"]
        reply = _attach_tool_artifacts_to_message(reply, result.meta["toolRuns"])
    from .runtime.turns import runtime_result_metadata

    runtime_meta = runtime_result_metadata(result, turn_dept)
    if runtime_meta:
        reply["runtime"] = runtime_meta
    reply = {**reply, **agent_message_metadata(dept, war_room_id=war_room_id or None, meeting_id=meeting_id or None)}
    usage = {
        "usd": result.usd,
        "compactEnqueued": compact_enqueued,
        "thinkingEffort": turn_dept.get("thinkingEffort", "high"),
        "speed": result.speed or turn_dept.get("speed", "standard"),
        "toolRuns": result.meta.get("toolRuns", []),
        "warnings": safety_warnings,
        "rateLimit": rate_limit,
        "budget": budget,
    }
    reply = ensure_rendering_metadata(
        reply,
        usage=usage,
        citations=citation_chips(memory_hits, department_id=dept["id"]),
        notices=(
            (["runtime_dependency"] if runtime_dependency else [])
            + [
                item["code"]
            for item in safety_warnings
            if item.get("code") in {"rate_limit_warning", "budget_warning"}
            ]
        ),
        severity="warn" if safety_warnings or runtime_dependency else None,
    )
    reply, summary = await _apply_thread_cost(repo, thread_id, reply)
    usage["threadUsd"] = summary["totalUsd"]
    await repo.update_message(reply)
    if not is_exec(dept["id"]):
        current_task = await repo.get_task(str(dept.get("currentTaskId"))) if dept.get("currentTaskId") else None
        await _add_executive_department_reply_line(repo, dept, reply, task=current_task, now=now)
    if deferred_wake and deferred_wake.get("id"):
        await repo.put_entity(
            "deferred_chat_wake",
            {
                **deferred_wake,
                "status": reply.get("status") or "sent",
                "completedAt": now_ms(),
                "replyMessageId": reply.get("id") or deferred_wake.get("replyMessageId"),
            },
            dept=dept["id"],
            status=str(reply.get("status") or "sent"),
            ts=now_ms(),
        )
    await update_agent_state(repo, dept, _chat_reply_finished_state(dept), mood_delta=0.01)
    if memory_hits:
        await repo.add_activity(_activity(
            f"ดึง RAG {len(memory_hits)} รายการเข้าบริบทของ{dept['agentName']}",
            type_="system",
            department_id=dept["id"],
            severity="info",
            ts=now,
        ))
    await repo.add_activity(_activity(
        f"{dept['agentName']} ตอบกลับในแชต",
        type_="message",
        department_id=dept["id"],
        ts=now,
    ))
    await _tool_activity_lines(repo, thread_id, dept, result.meta.get("toolRuns", []), war_room_id=war_room_id or None)
    from .telegram_gateway import maybe_deliver_telegram_reply_for_message

    telegram_delivery = await maybe_deliver_telegram_reply_for_message(repo, reply)
    if telegram_delivery.get("status") in {"sent", "retry_queued", "failed"}:
        await repo.add_activity(_activity(
            f"Telegram outbound {telegram_delivery.get('status')} for chat reply",
            type_="system",
            department_id=dept["id"],
            severity="good" if telegram_delivery.get("status") == "sent" else "warn",
            ts=now_ms(),
        ))


def _target_department_id(target: str | None) -> str | None:
    if not target:
        return None
    if target.startswith("dept:"):
        return target.removeprefix("dept:")
    if target in {"company", "executive"}:
        return None
    return target


def _duration_ms(value: str | None, default: int) -> int:
    if not value:
        return default
    s = value.lower().strip()
    number_match = re.search(r"(\d+(?:\.\d+)?)", s)
    number = float(number_match.group(1)) if number_match else 1.0
    if number <= 0:
        return default
    if "minute" in s or " min" in s or s.endswith("min") or "นาที" in s:
        return int(number * 60_000)
    if "hour" in s or " hr" in s or s.endswith("hr") or "ชม" in s:
        return int(number * 3_600_000)
    if "weekly" in s or "week" in s or "สัปดาห์" in s:
        return int(number * 7 * DAY_MS)
    if "daily" in s or "day" in s or "วัน" in s:
        return int(number * DAY_MS)
    if number_match and number in {6.0, 12.0}:
        return int(number * 3_600_000)
    return default


def _cadence_ms(cadence: str) -> int:
    return cadence_interval_ms(cadence, DAY_MS) or DAY_MS


def _open_task_for(dept_id: str, tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    for task in tasks:
        if task.get("departmentId") == dept_id and task.get("status") in {"assigned", "backlog", "revising"}:
            return task
    return None


def _task_by_id(tasks: list[dict[str, Any]], task_id: str | None) -> dict[str, Any] | None:
    if not task_id:
        return None
    return next((task for task in tasks if task["id"] == task_id), None)


def _make_task(
    *,
    title: str,
    detail: str,
    dept_id: str,
    now: int,
    priority: str = "normal",
    origin: dict[str, Any] | None = None,
    log: list[str] | None = None,
    handoffs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": uid("task"),
        "title": title[:80],
        "detail": detail,
        "status": "assigned",
        "priority": priority,
        "departmentId": dept_id,
        "origin": origin or {"kind": "department", "id": dept_id},
        "progress": 0,
        "createdAt": now,
        "updatedAt": now,
        "handoffs": handoffs or [],
        "log": log or ["สร้างจาก engine"],
        "projectId": None,
        "deliverables": [],
        "watchers": ["executive"],
        "parentTaskId": None,
        "subTaskIds": [],
        "deadlineAt": None,
        "result": None,
    }


async def _record_handoff_message(
    repo: Repo,
    handoff: dict[str, Any],
    *,
    from_actor: str,
    author_name: str,
    act: str,
    text: str,
    now: int,
    task_id: str,
) -> dict[str, Any]:
    message = make_handoff_message(
        handoff,
        from_actor=from_actor,
        act=act,
        text=text,
        now=now,
        task_id=task_id,
    )
    await repo.put_entity(
        "handoff_message",
        message,
        dept=from_actor,
        status=handoff["id"],
        ts=now,
    )
    await repo.add_message(handoff_chat_message(message, author_name))
    return message


async def _accrue(repo: Repo, dept: dict[str, Any], category: str, amount: float, now: int) -> bool:
    company = await repo.get_company()
    if not company:
        return False
    applied = max(0.0, amount)
    if applied <= 0:
        return False
    await repo.add_cost(
        uid("cost"),
        now,
        dept["id"],
        category,
        applied,
        detail=f"engine:{dept.get('model')}:{dept.get('thinkingEffort')}:{dept.get('speed', 'standard')}",
        provider_id=dept.get("providerId"),
        model=dept.get("model"),
        speed=dept.get("speed", "standard"),
    )
    await _record_budget_exhaustion(repo, dept, now)
    return True


async def _enqueue_due_objectives(repo: Repo, now: int) -> int:
    count = 0
    objectives = await repo.list_objectives()
    for obj in objectives:
        if not obj.get("enabled") or not obj.get("nextRunAt") or obj["nextRunAt"] > now:
            continue
        cadence = _cadence_ms(obj.get("cadence", ""))
        scheduled_for = int(obj["nextRunAt"])
        due_runs: list[int] = []
        while scheduled_for <= now and len(due_runs) < MAX_TRIGGER_CATCH_UP_RUNS:
            due_runs.append(scheduled_for)
            scheduled_for += cadence
        if not due_runs:
            continue
        for run_at in due_runs:
            await repo.enqueue(
                uid("job"),
                "objective_run",
                {
                    "objectiveId": obj["id"],
                    "title": obj["title"],
                    "departmentId": obj["departmentId"],
                    "cadence": obj.get("cadence", ""),
                    "scheduledFor": run_at,
                    "catchUp": run_at < now,
                },
                now,
                priority=4,
            )
        obj["lastRunAt"] = due_runs[-1]
        obj["nextRunAt"] = due_runs[-1] + cadence
        await repo.save_objective(obj)
        count += len(due_runs)
    return count


async def _trigger_targets(repo: Repo, target: str | None) -> list[dict[str, Any]]:
    departments = await repo.list_departments()
    if target == "company":
        return [d for d in departments if not is_exec(d["id"])]
    dept_id = _target_department_id(target)
    if dept_id:
        dept = await repo.get_department(dept_id)
        return [dept] if dept else []
    if target == "executive":
        exec_dept = await repo.get_department(EXEC_ID)
        return [exec_dept] if exec_dept else []
    return []


def _trigger_wakes_executive(event: str | None) -> bool:
    return bool(event and event in EXECUTIVE_WAKE_EVENTS)


async def _trigger_assignees(repo: Repo, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("kind") == "event" and _trigger_wakes_executive(payload.get("event")):
        exec_dept = await repo.get_department(EXEC_ID)
        if exec_dept:
            return [exec_dept]
    return await _trigger_targets(repo, payload.get("target"))


def _task_has_escalated_handoff(task: dict[str, Any]) -> bool:
    waiting_on = task.get("waitingOn") or {}
    if waiting_on.get("dept") == "executive" and waiting_on.get("reason") == "handoff_guardrail":
        return True
    escalated_ids = {
        handoff.get("id")
        for handoff in task.get("handoffs", [])
        if handoff.get("status") == "escalated"
    }
    if not escalated_ids:
        return False
    return not waiting_on.get("handoffId") or waiting_on.get("handoffId") in escalated_ids


async def _handoff_escalation_ready(repo: Repo, targets: list[dict[str, Any]], last_run_at: int) -> bool:
    for dept in targets:
        for task in await repo.tasks_for_dept(dept["id"]):
            if int(task.get("updatedAt", 0)) <= last_run_at:
                continue
            if _task_has_escalated_handoff(task):
                return True
    return False


async def _trigger_event_ready(repo: Repo, trigger: dict[str, Any], now: int) -> bool:
    # Prevent event triggers from firing continuously while a condition remains true.
    if trigger.get("lastRunAt") and now - int(trigger["lastRunAt"]) < DAY_MS:
        return False
    targets = await _trigger_targets(repo, trigger.get("target"))
    if not targets:
        return False
    event = trigger.get("event")
    if event == "idle":
        idle_for_ms = _duration_ms(trigger.get("cadence"), 0)
        if idle_for_ms <= 0:
            return any(d.get("state") == "idle" for d in targets)

        ready = False
        for dept in targets:
            if dept.get("state") != "idle":
                if dept.get("idleSinceAt") is not None:
                    dept.pop("idleSinceAt", None)
                    await repo.save_department(dept)
                continue
            idle_since_raw = dept.get("idleSinceAt")
            try:
                idle_since = int(idle_since_raw) if idle_since_raw is not None else None
            except (TypeError, ValueError):
                idle_since = None
            if idle_since is None or idle_since > now:
                dept["idleSinceAt"] = now
                await repo.save_department(dept)
                continue
            if now - idle_since >= idle_for_ms:
                ready = True
        return ready
    if event == "escalate":
        return await _handoff_escalation_ready(repo, targets, int(trigger.get("lastRunAt") or 0))
    if event == "blocked":
        return any(d.get("state") == "blocked" for d in targets)
    if event == "budget":
        return await _budget_threshold_reached(repo)
    if event == "dept_done":
        for dept in targets:
            for task in await repo.tasks_for_dept(dept["id"]):
                if task.get("status") == "done" and int(task.get("updatedAt", 0)) > int(trigger.get("lastRunAt") or 0):
                    return True
    return False


async def _enqueue_due_triggers(repo: Repo, now: int) -> int:
    async with _TRIGGER_ENQUEUE_LOCK:
        count = 0
        triggers = await repo.list_entities("trigger", limit=1000)
        for trigger in triggers:
            if not trigger.get("enabled"):
                continue
            due_runs: list[int] = []
            if trigger.get("kind") == "cron":
                if repair_trigger_schedule(trigger, now=now):
                    await repo.put_entity("trigger", trigger, status=trigger.get("kind"), ts=now)
                next_run = trigger.get("nextRunAt")
                if next_run is not None and int(next_run) <= now:
                    if trigger.get("oneShotAt"):
                        due_runs = [int(next_run)]
                    else:
                        cadence = _cadence_ms(trigger.get("cadence") or "")
                        scheduled_for = int(next_run)
                        while scheduled_for <= now and len(due_runs) < MAX_TRIGGER_CATCH_UP_RUNS:
                            due_runs.append(scheduled_for)
                            scheduled_for += cadence
            elif trigger.get("kind") == "event":
                if await _trigger_event_ready(repo, trigger, now):
                    due_runs = [now]
            if not due_runs:
                continue
            for scheduled_for in due_runs:
                await repo.enqueue(
                    uid("job"),
                    "trigger_run",
                    {
                        "triggerId": trigger["id"],
                        "title": trigger["title"],
                        "target": trigger.get("target"),
                        "kind": trigger.get("kind"),
                        "cadence": trigger.get("cadence"),
                        "event": trigger.get("event"),
                        "scheduledFor": scheduled_for,
                        "catchUp": bool(trigger.get("kind") == "cron" and scheduled_for < now),
                    },
                    now,
                    priority=3,
                )
            trigger["lastRunAt"] = due_runs[-1]
            if trigger.get("kind") == "cron" and trigger.get("oneShotAt"):
                trigger["enabled"] = False
                trigger["nextRunAt"] = None
            elif trigger.get("kind") == "cron":
                trigger["nextRunAt"] = due_runs[-1] + _cadence_ms(trigger.get("cadence") or "")
            await repo.put_entity("trigger", trigger, status=trigger.get("kind"), ts=now)
            count += len(due_runs)
        return count


async def _process_task_review_reminder_job(repo: Repo, payload: dict[str, Any], now: int) -> None:
    task_id = str(payload.get("taskId") or "").strip()
    if not task_id:
        return
    task = await repo.get_task(task_id)
    if not task or task.get("status") in TASK_TERMINAL_STATUSES:
        return
    token = str(payload.get("reviewScheduleToken") or "").strip()
    if token != str(task.get("reviewScheduleToken") or "").strip():
        return
    interval_ms = normalize_review_interval_ms(task.get("reviewIntervalMs"))
    if not interval_ms:
        return
    next_review_at = int(task.get("nextReviewAt") or 0)
    if next_review_at > now + 5_000:
        await enqueue_task_review_reminder(repo, task, now=now)
        return

    dept = await repo.get_department(str(task.get("departmentId") or ""))
    if not dept:
        return
    progress = max(0.0, min(1.0, float(task.get("progress") or 0.0)))
    status = str(task.get("status") or "assigned")
    label = review_interval_label(interval_ms)
    text = (
        f"ถึงรอบตรวจงานทุก {label}: ฝ่าย{dept.get('name')}ยังมีงาน “{task.get('title')}” "
        f"สถานะ {status} คืบหน้า {round(progress * 100)}% ผู้บริหารควรเปิดดูและสานงานต่อถ้าจำเป็น"
    )
    await _add_executive_watch_line(
        repo,
        dept,
        task,
        text,
        event="task_review_reminder",
        severity="warn" if status in {"blocked", "review"} else "info",
        now=now,
    )
    await _notify(
        repo,
        type_="digest",
        severity="warn" if status in {"blocked", "review"} else "info",
        title=f"ถึงรอบตรวจงาน: {task.get('title')}",
        body=text,
        now=now,
        links=[f"atrium://task/{task_id}", f"atrium://department/{dept['id']}"],
    )
    task["lastReviewReminderAt"] = now
    task["reviewReminderCount"] = int(task.get("reviewReminderCount") or 0) + 1
    task["nextReviewAt"] = now + interval_ms
    task["updatedAt"] = now
    task.setdefault("log", []).append(f"ปลุกผู้บริหารตรวจงานตามรอบ {label}")
    await repo.save_task(task)
    await repo.add_activity(_activity(
        f"ปลุกผู้บริหารตรวจงาน “{task.get('title')}” ของฝ่าย{dept.get('name')}",
        type_="task_progress",
        department_id=dept["id"],
        severity="warn" if status in {"blocked", "review"} else "info",
        ts=now,
    ))
    await enqueue_task_review_reminder(repo, task, now=now)
    hub.pulse({"kind": "state", "departmentId": dept["id"], "taskId": task_id})


async def _process_due_job(repo: Repo, job, now: int) -> None:
    if job.kind == "objective_run":
        payload = job.payload or {}
        dept_id = payload.get("departmentId")
        if is_exec(dept_id):
            departments = [d for d in await repo.list_departments() if not is_exec(d["id"])]
            dept = departments[0] if departments else None
        else:
            dept = await repo.get_department(dept_id)
        if dept:
            scheduled_for = int(payload.get("scheduledFor") or now)
            catch_up = bool(payload.get("catchUp"))
            task = _make_task(
                title=payload.get("title", "งานตาม objective"),
                detail=(
                    f"งานประจำจาก objective: {payload.get('cadence', '')}"
                    f"\nscheduledFor={scheduled_for}"
                    + ("\ncatchUp=true" if catch_up else "")
                ),
                dept_id=dept["id"],
                now=now,
                priority="normal",
                origin={"kind": "executive"} if is_exec(dept_id) else {"kind": "department", "id": dept["id"]},
                log=[
                    "scheduler สร้างงานจาก objective",
                    f"objective scheduledFor={scheduled_for}",
                    "objective catch-up run" if catch_up else "objective on-time run",
                ],
            )
            await repo.save_task(task)
            await repo.add_activity(_activity(
                f"Scheduler สร้างงาน “{task['title']}” ให้ฝ่าย{dept['name']}",
                type_="autonomous",
                department_id=dept["id"],
                severity="good",
                ts=now,
            ))
            await _notify(
                repo,
                type_="digest",
                severity="warn" if catch_up else "info",
                title=f"Objective สร้างงาน: {task['title']}",
                body=(
                    f"Scheduler สร้างงานให้ฝ่าย{dept['name']}จาก objective {payload.get('objectiveId')}"
                    + (" หลัง catch-up รอบที่พลาด" if catch_up else "")
                ),
                now=now,
                links=[
                    f"atrium://task/{task['id']}",
                    f"atrium://objective/{payload.get('objectiveId')}",
                    f"atrium://department/{dept['id']}",
                ],
            )
            hub.pulse({"kind": "autonomous", "departmentId": dept["id"]})
    elif job.kind == "trigger_run":
        payload = job.payload or {}
        event = payload.get("event")
        observed_targets = await _trigger_targets(repo, payload.get("target"))
        observed_ids = [d["id"] for d in observed_targets if d]
        wake_executive = _trigger_wakes_executive(event)
        for dept in await _trigger_assignees(repo, payload):
            if not dept:
                continue
            scheduled_for = int(payload.get("scheduledFor") or now)
            catch_up = bool(payload.get("catchUp"))
            observed_detail = (
                f"\nobservedTarget={payload.get('target')}"
                + (f"\nobservedDepartments={','.join(observed_ids)}" if observed_ids else "")
            )
            task_log = [
                "scheduler สร้างงานจาก trigger",
                f"trigger scheduledFor={scheduled_for}",
                "trigger catch-up run" if catch_up else "trigger on-time run",
            ]
            if payload.get("kind") == "event":
                task_log.append(
                    "event trigger woke executive"
                    if wake_executive and is_exec(dept["id"])
                    else "event trigger assigned target"
                )
            task = _make_task(
                title=payload.get("title", "งานจาก trigger"),
                detail=(
                    f"งานจาก trigger {payload.get('triggerId')}"
                    + (f" ({event})" if event else "")
                    + f"\nscheduledFor={scheduled_for}"
                    + ("\ncatchUp=true" if catch_up else "")
                    + (observed_detail if wake_executive else "")
                ),
                dept_id=dept["id"],
                now=now,
                priority="high" if event in {"blocked", "budget", "escalate"} else "normal",
                origin={"kind": "executive"},
                log=task_log,
            )
            await repo.save_task(task)
            await repo.add_activity(_activity(
                f"Trigger สร้างงาน “{task['title']}” ให้ฝ่าย{dept['name']}",
                type_="autonomous",
                department_id=dept["id"],
                severity="good",
                ts=now,
            ))
            notif_type = {
                "budget": "budget",
                "blocked": "blocked",
                "escalate": "blocked",
                "dept_done": "task_done",
            }.get(event, "digest")
            severity = "warn" if notif_type in {"budget", "blocked"} or catch_up else "info"
            links = [
                f"atrium://task/{task['id']}",
                f"atrium://trigger/{payload.get('triggerId')}",
                f"atrium://department/{dept['id']}",
            ]
            for observed_id in observed_ids:
                link = f"atrium://department/{observed_id}"
                if link not in links:
                    links.append(link)
            if event == "budget" and "atrium://budget/company" not in links:
                links.append("atrium://budget/company")
            await _notify(
                repo,
                type_=notif_type,
                severity=severity,
                title=f"Trigger สร้างงาน: {task['title']}",
                body=(
                    (
                        f"Scheduler ปลุกผู้บริหารจาก event {event} ของ {', '.join(observed_ids) or payload.get('target')}"
                        if wake_executive and is_exec(dept["id"])
                        else f"Scheduler สร้างงานให้ฝ่าย{dept['name']}จาก trigger {payload.get('triggerId')}"
                    )
                    + (" หลัง catch-up รอบที่พลาด" if catch_up else "")
                ),
                now=now,
                links=links,
            )
            hub.pulse({"kind": "autonomous", "departmentId": dept["id"]})
    elif job.kind == "compact_dept":
        payload = job.payload or {}
        dept_id = payload.get("departmentId")
        dept = await repo.get_department(dept_id)
        if dept:
            await _compact_department(repo, dept, now, thread_id=payload.get("threadId"))
    elif job.kind == "reflect":
        from .learning.reflection import process_reflection_job

        await process_reflection_job(repo, job.payload or {})
    elif job.kind == "consolidate_memory":
        from .learning.consolidation import process_consolidation_job

        await process_consolidation_job(repo, now, job.payload or {})
    elif job.kind == "org_lifecycle":
        from .org.lifecycle import enqueue_org_lifecycle, process_org_lifecycle

        await process_org_lifecycle(repo, now)
        settings = get_settings()
        if settings.org_lifecycle_enabled:
            delay_ms = int(settings.org_lifecycle_interval_hours * 3600 * 1000)
            await enqueue_org_lifecycle(repo, run_after=now + delay_ms)
    elif job.kind == "chat_reply":
        await _process_chat_reply_job(repo, job.payload or {}, now)
    elif job.kind == TASK_REVIEW_REMINDER_KIND:
        await _process_task_review_reminder_job(repo, job.payload or {}, now)
    elif job.kind == "telegram_outbound":
        from .telegram_gateway import process_telegram_outbound_job

        await process_telegram_outbound_job(repo, job.payload or {}, now)
    elif job.kind == "telegram_progress":
        from .telegram_gateway import process_telegram_progress_job

        await process_telegram_progress_job(repo, job.payload or {}, now)
    elif job.kind == "image_generation":
        await process_image_generation_job(repo, job.payload or {}, now)
    elif job.kind == "video_tool":
        from .video_editing import process_video_job

        await process_video_job(repo, job.payload or {}, now)


async def _claim_due_jobs(
    repo: Repo,
    now: int,
    *,
    limit: int,
    kind: str | None = None,
    exclude_kinds: set[str] | None = None,
) -> list[Any]:
    async with _JOB_CLAIM_LOCK:
        claim_due_jobs = getattr(repo, "claim_due_jobs", None)
        if callable(claim_due_jobs):
            jobs = await claim_due_jobs(now, limit=limit, kind=kind, exclude_kinds=exclude_kinds)
        else:
            jobs = await repo.due_jobs(now, limit=limit, kind=kind, exclude_kinds=exclude_kinds)
            for job in jobs:
                await repo.mark_job(job.id, "running")
        if jobs:
            await commit_and_release(repo.s)
        return jobs


def _bounded_worker_concurrency(value: Any, *, default: int, ceiling: int) -> int:
    try:
        raw = int(value)
    except (TypeError, ValueError):
        raw = default
    return max(1, min(ceiling, raw or default))


def _job_record(job: Any) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(getattr(job, "id", "")),
        kind=str(getattr(job, "kind", "")),
        payload=dict(getattr(job, "payload", None) or {}),
    )


def _chat_job_department_id(job: Any) -> str:
    payload = dict(getattr(job, "payload", None) or {})
    return str(payload.get("departmentId") or EXEC_ID)


def _partition_parallel_chat_jobs(
    jobs: list[SimpleNamespace],
    *,
    limit: int,
    busy_department_ids: set[str] | None = None,
) -> tuple[list[SimpleNamespace], list[SimpleNamespace]]:
    busy = set(busy_department_ids or set())
    runnable: list[SimpleNamespace] = []
    deferred: list[SimpleNamespace] = []
    for job in jobs:
        dept_id = _chat_job_department_id(job)
        if len(runnable) >= limit or dept_id in busy:
            deferred.append(job)
            continue
        runnable.append(job)
        busy.add(dept_id)
    return runnable, deferred


async def _claim_due_job_records(
    now: int,
    *,
    kind: str,
    limit: int,
    exclude_kinds: set[str] | None = None,
) -> list[SimpleNamespace]:
    async with session_scope() as s:
        repo = Repo(s)
        jobs = await _claim_due_jobs(repo, now, limit=limit, kind=kind, exclude_kinds=exclude_kinds)
        return [_job_record(job) for job in jobs]


async def _requeue_claimed_job_records(records: list[SimpleNamespace], *, delay_ms: int) -> None:
    if not records:
        return
    run_after = now_ms() + max(1, int(delay_ms))
    async with session_scope() as s:
        repo = Repo(s)
        for job in records:
            await repo.mark_job(job.id, "queued", run_after=run_after)


async def _running_chat_reply_department_ids() -> set[str]:
    async with session_scope() as s:
        repo = Repo(s)
        jobs = await repo.active_jobs(limit=500)
    return {
        str((job.get("payload") or {}).get("departmentId") or EXEC_ID)
        for job in jobs
        if job.get("kind") == "chat_reply" and job.get("status") == "running"
    }


async def _process_claimed_job_record(job: SimpleNamespace, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    timeout_s = max(1.0, float(settings.engine_job_timeout_s))
    async with session_scope() as s:
        repo = Repo(s)
        try:
            await asyncio.wait_for(_process_due_job(repo, job, now_ms()), timeout=timeout_s)
            await repo.mark_job(job.id, "done")
            return 1
        except _RetryJobLater as exc:
            await repo.mark_job(job.id, "queued", error=exc.reason, run_after=now_ms() + exc.delay_ms)
        except asyncio.TimeoutError:
            await repo.mark_job(job.id, "failed", error=f"TimeoutError: job exceeded {timeout_s:g}s")
        except Exception as exc:  # pragma: no cover - defensive runtime path
            await repo.mark_job(job.id, "failed", error=f"{type(exc).__name__}: {exc}")
    return 0


async def _start_available_chat_reply_jobs(
    in_flight: dict[asyncio.Task[int], SimpleNamespace],
    settings: Settings,
) -> int:
    concurrency = _bounded_worker_concurrency(
        getattr(settings, "chat_reply_worker_concurrency", 1),
        default=1,
        ceiling=20,
    )
    available = max(0, concurrency - len(in_flight))
    started = 0
    if available:
        busy_department_ids = {
            *{_chat_job_department_id(job) for job in in_flight.values()},
            *await _running_chat_reply_department_ids(),
        }
        claim_limit = max(available, available * 3)
        claimed = await _claim_due_job_records(
            now_ms(),
            kind="chat_reply",
            limit=claim_limit,
        )
        runnable, deferred = _partition_parallel_chat_jobs(
            claimed,
            limit=available,
            busy_department_ids=busy_department_ids,
        )
        await _requeue_claimed_job_records(
            deferred,
            delay_ms=CHAT_REPLY_DEFER_COLLISION_MS,
        )
        for job in runnable:
            task = asyncio.create_task(_process_claimed_job_record(job, settings))
            in_flight[task] = job
        started = len(runnable)

    _CHAT_REPLY_WORKER_RUNTIME.update({
        "concurrency": concurrency,
        "inFlight": len(in_flight),
        "lastStartedAt": now_ms() if started else _CHAT_REPLY_WORKER_RUNTIME.get("lastStartedAt"),
        "lastBatchStarted": started,
    })
    return started


async def _process_due_jobs(
    repo: Repo,
    now: int,
    settings: Settings | None = None,
    *,
    kind: str | None = None,
    limit: int = 12,
) -> int:
    settings = settings or get_settings()
    processed = 0
    timeout_s = max(1.0, float(settings.engine_job_timeout_s))
    exclude_kinds = DEDICATED_WORKER_JOB_KINDS if kind is None else None
    jobs = await _claim_due_jobs(repo, now, limit=limit, kind=kind, exclude_kinds=exclude_kinds)
    for job in jobs:
        try:
            await asyncio.wait_for(_process_due_job(repo, job, now), timeout=timeout_s)
            await repo.mark_job(job.id, "done")
            processed += 1
        except _RetryJobLater as exc:
            await repo.mark_job(job.id, "queued", error=exc.reason, run_after=now_ms() + exc.delay_ms)
        except asyncio.TimeoutError:
            await repo.mark_job(job.id, "failed", error=f"TimeoutError: job exceeded {timeout_s:g}s")
        except Exception as exc:  # pragma: no cover - defensive runtime path
            await repo.mark_job(job.id, "failed", error=f"{type(exc).__name__}: {exc}")
    return processed


async def run_chat_reply_loop(settings: Settings | None = None) -> None:
    in_flight: dict[asyncio.Task[int], SimpleNamespace] = {}
    try:
        while True:
            await _engine_sleep(0.5)
            try:
                settings = get_settings()
                finished = 0
                for task in [task for task in in_flight if task.done()]:
                    in_flight.pop(task, None)
                    try:
                        finished += int(task.result() or 0)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # pragma: no cover - defensive runtime path
                        await _record_engine_loop_error(exc, now_ms())

                started = await _start_available_chat_reply_jobs(in_flight, settings)
                if finished:
                    _CHAT_REPLY_WORKER_RUNTIME.update({
                        "inFlight": len(in_flight),
                        "lastFinishedAt": now_ms(),
                    })

                async with session_scope() as s:
                    repo = Repo(s)
                    # Explicit chat replies should not freeze just because the
                    # autonomous company engine is paused from the UI.
                    processed = finished
                    processed += await _process_due_jobs(
                        repo,
                        now_ms(),
                        settings,
                        kind="telegram_outbound",
                        limit=6,
                    )
                    processed += await _process_due_jobs(
                        repo,
                        now_ms(),
                        settings,
                        kind="telegram_progress",
                        limit=6,
                    )
                    if processed or started:
                        hub.mark_dirty()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await _record_engine_loop_error(exc, now_ms())
    finally:
        for task in in_flight:
            task.cancel()
        if in_flight:
            await asyncio.gather(*in_flight.keys(), return_exceptions=True)
        _CHAT_REPLY_WORKER_RUNTIME.update({"inFlight": 0})


def _image_generation_timeout_s(settings: Settings) -> float | None:
    value = settings.image_generation_timeout_s
    if value is None or value <= 0:
        return None
    return max(1.0, float(value))


async def _claim_image_generation_records(settings: Settings, *, limit: int) -> list[SimpleNamespace]:
    async with session_scope() as s:
        repo = Repo(s)
        jobs = await _claim_due_jobs(repo, now_ms(), limit=limit, kind="image_generation")
        return [
            SimpleNamespace(id=job.id, kind=job.kind, payload=dict(job.payload or {}))
            for job in jobs
        ]


async def _process_claimed_image_generation_record(job: SimpleNamespace, settings: Settings) -> int:
    timeout_s = _image_generation_timeout_s(settings)
    async with session_scope() as s:
        repo = Repo(s)
        try:
            await asyncio.wait_for(_process_due_job(repo, job, now_ms()), timeout=timeout_s)
            await repo.mark_job(job.id, "done")
            return 1
        except RetryableImageGenerationError as exc:
            await repo.mark_job(job.id, "queued", error=str(exc), run_after=now_ms() + exc.delay_ms)
        except _RetryJobLater as exc:
            await repo.mark_job(job.id, "queued", error=exc.reason, run_after=now_ms() + exc.delay_ms)
        except asyncio.TimeoutError:
            label = "unbounded" if timeout_s is None else f"{timeout_s:g}s"
            error = f"TimeoutError: image job exceeded {label}"
            await mark_image_generation_job_failed(repo, job.payload or {}, error)
            await repo.mark_job(job.id, "failed", error=error)
        except Exception as exc:  # pragma: no cover - defensive runtime path
            await repo.mark_job(job.id, "failed", error=f"{type(exc).__name__}: {exc}")
    return 0


async def run_image_generation_loop(settings: Settings | None = None) -> None:
    while True:
        await _engine_sleep(0.5)
        try:
            settings = get_settings()
            async with session_scope() as s:
                repo = Repo(s)
                company = await repo.get_company()
                if not company or not company.running:
                    continue
            concurrency = max(1, min(10, int(settings.image_generation_worker_concurrency or 1)))
            records = await _claim_image_generation_records(settings, limit=concurrency)
            if not records:
                continue
            processed = sum(await asyncio.gather(
                *[_process_claimed_image_generation_record(job, settings) for job in records]
            ))
            if processed:
                hub.mark_dirty()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await _record_engine_loop_error(exc, now_ms())


async def run_trigger_scheduler_loop(settings: Settings | None = None) -> None:
    while True:
        await _engine_sleep(1.0)
        try:
            settings = get_settings()
            async with session_scope() as s:
                repo = Repo(s)
                company = await repo.get_company()
                if not company or not company.running:
                    continue
                now = now_ms()
                enqueued = await _enqueue_due_triggers(repo, now)
                processed = await _process_due_jobs(
                    repo,
                    now_ms(),
                    settings,
                    kind="trigger_run",
                    limit=5,
                )
                if enqueued or processed:
                    hub.mark_dirty()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await _record_engine_loop_error(exc, now_ms())


def _format_transcript(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        author = msg.get("authorName") or role
        text = str(msg.get("text") or "").replace("\r\n", "\n").strip()
        lines.append(f"[{msg.get('ts')}] {author} ({role}): {text}")
    return "\n".join(lines)


async def _compact_department(
    repo: Repo,
    dept: dict[str, Any],
    now: int,
    thread_id: str | None = None,
) -> bool:
    saved = 8000 + int(random.random() * 30000)
    messages = await repo.thread_messages(thread_id, limit=500) if thread_id else []
    transcript = _format_transcript(messages) if messages else None
    memory_context = await _task_memory_context(repo, dept, None)
    system = _department_system(
        dept,
        (
            "สกัดความจำจาก transcript/บริบทเป็น JSON เดียวเท่านั้น schema: "
            "{\"archiveSummary\":string,\"knowledge\":[{\"title\":string,\"text\":string,"
            "\"tags\":[string],\"score\":number}],\"graphNodes\":[{\"id\":string,\"label\":string,"
            "\"type\":\"concept\"|\"entity\"|\"task\"|\"person\"|\"artifact\","
            "\"validFrom\":number?,\"validTo\":number?,\"confidence\":number?,\"source\":string?}],"
            "\"graphEdges\":[{\"from\":string,\"to\":string,\"rel\":string,"
            "\"validFrom\":number?,\"validTo\":number?,\"confidence\":number?,\"source\":string?}]}. "
            "เก็บเฉพาะข้อเท็จจริงที่ใช้ซ้ำได้และอย่าเดาสิ่งที่ไม่มีในบริบท."
        ),
        memory_context,
    )
    result = await _complete_engine_turn(
        repo,
        dept,
        category="memory",
        system=system,
        messages=[LLMMessage(
            role="user",
            content=(
                f"department={dept['id']} thread={thread_id or 'recent-work'}\n"
                f"transcript:\n{_clip_text(transcript or 'ไม่มี transcript; compact สถานะงานล่าสุดของแผนกนี้', 30000)}"
            ),
        )],
        now=now,
        input_tokens=9000 if transcript else 3500,
    )
    data = _parse_json_object(result.text) if result else {}
    archive_summary = _clip_text(
        data.get("archiveSummary"),
        1600,
    ) or (
        f"ย่อบริบท thread {thread_id} พร้อมเก็บ transcript ต้นฉบับไว้ตรวจสอบย้อนหลัง"
        if thread_id else
        "ย่อบริบทล่าสุดเป็นประเด็นสำคัญ เก็บต้นฉบับไว้อ้างอิงได้"
    )
    archive = {
        "id": uid("arc"),
        "title": f"Transcript archive: {thread_id}" if thread_id else "บีบอัดบริบทการทำงาน",
        "ts": now,
        "tokens": saved,
        "summary": archive_summary,
        "threadId": thread_id,
        "messageCount": len(messages) if messages else None,
        "transcript": transcript[:60000] if transcript else None,
    }
    archive = attach_archive_audit(archive, department_id=dept["id"], source="compact")
    await repo.add_archive(dept["id"], archive)
    from .memory.ledger import record_compaction_ledger

    await record_compaction_ledger(
        repo,
        department_id=dept["id"],
        thread_id=thread_id,
        archive_id=archive["id"],
        transcript=archive.get("transcript"),
        summary=archive_summary,
    )
    extracted = normalize_compaction_extraction(
        data,
        department_id=dept["id"],
        archive_id=archive["id"],
        thread_id=thread_id,
        now_ms=now,
        id_factory=uid,
        fallback_title="บทเรียนจากงานล่าสุด",
        fallback_text=f"ฝ่าย{dept['name']}ควรแนบบริบทและเกณฑ์รับงานให้ชัดก่อนส่งต่อ เพื่อให้ทีมอื่นรับช่วงได้ทันที",
    )
    knowledge_items = extracted["knowledge"]
    embedder = await resolve_embedder()
    vecs = await embedder.embed([k["text"] for k in knowledge_items])
    for idx, knowledge in enumerate(knowledge_items):
        vector = vecs[idx] if idx < len(vecs) else None
        await repo.add_knowledge(
            dept["id"],
            knowledge,
            embedding=vector,
            source=f"compact:{archive['id']}",
            embedding_meta=embedding_metadata(embedder, vector),
        )
    for item in extracted["graphNodes"]:
        await repo.add_graph_node(
            dept["id"],
            item["id"],
            item["label"],
            item["type"],
            0.5,
            0.5,
            valid_from=item.get("validFrom"),
            valid_to=item.get("validTo"),
            confidence=item.get("confidence", 0.7),
            source=item.get("source"),
        )
    for edge in extracted["graphEdges"]:
        await repo.add_graph_edge(
            dept["id"],
            edge["from"],
            edge["to"],
            edge["rel"],
            valid_from=edge.get("validFrom"),
            valid_to=edge.get("validTo"),
            confidence=edge.get("confidence", 0.7),
            source=edge.get("source"),
        )
    graph_nodes, graph_edges = await repo.count_graph(dept["id"])
    old = dept.get("memory", {})
    dept["memory"] = {
        **old,
        "archiveChunks": await repo.count_archive(dept["id"]),
        "ragEntries": len(await repo.list_knowledge(dept["id"])),
        "graphNodes": graph_nodes,
        "graphEdges": graph_edges,
        "lastCompactionAt": now,
        "tokensSaved": old.get("tokensSaved", 0) + saved,
        "workingSummary": archive_summary,
        "workingArchiveId": archive["id"],
        "workingThreadId": thread_id,
        "workingMessageCount": len(messages) if messages else None,
        "workingUpdatedAt": now,
    }
    await repo.save_department(dept)
    await repo.add_activity(_activity(
        f"บีบอัดความจำด้วย LLM: เก็บ {len(knowledge_items)} knowledge จาก archive {archive['id']} · ประหยัด {saved / 1000:.1f}k โทเค็น",
        type_="compaction",
        department_id=dept["id"],
        severity="good",
        ts=now,
    ))
    hub.pulse({
        "kind": "compaction",
        "departmentId": dept["id"],
        "archiveId": archive["id"],
        "threadId": thread_id,
        "messageCount": archive["messageCount"],
        "tokensSaved": saved,
        "knowledgeCount": len(knowledge_items),
        "graphNodes": graph_nodes,
        "graphEdges": graph_edges,
    })
    return True


async def _create_approval(
    repo: Repo,
    dept: dict[str, Any],
    task: dict[str, Any],
    now: int,
    *,
    publish: bool,
    detail: str | None = None,
) -> None:
    """Compatibility audit record for legacy approval-shaped consumers.

    Full Auto has no approval gate. These records are written as already
    approved evidence so old drawers/probes can still show what happened.
    """
    approval = {
        "id": uid("apr"),
        "ts": now,
        "kind": "publish" if publish else "external_action",
        "title": f"Full Auto audit: {'เผยแพร่ผลงาน' if publish else 'action ภายนอก'}ของฝ่าย{dept['name']}",
        "detail": detail or f"{dept['agentName']} บันทึก action ภายนอกสำหรับ “{task['title']}”",
        "departmentId": dept["id"],
        "status": "approved",
        "resolvedBy": "full_auto",
        "resolvedAt": now,
    }
    await repo.add_approval(approval)
    await repo.add_activity(_activity(
        f"{dept['agentName']} บันทึก Full Auto external-action audit",
        type_="system",
        department_id=dept["id"],
        severity="info",
        ts=now,
    ))
    await _notify(
        repo,
        type_="security",
        severity="warn",
        title=approval["title"],
        body=approval["detail"],
        now=now,
        links=[
            f"atrium://approval/{approval['id']}",
            f"atrium://task/{task['id']}",
            f"atrium://department/{dept['id']}",
        ],
    )


async def _maybe_create_approval(repo: Repo, dept: dict[str, Any], task: dict[str, Any], now: int) -> None:
    if random.random() > 0.35:
        return
    await _create_approval(repo, dept, task, now, publish=random.random() < 0.5)


def _artifact_content_path(dept_id: str, artifact_id: str, version: int) -> str:
    path = (get_settings().workspace_dir / dept_id / "artifacts" / artifact_id / f"v{version}.md").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


async def _record_task_deliverable(
    repo: Repo,
    dept: dict[str, Any],
    task: dict[str, Any],
    now: int,
    *,
    content: str | None = None,
    decision: dict[str, Any] | None = None,
    artifact_status: str = "approved",
    decision_status: str = "approved",
    approved_by: str | None = "executive",
) -> str:
    artifact_id = uid("art")
    report = _clip_text(content, 30000) or (
        f"# สรุปผลงาน: {task['title']}\n\n"
        f"- แผนก: {dept['name']}\n"
        f"- สถานะ: เสร็จและพร้อมตรวจย้อนหลัง\n"
        f"- บันทึกงาน: {'; '.join(task.get('log', [])[-6:])}\n"
    )
    content_uri = _artifact_content_path(dept["id"], artifact_id, 1)
    with open(content_uri, "w", encoding="utf-8") as f:
        f.write(report)
    artifact = {
        "id": artifact_id,
        "name": f"สรุปผลงาน: {task['title']}",
        "kind": "report",
        "mime": "text/markdown",
        "ownerDept": dept["id"],
        "taskIds": [task["id"]],
        "projectId": task.get("projectId"),
        "version": 1,
        "status": artifact_status,
        "uri": content_uri,
        "tags": ["engine", "deliverable", dept["id"]],
        "links": [f"atrium://task/{task['id']}"],
        "preview": {"kind": "md", "uri": content_uri},
        "createdAt": now,
        "createdBy": dept["id"],
        "updatedAt": now,
        "updatedBy": dept["id"],
        "approvalTier": "user" if artifact_status == "in_review" else "department",
        "approvedBy": approved_by if artifact_status == "approved" else None,
        "approvedAt": now if artifact_status == "approved" else None,
    }
    version = {
        "id": f"{artifact_id}:1",
        "artifactId": artifact_id,
        "version": 1,
        "author": dept["id"],
        "ts": now,
        "note": "engine generated deliverable",
        "parent": None,
        "uri": content_uri,
    }
    decision_info = decision or {}
    decision_record = {
        "id": uid("dec"),
        "title": f"ปิดงาน “{task['title']}”",
        "proposedBy": dept["id"],
        "approvedBy": decision_info.get("approvedBy") or approved_by,
        "rationale": _clip_text(decision_info.get("rationale"), 1000)
        or "งานผ่าน review loop ภายในและมี deliverable ย้อนดูได้",
        "alternatives": [str(x)[:240] for x in decision_info.get("alternatives", []) if isinstance(x, str)][:6],
        "impact": _clip_text(decision_info.get("impact"), 1000)
        or "บันทึกเหตุผลการปิดงานและผูกผลงานกับ task เพื่อ audit ภายหลัง",
        "linkedTask": task["id"],
        "linkedArtifacts": [artifact_id],
        "status": decision_status,
        "supersedes": None,
        "ts": now,
    }
    final_done = artifact_status == "approved" and decision_status == "approved"
    notification = {
        "id": uid("notif"),
        "type": "task_done" if final_done else "approval",
        "severity": "good" if final_done else "warn",
        "title": f"งานเสร็จ: {task['title']}" if final_done else f"รอผู้ใช้อนุมัติ: {task['title']}",
        "body": (
            f"ฝ่าย{dept['name']}ส่งมอบ artifact และ decision log แล้ว"
            if final_done
            else f"ฝ่าย{dept['name']}ส่ง final deliverable ของโปรเจกต์ให้ผู้ใช้อนุมัติ"
        ),
        "ts": now,
        "read": False,
        "links": [f"atrium://task/{task['id']}", artifact["uri"], f"atrium://decision/{decision_record['id']}"],
    }
    await repo.put_entity("artifact", artifact, dept=dept["id"], project=task.get("projectId"), status=artifact_status, ts=now)
    await repo.put_entity(
        "artifact_version",
        version,
        dept=dept["id"],
        project=task.get("projectId"),
        status=artifact_status,
        ts=now,
    )
    await repo.put_entity("decision", decision_record, dept=dept["id"], project=task.get("projectId"), status=decision_status, ts=now)
    await repo.put_entity("notification", notification, status="unread", ts=now)
    task["deliverables"] = [*task.get("deliverables", []), artifact_id]
    task["result"] = {
        "summary": f"ฝ่าย{dept['name']}ส่งมอบรายงานสรุปและ decision log แล้ว",
        "artifactId": artifact_id,
        "decisionId": decision_record["id"],
        "completedAt": now,
    }
    task["log"] = [*task.get("log", []), f"ส่งมอบ artifact {artifact_id}"]
    parent_id = task.get("parentTaskId")
    if parent_id:
        parent = await repo.get_task(parent_id)
        if parent:
            children = list(parent.get("subTaskIds", []))
            if task["id"] not in children:
                children.append(task["id"])
            parent["subTaskIds"] = children
            parent["updatedAt"] = now
            parent["log"] = [*parent.get("log", []), f"subtask {task['id']} ส่งมอบ artifact {artifact_id}"]
            await repo.save_task(parent)
    return artifact_id


async def _task_close_approval_message(
    repo: Repo,
    approval: dict[str, Any],
    dept: dict[str, Any],
    task: dict[str, Any],
    now: int,
) -> None:
    msg = system_chat_message(
        thread_id_for(EXEC_ID),
        (
            f"รอผู้บริหารอนุมัติปิดงาน: “{task['title']}”\n"
            f"ฝ่าย{dept.get('name', dept['id'])}ส่งผลงานและขอปิดงาน\n"
            f"approval={approval['id']}\n"
            f"task={task['id']}"
        ),
        department_id=dept["id"],
        severity="warn",
        ts=now,
    )
    msg["id"] = f"msg_{approval['id']}"
    msg["status"] = "pending_approval"
    msg["approvalId"] = approval["id"]
    msg["approvalStatus"] = "pending"
    msg["input"] = {
        "status": "pending_approval",
        "routedDepartmentId": dept["id"],
        "taskId": task["id"],
        "approvalAction": "close_task",
    }
    await repo.add_message(msg)


def _pending_task_close_approval(approvals: list[dict[str, Any]], task_id: str) -> dict[str, Any] | None:
    for approval in approvals:
        action = approval.get("action") if isinstance(approval.get("action"), dict) else {}
        if (
            approval.get("status") == "pending"
            and action.get("action") == "close_task"
            and action.get("taskId") == task_id
        ):
            return approval
    return None


async def request_task_close_approval(
    repo: Repo,
    dept: dict[str, Any],
    task: dict[str, Any],
    now: int,
    *,
    content: str | None = None,
    decision: dict[str, Any] | None = None,
    source: str = "department_review",
) -> dict[str, Any]:
    existing = _pending_task_close_approval(await repo.list_approvals(), task["id"])
    if existing:
        return existing

    artifact_id = await _record_task_deliverable(
        repo,
        dept,
        task,
        now,
        content=content,
        decision=decision,
        artifact_status="in_review",
        decision_status="proposed",
        approved_by=None,
    )
    approval = {
        "id": uid("apr"),
        "ts": now,
        "kind": "task_close",
        "title": f"อนุมัติปิดงาน: {task['title']}",
        "detail": (
            f"ฝ่าย{dept.get('name', dept['id'])} / {dept.get('agentName', dept['id'])} "
            f"ขอปิดงาน “{task['title']}”. ตรวจ deliverable={artifact_id} และอนุมัติหรือส่งกลับแก้"
        ),
        "departmentId": dept["id"],
        "status": "pending",
        "action": {
            "action": "close_task",
            "departmentId": dept["id"],
            "projectId": task.get("projectId"),
            "artifactId": artifact_id,
            "taskId": task["id"],
            "requestedBy": dept["id"],
            "source": source,
        },
    }
    task["status"] = "review"
    task["progress"] = 1
    task["updatedAt"] = now
    task["pendingCloseApprovalId"] = approval["id"]
    task["waitingOn"] = {"dept": EXEC_ID, "approvalId": approval["id"], "reason": "task_close_approval"}
    task["result"] = {
        **(task.get("result") or {}),
        "summary": f"ฝ่าย{dept.get('name', dept['id'])}ขอปิดงาน รอผู้บริหารอนุมัติ",
        "artifactId": artifact_id,
        "approvalId": approval["id"],
        "reviewStatus": "pending_executive_approval",
        "completedAt": None,
    }
    task["log"] = [*task.get("log", []), f"ขอผู้บริหารอนุมัติปิดงาน approval={approval['id']}"]
    if dept.get("currentTaskId") == task["id"]:
        dept["currentTaskId"] = None
        if dept.get("state") not in {"offline", "blocked"}:
            dept["state"] = "idle"
    await repo.save_task(task)
    await repo.save_department(dept)
    await repo.add_approval(approval)
    await _task_close_approval_message(repo, approval, dept, task, now)
    await repo.add_activity(_activity(
        f"{dept.get('agentName', dept['id'])} ขออนุมัติปิดงาน “{task['title']}”",
        type_="approval",
        department_id=dept["id"],
        severity="warn",
        ts=now,
    ))
    hub.pulse({"kind": "state", "departmentId": dept["id"]})
    hub.pulse({"kind": "approval", "departmentId": dept["id"], "approvalId": approval["id"]})
    return approval


async def approve_task_close_request(
    repo: Repo,
    approval: dict[str, Any],
    *,
    approved_by: str,
    now: int,
) -> bool:
    action = approval.get("action") if isinstance(approval.get("action"), dict) else {}
    task_id = str(action.get("taskId") or "")
    task = await repo.get_task(task_id)
    if not task:
        raise ValueError(f"task not found: {task_id}")
    dept = await repo.get_department(task.get("departmentId"))
    if not dept:
        raise ValueError(f"department not found: {task.get('departmentId')}")
    artifact_id = str(action.get("artifactId") or (task.get("result") or {}).get("artifactId") or "")
    artifact = await repo.get_entity("artifact", artifact_id) if artifact_id else None
    if artifact:
        artifact["status"] = "approved"
        artifact["approvalTier"] = "executive"
        artifact["approvedBy"] = approved_by
        artifact["approvedAt"] = now
        artifact["updatedAt"] = now
        artifact["updatedBy"] = approved_by
        await repo.put_entity(
            "artifact",
            artifact,
            dept=artifact.get("ownerDept"),
            project=artifact.get("projectId"),
            status="approved",
            ts=now,
        )
    decision_id = (task.get("result") or {}).get("decisionId")
    decision = await repo.get_entity("decision", decision_id) if decision_id else None
    if decision:
        decision["status"] = "approved"
        decision["approvedBy"] = approved_by
        decision["ts"] = now
        await repo.put_entity(
            "decision",
            decision,
            dept=dept["id"],
            project=task.get("projectId"),
            status="approved",
            ts=now,
        )
    task["status"] = "done"
    task["progress"] = 1
    task["updatedAt"] = now
    task.pop("pendingCloseApprovalId", None)
    task.pop("waitingOn", None)
    task["result"] = {
        **(task.get("result") or {}),
        "summary": f"ผู้บริหารอนุมัติปิดงานของฝ่าย{dept.get('name', dept['id'])}",
        "artifactId": artifact_id or (task.get("result") or {}).get("artifactId"),
        "approvalId": approval["id"],
        "reviewStatus": "approved_by_executive",
        "completedAt": now,
    }
    task["log"] = [*task.get("log", []), f"ผู้บริหารอนุมัติปิดงาน approval={approval['id']}"]
    if _is_project_final_task(task) and task.get("projectId"):
        project = await repo.get_entity("project", task["projectId"])
        if project:
            project["status"] = "done"
            project["deliverableArtifactId"] = artifact_id or project.get("deliverableArtifactId")
            project["finalApprovalId"] = approval["id"]
            project["reviewStatus"] = "approved_by_executive"
            project["completedAt"] = now
            project["resolvedBy"] = approved_by
            await repo.put_entity("project", project, project=task["projectId"], status="done", ts=now)
    if dept.get("currentTaskId") == task["id"]:
        dept["currentTaskId"] = None
        if dept.get("state") not in {"offline", "blocked"}:
            dept["state"] = "idle"
    elif dept.get("state") == "review" and not dept.get("currentTaskId"):
        dept["state"] = "idle"
    await repo.save_task(task)
    await repo.save_department(dept)
    from .eval.scoring import record_task_outcome

    revision_count = sum(1 for line in task.get("log", []) if "review ไม่ผ่าน" in str(line))
    await record_task_outcome(
        repo,
        task_id=task["id"],
        department_id=dept["id"],
        outcome="done",
        revision_count=revision_count,
        accepted=True,
        skill_ids=[str(item) for item in task.get("activeSkillIds", []) if item],
    )
    await _enqueue_task_done_reflection(
        repo,
        dept,
        task,
        artifact_id=artifact_id or None,
        project_final=_is_project_final_task(task),
    )
    await repo.add_activity(_activity(
        f"ผู้บริหารอนุมัติปิดงาน “{task['title']}”",
        type_="task_done",
        department_id=dept["id"],
        severity="good",
        ts=now,
    ))
    await _add_executive_watch_line(
        repo,
        dept,
        task,
        f"ฝ่าย{dept.get('name', dept['id'])}ปิดงานสำเร็จ: “{task['title']}”",
        event="task_done",
        severity="good",
        now=now,
    )
    hub.pulse({"kind": "done", "departmentId": dept["id"]})
    return True


async def reject_task_close_request(
    repo: Repo,
    approval: dict[str, Any],
    *,
    rejected_by: str,
    reason: str | None,
    now: int,
) -> bool:
    action = approval.get("action") if isinstance(approval.get("action"), dict) else {}
    task_id = str(action.get("taskId") or "")
    task = await repo.get_task(task_id)
    if not task:
        raise ValueError(f"task not found: {task_id}")
    dept = await repo.get_department(task.get("departmentId"))
    if not dept:
        raise ValueError(f"department not found: {task.get('departmentId')}")
    note = _clip_text(reason, 800) or "ผู้บริหารส่งกลับให้แก้ก่อนปิดงาน"
    task["status"] = "revising"
    task["progress"] = min(float(task.get("progress", 1)), 0.92)
    task["updatedAt"] = now
    task.pop("pendingCloseApprovalId", None)
    task.pop("waitingOn", None)
    task["result"] = {
        **(task.get("result") or {}),
        "approvalId": approval["id"],
        "reviewStatus": "rejected_by_executive",
        "completedAt": None,
    }
    task["log"] = [*task.get("log", []), f"ผู้บริหารไม่อนุมัติปิดงาน: {note}"]
    if not dept.get("currentTaskId") and dept.get("state") not in {"offline", "blocked"}:
        dept["currentTaskId"] = task["id"]
        dept["state"] = "working"
    await repo.save_task(task)
    await repo.save_department(dept)
    from .learning.reflection import enqueue_reflection

    await enqueue_reflection(
        repo,
        department_id=dept["id"],
        source="reject",
        what_went_wrong=f"ผู้บริหารไม่อนุมัติปิดงาน: {note}",
        fallback_lesson="ก่อนขอปิดงาน ต้องแนบ deliverable, หลักฐาน, acceptance criteria และข้อจำกัดให้ครบ",
        task_id=task["id"],
        applied_to=["knowledge", "playbook"],
    )
    await repo.add_activity(_activity(
        f"ผู้บริหารส่งกลับแก้งาน “{task['title']}”",
        type_="approval",
        department_id=dept["id"],
        severity="warn",
        ts=now,
    ))
    await _add_executive_watch_line(
        repo,
        dept,
        task,
        f"ผู้บริหารส่งงานกลับให้ฝ่าย{dept.get('name', dept['id'])}แก้ต่อ: “{task['title']}” · {note}",
        event="task_revising",
        severity="warn",
        now=now,
    )
    hub.pulse({"kind": "state", "departmentId": dept["id"]})
    return True


def _is_project_final_task(task: dict[str, Any]) -> bool:
    return bool(task.get("projectId")) and not task.get("parentTaskId")


async def _request_project_final_approval(
    repo: Repo,
    dept: dict[str, Any],
    task: dict[str, Any],
    artifact_id: str,
    now: int,
) -> str:
    project_id = task["projectId"]
    artifact = await repo.get_entity("artifact", artifact_id)
    evidence_pack_id: str | None = None
    critique_report_id: str | None = None
    preview_available = False
    if artifact:
        preview_available = bool(artifact.get("preview"))
        evidence_pack_id = uid("evp")
        evidence = {
            "id": evidence_pack_id,
            "artifactId": artifact_id,
            "citations": [
                {
                    "source": link,
                    "url": link if str(link).startswith(("http://", "https://")) else None,
                    "quote": "linked by artifact",
                }
                for link in artifact.get("links", [])[:8]
            ],
            "rawNotes": (
                "Auto-generated by executive Full Auto review before project-final closure. "
                f"project={project_id}; task={task['id']}; department={dept['id']}"
            ),
            "confidence": 0.72 if artifact.get("links") else 0.55,
            "gaps": [] if artifact.get("links") else ["ยังไม่มี citation ภายนอกหรือไฟล์หลักฐานแนบกับ artifact"],
            "assumptions": ["งานย่อยผ่าน review ระดับผู้บริหารก่อนปิดแบบ Full Auto"],
            "methodology": "ATRIUM project-final Full Auto review: evidence pack + devil's advocate critique + preview check.",
        }
        await repo.put_entity("evidence_pack", evidence, dept=dept["id"], project=project_id, status=artifact_id, ts=now)
        critique_report_id = uid("crit")
        critique = {
            "id": critique_report_id,
            "targetType": "artifact",
            "targetId": artifact_id,
            "risks": [
                "final deliverable อาจยังไม่ตอบ acceptance criteria ครบทุกข้อ",
                "หลักฐานอาจไม่พอถ้าไม่มี citation หรือ preview ตรวจได้",
            ],
            "untestedAssumptions": [
                "ผู้ใช้ต้องการตรวจเฉพาะ deliverable รวม ไม่ใช่งานย่อยรายแผนก",
                "preview/content เป็น version ล่าสุด",
            ],
            "missedAlternatives": [
            "ส่งกลับให้แผนกแก้ก่อนปิด final deliverable",
                "เปิด war room/devil's advocate เพิ่มสำหรับงานเสี่ยงสูง",
            ],
            "openQuestions": [
                "มี gap ใน evidence pack ที่ควรแก้ก่อนส่งหรือไม่",
                "preview เปิดดูได้ครบหรือไม่",
            ],
            "ts": now,
        }
        await repo.put_entity("critique_report", critique, dept=dept["id"], project=project_id, status="artifact", ts=now)
        artifact["reviewGate"] = {
            "required": True,
            "reason": "project-final",
            "evidencePackId": evidence_pack_id,
            "critiqueReportId": critique_report_id,
            "previewAvailable": preview_available,
            "checkedAt": now,
        }
        artifact["tags"] = list(dict.fromkeys([*artifact.get("tags", []), "review-gated", "evidence-pack", "devils-advocate"]))
        await repo.put_entity(
            "artifact",
            artifact,
            dept=artifact.get("ownerDept"),
            project=project_id,
            status=artifact.get("status"),
            ts=now,
        )
    approval = {
        "id": uid("apr"),
        "ts": now,
        "kind": "publish",
        "title": f"Full Auto audit: deliverable รวมของโปรเจกต์: {task['title']}",
        "detail": (
            f"ฝ่าย{dept['name']}ส่ง final deliverable {artifact_id} ของโปรเจกต์ {project_id} "
            "เพื่อปิด project/main task แบบ Full Auto; "
            f"gate evidence={evidence_pack_id or '-'}, critique={critique_report_id or '-'}, preview={preview_available}"
        ),
        "departmentId": dept["id"],
        "status": "approved",
        "action": {
            "action": "resolve_project",
            "departmentId": dept["id"],
            "projectId": project_id,
            "artifactId": artifact_id,
            "taskId": task["id"],
            "requestedBy": dept["id"],
        },
    }
    project = await repo.get_entity("project", project_id)
    if project:
        project["status"] = "done"
        project["deliverableArtifactId"] = artifact_id
        project["finalApprovalId"] = approval["id"]
        project["reviewStatus"] = "approved_by_full_auto"
        project["completedAt"] = now
        project["resolvedBy"] = "full_auto"
        await repo.put_entity("project", project, project=project_id, status=project.get("status"), ts=now)
    await repo.add_approval(approval)
    await repo.add_activity(_activity(
        f"Full Auto audit final deliverable ของโปรเจกต์ {project_id}",
        type_="task_done",
        department_id=dept["id"],
        severity="good",
        ts=now,
    ))
    return approval["id"]


def _task_payload(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task["id"],
        "title": task.get("title"),
        "detail": task.get("detail"),
        "status": task.get("status"),
        "priority": task.get("priority"),
        "progress": task.get("progress"),
        "log": task.get("log", [])[-10:],
        "handoffs": task.get("handoffs", [])[-5:],
        "waitingOn": task.get("waitingOn"),
        "deliverables": task.get("deliverables", []),
        "draftDeliverableMarkdown": _clip_text(task.get("draftDeliverableMarkdown"), 12000),
    }


async def _llm_work_step(
    repo: Repo,
    dept: dict[str, Any],
    task: dict[str, Any],
    now: int,
) -> dict[str, Any] | None:
    memory_context = await _task_memory_context(repo, dept, task)
    from .learning.skills import retrieve_relevant_skill_matches

    skills_ctx, skill_ids = await retrieve_relevant_skill_matches(repo, dept, task)
    if skills_ctx:
        memory_context = (memory_context + "\n\n" + skills_ctx) if memory_context else skills_ctx
    system = _department_system(
        dept,
        (
            "ทำหนึ่ง work step ให้ task นี้ และตอบ JSON เดียวเท่านั้น schema: "
            "{\"progressDelta\":number,\"status\":\"in_progress\"|\"review\"|\"blocked\","
            "\"log\":string,\"needsApproval\":boolean,\"approvalKind\":\"publish\"|\"external_action\"|null,"
            "\"approvalDetail\":string|null,\"draftDeliverableMarkdown\":string|null}. "
            "progressDelta เป็น 0.04-0.35; ถ้างานพร้อมตรวจให้ status=review. "
            "log ต้องเป็นบันทึกสั้น ๆ ว่าทำอะไร/สถานะอะไร ไม่ต้องอธิบายเหตุผลหรือแผนยาว และไม่เกิน 160 ตัวอักษร. "
            "หากต้องเผยแพร่/เรียก API/ใช้เงิน/ทำ action ภายนอก ให้ระบุ needsApproval=true เพื่อให้ระบบสร้าง audit/checkpoint metadata; "
            "ไม่ต้องรอ approval gate."
        ),
        memory_context,
    )
    messages = [LLMMessage(role="user", content=json.dumps(_task_payload(task), ensure_ascii=False))]
    settings = get_settings()
    result = await _complete_runtime_turn(
        repo,
        dept,
        category="work",
        system=system,
        messages=messages,
        now=now,
        thread_id=f"task:{task['id']}",
        input_tokens=3500,
    )
    if result is None and not _uses_agent_runtime(settings, dept):
        result = await _complete_engine_turn(
            repo,
            dept,
            category="work",
            system=system,
            messages=messages,
            now=now,
        )
    if not result:
        return None
    data = _parse_json_object(result.text)
    if not data:
        data = {"log": result.text, "status": "in_progress", "progressDelta": 0.12}
    data["_rawText"] = result.text
    data["_skillIds"] = skill_ids
    return data


async def _llm_review_task(
    repo: Repo,
    dept: dict[str, Any],
    task: dict[str, Any],
    departments: list[dict[str, Any]],
    now: int,
) -> dict[str, Any] | None:
    memory_context = await _task_memory_context(repo, dept, task)
    peer_departments = [
        {"id": d["id"], "name": d.get("name"), "role": d.get("role")}
        for d in departments
        if not is_exec(d["id"]) and d["id"] != dept["id"]
    ]
    system = _department_system(
        dept,
        (
            "ตรวจงานก่อนปิด task และตอบ JSON เดียวเท่านั้น schema: "
            "{\"approved\":boolean,\"revisionNote\":string,\"deliverableMarkdown\":string,"
            "\"decisionRationale\":string,\"alternatives\":[string],\"impact\":string,"
            "\"handoffRecommendation\":{\"toDept\":string,\"kind\":\"delegate\"|\"consult\"|\"collaborate\"|\"return\","
            "\"reason\":string}|null}. "
            "ถ้ายังไม่ผ่าน ให้ approved=false และ revisionNote ระบุสิ่งที่ต้องแก้. "
            "ถ้าผ่าน deliverableMarkdown ต้องเป็นรายงาน markdown ที่เปิดดูย้อนหลังได้."
        ),
        memory_context,
    )
    payload = {"task": _task_payload(task), "peerDepartments": peer_departments}
    messages = [LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False))]
    settings = get_settings()
    result = await _complete_runtime_turn(
        repo,
        dept,
        category="work",
        system=system,
        messages=messages,
        now=now,
        thread_id=f"task:{task['id']}:review",
        input_tokens=5000,
    )
    if result is None and not _uses_agent_runtime(settings, dept):
        result = await _complete_engine_turn(
            repo,
            dept,
            category="work",
            system=system,
            messages=messages,
            now=now,
            input_tokens=5000,
        )
    if not result:
        return None
    data = _parse_json_object(result.text)
    if not data:
        data = {"approved": True, "deliverableMarkdown": result.text}
    data["_rawText"] = result.text
    return data


async def _enqueue_task_done_reflection(
    repo: Repo,
    dept: dict[str, Any],
    task: dict[str, Any],
    *,
    artifact_id: str | None = None,
    project_final: bool = False,
    run_after: int | None = None,
) -> str:
    """Schedule a success-path reflection from the durable task trajectory."""
    from .learning.reflection import enqueue_reflection

    scope = "final deliverable" if project_final else "task"
    revision_count = sum(1 for line in task.get("log", []) if "review ไม่ผ่าน" in str(line))
    title = _clip_text(task.get("title"), 180) or task.get("id") or "task"
    return await enqueue_reflection(
        repo,
        department_id=dept["id"],
        source="done",
        what_went_wrong=(
            f"{scope} ผ่าน review แล้ว: {title}. "
            f"สกัด pattern ที่ควรทำซ้ำจาก trajectory จริง; revisions={revision_count}."
        ),
        fallback_lesson=(
            "เมื่องานผ่าน review ให้บันทึกขั้นตอนที่ทำให้สำเร็จ หลักฐานที่ช่วยให้ผ่าน "
            "และ acceptance criteria ที่ควรใช้ซ้ำในงานลักษณะเดียวกัน"
        ),
        task_id=task.get("id"),
        artifact_id=artifact_id,
        applied_to=["knowledge", "playbook"],
        run_after=run_after,
    )


async def _llm_autonomous_task(
    repo: Repo,
    dept: dict[str, Any],
    tasks: list[dict[str, Any]],
    now: int,
) -> dict[str, Any] | None:
    memory_context = await _task_memory_context(repo, dept, None)
    open_titles = [
        {"title": t.get("title"), "status": t.get("status"), "priority": t.get("priority")}
        for t in tasks
        if t.get("departmentId") == dept["id"] and t.get("status") not in {"done", "cancelled"}
    ][:10]
    system = _department_system(
        dept,
        (
            "ริเริ่มงานใหม่ที่มีประโยชน์ต่อบริษัทจาก charter/ความจำ/งานค้าง และตอบ JSON เดียวเท่านั้น schema: "
            "{\"title\":string,\"detail\":string,\"priority\":\"low\"|\"normal\"|\"high\"|\"urgent\","
            "\"whyNow\":string,\"needsApproval\":boolean,\"approvalKind\":\"publish\"|\"external_action\"|null,"
            "\"approvalDetail\":string|null}. "
            "เลือกงานเล็กพอให้แผนกเริ่มเองได้และไม่ซ้ำกับงานค้าง. "
            "needsApproval เป็น legacy flag สำหรับ audit metadata เท่านั้น ไม่ใช่ gate."
        ),
        memory_context,
    )
    messages = [LLMMessage(
        role="user",
        content=json.dumps({"department": dept["id"], "openTasks": open_titles}, ensure_ascii=False),
    )]
    settings = get_settings()
    result = await _complete_runtime_turn(
        repo,
        dept,
        category="autonomous",
        system=system,
        messages=messages,
        now=now,
        thread_id=thread_id_for(dept["id"]),
        input_tokens=3500,
    )
    if result is None and not _uses_agent_runtime(settings, dept):
        result = await _complete_engine_turn(
            repo,
            dept,
            category="autonomous",
            system=system,
            messages=messages,
            now=now,
        )
    if not result:
        return None
    data = _parse_json_object(result.text)
    if not data:
        data = {"title": _clip_text(result.text, 80), "detail": result.text, "priority": "low"}
    data["_rawText"] = result.text
    return data


async def _create_handoff_task(
    repo: Repo,
    dept: dict[str, Any],
    target: dict[str, Any],
    task: dict[str, Any],
    *,
    reason: str,
    kind: str,
    now: int,
) -> dict[str, Any] | None:
    settings = get_settings()
    source_depths = [int(h.get("depth") or 0) for h in task.get("handoffs", [])]
    depth = (max(source_depths) if source_depths else 0) + 1
    normalized_kind = _choice(kind, {"delegate", "consult", "collaborate", "return"}, "delegate")
    consult_rounds = sum(
        1
        for h in task.get("handoffs", [])
        if h.get("fromDept") == dept["id"]
        and h.get("toDept") == target["id"]
        and h.get("kind") == "consult"
    )
    if depth > settings.max_handoff_depth or (
        normalized_kind == "consult" and consult_rounds >= settings.max_consult_rounds
    ):
        task["status"] = "blocked"
        task["waitingOn"] = {"dept": "executive", "handoffId": None, "reason": "handoff_guardrail"}
        task["updatedAt"] = now
        task["log"] = [*task.get("log", []), "handoff guardrail escalated to executive"]
        decision = {
            "id": uid("dec"),
            "title": f"Escalate handoff for {task['title']}",
            "proposedBy": dept["id"],
            "approvedBy": "executive",
            "rationale": (
                "handoff guardrail triggered: "
                f"depth={depth}, consultRounds={consult_rounds}, target={target['id']}"
            ),
            "alternatives": ["manual executive routing", "revise task scope"],
            "impact": "prevents autonomous handoff loops and waits for executive direction",
            "linkedTask": task["id"],
            "linkedArtifacts": [],
            "status": "approved",
            "supersedes": None,
            "ts": now,
        }
        await repo.put_entity("decision", decision, dept=dept["id"], status="approved", ts=now)
        await repo.save_task(task)
        await repo.add_activity(_activity(
            f"handoff guardrail escalated “{task['title']}” to executive",
            type_="handoff",
            department_id=dept["id"],
            severity="warn",
            ts=now,
        ))
        return None

    handoff = {
        "id": uid("ho"),
        "fromDept": dept["id"],
        "toDept": target["id"],
        "ts": now,
        "reason": reason,
        "kind": normalized_kind,
        "status": "open",
        "depth": depth,
        "contextPacketRef": None,
        "sourceTaskId": task["id"],
        "targetTaskId": None,
        "warRoomId": None,
        "messages": [],
    }
    next_task = _make_task(
        title=f"ต่อยอด: {task['title']}",
        detail=reason,
        dept_id=target["id"],
        now=now,
        priority=task.get("priority", "normal"),
        origin={"kind": "department", "id": dept["id"]},
        handoffs=[handoff],
        log=[f"รับ context packet จาก {dept['name']}"],
    )
    handoff["targetTaskId"] = next_task["id"]
    packet = (
        f"Context packet for task {task['id']}\n"
        f"จากฝ่าย{dept['name']}ถึงฝ่าย{target['name']}\n"
        f"kind: {handoff['kind']} depth: {handoff['depth']}\n"
        f"เหตุผล: {reason}\n"
        f"ผลงานที่เกี่ยวข้อง: {', '.join(task.get('deliverables', [])) or '-'}\n"
        f"บันทึกล่าสุด: {'; '.join(task.get('log', [])[-5:])}"
    )
    message = await _record_handoff_message(
        repo,
        handoff,
        from_actor=dept["id"],
        author_name=dept.get("agentName") or dept.get("name") or dept["id"],
        act="request",
        text=packet,
        now=now,
        task_id=task["id"],
    )
    handoff = append_handoff_message(handoff, message, status=handoff_status_for_act("request"))
    if handoff["kind"] == "collaborate":
        war_room = {
            "id": uid("war"),
            "title": f"War Room: {task['title'][:80]}",
            "goal": reason,
            "lead": dept["id"],
            "members": [dept["id"], target["id"]],
            "projectId": task.get("projectId"),
            "taskId": task["id"],
            "status": "active",
            "createdAt": now,
            "updatedAt": now,
            "scratchpad": packet,
        }
        await repo.put_entity("war_room", war_room, project=task.get("projectId"), status="active", ts=now)
        handoff["warRoomId"] = war_room["id"]
    next_task["handoffs"] = [handoff]
    task["handoffs"] = [*task.get("handoffs", []), handoff]
    if handoff["kind"] in {"consult", "collaborate"}:
        task["status"] = "blocked"
        task["progress"] = min(float(task.get("progress", 0.9)), 0.95)
        task["waitingOn"] = {"dept": target["id"], "handoffId": handoff["id"]}
        task["log"] = [*task.get("log", []), f"รอคำตอบจาก {target['name']} ({handoff['kind']})"]
    await repo.save_task(next_task)
    await repo.save_task(task)
    await repo.add_activity(_activity(
        f"ส่งต่องาน → {target['name']}: {reason}",
        type_="handoff",
        department_id=dept["id"],
        ts=now,
    ))
    hub.pulse({"kind": "handoff", "departmentId": dept["id"], "toDepartmentId": target["id"]})
    return next_task


async def _advance_department(repo: Repo, dept: dict[str, Any], tasks: list[dict[str, Any]], departments: list[dict[str, Any]], now: int) -> bool:
    changed = False
    state = dept.get("state")
    task = _task_by_id(tasks, dept.get("currentTaskId"))

    if state == "working":
        if not task:
            dept["state"] = "idle"
            dept["currentTaskId"] = None
            await repo.save_department(dept)
            return True
        step = await _llm_work_step(repo, dept, task, now)
        if step is None:
            return False
        status = _choice(step.get("status"), {"in_progress", "review", "blocked"}, "in_progress")
        log_line = _clip_text(step.get("log"), 160) or "LLM work step completed"
        task["progress"] = _clamp(
            float(task.get("progress", 0)) + _number(step.get("progressDelta"), 0.12, 0.04, 0.35),
            0,
            1,
        )
        task["updatedAt"] = now
        if step.get("draftDeliverableMarkdown"):
            task["draftDeliverableMarkdown"] = _clip_text(step["draftDeliverableMarkdown"], 30000)
        if step.get("_skillIds"):
            task["activeSkillIds"] = list(dict.fromkeys([*task.get("activeSkillIds", []), *step["_skillIds"]]))[:12]
        task["log"] = [*task.get("log", []), log_line]
        if step.get("needsApproval"):
            await _create_approval(
                repo,
                dept,
                task,
                now,
                publish=step.get("approvalKind") == "publish",
                detail=_clip_text(step.get("approvalDetail"), 1000) or None,
            )
        if status == "blocked":
            task["status"] = "blocked"
            dept["state"] = "blocked"
            dept["mood"] = min(float(dept.get("mood", 0.6)), 0.35)
            await repo.save_task(task)
            await repo.save_department(dept)
            await repo.add_activity(_activity(
                f"“{task['title']}” ติดบล็อก: {log_line}",
                type_="state_change",
                department_id=dept["id"],
                severity="warn",
                ts=now,
            ))
            await _add_executive_watch_line(
                repo,
                dept,
                task,
                f"ฝ่าย{dept.get('name', dept['id'])}ติดปัญหาในงาน “{task['title']}”: {log_line}",
                event="task_blocked",
                severity="warn",
                now=now,
            )
            hub.pulse({"kind": "state", "departmentId": dept["id"]})
            return True
        task["status"] = "review" if task["progress"] >= 1 or status == "review" else "in_progress"
        await repo.save_task(task)
        if task["status"] == "review":
            dept["state"] = "review"
            await repo.add_activity(_activity(f"“{task['title']}” พร้อมตรวจแล้ว", type_="task_progress", department_id=dept["id"], ts=now))
            await _add_executive_watch_line(
                repo,
                dept,
                task,
                f"ฝ่าย{dept.get('name', dept['id'])}ทำงาน “{task['title']}” ถึงจุดรอตรวจแล้ว: {log_line}",
                event="task_review",
                severity="good",
                now=now,
            )
            hub.pulse({"kind": "state", "departmentId": dept["id"]})
        else:
            await repo.add_activity(_activity(
                f"“{task['title']}” คืบหน้า {round(task['progress'] * 100)}%",
                type_="task_progress",
                department_id=dept["id"],
                ts=now,
            ))
            await _add_executive_watch_line(
                repo,
                dept,
                task,
                f"ฝ่าย{dept.get('name', dept['id'])}คืบหน้า {round(task['progress'] * 100)}% ในงาน “{task['title']}”: {log_line}",
                event="task_progress",
                severity="info",
                now=now,
            )
        dept["mood"] = _clamp(float(dept.get("mood", 0.7)) - 0.004, 0.15, 1)
        await repo.save_department(dept)
        return True

    if state == "review":
        if task:
            review = await _llm_review_task(repo, dept, task, departments, now)
            if review is None:
                return False
            approved = review.get("approved")
            if approved is False:
                note = _clip_text(review.get("revisionNote"), 800) or "review ขอแก้ก่อนปิดงาน"
                task["status"] = "revising"
                task["progress"] = min(float(task.get("progress", 0.7)), 0.88)
                task["updatedAt"] = now
                task["log"] = [*task.get("log", []), f"review ไม่ผ่าน: {note}"]
                dept["state"] = "working"
                await repo.save_task(task)
                await repo.save_department(dept)
                revision_count = sum(1 for line in task.get("log", []) if "review ไม่ผ่าน" in str(line))
                from .eval.scoring import record_task_outcome
                from .learning.reflection import enqueue_reflection

                await record_task_outcome(
                    repo,
                    task_id=task["id"],
                    department_id=dept["id"],
                    outcome="revising",
                    revision_count=revision_count,
                    accepted=False,
                    skill_ids=[str(item) for item in task.get("activeSkillIds", []) if item],
                    detail=note,
                )
                await enqueue_reflection(
                    repo,
                    department_id=dept["id"],
                    source="reject",
                    what_went_wrong=f"review ไม่ผ่าน: {note}",
                    fallback_lesson="ก่อนส่ง review ต้องเทียบ acceptance criteria, แนบ preview และหลักฐานให้ครบ",
                    task_id=task["id"],
                    applied_to=["knowledge", "playbook"],
                )
                await repo.add_activity(_activity(
                    f"“{task['title']}” ต้องแก้ก่อนปิดงาน",
                    type_="task_progress",
                    department_id=dept["id"],
                    severity="warn",
                    ts=now,
                ))
                hub.pulse({"kind": "state", "departmentId": dept["id"]})
                return True

            report = (
                _clip_text(review.get("deliverableMarkdown"), 30000)
                or _clip_text(task.get("draftDeliverableMarkdown"), 30000)
                or _clip_text(review.get("_rawText"), 30000)
            )
            task["progress"] = 1
            task["updatedAt"] = now
            decision_payload = {
                "rationale": review.get("decisionRationale") or review.get("rationale"),
                "alternatives": _json_list(review, "alternatives"),
                "impact": review.get("impact"),
            }
            await request_task_close_approval(
                repo,
                dept,
                task,
                now,
                content=report,
                decision=decision_payload,
                source="engine_review",
            )
        dept["state"] = "idle"
        dept["currentTaskId"] = None
        dept["mood"] = _clamp(float(dept.get("mood", 0.7)) + 0.05, 0, 1)
        await repo.save_department(dept)
        return True

    if state == "handoff":
        dept["state"] = "idle"
        await repo.save_department(dept)
        return True

    if state == "thinking":
        dept["state"] = "idle"
        await repo.save_department(dept)
        return True

    if state == "blocked":
        if random.random() < 0.25:
            dept["state"] = "working" if dept.get("currentTaskId") else "idle"
            dept["mood"] = _clamp(float(dept.get("mood", 0.4)) + 0.08, 0, 1)
            await repo.save_department(dept)
            await repo.add_activity(_activity(
                f"{dept['agentName']} กลับมาทำงานต่อได้แล้ว",
                type_="state_change",
                department_id=dept["id"],
                severity="good",
                ts=now,
            ))
            hub.pulse({"kind": "state", "departmentId": dept["id"]})
            return True
        return False

    open_task = _open_task_for(dept["id"], tasks)
    if open_task:
        open_task["status"] = "in_progress"
        open_task["updatedAt"] = now
        open_task["log"] = [*open_task.get("log", []), "เริ่มลงมือ"]
        dept["currentTaskId"] = open_task["id"]
        dept["state"] = "working"
        await repo.save_task(open_task)
        await repo.save_department(dept)
        await repo.add_activity(_activity(
            f"{dept['agentName']} เริ่มงาน “{open_task['title']}”",
            type_="task_assigned",
            department_id=dept["id"],
            ts=now,
        ))
        await _add_executive_watch_line(
            repo,
            dept,
            open_task,
            f"ฝ่าย{dept.get('name', dept['id'])}เริ่มทำงาน “{open_task['title']}”",
            event="task_started",
            severity="good",
            now=now,
        )
        hub.pulse({"kind": "state", "departmentId": dept["id"]})
        return True

    if dept.get("autonomy") and random.random() < 0.08:
        proposal = await _llm_autonomous_task(repo, dept, tasks, now)
        if proposal is None:
            return False
        ideas = AUTONOMY_IDEAS.get(dept["id"], ["ปรับปรุงงานในความดูแล"])
        title = _clip_text(proposal.get("title"), 80) or random.choice(ideas)
        task = _make_task(
            title=title,
            detail=_clip_text(proposal.get("detail"), 1200) or "งานที่ฝ่ายริเริ่มเองตามวัตถุประสงค์ที่ตั้งไว้",
            dept_id=dept["id"],
            now=now,
            priority=_choice(proposal.get("priority"), {"low", "normal", "high", "urgent"}, "low"),
            origin={"kind": "department", "id": dept["id"]},
            log=[
                "LLM ริเริ่มงานนี้เอง",
                _clip_text(proposal.get("whyNow"), 500) or "เลือกจาก charter และงานค้างของแผนก",
            ],
        )
        await repo.save_task(task)
        if proposal.get("needsApproval"):
            await _create_approval(
                repo,
                dept,
                task,
                now,
                publish=proposal.get("approvalKind") == "publish",
                detail=_clip_text(proposal.get("approvalDetail"), 1000) or None,
            )
        await repo.add_activity(_activity(
            f"{dept['agentName']} เริ่มงานเอง: “{task['title']}”",
            type_="autonomous",
            department_id=dept["id"],
            severity="good",
            ts=now,
        ))
        await _add_executive_watch_line(
            repo,
            dept,
            task,
            f"ฝ่าย{dept.get('name', dept['id'])}ริเริ่มงานเอง: “{task['title']}”",
            event="task_autonomous",
            severity="good",
            now=now,
        )
        hub.pulse({"kind": "autonomous", "departmentId": dept["id"]})
        return True

    if random.random() < 0.05:
        dept["state"] = "thinking"
        await repo.save_department(dept)
        return True

    return changed


async def _advance_department_in_session(
    dept_id: str,
    *,
    departments: list[dict[str, Any]],
    now: int,
) -> bool:
    async with session_scope() as s:
        repo = Repo(s)
        dept = await repo.get_department(dept_id)
        if not dept or is_exec(str(dept.get("id") or "")):
            return False
        tasks = await repo.list_active_tasks(limit=1000)
        return await _advance_department(repo, dept, tasks, departments, now)


async def _advance_departments_parallel(
    departments: list[dict[str, Any]],
    now: int,
    settings: Settings | None = None,
) -> int:
    settings = settings or get_settings()
    targets = [dept for dept in departments if not is_exec(str(dept.get("id") or ""))]
    if not targets:
        _DEPARTMENT_WORKER_RUNTIME.update({
            "concurrency": _bounded_worker_concurrency(
                getattr(settings, "department_worker_concurrency", 1),
                default=1,
                ceiling=20,
            ),
            "inFlight": 0,
            "lastBatchStarted": 0,
        })
        return 0
    concurrency = _bounded_worker_concurrency(
        getattr(settings, "department_worker_concurrency", 1),
        default=1,
        ceiling=20,
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(dept: dict[str, Any]) -> bool:
        async with semaphore:
            return await _advance_department_in_session(str(dept["id"]), departments=departments, now=now)

    started_at = now_ms()
    _DEPARTMENT_WORKER_RUNTIME.update({
        "concurrency": concurrency,
        "inFlight": len(targets),
        "lastStartedAt": started_at,
        "lastBatchStarted": len(targets),
    })
    try:
        results = await asyncio.gather(*(run_one(dept) for dept in targets), return_exceptions=True)
    finally:
        _DEPARTMENT_WORKER_RUNTIME.update({
            "inFlight": 0,
            "lastFinishedAt": now_ms(),
        })
    changed = 0
    for result in results:
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, Exception):
            await _record_engine_loop_error(result, now_ms())
            continue
        if result:
            changed += 1
    return changed


def _next_executive_summary_at(now: int) -> int:
    jitter = int((random.random() * 2 - 1) * EXECUTIVE_SUMMARY_JITTER_MS)
    return now + EXECUTIVE_SUMMARY_INTERVAL_MS + jitter


def _valid_ms(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return None


def _executive_summary_schedule(exec_dept: dict[str, Any], now: int) -> tuple[bool, bool]:
    next_run = _valid_ms(exec_dept.get(EXECUTIVE_SUMMARY_NEXT_RUN_KEY))
    if next_run is None:
        exec_dept[EXECUTIVE_SUMMARY_NEXT_RUN_KEY] = _next_executive_summary_at(now)
        return False, True
    if next_run > now:
        return False, False
    exec_dept[EXECUTIVE_SUMMARY_LAST_ATTEMPT_KEY] = now
    exec_dept[EXECUTIVE_SUMMARY_NEXT_RUN_KEY] = _next_executive_summary_at(now)
    return True, True


async def _executive_cadence(repo: Repo, departments: list[dict[str, Any]], now: int) -> bool:
    exec_dept = next((d for d in departments if is_exec(d["id"])), None)
    if not exec_dept:
        return False
    due, schedule_changed = _executive_summary_schedule(exec_dept, now)
    if not due:
        if schedule_changed:
            await repo.save_department(exec_dept)
        return False
    exec_dept["state"] = "thinking" if exec_dept.get("state") != "thinking" else "working"
    await repo.save_department(exec_dept)
    tasks = await repo.list_tasks(limit=500, newest_first=True)
    approvals = await repo.list_approvals(limit=200)
    budget = await repo.get_budget()
    system = _department_system(
        exec_dept,
        (
            "สรุปความคืบหน้าอัตโนมัติให้เจ้าของบริษัทเป็นภาษาไทย 1-2 ประโยค. "
            "อ้างอิงสถานะจริงจาก tasks/approvals/budget เท่านั้น; ถ้ามีความเสี่ยงหรือ approval ค้างให้บอกให้ชัด."
        ),
    )
    result = await _complete_engine_turn(
        repo,
        exec_dept,
        category="chat",
        system=system,
        messages=[LLMMessage(role="user", content=json.dumps({
            "departments": [
                {"id": d["id"], "name": d.get("name"), "state": d.get("state"), "currentTaskId": d.get("currentTaskId")}
                for d in departments
            ],
            "openTasks": [
                {"id": t["id"], "title": t.get("title"), "status": t.get("status"), "departmentId": t.get("departmentId"), "progress": t.get("progress")}
                for t in tasks
                if t.get("status") not in {"done", "cancelled"}
            ][:16],
            "pendingApprovals": [a for a in approvals if a.get("status") == "pending"][:8],
            "budget": budget,
        }, ensure_ascii=False))],
        now=now,
        input_tokens=5000,
    )
    if result is None:
        return False
    text = _clip_text(result.text, 1400) or random.choice(EXEC_LINES)
    msg = {
        "id": uid("msg"),
        "threadId": "executive",
        "role": "executive",
        "authorName": exec_dept["agentName"],
        "text": text,
        "ts": now,
    }
    await repo.add_message(msg)
    await repo.add_activity(_activity("ผู้บริหารสรุปความคืบหน้าอัตโนมัติด้วย LLM", type_="message", department_id=EXEC_ID, ts=now))
    hub.pulse({"kind": "state", "departmentId": EXEC_ID})
    return True


async def run_engine_tick(
    *,
    force_compact: bool = False,
    compact_department_id: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    now = now_ms()
    _set_engine_runtime(state="ticking", lastTickStartedAt=now, lastError=None)
    changed = False
    stats = {
        "objectiveJobs": 0,
        "triggerJobs": 0,
        "jobs": 0,
        "departments": 0,
        "notifications": 0,
        "compactions": 0,
        "running": True,
    }

    try:
        async with session_scope() as s:
            repo = Repo(s)
            company = await repo.get_company()
            if not company:
                stats = {**stats, "running": False}
                return stats
            if not company.running:
                stats = {**stats, "running": False}
                return stats

            _set_engine_phase("enqueue_objectives")
            stats["objectiveJobs"] = await _enqueue_due_objectives(repo, now)
            _set_engine_phase("enqueue_triggers")
            stats["triggerJobs"] = await _enqueue_due_triggers(repo, now)
            _set_engine_phase("process_due_jobs")
            stats["jobs"] = await _process_due_jobs(repo, now, settings)
            changed = changed or bool(stats["objectiveJobs"] or stats["triggerJobs"] or stats["jobs"])

            departments = await repo.list_departments()
            _set_engine_phase("advance_departments_parallel")
            await commit_and_release(repo.s)
            stats["departments"] = await _advance_departments_parallel(departments, now, settings)
            changed = changed or bool(stats["departments"])

            _set_engine_phase("knowledge_debt")
            if await _record_knowledge_debt_notifications(repo, departments, now):
                changed = True

            _set_engine_phase("operational_notifications")
            stats["notifications"] = await _record_daily_operational_notifications(repo, departments, now)
            changed = changed or bool(stats["notifications"])

            _set_engine_phase("executive_cadence", dept=await repo.get_department(EXEC_ID))
            if await _executive_cadence(repo, departments, now):
                changed = True

            should_compact = force_compact or random.random() < 0.04
            if should_compact:
                if compact_department_id:
                    selected = await repo.get_department(compact_department_id)
                    pool = [selected] if selected and not is_exec(selected["id"]) else []
                else:
                    pool = [d for d in await repo.list_departments() if not is_exec(d["id"])]
                if pool:
                    selected_dept = random.choice(pool)
                    _set_engine_phase("compact_department", dept=selected_dept)
                    await _compact_department(repo, selected_dept, now)
                    stats["compactions"] += 1
                    changed = True
    except Exception as exc:
        error_now = now_ms()
        _set_engine_runtime(
            state="error",
            lastError=f"{type(exc).__name__}: {_clip_text(str(exc), 700)}",
            lastErrorAt=error_now,
        )
        raise
    finally:
        finished = now_ms()
        started = int(_ENGINE_RUNTIME.get("lastTickStartedAt") or now)
        _set_engine_runtime(
            state="idle" if stats.get("running") else "paused",
            currentPhase=None,
            currentDepartmentId=None,
            currentDepartmentName=None,
            currentTaskId=None,
            currentJobId=None,
            lastTickFinishedAt=finished,
            lastTickDurationMs=max(0, finished - started),
            lastTickStats=stats,
        )

    if changed:
        hub.mark_dirty()
    return stats


async def _engine_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def _record_engine_loop_error(exc: Exception, now: int) -> bool:
    try:
        message = f"{type(exc).__name__}: {_clip_text(str(exc), 700)}"
        async with session_scope() as s:
            repo = Repo(s)
            await repo.add_activity(_activity(
                f"engine loop recovered after error: {message}",
                type_="system",
                severity="alert",
                ts=now,
            ))
            await _notify(
                repo,
                type_="crash",
                severity="alert",
                title="Engine loop recovered after error",
                body=message,
                now=now,
                links=["atrium://system/engine"],
            )
        hub.mark_dirty()
        return True
    except Exception:
        return False


async def run_engine_loop(settings: Settings | None = None) -> None:
    _set_engine_runtime(enabled=True, state="idle")
    last_error_notified_at: int | None = None
    while True:
        settings = get_settings()
        await _engine_sleep(max(0.25, settings.tick_seconds))
        try:
            await asyncio.wait_for(
                run_engine_tick(settings=settings),
                timeout=max(1.0, float(settings.engine_tick_timeout_s)),
            )
        except TimeoutError as exc:
            error_now = now_ms()
            _set_engine_runtime(
                state="error",
                lastError=f"TimeoutError: engine tick exceeded {settings.engine_tick_timeout_s:g}s",
                lastErrorAt=error_now,
            )
            if (
                last_error_notified_at is None
                or error_now - last_error_notified_at >= ENGINE_ERROR_NOTIFY_INTERVAL_MS
            ):
                if await _record_engine_loop_error(exc, error_now):
                    last_error_notified_at = error_now
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_now = now_ms()
            if (
                last_error_notified_at is None
                or error_now - last_error_notified_at >= ENGINE_ERROR_NOTIFY_INTERVAL_MS
            ):
                if await _record_engine_loop_error(exc, error_now):
                    last_error_notified_at = error_now
            # Keep the always-on service alive; the next tick gets a fresh DB session.
            continue
