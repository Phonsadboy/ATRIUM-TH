"""Thread-id helpers — mirror of ui/src/lib/threads.ts."""
from __future__ import annotations

EXEC_ID = "exec"
EXEC_THREAD = "executive"
BRANCH_MARKER = ":branch:"
WAR_ROOM_THREAD_PREFIX = "war:"
MEETING_THREAD_PREFIX = "meet:"


def thread_id_for(dept_id: str) -> str:
    return EXEC_THREAD if dept_id == EXEC_ID else f"dept:{dept_id}"


def war_room_thread_id(war_room_id: str) -> str:
    return f"{WAR_ROOM_THREAD_PREFIX}{war_room_id}"


def meeting_thread_id(meeting_id: str) -> str:
    return f"{MEETING_THREAD_PREFIX}{meeting_id}"


def base_thread_id(thread_id: str) -> str:
    if BRANCH_MARKER not in thread_id:
        return thread_id
    base, _branch = thread_id.split(BRANCH_MARKER, 1)
    return base or thread_id


def dept_id_from_thread(thread_id: str) -> str:
    thread_id = base_thread_id(thread_id)
    if thread_id == EXEC_THREAD:
        return EXEC_ID
    return thread_id[5:] if thread_id.startswith("dept:") else thread_id


def is_exec(dept_id: str) -> bool:
    return dept_id == EXEC_ID
