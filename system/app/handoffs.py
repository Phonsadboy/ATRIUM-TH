from __future__ import annotations

from typing import Any

from .ids import uid
from .threads import EXEC_ID, is_exec, thread_id_for

HANDOFF_TERMINAL_STATUSES = {"closed", "cancelled", "rejected", "escalated"}
HANDOFF_OPEN_STATUSES = {
    "draft",
    "requested",
    "accepted",
    "in_progress",
    "clarification_requested",
    "missing_file",
    "delivered",
    "returned",
    "open",
    "clarifying",
}


def handoff_thread_id(task_id: str, from_dept: str, to_dept: str) -> str:
    return f"handoff:{task_id}:{from_dept}:{to_dept}"


def handoff_status_for_act(act: str) -> str:
    return {
        "accept": "accepted",
        "reject": "rejected",
        "clarify": "clarification_requested",
        "reply": "in_progress",
        "deliver": "delivered",
        "return": "returned",
        "request": "requested",
    }.get(act, "requested")


def normalize_handoff_status(status: Any) -> str:
    value = str(status or "requested").strip().lower()
    return {
        "open": "requested",
        "clarifying": "clarification_requested",
        "reply": "in_progress",
    }.get(value, value)


def handoff_is_open(status: Any) -> bool:
    normalized = normalize_handoff_status(status)
    return normalized in HANDOFF_OPEN_STATUSES and normalized not in HANDOFF_TERMINAL_STATUSES


def handoff_source_task_id(handoff: dict[str, Any], fallback_task_id: str) -> str:
    return handoff.get("sourceTaskId") or fallback_task_id


def make_handoff_message(
    handoff: dict[str, Any],
    *,
    from_actor: str,
    act: str,
    text: str,
    now: int,
    task_id: str,
) -> dict[str, Any]:
    source_task_id = handoff_source_task_id(handoff, task_id)
    thread_id = handoff_thread_id(source_task_id, handoff["fromDept"], handoff["toDept"])
    return {
        "id": uid("hmsg"),
        "handoffId": handoff["id"],
        "from": from_actor,
        "fromDept": handoff.get("fromDept"),
        "toDept": handoff.get("toDept"),
        "act": act,
        "text": text,
        "ts": now,
        "taskId": source_task_id,
        "threadId": thread_id,
    }


def handoff_chat_message(message: dict[str, Any], author_name: str) -> dict[str, Any]:
    actor = message["from"]
    role = "executive" if is_exec(actor) else "user" if actor == "user" else "agent"
    actor_dept_id = EXEC_ID if is_exec(actor) else actor if actor != "user" else None
    target_dept_id = message.get("toDept") if message.get("toDept") != actor_dept_id else message.get("fromDept")
    participant = None
    if actor_dept_id:
        participant = {
            "departmentId": actor_dept_id,
            "agentName": author_name,
            "role": "executive" if is_exec(actor_dept_id) else "department",
        }
    return {
        "id": uid("msg"),
        "threadId": message["threadId"],
        "role": role,
        "authorName": author_name,
        "text": f"[{message['act']}] {message['text']}",
        "ts": message["ts"],
        "status": "sent",
        "departmentId": actor_dept_id,
        "participant": participant,
        "input": {"status": "sent", "routedDepartmentId": target_dept_id} if target_dept_id else {"status": "sent"},
        "mentions": [
            {
                "raw": f"@{target_dept_id}",
                "departmentId": target_dept_id,
                "threadId": thread_id_for(target_dept_id),
                "displayName": target_dept_id,
                "matchedBy": "handoff",
            }
        ] if target_dept_id else [],
        "handoffId": message.get("handoffId"),
        "taskId": message.get("taskId"),
    }


def append_handoff_message(
    handoff: dict[str, Any],
    message: dict[str, Any],
    *,
    status: str | None = None,
) -> dict[str, Any]:
    existing = [m for m in handoff.get("messages", []) if m.get("id") != message["id"]]
    updated = {**handoff, "messages": [*existing, message]}
    updated["lastActionAt"] = message.get("ts") or updated.get("lastActionAt")
    if status is not None:
        updated["status"] = normalize_handoff_status(status)
    if not updated.get("contextPacketRef") and message.get("act") == "request":
        updated["contextPacketRef"] = message["id"]
    return updated


def handoff_participants(handoff: dict[str, Any]) -> set[str]:
    return {handoff["fromDept"], handoff["toDept"], "executive", "user"}
