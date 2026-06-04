from __future__ import annotations

from typing import Any

from .atrium_domain import agent_message_metadata, system_chat_message, thread_cost_summary
from .clock import now_ms
from .events import hub
from .ids import uid
from .threads import EXEC_ID, EXEC_THREAD, is_exec, thread_id_for


WORK_STATUS_EVENT_LABELS: dict[str, str] = {
    "task_assigned": "รับมอบหมายงาน",
    "task_started": "เริ่มงาน",
    "task_review": "พร้อมตรวจ",
    "task_blocked": "ติดปัญหา",
    "task_revising": "ต้องแก้ก่อนปิด",
    "task_close_requested": "ขอปิดงาน",
    "task_close_approved": "อนุมัติปิดงาน",
    "task_close_rejected": "ตีกลับให้แก้",
    "handoff_requested": "ส่งต่องาน",
    "handoff_reply": "ตอบกลับงานส่งต่อ",
    "handoff_accepted": "รับงานส่งต่อ",
    "handoff_clarify": "ขอข้อมูลเพิ่ม",
    "handoff_delivered": "ส่งงานกลับ",
    "handoff_returned": "ส่งงานคืนต้นทาง",
    "handoff_rejected": "ปฏิเสธงานส่งต่อ",
    "handoff_escalated": "ส่งต่อผู้บริหาร",
    "agent_reply_finished": "ตอบกลับแล้ว",
}

HANDOFF_EVENTS = {
    "handoff_requested",
    "handoff_reply",
    "handoff_accepted",
    "handoff_clarify",
    "handoff_delivered",
    "handoff_returned",
    "handoff_rejected",
    "handoff_escalated",
}


def visibility_event_label(event: str) -> str:
    return WORK_STATUS_EVENT_LABELS.get(event, event.replace("_", " "))


def _dept_id(dept: dict[str, Any] | str | None) -> str | None:
    if isinstance(dept, dict):
        value = dept.get("id")
    else:
        value = dept
    text = str(value or "").strip()
    return text or None


def _dept_name(dept: dict[str, Any] | str | None) -> str | None:
    if isinstance(dept, dict):
        value = dept.get("name") or dept.get("agentName") or dept.get("id")
    else:
        value = dept
    text = str(value or "").strip()
    return text or None


def _route_thread_ids(
    *,
    source_dept: dict[str, Any] | str | None,
    target_dept: dict[str, Any] | str | None,
    include_executive: bool,
    extra_threads: list[str] | tuple[str, ...] | set[str] | None,
) -> list[str]:
    routes: list[str] = []
    if include_executive:
        routes.append(EXEC_THREAD)
    for dept in (source_dept, target_dept):
        dept_id = _dept_id(dept)
        if dept_id:
            routes.append(thread_id_for(dept_id))
    for thread_id in extra_threads or []:
        text = str(thread_id or "").strip()
        if text:
            routes.append(text)
    return list(dict.fromkeys(routes))


async def _recent_thread_messages(repo: Any, thread_id: str) -> list[dict[str, Any]]:
    if hasattr(repo, "thread_messages"):
        try:
            return await repo.thread_messages(thread_id, limit=120)
        except TypeError:
            return await repo.thread_messages(thread_id)
    messages = getattr(repo, "messages", None)
    if isinstance(messages, list):
        return [m for m in messages if isinstance(m, dict) and m.get("threadId") == thread_id]
    return []


async def _has_visibility_event_key(repo: Any, thread_id: str, key: str) -> bool:
    for msg in await _recent_thread_messages(repo, thread_id):
        meta = msg.get("input") if isinstance(msg.get("input"), dict) else {}
        if meta.get("visibilityEventKey") == key:
            return True
    return False


def work_status_flow(
    *,
    event: str,
    summary: str,
    source_dept: dict[str, Any] | str | None = None,
    target_dept: dict[str, Any] | str | None = None,
    task: dict[str, Any] | None = None,
    task_id: str | None = None,
    handoff_id: str | None = None,
    visibility_event_key: str | None = None,
) -> dict[str, Any]:
    source_id = _dept_id(source_dept)
    target_id = _dept_id(target_dept)
    resolved_task_id = task_id or (str(task.get("id")) if isinstance(task, dict) and task.get("id") else None)
    label = visibility_event_label(event)
    title_task = task.get("title") if isinstance(task, dict) else None
    title = str(title_task or summary or label).strip()[:140]
    steps: list[dict[str, Any]] = []
    if source_id:
        steps.append({
            "kind": "source",
            "label": _dept_name(source_dept) or source_id,
            "departmentId": source_id,
            "taskId": resolved_task_id,
        })
    steps.append({
        "kind": event,
        "label": label,
        "departmentId": target_id or source_id,
        "taskId": resolved_task_id,
    })
    if target_id and target_id != source_id:
        steps.append({
            "kind": "target",
            "label": _dept_name(target_dept) or target_id,
            "departmentId": target_id,
            "taskId": resolved_task_id,
        })
    refs = {
        "event": event,
        "taskId": resolved_task_id,
        "handoffId": handoff_id,
        "sourceDepartmentId": source_id,
        "targetDepartmentId": target_id,
        "visibilityEventKey": visibility_event_key,
        "status": task.get("status") if isinstance(task, dict) else None,
        "progress": task.get("progress") if isinstance(task, dict) else None,
    }
    return {
        "kind": "handoff" if event in HANDOFF_EVENTS or handoff_id else "department_work",
        "title": title,
        "steps": steps,
        "refs": {k: v for k, v in refs.items() if v is not None},
    }


def _make_agent_work_status_message(
    thread_id: str,
    text: str,
    *,
    active_dept: dict[str, Any],
    flow: dict[str, Any],
    input_meta: dict[str, Any],
    ts: int,
) -> dict[str, Any]:
    dept_id = str(active_dept.get("id") or "")
    return {
        "id": uid("msg"),
        "threadId": thread_id,
        "role": "executive" if is_exec(dept_id) else "agent",
        "authorName": active_dept.get("agentName") or active_dept.get("name") or dept_id or "Agent",
        "text": text,
        "ts": ts,
        "status": "sent",
        "flow": flow,
        "input": input_meta,
        **agent_message_metadata(active_dept),
    }


async def emit_work_status_notice(
    repo: Any,
    *,
    event: str,
    summary: str,
    source_dept: dict[str, Any] | str | None = None,
    target_dept: dict[str, Any] | str | None = None,
    task: dict[str, Any] | None = None,
    task_id: str | None = None,
    handoff_id: str | None = None,
    severity: str = "info",
    now: int | None = None,
    dedupe_key: str | None = None,
    include_executive: bool = True,
    extra_threads: list[str] | tuple[str, ...] | set[str] | None = None,
    as_agent: bool = False,
    author_dept: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    ts = now or now_ms()
    source_id = _dept_id(source_dept)
    target_id = _dept_id(target_dept)
    resolved_task_id = task_id or (str(task.get("id")) if isinstance(task, dict) and task.get("id") else None)
    base_key = dedupe_key or ":".join(
        item for item in [event, resolved_task_id or "", handoff_id or "", source_id or "", target_id or ""] if item
    )
    if not base_key:
        base_key = f"{event}:{ts}"
    routes = _route_thread_ids(
        source_dept=source_dept,
        target_dept=target_dept,
        include_executive=include_executive,
        extra_threads=extra_threads,
    )
    messages: list[dict[str, Any]] = []
    for thread_id in routes:
        thread_key = f"{base_key}:{thread_id}"
        if await _has_visibility_event_key(repo, thread_id, thread_key):
            continue
        flow = work_status_flow(
            event=event,
            summary=summary,
            source_dept=source_dept,
            target_dept=target_dept,
            task=task,
            task_id=resolved_task_id,
            handoff_id=handoff_id,
            visibility_event_key=thread_key,
        )
        input_meta = {
            "status": "sent",
            "visibilityEvent": event,
            "visibilityEventKey": thread_key,
            "taskId": resolved_task_id,
            "handoffId": handoff_id,
            "routedDepartmentId": target_id or source_id or (EXEC_ID if thread_id == EXEC_THREAD else None),
        }
        input_meta = {k: v for k, v in input_meta.items() if v is not None}
        if as_agent and author_dept:
            msg = _make_agent_work_status_message(
                thread_id,
                summary,
                active_dept=author_dept,
                flow=flow,
                input_meta=input_meta,
                ts=ts,
            )
        else:
            cost = None
            if hasattr(repo, "thread_messages"):
                try:
                    cost = thread_cost_summary(thread_id, await repo.thread_messages(thread_id, limit=500))
                except Exception:
                    cost = None
            msg = system_chat_message(
                thread_id,
                summary,
                department_id=target_id or source_id,
                flow=flow,
                thread_cost=cost,
                severity=severity,
                ts=ts,
            )
            msg["input"] = input_meta
        await repo.add_message(msg)
        messages.append(msg)
        hub.pulse({
            "kind": "chat_activity",
            "threadId": thread_id,
            "msgId": msg["id"],
            "departmentId": msg.get("departmentId"),
            "message": msg,
        })
    if messages:
        hub.mark_dirty()
    return messages
