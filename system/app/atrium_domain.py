"""ATRIUM-specific chat domain helpers.

These helpers keep the chat endpoint and background engine aligned on the
office metaphor: departments are named agents, war-room threads are shared
rooms, activity can become durable system lines, and cost is tracked per thread
from the message metadata that is already persisted.
"""
from __future__ import annotations

from typing import Any

from .chat_rendering import ensure_rendering_metadata
from .clock import now_ms
from .ids import uid

WAR_ROOM_THREAD_PREFIXES = ("war:", "war-room:", "war_room:")
MEETING_THREAD_PREFIXES = ("meet:", "meeting:")
MAX_WAR_ROOM_PARTICIPANTS = 4


def war_room_thread_id(war_room_id: str) -> str:
    return f"war:{war_room_id}"


def meeting_thread_id(meeting_id: str) -> str:
    return f"meet:{meeting_id}"


def is_war_room_thread(thread_id: str) -> bool:
    return any(thread_id.startswith(prefix) for prefix in WAR_ROOM_THREAD_PREFIXES)


def is_meeting_thread(thread_id: str) -> bool:
    return any(thread_id.startswith(prefix) for prefix in MEETING_THREAD_PREFIXES)


def war_room_id_from_thread(thread_id: str) -> str | None:
    for prefix in WAR_ROOM_THREAD_PREFIXES:
        if thread_id.startswith(prefix):
            return thread_id.removeprefix(prefix)
    return None


def meeting_id_from_thread(thread_id: str) -> str | None:
    for prefix in MEETING_THREAD_PREFIXES:
        if thread_id.startswith(prefix):
            return thread_id.removeprefix(prefix)
    return None


def _clip(value: Any, limit: int = 220) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = str(value or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def agent_snapshot(dept: dict[str, Any]) -> dict[str, Any]:
    return {
        "departmentId": dept.get("id"),
        "departmentName": dept.get("name"),
        "agentName": dept.get("agentName"),
        "role": dept.get("role"),
        "state": dept.get("state"),
        "mood": dept.get("mood"),
        "currentTaskId": dept.get("currentTaskId"),
    }


def agent_message_metadata(
    dept: dict[str, Any],
    *,
    war_room_id: str | None = None,
    meeting_id: str | None = None,
    thread_cost: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = {
        "departmentId": dept.get("id"),
        "agentState": dept.get("state"),
        "agentMood": dept.get("mood"),
        "participant": agent_snapshot(dept),
    }
    if war_room_id:
        out["warRoomId"] = war_room_id
    if meeting_id:
        out["meetingId"] = meeting_id
    if thread_cost:
        out["threadCost"] = thread_cost
    return out


def operating_protocol_prompt() -> str:
    return (
        "\n\nATRIUM operating protocol:\n"
        "- Every department must treat handoff context as durable work material, not as chat-only memory. "
        "When a handoff includes contextPacketArtifactId/contextPacketFilename/contextPacketUri or deliverables, "
        "read and continue from those versioned Markdown work packets before answering.\n"
        "- When handing work to another department, make the reason, expected answer, and relevant work-file/artifact references explicit "
        "so future departments created later can still understand the job after chat compaction.\n"
        "- If your task is waiting for another department's handoff response, describe it as "
        "รอการตอบกลับจากฝ่ายนั้น. Reserve blocked/ติดปัญหา for true inability to proceed, failed tools, guardrails, or executive escalation."
    )


def persona_prompt(dept: dict[str, Any]) -> str:
    skills = ", ".join(str(item) for item in (dept.get("skills") or [])[:8]) or "none"
    tools = ", ".join(str(item) for item in (dept.get("tools") or [])[:8]) or "none"
    current_task = dept.get("currentTaskId") or "none"
    mood = dept.get("mood")
    mood_text = f"{float(mood):.2f}" if isinstance(mood, int | float) else "unknown"
    return (
        "\n\nATRIUM employee context:\n"
        f"- departmentId: {dept.get('id')}\n"
        f"- current state: {dept.get('state', 'idle')}; mood: {mood_text}; currentTaskId: {current_task}\n"
        f"- skills: {skills}\n"
        f"- tools: {tools}\n"
        "- Executive and departments have separate user-visible chat rooms; department identity is shown on each message.\n"
        "- Treat only this room's visible prior messages as default context. If you are tagged, handed off, or need another room, use the conversation tools to read or send cross-room messages.\n"
        "- Stay inside this department charter. Speak as this employee, not as a generic chatbot.\n"
        "- If work should move to another department, name the target department and reason clearly."
        f"{operating_protocol_prompt()}"
    )


def war_room_participants(
    war_room: dict[str, Any],
    departments: list[dict[str, Any]],
    *,
    max_participants: int = MAX_WAR_ROOM_PARTICIPANTS,
) -> list[dict[str, Any]]:
    by_id = {dept.get("id"): dept for dept in departments if dept.get("id")}
    ordered_ids = _ordered_unique([
        str(war_room.get("lead") or ""),
        *[str(item) for item in (war_room.get("members") or [])],
    ])
    if not ordered_ids:
        ordered_ids = [str(dept.get("id")) for dept in departments if dept.get("id") != "exec"]
    participants = [by_id[dept_id] for dept_id in ordered_ids if dept_id in by_id]
    return participants[:max(1, max_participants)]


def war_room_context(war_room: dict[str, Any], participants: list[dict[str, Any]]) -> str:
    lines = [
        "\n\nATRIUM war-room context:",
        f"- warRoomId: {war_room.get('id')}",
        f"- title: {war_room.get('title')}",
        f"- goal: {_clip(war_room.get('goal'), 360)}",
        f"- lead: {war_room.get('lead') or 'none'}",
        f"- status: {war_room.get('status', 'active')}",
    ]
    if war_room.get("taskId"):
        lines.append(f"- taskId: {war_room.get('taskId')}")
    if war_room.get("projectId"):
        lines.append(f"- projectId: {war_room.get('projectId')}")
    if war_room.get("scratchpad"):
        lines.append(f"- scratchpad: {_clip(war_room.get('scratchpad'), 420)}")
    if participants:
        participant_lines = [
            f"  - {dept.get('id')}: {dept.get('agentName')} / ฝ่าย{dept.get('name')} ({dept.get('role')})"
            for dept in participants
        ]
        lines.append("- participants:\n" + "\n".join(participant_lines))
    lines.append("- Coordinate with the visible participants; avoid pretending absent departments spoke.")
    return "\n".join(lines)


def war_room_flow(war_room: dict[str, Any], participants: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "kind": "war_room",
        "title": f"War room: {war_room.get('title')}",
        "steps": [
            {"kind": "room", "label": war_room.get("title"), "warRoomId": war_room.get("id")},
            *[
                {
                    "kind": "participant",
                    "label": dept.get("agentName") or dept.get("name") or dept.get("id"),
                    "departmentId": dept.get("id"),
                }
                for dept in participants
            ],
        ],
        "refs": {
            "warRoomId": war_room.get("id"),
            "projectId": war_room.get("projectId"),
            "taskId": war_room.get("taskId"),
        },
    }


def meeting_participants(meeting: dict[str, Any], departments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {dept.get("id"): dept for dept in departments if dept.get("id")}
    ordered_ids = _ordered_unique([str(item) for item in (meeting.get("participants") or [])])
    participants = [by_id[dept_id] for dept_id in ordered_ids if dept_id in by_id]
    return participants[:MAX_WAR_ROOM_PARTICIPANTS]


def meeting_context(meeting: dict[str, Any], participants: list[dict[str, Any]]) -> str:
    lines = [
        "\n\nATRIUM meeting context:",
        f"- meetingId: {meeting.get('id')}",
        f"- title: {meeting.get('title')}",
        f"- status: {meeting.get('status', 'scheduled')}",
    ]
    if meeting.get("projectId"):
        lines.append(f"- projectId: {meeting.get('projectId')}")
    agenda = [str(item).strip() for item in (meeting.get("agenda") or []) if str(item).strip()]
    if agenda:
        lines.append("- agenda:\n" + "\n".join(f"  - {_clip(item, 180)}" for item in agenda[:8]))
    decisions = [str(item).strip() for item in (meeting.get("decisions") or []) if str(item).strip()]
    if decisions:
        lines.append("- decisions:\n" + "\n".join(f"  - {_clip(item, 180)}" for item in decisions[:8]))
    action_items = [str(item).strip() for item in (meeting.get("actionItems") or []) if str(item).strip()]
    if action_items:
        lines.append("- actionItems:\n" + "\n".join(f"  - {item}" for item in action_items[:12]))
    if meeting.get("notes"):
        lines.append(f"- notes: {_clip(meeting.get('notes'), 520)}")
    if participants:
        participant_lines = [
            f"  - {dept.get('id')}: {dept.get('agentName')} / ฝ่าย{dept.get('name')} ({dept.get('role')})"
            for dept in participants
        ]
        lines.append("- participants:\n" + "\n".join(participant_lines))
    lines.append("- Treat this as a structured meeting. Make decisions explicit and turn follow-ups into concrete action items.")
    return "\n".join(lines)


def meeting_flow(meeting: dict[str, Any], participants: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "kind": "meeting",
        "title": f"Meeting: {meeting.get('title')}",
        "steps": [
            {"kind": "meeting", "label": meeting.get("title"), "meetingId": meeting.get("id")},
            *[
                {
                    "kind": "participant",
                    "label": dept.get("agentName") or dept.get("name") or dept.get("id"),
                    "departmentId": dept.get("id"),
                }
                for dept in participants
            ],
        ],
        "refs": {
            "meetingId": meeting.get("id"),
            "projectId": meeting.get("projectId"),
        },
    }


def handoff_flow(source: dict[str, Any], target: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "handoff",
        "title": f"{source.get('agentName', 'Executive')} -> {target.get('name', target.get('id'))}",
        "steps": [
            {
                "kind": "source",
                "label": source.get("agentName") or source.get("name") or source.get("id"),
                "departmentId": source.get("id"),
            },
            {
                "kind": "task",
                "label": task.get("title"),
                "taskId": task.get("id"),
            },
            {
                "kind": "target",
                "label": f"ฝ่าย{target.get('name')}",
                "departmentId": target.get("id"),
            },
        ],
        "refs": {"taskId": task.get("id"), "targetDepartmentId": target.get("id")},
    }


def activity_flow(activity: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "activity",
        "title": activity.get("text"),
        "steps": [
            {
                "kind": activity.get("type") or "system",
                "label": activity.get("text"),
                "departmentId": activity.get("departmentId"),
            }
        ],
        "refs": {"activityId": activity.get("id"), "activityType": activity.get("type")},
    }


def thread_cost_summary(thread_id: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    by_agent: dict[str, float] = {}
    total = 0.0
    costed = 0
    for msg in messages:
        render = msg.get("render") or {}
        cost = render.get("costUsd")
        if not isinstance(cost, int | float):
            continue
        usd = float(cost)
        total += usd
        costed += 1
        agent = str(msg.get("authorName") or msg.get("departmentId") or msg.get("role") or "unknown")
        by_agent[agent] = round(by_agent.get(agent, 0.0) + usd, 6)
    return {
        "threadId": thread_id,
        "totalUsd": round(total, 6),
        "byAgent": by_agent,
        "costedMessages": costed,
        "messageCount": len(messages),
        "updatedAt": now_ms(),
    }


def system_chat_message(
    thread_id: str,
    text: str,
    *,
    activity: dict[str, Any] | None = None,
    department_id: str | None = None,
    flow: dict[str, Any] | None = None,
    thread_cost: dict[str, Any] | None = None,
    war_room_id: str | None = None,
    meeting_id: str | None = None,
    severity: str = "info",
    ts: int | None = None,
) -> dict[str, Any]:
    msg = {
        "id": uid("msg"),
        "threadId": thread_id,
        "role": "system",
        "authorName": "ATRIUM",
        "text": text,
        "ts": ts or now_ms(),
        "departmentId": department_id or (activity or {}).get("departmentId"),
        "activityId": (activity or {}).get("id"),
        "activityType": (activity or {}).get("type"),
        "flow": flow or (activity_flow(activity) if activity else None),
        "threadCost": thread_cost,
        "warRoomId": war_room_id,
        "meetingId": meeting_id,
    }
    return ensure_rendering_metadata(msg, severity=severity)


async def update_agent_state(repo: Any, dept: dict[str, Any], state: str, *, mood_delta: float = 0.0) -> dict[str, Any]:
    if not dept or dept.get("state") == "offline":
        return dept
    next_dept = dict(dept)
    next_dept["state"] = state
    if isinstance(next_dept.get("mood"), int | float) and mood_delta:
        next_dept["mood"] = min(1.0, max(0.0, float(next_dept["mood"]) + mood_delta))
    await repo.save_department(next_dept)
    return next_dept
