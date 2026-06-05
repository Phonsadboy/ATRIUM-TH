"""Repository — all data access in one place.

The wire-shape (camelCase dict from a Schema `.dump()`) is stored verbatim in
each row's `data` column, with a few columns mirrored out for indexing. Reads
return those dicts directly, so the API never has to re-serialize.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, Optional

from sqlalchemy import case, delete, func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from ..atrium_domain import system_chat_message
from ..catalog import (
    DEFAULT_MODEL,
    coerce_model,
    coerce_model_speed,
    coerce_provider,
    coerce_thinking_effort,
)
from ..clock import DAY_MS, day_key, now_ms
from ..chat_rendering import ensure_rendering_metadata
from ..config import get_settings
from ..ids import uid
from ..memory.graph_store import get_graph_mirror
from ..task_review import TASK_REVIEW_REMINDER_KIND
from ..threads import EXEC_ID, EXEC_THREAD, thread_id_for
from . import tables as T
from .base import sqlite_schema_status

MAX_ACTIVITY = 80
MAX_MSGS = 60
IMMUTABLE_ENTITY_TYPES = {"artifact_version", "conversation_ledger"}
MAX_GRAPH_ID = 96
COST_CATEGORIES = ("work", "chat", "meeting", "autonomous", "memory", "tool")
_COST_CATEGORY_ALIASES = {
    "agent": "work",
    "agents": "work",
    "task": "work",
    "tasks": "work",
    "message": "chat",
    "messages": "chat",
    "memory_compaction": "memory",
    "rag": "memory",
    "owner_tool": "tool",
    "tools": "tool",
}
CHAT_VISIBLE_ACTIVITY_TYPES = {
    "approval",
    "autonomous",
    "handoff",
    "state_change",
    "task_assigned",
    "task_created",
    "task_done",
    "task_progress",
}


def normalize_cost_category(category: Any) -> tuple[str, str | None]:
    raw = str(category or "").strip().lower()
    normalized = _COST_CATEGORY_ALIASES.get(raw, raw)
    if normalized in COST_CATEGORIES:
        return normalized, None if normalized == raw else raw
    return "tool", raw or None
EXECUTIVE_QUEUE_KINDS = {"chat_reply", TASK_REVIEW_REMINDER_KIND, "objective_run", "trigger_run"}


def _canonical_chat_thread_id(thread_id: str) -> str:
    thread_id = str(thread_id or "").strip()
    if not thread_id or thread_id in {EXEC_ID, "company"}:
        return EXEC_THREAD
    return thread_id


def _clip_snapshot_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _compact_task_for_snapshot(task: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    out = dict(task)
    out["detail"] = _clip_snapshot_text(out.get("detail"), settings.state_task_detail_char_limit)
    log_limit = settings.state_task_log_entry_limit
    char_limit = settings.state_task_log_char_limit
    out["log"] = [
        _clip_snapshot_text(line, char_limit)
        for line in list(task.get("log") or [])[-log_limit:]
    ] if log_limit else []
    deliverable_limit = settings.state_task_deliverable_char_limit
    if deliverable_limit and task.get("draftDeliverableMarkdown"):
        out["draftDeliverableMarkdown"] = _clip_snapshot_text(task.get("draftDeliverableMarkdown"), deliverable_limit)
    elif not deliverable_limit:
        out["draftDeliverableMarkdown"] = None
    out["handoffs"] = [_compact_handoff_for_snapshot(item) for item in list(task.get("handoffs") or [])]
    if isinstance(task.get("result"), dict):
        result = task["result"]
        out["result"] = {
            key: result.get(key)
            for key in ("summary", "artifactId", "decisionId", "completedAt")
            if key in result
        }
    return out


def _job_queue_title(kind: str, payload: dict[str, Any]) -> str:
    if payload.get("queueTitle"):
        return _clip_snapshot_text(payload.get("queueTitle"), 160)
    if kind == "chat_reply":
        dept_id = str(payload.get("departmentId") or EXEC_ID)
        target = "ผู้บริหาร" if dept_id == EXEC_ID else f"ฝ่าย {dept_id}"
        return f"ตอบแชต: {target}"
    if kind == TASK_REVIEW_REMINDER_KIND:
        return f"ปลุกตรวจงาน: {payload.get('taskTitle') or payload.get('taskId') or 'งาน'}"
    if kind == "objective_run":
        return f"Objective: {payload.get('title') or payload.get('objectiveId') or 'งานตามรอบ'}"
    if kind == "trigger_run":
        event = payload.get("event")
        return f"Trigger{f' {event}' if event else ''}: {payload.get('title') or payload.get('triggerId') or 'งานอัตโนมัติ'}"
    return kind


def _job_queue_detail(kind: str, payload: dict[str, Any]) -> str | None:
    if kind == "chat_reply":
        return _clip_snapshot_text(payload.get("text"), 220)
    if kind == TASK_REVIEW_REMINDER_KIND:
        return _clip_snapshot_text(payload.get("taskTitle"), 220)
    detail = payload.get("detail") or payload.get("event") or payload.get("target")
    return _clip_snapshot_text(detail, 220) if detail else None


def _queue_item_from_job(row: T.Job) -> dict[str, Any]:
    payload = dict(row.payload or {})
    return {
        "id": row.id,
        "kind": row.kind,
        "status": row.status,
        "title": _job_queue_title(row.kind, payload),
        "detail": _job_queue_detail(row.kind, payload),
        "departmentId": payload.get("departmentId"),
        "threadId": payload.get("threadId"),
        "taskId": payload.get("taskId"),
        "userMessageId": payload.get("userMessageId"),
        "replyMessageId": payload.get("replyMessageId"),
        "runAfter": row.run_after,
        "priority": row.priority,
        "attempts": row.attempts,
        "lastError": row.last_error,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def _queue_item_from_close_approval(approval: dict[str, Any]) -> dict[str, Any]:
    action = approval.get("action") if isinstance(approval.get("action"), dict) else {}
    title = str(approval.get("title") or "ตรวจปิดงาน").replace("อนุมัติปิดงาน:", "ตรวจปิดงาน:")
    return {
        "id": approval.get("id"),
        "kind": "task_close_review",
        "status": "queued",
        "title": _clip_snapshot_text(title, 160),
        "detail": _clip_snapshot_text(approval.get("detail"), 220),
        "departmentId": approval.get("departmentId") or action.get("departmentId"),
        "threadId": thread_id_for(EXEC_ID),
        "taskId": action.get("taskId"),
        "userMessageId": None,
        "replyMessageId": None,
        "runAfter": int(approval.get("ts") or now_ms()),
        "priority": 15,
        "attempts": 0,
        "lastError": None,
        "createdAt": int(approval.get("ts") or now_ms()),
        "updatedAt": int(approval.get("ts") or now_ms()),
    }


def _compact_handoff_for_snapshot(handoff: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    messages = list(handoff.get("messages") or [])
    message_limit = settings.state_task_handoff_message_limit
    visible_messages = messages[-message_limit:] if message_limit else []
    return {
        "id": handoff.get("id"),
        "fromDept": handoff.get("fromDept"),
        "toDept": handoff.get("toDept"),
        "ts": handoff.get("ts"),
        "reason": _clip_snapshot_text(handoff.get("reason"), settings.state_task_handoff_reason_char_limit),
        "kind": handoff.get("kind") or "consult",
        "status": handoff.get("status"),
        "depth": handoff.get("depth") or 0,
        "chainId": handoff.get("chainId"),
        "parentHandoffId": handoff.get("parentHandoffId"),
        "replyToHandoffId": handoff.get("replyToHandoffId"),
        "lastActionAt": handoff.get("lastActionAt"),
        "deadlineAt": handoff.get("deadlineAt"),
        "closedAt": handoff.get("closedAt"),
        "closedBy": handoff.get("closedBy"),
        "statusReason": handoff.get("statusReason"),
        "deliverableArtifactIds": list(handoff.get("deliverableArtifactIds") or []),
        "contextPacketRef": handoff.get("contextPacketRef"),
        "contextPacketArtifactId": handoff.get("contextPacketArtifactId"),
        "contextPacketArtifactVersion": handoff.get("contextPacketArtifactVersion"),
        "contextPacketFilename": handoff.get("contextPacketFilename"),
        "contextPacketUri": handoff.get("contextPacketUri"),
        "sourceTaskId": handoff.get("sourceTaskId"),
        "targetTaskId": handoff.get("targetTaskId"),
        "warRoomId": handoff.get("warRoomId"),
        "messageCount": len(messages),
        "messagesTruncated": len(visible_messages) < len(messages),
        "messages": [
            _compact_handoff_message_for_snapshot(item, settings.state_task_handoff_message_char_limit)
            for item in visible_messages
        ],
    }


def _compact_handoff_message_for_snapshot(message: Any, char_limit: int) -> dict[str, Any]:
    if not isinstance(message, dict):
        message = {"text": str(message or "")}
    raw_text = str(message.get("text") or "")
    text = _clip_snapshot_text(raw_text, char_limit)
    out = {
        "id": message.get("id"),
        "handoffId": message.get("handoffId"),
        "from": message.get("from") or message.get("from_"),
        "act": message.get("act"),
        "text": text,
        "ts": message.get("ts"),
        "taskId": message.get("taskId"),
        "threadId": message.get("threadId"),
    }
    if text != raw_text:
        out["textTruncated"] = True
    return {key: value for key, value in out.items() if value is not None}


def _compact_approval_for_snapshot(approval: dict[str, Any]) -> dict[str, Any]:
    detail_limit = get_settings().state_task_detail_char_limit
    return {
        "id": approval.get("id"),
        "ts": approval.get("ts"),
        "kind": approval.get("kind"),
        "title": approval.get("title"),
        "detail": _clip_snapshot_text(approval.get("detail"), detail_limit),
        "departmentId": approval.get("departmentId"),
        "costUsd": approval.get("costUsd"),
        "status": approval.get("status"),
        "action": approval.get("action") if approval.get("status") == "pending" else None,
    }


def _normalize_department_for_snapshot(dept: dict[str, Any]) -> dict[str, Any]:
    data = dict(dept or {})
    provider_id = coerce_provider(str(data.get("providerId") or "claude_code"))
    model = coerce_model(provider_id, str(data.get("model") or DEFAULT_MODEL))
    data["providerId"] = provider_id
    data["model"] = model
    data["thinkingEffort"] = coerce_thinking_effort(model, str(data.get("thinkingEffort") or "high"))
    data["speed"] = coerce_model_speed(model, str(data.get("speed") or "standard"))
    visibility = data.get("visibilityPolicy")
    if isinstance(visibility, dict):
        data["visibilityPolicy"] = {
            "dept": visibility.get("dept") or data.get("id") or "unknown",
            "archive": visibility.get("archive") or "private",
            "knowledge": visibility.get("knowledge") or "on_request",
            "tasks": visibility.get("tasks") or "company",
            "artifacts": visibility.get("artifacts") or "company",
        }
    return data


def _snapshot_task_sort_key(task: dict[str, Any]) -> tuple[int, int]:
    terminal = task.get("status") in {"done", "cancelled"}
    return (1 if terminal else 0, -int(task.get("updatedAt") or task.get("createdAt") or 0))


def _vector_literal(vec: list[float]) -> str:
    values = []
    for value in vec:
        number = float(value)
        values.append(format(number if math.isfinite(number) else 0.0, ".9g"))
    return "[" + ",".join(values) + "]"

def _tool(
    name: str,
    risk: str,
    *,
    mutates: bool,
    external: bool = False,
    executor: str = "host",
    timeout: int = 10_000,
    checkpoint: bool = False,
    credentials: bool = False,
    description: str,
    schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    rollback_capable: bool | None = None,
    redaction_rules: list[str] | None = None,
) -> dict[str, Any]:
    output = output_schema or _default_tool_output_schema(name)
    rollback = _default_tool_rollback_capable(name, mutates, checkpoint) if rollback_capable is None else rollback_capable
    return {
        "tool": name,
        "riskClass": risk,
        "mutatesState": mutates,
        "externalSystem": external,
        "description": description,
        "executor": executor,
        "defaultTimeoutMs": timeout,
        "outputLimitBytes": 60_000,
        "supportsCheckpoint": checkpoint,
        "canUseCredentials": credentials,
        "rollbackCapable": rollback,
        "inputSchema": schema or {},
        "outputSchema": output,
        "redactionRules": redaction_rules or _default_tool_redaction_rules(name, risk, credentials, external),
    }


def _default_tool_output_schema(name: str) -> dict[str, Any]:
    if name in {"fs.list"}:
        return {"type": "object", "properties": {"entries": "array", "path": "string"}}
    if name in {"fs.read", "read_file"}:
        return {"type": "object", "properties": {"text": "string", "bytes": "number", "path": "string"}}
    if name == "web.search":
        return {"type": "object", "properties": {"results": "array", "provider": "string", "query": "string"}}
    if name == "web.fetch":
        return {"type": "object", "properties": {"url": "string", "title": "string", "text": "string", "images": "array", "links": "array", "networkAudit": "object?"}}
    if name in {"fs.write", "fs.patch", "write_file"}:
        return {"type": "object", "properties": {"path": "string", "bytes": "number", "checkpointId": "string?"}}
    if name in {"fs.copy", "copy_file"}:
        return {"type": "object", "properties": {"copied": "boolean", "sourcePath": "string", "destinationPath": "string", "bytes": "number"}}
    if name in {"fs.move", "move_file"}:
        return {"type": "object", "properties": {"moved": "boolean", "sourcePath": "string", "destinationPath": "string"}}
    if name == "fs.delete":
        return {"type": "object", "properties": {"deleted": "boolean", "path": "string", "checkpointId": "string?"}}
    if name in {"shell.exec", "sandbox.exec", "run_command"}:
        return {"type": "object", "properties": {"returnCode": "number", "stdout": "string", "stderr": "string"}}
    if name in {"http.get", "http.post", "http_get"}:
        return {"type": "object", "properties": {"status": "number", "headers": "object", "body": "string"}}
    if name in {"import.url", "import_url"}:
        return {"type": "object", "properties": {"imported": "boolean", "status": "number", "path": "string", "bytes": "number"}}
    if name.startswith("git."):
        return {"type": "object", "properties": {"returnCode": "number", "stdout": "string", "stderr": "string", "checkpointId": "string?"}}
    if name == "browser.profiles":
        return {"type": "object", "properties": {"ownProfile": "string", "defaultProfile": "string", "platform": "string", "profilesRoot": "string", "browserApp": "object?", "profiles": "object[]"}}
    if name == "browser.open":
        return {"type": "object", "properties": {"returnCode": "number", "stdout": "string", "stderr": "string", "profile": "string", "profileKind": "user|isolated", "isOwnProfile": "boolean?", "userDataDir": "string?", "browserApp": "string?", "browserAppPath": "string?", "processId": "number?", "startedProcessId": "number?", "processName": "string?", "profileVerified": "boolean?", "processVerified": "boolean?", "progId": "string?", "source": "startProcess|processLookup|profileProcessLookup|defaultBrowserRegistry|shellAssociation?", "platform": "string?", "url": "string"}}
    if name in {"browser.snapshot", "browser.act"}:
        return {"type": "object", "properties": {"returnCode": "number", "ok": "boolean?", "backend": "string?", "profile": "string", "profileKind": "isolated", "isOwnProfile": "boolean?", "userDataDir": "string?", "browserApp": "string?", "browserAppPath": "string?", "url": "string?", "title": "string?", "refCount": "number?", "action": "object?", "snapshot": "object?", "platform": "string?", "stdout": "string?", "stderr": "string?"}}
    if name == "desktop.apps":
        return {"type": "object", "properties": {"running": "string[]|object[]", "installed": "object[]", "installedCount": "number", "query": "string?", "runningReturnCode": "number?", "installedReturnCode": "number?", "installedError": "string?", "platform": "string?"}}
    if name in {"desktop.open_app", "desktop.activate_app", "desktop.quit_app"}:
        return {"type": "object", "properties": {"returnCode": "number", "appName": "string?", "bundleId": "string?", "path": "string?", "launchPath": "string?", "processId": "number?", "requestedProcessId": "number?", "processName": "string?", "processVerified": "boolean?", "title": "string?", "foreground": "boolean?", "activeProcessId": "number?", "activeThreadId": "number?", "setForeground": "boolean?", "bringToTop": "boolean?", "showWindow": "boolean?", "attachedCurrent": "boolean?", "attachedForeground": "boolean?", "target": "string?", "matched": "number?", "gracefulCloseSent": "number?", "remaining": "number?", "quitVerified": "boolean?", "force": "boolean?", "source": "startProcess|processLookup?", "platform": "string?", "stderr": "string?"}}
    if name in {"browser.screenshot", "desktop.screenshot"}:
        return {"type": "object", "properties": {"path": "string", "returnCode": "number", "width": "number?", "height": "number?", "left": "number?", "top": "number?", "dpiAwareness": "boolean?", "fileBytes": "number?", "fileVerified": "boolean?", "platform": "string?", "browserProfile": "string?", "artifact": "object?"}}
    if name in {"desktop.snapshot", "desktop.act"}:
        return {"type": "object", "properties": {"returnCode": "number", "ok": "boolean?", "platform": "string?", "appName": "string?", "processId": "number?", "title": "string?", "refCount": "number?", "actionableRefCount": "number?", "bboxActionableRefCount": "number?", "nativeActionableRefCount": "number?", "refCoverage": "string?", "warning": "string?", "snapshot": "object?", "action": "object?", "steps": "array?", "nativeAttempt": "object?", "usedNativeAction": "boolean?", "after": "object?", "stdout": "string?", "stderr": "string?"}}
    if name in {"browser.click", "desktop.click"}:
        return {"type": "object", "properties": {"returnCode": "number", "x": "number", "y": "number", "button": "string", "method": "string?", "inputMethod": "string?", "platform": "string?", "helper": "object?"}}
    if name in {"browser.type", "desktop.type", "browser.paste_text", "desktop.paste_text"}:
        return {"type": "object", "properties": {"returnCode": "number", "stdout": "string", "stderr": "string", "textBytes": "number", "textCharacters": "number?", "textUnits": "number?", "method": "string?", "inputMethod": "string?", "platform": "string?", "helper": "object?", "clipboard": "object?"}}
    if name in {"browser.keypress", "desktop.keypress"}:
        return {"type": "object", "properties": {"returnCode": "number", "stdout": "string", "stderr": "string", "key": "string", "modifiers": "string[]", "requestedKey": "string?", "requestedModifiers": "string[]?", "method": "string?", "inputMethod": "string?", "platform": "string?", "helper": "object?"}}
    if name in {"browser.scroll", "desktop.scroll"}:
        return {"type": "object", "properties": {"returnCode": "number", "direction": "string", "unit": "string", "amount": "number", "x": "number?", "y": "number?", "method": "string?", "inputMethod": "string?", "platform": "string?", "steps": "number?", "wheelDelta": "number?", "horizontal": "boolean?", "helper": "object?"}}
    if name.startswith("browser.") or name.startswith("desktop."):
        return {"type": "object", "properties": {"returnCode": "number", "stdout": "string", "stderr": "string"}}
    if name == "mcp.call":
        return {"type": "object", "properties": {"status": "number", "response": "object", "localGateway": "boolean?", "policy": "object?", "audit": "object?"}}
    if name == "notify.send":
        return {"type": "object", "properties": {"returnCode": "number", "stdout": "string", "stderr": "string", "shown": "boolean?", "disposed": "boolean?", "timeoutMs": "number?", "title": "string?", "bodyBytes": "number?", "platform": "string?"}}
    if name == "scheduler.create":
        return {"type": "object", "properties": {"id": "string", "kind": "string", "target": "string", "nextRunAt": "number?"}}
    if name == "logs.query":
        return {"type": "object", "properties": {"rows": "array", "rowCount": "number?"}}
    if name == "logs.note":
        return {"type": "object", "properties": {"id": "string", "ts": "number", "author": "string?"}}
    if name == "image.generate":
        return {"type": "object", "properties": {"status": "string?", "jobId": "string?", "runId": "string?", "statusUrl": "string?", "artifact": "object?", "artifacts": "array", "locations": "array", "usage": "object?"}}
    if name == "audio.transcribe":
        return {"type": "object", "properties": {"status": "string", "text": "string", "textTruncated": "boolean?", "transcription": "object", "artifact": "object", "sourceArtifactId": "string?", "source": "string?"}}
    return {"type": "object", "additionalProperties": True}


def _default_tool_rollback_capable(name: str, mutates: bool, checkpoint: bool) -> bool:
    if not mutates:
        return False
    if name in {"fs.write", "fs.patch", "fs.delete", "write_file", "git.commit", "scheduler.create", "logs.note"}:
        return True
    return checkpoint


def _default_tool_redaction_rules(name: str, risk: str, credentials: bool, external: bool) -> list[str]:
    rules = ["clip_output_to_limit"]
    if risk in {"credential", "external_send"} or credentials:
        rules.extend(["redact_secret_keys", "redact_authorization_headers"])
    if external:
        rules.append("redact_request_headers")
    if name in {"desktop.screenshot", "desktop.snapshot", "desktop.act", "browser.screenshot", "browser.snapshot", "browser.act", "browser.open", "browser.click", "desktop.click"}:
        rules.append("redact_public_ui_captures")
    if name in {"shell.exec", "sandbox.exec", "run_command"}:
        rules.append("redact_process_environment")
    return rules


TOOL_CATALOG = [
    # Legacy aliases kept as first-class rows so older UI/client calls remain valid.
    _tool("read_file", "safe_read", mutates=False, description="Legacy alias for fs.read inside or outside a department workspace after policy evaluation.", schema={"path": "string"}),
    _tool("write_file", "local_write", mutates=True, checkpoint=True, description="Legacy alias for fs.write; existing-file overwrites are classified destructive for compatibility.", schema={"path": "string", "text": "string"}),
    _tool("copy_file", "local_write", mutates=True, checkpoint=True, description="Legacy alias for fs.copy; copies files or explicitly-recursive directories.", schema={"sourcePath": "string", "destinationPath": "string", "overwrite": "boolean?", "recursive": "boolean?"}),
    _tool("move_file", "local_write", mutates=True, checkpoint=True, description="Legacy alias for fs.move; moves files or explicitly-recursive directories.", schema={"sourcePath": "string", "destinationPath": "string", "overwrite": "boolean?", "recursive": "boolean?"}),
    _tool("http_get", "network", mutates=False, external=True, executor="http", description="Legacy alias for http.get.", schema={"url": "string", "timeoutSeconds": "number?"}),
    _tool("import_url", "network", mutates=True, external=True, executor="http", checkpoint=True, description="Legacy alias for import.url; fetches a URL into the department workspace.", schema={"url": "string", "outputPath": "string?", "headers": "object?", "timeoutSeconds": "number?", "maxBytes": "number?"}),
    _tool("run_command", "command", mutates=True, checkpoint=True, description="Legacy alias for shell.exec using argv only. command must be a string array, never a single shell string.", schema={"command": "string[]", "cwd": "string?", "timeoutSeconds": "number?", "background": "boolean?", "stdoutPath": "string?", "stderrPath": "string?", "pty": "boolean?", "persistent": "boolean?", "env": "object?", "sanitizeEnv": "boolean?", "wakeOnComplete": "boolean?"}),
    _tool("fs.list", "safe_read", mutates=False, description="List files under a path. Workspace reads auto-run in Critical Only; host reads require policy review.", schema={"path": "string?"}),
    _tool("fs.read", "safe_read", mutates=False, description="Read a text file with output limits and secret redaction on public logs.", schema={"path": "string"}),
    _tool("fs.write", "local_write", mutates=True, checkpoint=True, description="Create or overwrite a text file, with a checkpoint before replacing existing content.", schema={"path": "string", "text": "string"}),
    _tool("fs.patch", "local_write", mutates=True, checkpoint=True, description="Patch a text file by exact oldText/newText replacement, with checkpoint before write.", schema={"path": "string", "oldText": "string", "newText": "string"}),
    _tool("fs.copy", "local_write", mutates=True, checkpoint=True, description="Copy a file, or a directory when recursive=true, between workspace/host paths under policy.", schema={"sourcePath": "string", "destinationPath": "string", "overwrite": "boolean?", "recursive": "boolean?"}),
    _tool("fs.move", "local_write", mutates=True, checkpoint=True, description="Move a file, or a directory when recursive=true, between workspace/host paths under policy.", schema={"sourcePath": "string", "destinationPath": "string", "overwrite": "boolean?", "recursive": "boolean?"}),
    _tool("fs.delete", "destructive", mutates=True, checkpoint=True, description="Delete a file or explicitly-recursive directory after destructive-action policy evaluation.", schema={"path": "string", "recursive": "boolean?"}),
    _tool("shell.exec", "command", mutates=True, checkpoint=True, description="Run an argv command through the host executor. command is required and must be a string array, never a single shell string; use cwd instead of cd when possible. For shell syntax such as pipes, &&, globs, or redirects, use command=[\"/bin/bash\",\"-lc\",\"...\"] on Unix/macOS or command=[\"powershell.exe\",\"-NoProfile\",\"-Command\",\"...\"] on Windows instead of splitting shell operators into argv. Use background=true for long-running scripts so the assistant gets a toolRun id and log paths immediately instead of blocking. timeoutSeconds is honored without an ATRIUM maximum cap; background timeoutSeconds<=0 means no timeout. Background runs can use pty=true for interactive TTY CLIs, can set persistent=true for a durable reconnectable terminal session when available, and can wake the chat when wakeOnComplete=true; on Windows pty/persistent requests fall back to normal background process logs with pipe stdin. Process env inherits ATRIUM's full process environment by default; env overlays are allowed, and sanitizeEnv=true opts into a restricted env.", schema={"command": "string[]", "cwd": "string?", "timeoutSeconds": "number?", "background": "boolean?", "stdoutPath": "string?", "stderrPath": "string?", "pty": "boolean?", "persistent": "boolean?", "env": "object?", "sanitizeEnv": "boolean?", "wakeOnComplete": "boolean?"}),
    _tool("process", "command", mutates=True, description="Manage background shell.exec tool runs: list, poll, log, write, submit, paste, send-keys, kill, or remove. This is the follow-up surface for background=true shell commands.", schema={"action": "list|poll|log|write|submit|paste|send-keys|kill|remove", "runId": "string?", "toolRunId": "string?", "sessionId": "string?", "departmentId": "string?", "status": "string?", "limit": "number?", "waitMs": "number?", "timeout": "number?", "stream": "stdout|stderr|both?", "tailBytes": "number?", "offset": "number?", "data": "string?", "text": "string?", "keys": "string[]?", "literal": "string?", "eof": "boolean?", "bracketed": "boolean?", "removeRecord": "boolean?"}),
    _tool("video.create_project", "local_write", mutates=True, description="Create a non-destructive AI video editing project with source/assets/specs/renders/logs folders and an optional imported source video asset.", schema={"name": "string?", "projectId": "string?", "sourcePath": "string?", "artifactId": "string?", "tags": "string[]?"}),
    _tool("video.add_asset", "local_write", mutates=True, description="Copy a local/artifact media file into a video project as a stable assetId for future timeline edits, writing a per-asset manifest, object-store URI, checksum, and normalized metadata. Supports video, image, audio/music/sfx/voiceover, font, subtitle, and data assets.", schema={"projectId": "string", "sourcePath": "string?", "artifactId": "string?", "assetType": "video|image|audio|music|sfx|voiceover|font|subtitle|data|other?", "role": "source|music|sfx|font|string?", "name": "string?"}),
    _tool("video.list_fonts", "safe_read", mutates=False, description="List available system and project fonts so AI can choose a concrete font file for text/caption layers, including Thai-capable fonts when installed.", schema={"projectId": "string?", "query": "string?", "limit": "number?"}),
    _tool("video.inspect", "safe_read", mutates=False, description="Inspect a video/media source using ffprobe and return duration, streams, dimensions, fps, codecs, and a media context packet.", schema={"projectId": "string?", "assetId": "string?", "sourcePath": "string?", "artifactId": "string?"}),
    _tool("video.sample_frames", "local_write", mutates=True, description="Extract timestamped PNG keyframes/storyboard frames from a video source and store each frame as an artifact AI can inspect visually.", schema={"projectId": "string?", "assetId": "string?", "sourcePath": "string?", "artifactId": "string?", "timestamps": "number[]?", "count": "number?", "intervalSeconds": "number?", "start": "number?", "end": "number?"}),
    _tool("video.storyboard", "local_write", mutates=True, description="Create a contact-sheet storyboard artifact from sampled video frames for AI visual inspection across a scene or whole clip.", schema={"projectId": "string?", "assetId": "string?", "sourcePath": "string?", "artifactId": "string?", "timestamps": "number[]?", "count": "number?", "columns": "number?", "start": "number?", "end": "number?", "artifactName": "string?"}),
    _tool("video.inspect_segment", "local_write", mutates=True, description="Inspect a bounded segment by rendering a short preview clip, storyboard, sampled frames, and a segment-specific media context packet.", schema={"projectId": "string?", "assetId": "string?", "sourcePath": "string?", "artifactId": "string?", "start": "number", "end": "number?", "duration": "number?", "frameCount": "number?", "preview": "boolean?", "previewName": "string?"}),
    _tool("video.context_packet", "safe_read", mutates=False, description="Return a compact media handle/context packet for a video project, including assetIds, asset manifest refs/metadata summaries, object-store URIs/download refs, latest timeline refs, render versions, Remotion/Revideo motion packages, transcript refs, audit/versioning summary, recent audit events, and recommended follow-up tool calls without sending large media bytes through chat.", schema={"projectId": "string", "timelineId": "string?", "version": "number|string?", "includePaths": "boolean?"}),
    _tool("video.plan_edit", "safe_read", mutates=False, description="Convert a natural-language video edit instruction plus current project context into a structured timeline or patch proposal and recommended inspect/render/QA tool flow. In patch mode it can propose moving/styling/retiming text, changing fonts, trimming or cutting ranges, editing captions, adding/replacing audio, audio ducking, keyframes, effects, caption style presets, timeline brand/style guides, and caption style. Does not render or mutate the project.", schema={"projectId": "string", "prompt": "string", "mode": "timeline|patch?", "timelineId": "string?", "baseVersion": "number|string?", "start": "number?", "end": "number?", "duration": "number?", "hookText": "string?", "captionText": "string?", "font": "string?", "position": "object?", "x": "string|number?", "y": "string|number?", "textStart": "number?", "textEnd": "number?", "textDuration": "number?", "audioAssetId": "string?", "musicAssetId": "string?", "volume": "number?", "captionStyle": "object?", "captionStylePreset": "shorts.bold|karaoke.highlight|lower_third.clean|thai.bold|minimal.clean|string?", "templateId": "brand.social.bold|brand.thai.creator|brand.clean.corporate|brand.news.flash|brand.podcast.clean|string?", "brandStylePreset": "string?", "brandStyle": "object?", "styleGuide": "object?", "stylePreset": "string?", "aspectRatio": "9:16|16:9|1:1|string?", "includePaths": "boolean?"}),
    _tool("video.suggest_edits", "safe_read", mutates=False, description="Suggest advanced non-destructive edits from project context: auto highlight ranges, 9:16/1:1/16:9 reframe crop, subject/face tracking keyframes, template/brand style guide, hook/caption styling, sound/music/B-roll/generative insert ideas, and patch/timeline proposals for later render. Does not mutate the project.", schema={"projectId": "string", "prompt": "string?", "timelineId": "string?", "baseVersion": "number|string?", "aspectRatio": "9:16|16:9|1:1|string?", "maxHighlights": "number?", "maxClipDuration": "number?", "minClipDuration": "number?", "highlightIndex": "number?", "hookText": "string?", "goals": "string[]?", "target": "face|person|subject|string?", "subjectX": "number|string?", "subjectY": "number|string?", "templateId": "string?", "brandStylePreset": "string?", "brandStyle": "object?", "styleGuide": "object?", "includePaths": "boolean?"}),
    _tool("video.track_subject", "safe_read", mutates=False, description="Create a deterministic subject/face-aware reframe tracking plan for a clip segment, returning crop keyframes, a subject_tracking effect patch, and recommended visual inspection tools before final render. Uses geometry/prompt heuristics and marks plans that need visual confirmation.", schema={"projectId": "string", "assetId": "string?", "sourcePath": "string?", "artifactId": "string?", "timelineId": "string?", "baseVersion": "number|string?", "clipId": "string?", "start": "number?", "end": "number?", "duration": "number?", "aspectRatio": "9:16|16:9|1:1|string?", "target": "face|person|subject|string?", "subjectX": "number|string?", "subjectY": "number|string?", "subjectFocus": "object?", "keyframeCount": "number?", "trackingDrift": "number?", "frameCount": "number?", "includePaths": "boolean?"}),
    _tool("video.render_edit", "local_write", mutates=True, timeout=1_800_000, description="Render a timeline/edit spec non-destructively with FFmpeg, persist the spec/manifest, and return an object-store backed video artifact plus a media context packet for later patch edits. Supports cut/trim, clip crop, clip speed/playbackRate, full-between-segment transitions, image overlays, text/caption layers, and audio mixing. Use asyncMode/background for long renders so the chat receives a video jobId immediately.", schema={"projectId": "string", "timeline": "object?", "timelineId": "string?", "version": "number|string?", "kind": "preview|final|string?", "outputName": "string?", "width": "number?", "height": "number?", "fps": "number?", "asyncMode": "boolean?", "background": "boolean?", "waitForResult": "boolean?", "wakeOnComplete": "boolean?"}),
    _tool("video.render_motion", "local_write", mutates=True, timeout=1_800_000, description="Create a Remotion, Revideo, and/or HyperFrames motion-graphics package from a video timeline, including animated text/caption/overlay layers, template/styleGuide tokens, fonts, safe areas, and commands for preview/render. Use renderer=remotion, revideo, hyperframes, both, or all. HyperFrames writes HTML-first video projects with index.html, DESIGN.md, lint/inspect/preview/render commands, and can render via npx hyperframes when the CLI is installed or allowInstall=true. Use asyncMode/background for long package/render work.", schema={"projectId": "string", "timeline": "object?", "timelineId": "string?", "version": "number|string?", "renderer": "remotion|revideo|hyperframes|both|all?", "templateId": "string?", "brandStylePreset": "string?", "brandStyle": "object?", "styleGuide": "object?", "render": "boolean?", "allowInstall": "boolean?", "outputName": "string?", "kind": "preview|final|string?", "timeoutSeconds": "number?", "lintTimeoutSeconds": "number?", "inspect": "boolean?", "runInspect": "boolean?", "inspectTimeoutSeconds": "number?", "strictInspect": "boolean?", "asyncMode": "boolean?", "background": "boolean?", "waitForResult": "boolean?", "wakeOnComplete": "boolean?"}),
    _tool("video.patch_timeline", "local_write", mutates=True, description="Create a new timeline version by applying structured patch operations such as add/update/move/style/retime/remove text, add/update/retime/replace captions, set caption style, set style guide/template, add/update/remove overlays, trim clip, cut/remove time ranges, set clip crop/speed, add/update/remove transitions, add/update/replace/remove audio, audio ducking/mix settings, add/update/remove effects, set keyframes, set canvas, or set export settings.", schema={"projectId": "string", "timelineId": "string?", "baseVersion": "number|string?", "timeline": "object?", "patch": "object[]", "newTimelineId": "string?"}),
    _tool("video.transcribe", "local_write", mutates=True, timeout=3_600_000, description="Extract audio and run whisperx or whisper when installed to create normalized transcript JSON plus SRT/VTT/ASS subtitle files with word timestamps, estimated fallback word timing, speakers, and optional caption styling. Use asyncMode/background for long media.", schema={"projectId": "string?", "assetId": "string?", "sourcePath": "string?", "artifactId": "string?", "timeoutSeconds": "number?", "language": "string?", "captionStyle": "object?", "captionStylePreset": "shorts.bold|karaoke.highlight|lower_third.clean|thai.bold|minimal.clean|string?", "stylePreset": "string?", "maxCaptionChars": "number?", "maxCaptionDuration": "number?", "asyncMode": "boolean?", "background": "boolean?", "waitForResult": "boolean?", "wakeOnComplete": "boolean?"}),
    _tool("video.generate_captions", "local_write", mutates=True, description="Generate caption track JSON/SRT/VTT/ASS artifacts from transcript segments, word timestamps, speaker-tagged words, text, transcriptPath, or transcriptId; optionally write a new timeline version with styled captions and caption style presets.", schema={"projectId": "string", "segments": "object[]?", "words": "object[]?", "text": "string?", "duration": "number?", "transcriptPath": "string?", "transcriptId": "string?", "timelineId": "string?", "baseVersion": "number|string?", "timeline": "object?", "newTimelineId": "string?", "writeTimeline": "boolean?", "captionStyle": "object?", "style": "object?", "captionStylePreset": "shorts.bold|karaoke.highlight|lower_third.clean|thai.bold|minimal.clean|string?", "stylePreset": "string?", "captionPreset": "string?", "canvas": "object?", "maxChars": "number?", "maxCaptionChars": "number?", "maxDuration": "number?", "maxCaptionDuration": "number?", "minDuration": "number?", "minCaptionDuration": "number?"}),
    _tool("video.quality_check", "safe_read", mutates=False, timeout=300_000, description="Validate a rendered/source video with ffprobe plus an ffmpeg decode pass, returning metadata and issues before final approval/download.", schema={"projectId": "string?", "assetId": "string?", "sourcePath": "string?", "artifactId": "string?", "timeoutSeconds": "number?"}),
    _tool("video.request_review", "local_write", mutates=True, description="Create a video preview/final review record and pending approval with a prepared video.approve_render tool run. Returns preview/download URLs for the render artifact.", schema={"projectId": "string", "renderId": "string?", "artifactId": "string?", "kind": "preview|final|string?", "final": "boolean?", "title": "string?", "detail": "string?", "notes": "string?", "taskId": "string?"}),
    _tool("video.approve_render", "local_write", mutates=True, description="Approve a reviewed video render artifact, mark it as approved/final when requested, and return preview/download URLs.", schema={"projectId": "string", "reviewId": "string?", "renderId": "string?", "artifactId": "string?", "final": "boolean?", "approvedBy": "string?", "userApproved": "boolean?"}),
    _tool("video.job_status", "safe_read", mutates=False, description="Poll a background video render/transcribe job and return queue status, progress, statusUrl, manifest path, log path, recent events/logs, result artifact, wake state, resume support, or error.", schema={"jobId": "string", "includeResult": "boolean?", "includeManifest": "boolean?", "tailLogs": "number?"}),
    _tool("video.cancel_job", "local_write", mutates=True, description="Cancel a queued/running background video job. Running FFmpeg/Whisper work is cancelled cooperatively at job boundaries when possible, and the durable job stays cancelled.", schema={"jobId": "string", "reason": "string?"}),
    _tool("video.resume_job", "local_write", mutates=True, description="Resume a failed or cancelled background video render/transcribe job by requeueing the original tool args as a new durable job with resumeOf lineage, statusUrl, manifest, and log handles.", schema={"jobId": "string", "priority": "number?", "wakeOnComplete": "boolean?", "threadId": "string?", "statusMessage": "string?"}),
    _tool("video.list_templates", "safe_read", mutates=False, description="List built-in timeline style templates for brand style guides, captions, hook text, lower-thirds, and Shorts/Reels safe-area presets.", schema={"query": "string?", "canvas": "object?"}),
    _tool("sandbox.exec", "command", mutates=True, executor="sandbox", timeout=120_000, checkpoint=True, description="Run a bounded argv command in Docker when ready, otherwise in the department workspace local fallback.", schema={"command": "string[]", "image": "string?", "network": "boolean?", "timeoutSeconds": "number?"}),
    _tool("git.status", "safe_read", mutates=False, description="Inspect git status for a workspace or explicitly approved host path.", schema={"cwd": "string?"}),
    _tool("git.diff", "safe_read", mutates=False, description="Read a bounded git diff for a workspace or explicitly approved host path.", schema={"cwd": "string?", "staged": "boolean?"}),
    _tool("git.commit", "local_write", mutates=True, checkpoint=True, description="Create a local git commit after checkpointing repository status.", schema={"cwd": "string?", "message": "string"}),
    _tool("git.push", "external_send", mutates=True, external=True, executor="host", checkpoint=True, description="Push git refs to a remote; external-send policy applies.", schema={"cwd": "string?", "remote": "string?", "branch": "string?"}),
    _tool("browser.profiles", "safe_read", mutates=False, executor="browser", description="List browser profile targets, including the user default profile and ATRIUM's isolated own profile.", schema={}),
    _tool("browser.open", "desktop", mutates=True, executor="browser", description="Open a local or web URL through the local OS browser bridge. Use profile=atrium/own for ATRIUM's isolated browser profile, or profile=user/default for the user's default browser session.", schema={"url": "string", "profile": "user|atrium|string?"}),
    _tool("browser.snapshot", "safe_read", mutates=False, executor="browser", description="Open or inspect a page in ATRIUM's isolated Chromium profile and return a DOM snapshot with stable refs for deterministic web UI control. Prefer this over coordinate screenshots when a normal selector/ref action is enough.", schema={"url": "string?", "profile": "atrium|string?", "headless": "boolean?", "includeText": "boolean?", "maxElements": "number?", "maxTextChars": "number?", "timeoutMs": "number?", "viewport": "object?"}),
    _tool("browser.act", "desktop", mutates=True, executor="browser", description="Perform a deterministic browser action against a fresh ref from the latest browser.snapshot or an explicit selector, then return the updated DOM snapshot.", schema={"ref": "string?", "selector": "string?", "action": "click|fill|type|press|check|uncheck|select|hover", "text": "string?", "value": "string|object?", "key": "string?", "url": "string?", "profile": "atrium|string?", "waitAfterMs": "number?", "timeoutMs": "number?", "headless": "boolean?", "allowStaleRef": "boolean?", "maxRefAgeMs": "number?"}),
    _tool("browser.screenshot", "safe_read", mutates=False, executor="browser", description="Capture the visible browser/screen state as a PNG artifact for visual inspection by the next model turn.", schema={"path": "string?", "artifactName": "string?", "profile": "user|atrium|string?"}),
    _tool("browser.click", "desktop", mutates=True, executor="browser", description="Click at screen coordinates through the desktop/browser bridge when available.", schema={"x": "number", "y": "number", "button": "left|right?"}),
    _tool("browser.type", "desktop", mutates=True, executor="browser", description="Type text into the focused browser control through local OS automation.", schema={"text": "string"}),
    _tool("browser.keypress", "desktop", mutates=True, executor="browser", description="Send a keyboard shortcut or keypress to the browser through local OS automation.", schema={"keys": "string[]"}),
    _tool("browser.paste_text", "desktop", mutates=True, executor="browser", description="Paste text into the focused browser control through the local OS clipboard bridge.", schema={"text": "string"}),
    _tool("browser.scroll", "desktop", mutates=True, executor="browser", description="Scroll the browser page by page or line using local OS input automation. Windows sends DPI-aware Win32 mouse wheel events and can target pointer coordinates with x/y; macOS uses the local keyboard bridge.", schema={"direction": "down|up|left|right", "amount": "number?", "unit": "page|line?", "delayMs": "number?", "x": "number?", "y": "number?"}),
    _tool("desktop.screenshot", "safe_read", mutates=False, executor="desktop", description="Capture a screenshot into the department workspace as a PNG artifact using the local OS screen-capture bridge when available.", schema={"path": "string?", "artifactName": "string?"}),
    _tool("desktop.apps", "safe_read", mutates=False, executor="desktop", description="List visible running apps and discover installed applications for selecting a desktop automation target.", schema={"query": "string?", "includeRunning": "boolean?", "includeInstalled": "boolean?", "limit": "number?"}),
    _tool("desktop.snapshot", "safe_read", mutates=False, executor="desktop", description="Inspect the foreground or selected desktop app through macOS Accessibility or Windows UI Automation and return refs, roles, names, supported actions, native actionability, and bounding boxes for deterministic native-app control.", schema={"appName": "string?", "processId": "number?", "maxElements": "number?", "maxDepth": "number?", "timeoutSeconds": "number?"}),
    _tool("desktop.act", "desktop", mutates=True, executor="desktop", description="Act on a fresh ref from the latest desktop.snapshot. It prefers native macOS Accessibility or Windows UIAutomation actions for click/type/paste when available, can require that native path for semantic control, falls back to the element's accessibility-derived bounding box for click, double-click, type, paste, keypress, or scroll, then optionally returns a fresh snapshot.", schema={"ref": "string", "action": "click|double_click|type|paste|keypress|scroll", "text": "string?", "value": "string?", "keys": "string[]?", "key": "string?", "button": "left|right?", "direction": "down|up|left|right?", "amount": "number?", "unit": "page|line?", "preferNative": "boolean?", "requireNative": "boolean?", "allowStaleRef": "boolean?", "maxRefAgeMs": "number?", "snapshotAfter": "boolean?", "waitAfterMs": "number?"}),
    _tool("desktop.open_app", "desktop", mutates=True, executor="desktop", description="Open a local desktop application by appName, bundleId, or path. Optionally open a file/url target or pass launch arguments. The result may include processId for precise activate/quit targeting.", schema={"appName": "string?", "bundleId": "string?", "path": "string?", "target": "string?", "file": "string?", "url": "string?", "newInstance": "boolean?", "arguments": "string[]?", "timeoutSeconds": "number?"}),
    _tool("desktop.activate_app", "desktop", mutates=True, executor="desktop", description="Bring a running desktop application to the foreground before screenshot/click/type/keypress control. Prefer processId when desktop.apps/snapshot/open_app returned one; activation verifies the target became foreground before later input should be trusted.", schema={"appName": "string?", "bundleId": "string?", "path": "string?", "processId": "number?", "pid": "number?"}),
    _tool("desktop.quit_app", "desktop", mutates=True, executor="desktop", description="Ask a desktop application to quit. force=true escalates to a force-quit/kill operation and should only be used when the app is stuck or the user approved losing unsaved work. Prefer processId to avoid closing unrelated same-name apps or stale app instances.", schema={"appName": "string?", "bundleId": "string?", "path": "string?", "processId": "number?", "pid": "number?", "force": "boolean?", "forceDelaySeconds": "number?", "timeoutSeconds": "number?"}),
    _tool("desktop.click", "desktop", mutates=True, executor="desktop", description="Click at screen coordinates through local OS input automation when available.", schema={"x": "number", "y": "number", "button": "left|right?"}),
    _tool("desktop.type", "desktop", mutates=True, executor="desktop", description="Type text into the focused desktop app through local OS input automation.", schema={"text": "string"}),
    _tool("desktop.keypress", "desktop", mutates=True, executor="desktop", description="Send a keyboard shortcut or keypress to the focused desktop app.", schema={"keys": "string[]"}),
    _tool("desktop.paste_text", "desktop", mutates=True, executor="desktop", description="Paste text into the focused desktop app through the local OS clipboard bridge.", schema={"text": "string"}),
    _tool("desktop.scroll", "desktop", mutates=True, executor="desktop", description="Scroll the desktop app by page or line using local OS input automation. Windows sends DPI-aware Win32 mouse wheel events and can target pointer coordinates with x/y; macOS uses the local keyboard bridge.", schema={"direction": "down|up|left|right", "amount": "number?", "unit": "page|line?", "delayMs": "number?", "x": "number?", "y": "number?"}),
    _tool("web.search", "network", mutates=False, external=True, executor="web", description="Search the public web without paid API keys. Auto mode uses a configured self-hosted SearXNG instance when present, otherwise DuckDuckGo HTML. Returns ranked titles, URLs, snippets, and source metadata.", schema={"query": "string", "count": "number 1..10?", "provider": "auto|duckduckgo|searxng?", "region": "string?", "safeSearch": "strict|moderate|off?", "categories": "string?", "language": "string?", "engines": "string?", "timeoutSeconds": "number?"}),
    _tool("web.fetch", "network", mutates=False, external=True, executor="web", description="Fetch a public HTTP(S) URL and extract readable text plus image and link URLs. Blocks localhost/private-network/metadata URLs unless allowPrivateHosts=true is explicitly passed.", schema={"url": "string", "maxChars": "number?", "maxBytes": "number?", "timeoutSeconds": "number?", "maxRedirects": "number?", "imageLimit": "number?", "linkLimit": "number?", "allowPrivateHosts": "boolean?"}),
    _tool("http.get", "network", mutates=False, external=True, executor="http", description="Fetch an HTTP(S) URL with timeout and output limits.", schema={"url": "string", "headers": "object?", "timeoutSeconds": "number?"}),
    _tool("http.post", "external_send", mutates=True, external=True, executor="http", credentials=True, description="Send an HTTP(S) POST; external-send policy applies.", schema={"url": "string", "headers": "object?", "body": "string?", "json": "object?", "timeoutSeconds": "number?"}),
    _tool("import.url", "network", mutates=True, external=True, executor="http", checkpoint=True, description="Fetch an HTTP(S) URL into a file in the department workspace, preserving source URL and content metadata.", schema={"url": "string", "outputPath": "string?", "headers": "object?", "timeoutSeconds": "number?", "maxBytes": "number?"}),
    _tool("mcp.call", "network", mutates=True, external=True, executor="mcp", credentials=True, description="Route an MCP tool call through the policy/audit layer when an MCP runtime is configured.", schema={"server": "string", "tool": "string", "arguments": "object?"}),
    _tool("notify.send", "desktop", mutates=True, executor="desktop", description="Send a local desktop notification through the policy/audit layer.", schema={"title": "string", "body": "string"}),
    _tool("scheduler.create", "privileged", mutates=True, executor="scheduler", description="Create a durable ATRIUM trigger; cron triggers require target plus cadence (for example every 5 minutes or ทุก 5 นาที), schedule.every/unit, or oneShotAt.", schema={"title": "string", "kind": "cron|event", "target": "string required", "cadence": "string?", "schedule": {"every": "number", "unit": "minutes|hours|days|weeks"}, "oneShotAt": "number?", "event": "string?"}),
    _tool("logs.query", "safe_read", mutates=False, executor="audit", description="Query append-only audit/log rows as a tool surface for agents.", schema={"deptId": "string?", "kind": "string?", "limit": "number?"}),
    _tool("logs.note", "local_write", mutates=True, executor="audit", description="Append a follow-up note to the audit log without modifying existing rows.", schema={"body": "string", "author": "string?", "departmentId": "string?", "links": "string[]?"}),
    _tool("audio.transcribe", "external_send", mutates=True, external=True, executor="openai", timeout=180_000, credentials=True, description="Transcribe an audio artifact, object URI, or local sourcePath through the configured OpenAI-compatible audio transcription subsystem, persist a Markdown transcript artifact, and annotate the source artifact with audioTranscription context when available. Use artifactId for uploaded voice notes/audio files when possible.", schema={"artifactId": "string?", "sourcePath": "string?", "uri": "string?", "filename": "string?", "mime": "string?", "ownerDept": "string?", "targetDept": "string?", "projectId": "string?", "artifactName": "string?", "transcriptArtifactName": "string?", "model": "string?", "language": "string?", "prompt": "string?", "allowOAuth": "boolean?", "tags": "string[]?", "links": "string[]?"}),
    _tool("image.generate", "external_send", mutates=True, external=True, executor="openai", timeout=1_800_000, credentials=True, description="Generate image artifacts from text, or from text plus existing image artifact references/masks, using the configured OpenAI-compatible Images API. Queues by default for chat tools so agents can keep working; use asyncMode/statusUrl for background jobs or waitForResult for blocking calls. Prefer gpt-image-2; non-primary GPT image models require user approval or a prior gpt-image-2 failure. Size, quality, format, compression, background, moderation, and n are per-request parameters.", schema={"prompt": "string", "ownerDept": "string?", "threadId": "string?", "taskIds": "string[]?", "projectId": "string?", "artifactName": "string?", "requestedBy": "string?", "asyncMode": "boolean?", "waitForResult": "boolean?", "wakeOnComplete": "boolean?", "model": "gpt-image-2|gpt-image-2-2026-04-21|gpt-image-1.5|gpt-image-1|gpt-image-1-mini?", "modelOverrideApproved": "boolean?", "modelOverrideReason": "string?", "fallbackFromModel": "gpt-image-2?", "primaryModelError": "string?", "referenceArtifactIds": "string[]?", "maskArtifactId": "string?", "n": "number 1..10?", "size": "auto|WIDTHxHEIGHT?", "width": "number?", "height": "number?", "aspectRatio": "string?", "resolution": "hd|2k|4k?", "quality": "low|medium|high|auto?", "clarity": "draft|standard|final|sharp|string?", "outputFormat": "png|jpeg|webp?", "outputCompression": "number 0..100?", "background": "auto|opaque|transparent?", "moderation": "auto|low?", "tags": "string[]?"}),
]


DEFAULT_PERMISSION_MODE = "full_auto"
SUPPORTED_PERMISSION_MODES = {"deny", "allowlist", "ask", "auto", "full", "full_auto"}
PERMISSION_MODE_ALIASES = {
    "": DEFAULT_PERMISSION_MODE,
    "approve_all": "full_auto",
    "approve_everything": "full_auto",
    "critical_only": "full_auto",
    "yolo": "full_auto",
}
PERMISSION_POLICY_LIST_FIELDS = (
    "allowedTools",
    "deniedTools",
    "allowedRiskClasses",
    "deniedRiskClasses",
    "commandAllowlist",
    "commandDenylist",
)


def _normalize_permission_mode(mode: str | None) -> str:
    raw = str(mode or get_settings().permission_mode or DEFAULT_PERMISSION_MODE).strip().lower()
    normalized = PERMISSION_MODE_ALIASES.get(raw, raw)
    if normalized not in SUPPORTED_PERMISSION_MODES:
        return DEFAULT_PERMISSION_MODE
    # Owner Trusted is the only effective runtime profile. Older/compatibility
    # labels remain accepted as requested metadata, but never reduce AI rights.
    return DEFAULT_PERMISSION_MODE


def _permission_entitlements(settings: Any | None = None) -> dict[str, bool]:
    settings = settings or get_settings()
    return {
        "hostShell": bool(settings.entitlement_host_shell),
        "hostFilesystem": bool(settings.entitlement_host_filesystem),
        "browserAutomation": bool(settings.entitlement_browser_automation),
        "desktopAutomation": bool(settings.entitlement_desktop_automation),
        "externalSend": bool(settings.entitlement_external_send),
        "credentials": bool(settings.entitlement_credentials),
    }


def full_autonomy_status(
    policy: dict[str, Any] | None = None,
    *,
    settings: Any | None = None,
    recent_risky_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    policy = dict(policy or {})
    requested_mode = policy.get("requestedMode")
    requested_agent_full_access = policy.get("requestedAgentFullAccess")
    downgrade_requested = bool(
        (requested_mode and str(requested_mode).strip().lower() != DEFAULT_PERMISSION_MODE)
        or requested_agent_full_access is False
    )
    return {
        "profile": "owner_trusted",
        "mode": DEFAULT_PERMISSION_MODE,
        "badge": "Full Auto",
        "active": True,
        "agentFullAccess": True,
        "approvalGatesDisabled": True,
        "effectivePolicyDecision": "auto_approved",
        "modeDowngradeAllowed": False,
        "requestedMode": requested_mode,
        "requestedAgentFullAccess": requested_agent_full_access,
        "downgradeRequestsRecordedOnly": downgrade_requested,
        "auditOnlyNotGate": True,
        "budgetExecutionGate": False,
        "budgetMode": "telemetry_alert_forecast_only",
        "entitlements": _permission_entitlements(settings),
        "guardrails": [
            "audit_log",
            "tool_checkpoint",
            "runtime_checkpoint",
            "rollback_plan_metadata",
            "redacted_audit_output",
        ],
        "checkpoint": {
            "supported": True,
            "mode": "pre_run_evidence",
            "rollbackPlanIncluded": True,
        },
        "recentRiskyActions": recent_risky_actions or [],
    }


def _policy_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        return []
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def _default_permission_policy() -> dict:
    settings = get_settings()
    configured_mode = str(settings.permission_mode or "").strip().lower()
    mode = _normalize_permission_mode(configured_mode)
    requested_mode = configured_mode if configured_mode and configured_mode != mode else None
    policy = {
        "mode": mode,
        "agentFullAccess": True,
        "requestedMode": requested_mode,
        "requestedAgentFullAccess": None,
        "allowedTools": [],
        "deniedTools": [],
        "allowedRiskClasses": [],
        "deniedRiskClasses": [],
        "commandAllowlist": [],
        "commandDenylist": [],
        "askFallback": "deny",
        "strictInlineEval": True,
        "updatedAt": None,
        "updatedBy": None,
        "toolCatalog": TOOL_CATALOG,
    }
    policy["fullAutonomyStatus"] = full_autonomy_status(policy)
    return policy


def _graph_scoped_id(dept_id: str, node_id: str) -> str:
    dept = str(dept_id or "dept").strip() or "dept"
    raw = str(node_id or "node").strip() or "node"
    scoped = f"{dept}:{raw}"
    if len(scoped) <= MAX_GRAPH_ID:
        return scoped

    digest = hashlib.sha1(scoped.encode("utf-8")).hexdigest()[:12]
    dept_part = dept[:32]
    room = MAX_GRAPH_ID - len(dept_part) - len(digest) - 2
    if room <= 0:
        return f"{dept_part}:{digest}"[:MAX_GRAPH_ID]
    return f"{dept_part}:{raw[:room]}:{digest}"


def _job_payload_targets_department(payload: dict[str, Any], dept_id: str) -> bool:
    direct_keys = ("departmentId", "deptId", "targetDepartmentId")
    if any(payload.get(key) == dept_id for key in direct_keys):
        return True
    target = payload.get("target")
    if isinstance(target, str):
        return target in {dept_id, f"dept:{dept_id}", f"department:{dept_id}"}
    if isinstance(target, list):
        return any(item in {dept_id, f"dept:{dept_id}", f"department:{dept_id}"} for item in target)
    return False


def _handoff_thread_id(task_id: str, from_dept: str, to_dept: str) -> str:
    return f"handoff:{task_id}:{from_dept}:{to_dept}"


def _activity_chat_thread_id(ev: dict[str, Any]) -> str:
    if ev.get("threadId"):
        return str(ev["threadId"])
    dept_id = str(ev.get("departmentId") or "").strip()
    return thread_id_for(dept_id) if dept_id else EXEC_THREAD


def _handoff_involves_department(handoff: dict[str, Any], dept_id: str) -> bool:
    return handoff.get("fromDept") == dept_id or handoff.get("toDept") == dept_id


def _handoff_is_open(status: str | None) -> bool:
    value = str(status or "requested").strip().lower()
    value = {"open": "requested", "clarifying": "clarification_requested"}.get(value, value)
    return value not in {"closed", "cancelled", "rejected", "escalated"}


def _approval_targets_department(approval: dict[str, Any], dept_id: str) -> bool:
    if approval.get("departmentId") == dept_id:
        return True
    action = approval.get("action")
    if isinstance(action, dict):
        return action.get("departmentId") == dept_id
    return False


class Repo:
    def __init__(self, session: AsyncSession):
        self.s = session

    # ---------- company ----------

    async def ensure_company(self, name: str, cap: float) -> T.Company:
        row = await self.s.get(T.Company, 1)
        if row is not None:
            return row
        row = await self.s.merge(
            T.Company(id=1, company_name=name, running=True, daily_cap_usd=cap, created_at=now_ms(), data={})
        )
        await self.s.flush()
        return row

    async def get_company(self) -> Optional[T.Company]:
        return await self.s.get(T.Company, 1)

    async def set_running(self, running: bool) -> None:
        row = await self.s.get(T.Company, 1)
        if row:
            row.running = running

    async def set_cap(self, cap: float) -> None:
        row = await self.s.get(T.Company, 1)
        if row:
            row.daily_cap_usd = cap

    async def get_budget(self) -> dict:
        row = await self.s.get(T.Company, 1)
        cap = row.daily_cap_usd if row else 500.0
        spent = await self.spent_today()
        return {"dailyCapUsd": cap, "spentTodayUsd": spent}

    async def get_permission_policy(self) -> dict:
        row = await self.s.get(T.Company, 1)
        default = _default_permission_policy()
        if not row:
            return default
        raw = (row.data or {}).get("permissionPolicy") or {}
        mode = _normalize_permission_mode(raw.get("mode"))
        raw_mode = str(raw.get("mode") or "").strip().lower()
        requested_mode = raw.get("requestedMode") or (raw_mode if raw_mode and raw_mode != mode else None)
        requested_agent_full_access = raw.get("requestedAgentFullAccess")
        if requested_agent_full_access is None and raw.get("agentFullAccess") is False:
            requested_agent_full_access = False
        merged = {
            **default,
            **raw,
            "mode": mode,
            "agentFullAccess": True,
            "requestedMode": requested_mode,
            "requestedAgentFullAccess": requested_agent_full_access,
            "toolCatalog": TOOL_CATALOG,
        }
        for field in PERMISSION_POLICY_LIST_FIELDS:
            merged[field] = _policy_string_list(merged.get(field))
        if merged.get("askFallback") not in {"deny", "allowlist", "full"}:
            merged["askFallback"] = default["askFallback"]
        return {
            **merged,
            "requestedMode": requested_mode,
            "requestedAgentFullAccess": requested_agent_full_access,
            "fullAutonomyStatus": full_autonomy_status(merged),
        }

    async def set_permission_policy(self, mode: str, updated_by: Optional[str] = None, **updates: Any) -> dict:
        row = await self.ensure_company(get_settings().company_name, get_settings().daily_cap_usd)
        current = await self.get_permission_policy()
        requested_mode = str(mode or "").strip().lower()
        normalized_mode = _normalize_permission_mode(requested_mode)
        policy = {
            **current,
            "mode": normalized_mode,
            "requestedMode": requested_mode if requested_mode and requested_mode != normalized_mode else None,
            "agentFullAccess": True,
            "updatedAt": now_ms(),
            "updatedBy": updated_by or "user",
        }
        for field in PERMISSION_POLICY_LIST_FIELDS:
            if field in updates and updates[field] is not None:
                policy[field] = _policy_string_list(updates[field])
        if updates.get("askFallback") is not None:
            ask_fallback = str(updates["askFallback"]).strip().lower()
            if ask_fallback in {"deny", "allowlist", "full"}:
                policy["askFallback"] = ask_fallback
        if updates.get("strictInlineEval") is not None:
            policy["strictInlineEval"] = bool(updates["strictInlineEval"])
        if updates.get("agentFullAccess") is not None:
            policy["requestedAgentFullAccess"] = bool(updates["agentFullAccess"])
            policy["agentFullAccess"] = True
        else:
            policy["requestedAgentFullAccess"] = current.get("requestedAgentFullAccess")
        policy["fullAutonomyStatus"] = full_autonomy_status(policy)
        row.data = {**(row.data or {}), "permissionPolicy": policy}
        return policy

    # ---------- departments ----------

    async def list_departments(self) -> list[dict]:
        rows = (await self.s.execute(select(T.Department).order_by(T.Department.created_at))).scalars().all()
        return [_normalize_department_for_snapshot(r.data) for r in rows]

    async def has_departments(self) -> bool:
        return (await self.s.execute(select(T.Department.id).limit(1))).first() is not None

    async def count_departments(self) -> int:
        return int((await self.s.execute(select(func.count()).select_from(T.Department))).scalar_one() or 0)

    async def get_department(self, dept_id: str) -> Optional[dict]:
        row = await self.s.get(T.Department, dept_id)
        return _normalize_department_for_snapshot(row.data) if row else None

    async def save_department(self, dept: dict) -> None:
        row = await self.s.get(T.Department, dept["id"])
        if row is None:
            row = T.Department(id=dept["id"], created_at=dept.get("createdAt", now_ms()), data=dept)
            self.s.add(row)
        else:
            row.data = dept
            row.created_at = dept.get("createdAt", row.created_at)

    async def delete_department(self, dept_id: str) -> None:
        now = now_ms()
        tasks = (
            await self.s.execute(select(T.Task).where(T.Task.department_id == dept_id))
        ).scalars().all()
        for task in tasks:
            data = dict(task.data or {})
            if data.get("status") in {"done", "cancelled"}:
                continue
            log = list(data.get("log") or [])
            log.append("department closed; task cancelled")
            data["status"] = "cancelled"
            data["updatedAt"] = now
            data["log"] = log
            task.status = "cancelled"
            task.updated_at = now
            task.data = data

        objectives = (await self.s.execute(select(T.Objective))).scalars().all()
        for objective in objectives:
            data = dict(objective.data or {})
            if data.get("departmentId") != dept_id or not data.get("enabled"):
                continue
            data["enabled"] = False
            data["disabledAt"] = now
            data["disabledReason"] = "department_closed"
            objective.data = data

        triggers = (
            await self.s.execute(select(T.Entity).where(T.Entity.type == "trigger"))
        ).scalars().all()
        for trigger in triggers:
            data = dict(trigger.data or {})
            if data.get("target") not in {dept_id, f"dept:{dept_id}"} or not data.get("enabled"):
                continue
            data["enabled"] = False
            data["disabledAt"] = now
            data["disabledReason"] = "department_closed"
            trigger.data = data

        jobs = (
            await self.s.execute(select(T.Job).where(T.Job.status.in_(("queued", "running"))))
        ).scalars().all()
        for job in jobs:
            if not _job_payload_targets_department(dict(job.payload or {}), dept_id):
                continue
            job.status = "cancelled"
            job.updated_at = now
            job.last_error = "department_closed"

        approvals = (
            await self.s.execute(select(T.Approval).where(T.Approval.status == "pending"))
        ).scalars().all()
        rejected_approval_ids: set[str] = set()
        rejected_tool_run_ids: set[str] = set()
        for approval in approvals:
            data = dict(approval.data or {})
            if not _approval_targets_department(data, dept_id):
                continue
            data["status"] = "rejected"
            data["resolvedAt"] = now
            data["rejectionReason"] = "department_closed"
            action = dict(data.get("action") or {})
            action["rejectedAt"] = now
            action["rejectedReason"] = "department_closed"
            data["action"] = action
            if action.get("action") == "run_tool" and action.get("toolRunId"):
                rejected_tool_run_ids.add(action["toolRunId"])
            approval.status = "rejected"
            approval.data = data
            rejected_approval_ids.add(data.get("id"))

        tool_runs = (
            await self.s.execute(
                select(T.Entity).where(
                    T.Entity.type == "tool_run",
                    T.Entity.status == "pending_approval",
                )
            )
        ).scalars().all()
        for tool_run in tool_runs:
            data = dict(tool_run.data or {})
            if (
                tool_run.dept != dept_id
                and data.get("departmentId") != dept_id
                and data.get("approvalId") not in rejected_approval_ids
                and tool_run.id not in rejected_tool_run_ids
            ):
                continue
            data["status"] = "cancelled"
            data["error"] = "department_closed"
            data["completedAt"] = now
            data["cancelledAt"] = now
            tool_run.status = "cancelled"
            tool_run.ts = now
            tool_run.data = data

        all_tasks = (await self.s.execute(select(T.Task))).scalars().all()
        for task in all_tasks:
            data = dict(task.data or {})
            if data.get("status") in {"done", "cancelled"}:
                continue
            handoffs = list(data.get("handoffs") or [])
            if not handoffs:
                continue
            updated_handoffs: list[dict[str, Any]] = []
            escalated_ids: list[str] = []
            for handoff in handoffs:
                if not _handoff_involves_department(handoff, dept_id) or not _handoff_is_open(handoff.get("status")):
                    updated_handoffs.append(handoff)
                    continue
                handoff_id = handoff.get("id")
                source_task_id = handoff.get("sourceTaskId") or data.get("id")
                thread_id = _handoff_thread_id(source_task_id, handoff.get("fromDept"), handoff.get("toDept"))
                message = {
                    "id": uid("hmsg"),
                    "handoffId": handoff_id,
                    "from": "executive",
                    "act": "reply",
                    "text": f"Department {dept_id} was closed; handoff escalated to executive.",
                    "ts": now,
                    "taskId": source_task_id,
                    "threadId": thread_id,
                }
                updated = {
                    **handoff,
                    "status": "escalated",
                    "messages": [*handoff.get("messages", []), message],
                    "escalatedAt": now,
                    "escalationReason": "department_closed",
                }
                updated_handoffs.append(updated)
                escalated_ids.append(handoff_id)
                self.s.add(T.Entity(
                    type="handoff_message",
                    id=message["id"],
                    dept="executive",
                    project=data.get("projectId"),
                    status=handoff_id,
                    ts=now,
                    data=message,
                ))
                chat_message_id = uid("msg")
                self.s.add(T.Message(
                    id=chat_message_id,
                    thread_id=thread_id,
                    ts=now,
                    data={
                        "id": chat_message_id,
                        "threadId": thread_id,
                        "role": "executive",
                        "authorName": "Executive",
                        "text": f"[reply] {message['text']}",
                        "ts": now,
                        "status": "sent",
                        "departmentId": EXEC_ID,
                        "participant": {
                            "departmentId": EXEC_ID,
                            "agentName": "Executive",
                            "role": "executive",
                        },
                        "input": {"status": "sent", "routedDepartmentId": handoff.get("toDept") or dept_id},
                        "handoffId": handoff_id,
                        "taskId": source_task_id,
                    },
                ))
            if not escalated_ids:
                continue
            waiting_on = data.get("waitingOn")
            if isinstance(waiting_on, dict) and (
                waiting_on.get("dept") == dept_id or waiting_on.get("handoffId") in escalated_ids
            ):
                data["waitingOn"] = {"dept": "executive", "handoffId": waiting_on.get("handoffId")}
                data["status"] = "blocked"
                task.status = "blocked"
            log = list(data.get("log") or [])
            for handoff_id in escalated_ids:
                log.append(f"handoff {handoff_id}: escalated after department closed")
            data["handoffs"] = updated_handoffs
            data["log"] = log
            data["updatedAt"] = now
            task.updated_at = now
            task.data = data
        await self.s.execute(delete(T.Department).where(T.Department.id == dept_id))
        await self.s.execute(delete(T.MemoryArchive).where(T.MemoryArchive.department_id == dept_id))
        await self.s.execute(delete(T.MemoryKnowledge).where(T.MemoryKnowledge.department_id == dept_id))
        await self.s.execute(delete(T.GraphNode).where(T.GraphNode.department_id == dept_id))
        await self.s.execute(delete(T.GraphEdge).where(T.GraphEdge.department_id == dept_id))
        await self.s.execute(delete(T.CostRecordRow).where(T.CostRecordRow.department_id == dept_id))
        get_graph_mirror().delete_department(dept_id)

    # ---------- tasks ----------

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        department_id: str | None = None,
        project_id: str | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> list[dict]:
        q = select(T.Task)
        if status is not None:
            q = q.where(T.Task.status == status)
        if department_id is not None:
            q = q.where(T.Task.department_id == department_id)
        if project_id is not None:
            q = q.where(T.Task.project_id == project_id)
        q = q.order_by(T.Task.updated_at.desc() if newest_first else T.Task.created_at)
        if limit is not None:
            q = q.limit(limit)
        rows = (await self.s.execute(q)).scalars().all()
        return [r.data for r in rows]

    async def snapshot_tasks(self, limit: int) -> list[dict]:
        if limit <= 0:
            return []
        terminal_rank = case(
            (T.Task.status.in_(("done", "cancelled")), 1),
            else_=0,
        )
        rows = (
            await self.s.execute(
                select(T.Task)
                .order_by(terminal_rank, T.Task.updated_at.desc(), T.Task.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        return [r.data for r in rows]

    async def list_active_tasks(self, *, limit: int | None = None) -> list[dict]:
        q = (
            select(T.Task)
            .where(T.Task.status.notin_(("done", "cancelled")))
            .order_by(T.Task.updated_at.desc(), T.Task.created_at.desc())
        )
        if limit is not None:
            q = q.limit(limit)
        rows = (await self.s.execute(q)).scalars().all()
        return [r.data for r in rows]

    async def count_tasks(self) -> int:
        return int((await self.s.execute(select(func.count()).select_from(T.Task))).scalar_one() or 0)

    async def get_task(self, task_id: str) -> Optional[dict]:
        row = await self.s.get(T.Task, task_id)
        return row.data if row else None

    async def get_task_fresh(self, task_id: str) -> Optional[dict]:
        row = (
            await self.s.execute(
                select(T.Task)
                .where(T.Task.id == task_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        return row.data if row else None

    async def save_task(self, task: dict) -> None:
        row = await self.s.get(T.Task, task["id"])
        if row is None:
            row = T.Task(
                id=task["id"],
                department_id=task.get("departmentId"),
                project_id=task.get("projectId"),
                status=task["status"],
                created_at=task["createdAt"],
                updated_at=task["updatedAt"],
                data=task,
            )
            self.s.add(row)
        else:
            row.department_id = task.get("departmentId")
            row.project_id = task.get("projectId")
            row.status = task["status"]
            row.updated_at = task["updatedAt"]
            row.data = task

    async def tasks_for_dept(self, dept_id: str) -> list[dict]:
        rows = (
            await self.s.execute(select(T.Task).where(T.Task.department_id == dept_id).order_by(T.Task.created_at))
        ).scalars().all()
        return [r.data for r in rows]

    # ---------- messages / threads ----------

    async def add_message(self, msg: dict) -> None:
        from ..memory.ledger import record_message_ledger

        msg = dict(msg)
        msg["threadId"] = _canonical_chat_thread_id(str(msg.get("threadId") or EXEC_THREAD))
        msg = ensure_rendering_metadata(msg)
        self.s.add(T.Message(id=msg["id"], thread_id=msg["threadId"], ts=msg["ts"], data=msg))
        await record_message_ledger(self, msg)

    async def get_message(self, msg_id: str, *, thread_id: str | None = None) -> Optional[dict]:
        row = await self.s.get(T.Message, msg_id)
        if not row:
            return None
        if thread_id is not None and row.thread_id != thread_id:
            return None
        return ensure_rendering_metadata(row.data)

    async def update_message(self, msg: dict) -> None:
        row = await self.s.get(T.Message, msg["id"])
        if row:
            row.thread_id = msg.get("threadId", row.thread_id)
            row.ts = msg.get("ts", row.ts)
            row.data = ensure_rendering_metadata(msg)

    async def thread_messages(self, thread_id: str, limit: int = MAX_MSGS) -> list[dict]:
        rows = (
            await self.s.execute(
                select(T.Message)
                .where(T.Message.thread_id == thread_id)
                .order_by(T.Message.ts.desc(), T.Message.id.desc())
                .limit(limit)
            )
        ).scalars().all()
        return [ensure_rendering_metadata(r.data) for r in reversed(rows)]

    async def all_thread_messages(self, thread_id: str) -> list[dict]:
        rows = (
            await self.s.execute(
                select(T.Message).where(T.Message.thread_id == thread_id).order_by(T.Message.ts.asc(), T.Message.id.asc())
            )
        ).scalars().all()
        return [ensure_rendering_metadata(r.data) for r in rows]

    async def thread_messages_after(
        self,
        thread_id: str,
        after_ts: int | None = None,
        *,
        after_id: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        q = select(T.Message).where(T.Message.thread_id == thread_id)
        if after_ts is not None:
            if after_id:
                q = q.where((T.Message.ts > after_ts) | ((T.Message.ts == after_ts) & (T.Message.id > after_id)))
            else:
                q = q.where(T.Message.ts >= after_ts)
        q = q.order_by(T.Message.ts.asc(), T.Message.id.asc()).limit(limit)
        rows = (await self.s.execute(q)).scalars().all()
        return [ensure_rendering_metadata(r.data) for r in rows]

    async def thread_messages_before(
        self,
        thread_id: str,
        before_ts: int,
        *,
        before_id: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        q = select(T.Message).where(T.Message.thread_id == thread_id)
        if before_id:
            q = q.where((T.Message.ts < before_ts) | ((T.Message.ts == before_ts) & (T.Message.id < before_id)))
        else:
            q = q.where(T.Message.ts < before_ts)
        rows = (
            await self.s.execute(
                q.order_by(T.Message.ts.desc(), T.Message.id.desc()).limit(limit)
            )
        ).scalars().all()
        return [ensure_rendering_metadata(r.data) for r in reversed(rows)]

    async def thread_messages_with_approval(self, thread_id: str, approval_id: str, limit: int = 200) -> list[dict]:
        rows = (
            await self.s.execute(
                select(T.Message).where(T.Message.thread_id == thread_id).order_by(T.Message.ts.desc()).limit(limit)
            )
        ).scalars().all()
        matches = [
            ensure_rendering_metadata(r.data)
            for r in rows
            if (r.data or {}).get("approvalId") == approval_id
        ]
        return list(reversed(matches))

    async def latest_user_message(self, thread_id: str) -> Optional[dict]:
        row = (
            await self.s.execute(
                select(T.Message)
                .where(T.Message.thread_id == thread_id)
                .order_by(T.Message.ts.desc())
                .limit(20)
            )
        ).scalars().all()
        for msg in row:
            data = msg.data or {}
            if data.get("role") == "user":
                return ensure_rendering_metadata(data)
        return None

    async def clear_thread_messages(self, thread_id: str) -> int:
        existing = (
            await self.s.execute(select(func.count()).select_from(T.Message).where(T.Message.thread_id == thread_id))
        ).scalar_one()
        await self.s.execute(delete(T.Message).where(T.Message.thread_id == thread_id))
        return int(existing or 0)

    async def thread_stats(self, thread_id: str, after_ts: int | None = None) -> dict:
        total = int((
            await self.s.execute(
                select(func.count()).select_from(T.Message).where(T.Message.thread_id == thread_id)
            )
        ).scalar_one())
        latest = (
            await self.s.execute(
                select(T.Message).where(T.Message.thread_id == thread_id).order_by(T.Message.ts.desc()).limit(1)
            )
        ).scalars().first()
        new_count = 0
        if after_ts is not None:
            new_count = int((
                await self.s.execute(
                    select(func.count()).select_from(T.Message).where(
                        T.Message.thread_id == thread_id,
                        T.Message.ts > after_ts,
                    )
                )
            ).scalar_one())
        return {
            "threadId": thread_id,
            "total": total,
            "latestTs": latest.ts if latest else None,
            "latestMessageId": latest.id if latest else None,
            "newCount": new_count,
        }

    async def all_threads(
        self,
        limit_per: int = MAX_MSGS,
        *,
        max_threads: int | None = None,
        preferred_thread_ids: list[str] | set[str] | None = None,
    ) -> dict[str, list[dict]]:
        if max_threads is not None:
            thread_limit = max(1, int(max_threads))
            selected: list[str] = []
            preferred = [str(tid) for tid in list(preferred_thread_ids or []) if str(tid)]
            if preferred:
                preferred_rows = (
                    await self.s.execute(
                        select(T.Message.thread_id)
                        .where(T.Message.thread_id.in_(preferred))
                        .group_by(T.Message.thread_id)
                    )
                ).all()
                existing_preferred = {str(row[0]) for row in preferred_rows}
            else:
                existing_preferred = set()
            for tid in preferred:
                if tid in existing_preferred and tid not in selected:
                    selected.append(tid)
                    if len(selected) >= thread_limit:
                        break
            recent_rows = (
                await self.s.execute(
                    select(T.Message.thread_id, func.max(T.Message.ts).label("latest_ts"))
                    .group_by(T.Message.thread_id)
                    .order_by(func.max(T.Message.ts).desc(), T.Message.thread_id)
                    .limit(thread_limit)
                )
            ).all()
            for row in recent_rows:
                if len(selected) >= thread_limit:
                    break
                tid = str(row[0])
                if tid not in selected:
                    selected.append(tid)
            thread_ids = selected[:thread_limit]
        else:
            rows = (
                await self.s.execute(
                    select(T.Message.thread_id, func.max(T.Message.ts).label("latest_ts"))
                    .group_by(T.Message.thread_id)
                    .order_by(func.max(T.Message.ts).desc(), T.Message.thread_id)
                )
            ).all()
            ordered_thread_ids = [str(row[0]) for row in rows]
            thread_ids = ordered_thread_ids
        out: dict[str, list[dict]] = {}
        if not thread_ids:
            return out
        msg_limit = max(1, int(limit_per))
        ranked = (
            select(
                T.Message.thread_id.label("thread_id"),
                T.Message.ts.label("ts"),
                T.Message.id.label("id"),
                T.Message.data.label("data"),
                func.row_number()
                .over(
                    partition_by=T.Message.thread_id,
                    order_by=(T.Message.ts.desc(), T.Message.id.desc()),
                )
                .label("rn"),
            )
            .where(T.Message.thread_id.in_(thread_ids))
            .subquery()
        )
        rows = (
            await self.s.execute(
                select(ranked.c.thread_id, ranked.c.ts, ranked.c.id, ranked.c.data)
                .where(ranked.c.rn <= msg_limit)
                .order_by(ranked.c.thread_id, ranked.c.ts.asc(), ranked.c.id.asc())
            )
        ).all()
        out = {tid: [] for tid in thread_ids}
        for row in rows:
            out[str(row.thread_id)].append(ensure_rendering_metadata(row.data))
        return out

    async def reconcile_chat_reply_placeholders(self, limit: int | None = None) -> int:
        """Finalize pending chat placeholders whose background jobs already ended."""
        q = (
            select(T.Job)
            .where(
                T.Job.kind == "chat_reply",
                T.Job.status.in_(("done", "failed", "cancelled")),
            )
            .order_by(T.Job.updated_at.desc())
        )
        if limit is not None:
            q = q.limit(limit)
        jobs = (
            await self.s.execute(q)
        ).scalars().all()
        terminal_jobs: dict[str, T.Job] = {}
        for job in jobs:
            payload = job.payload or {}
            reply_id = payload.get("replyMessageId")
            if reply_id:
                terminal_jobs[str(reply_id)] = job
        if not terminal_jobs:
            return 0

        rows = (
            await self.s.execute(select(T.Message).where(T.Message.id.in_(terminal_jobs.keys())))
        ).scalars().all()
        repaired = 0
        generic_pending = {"", "กำลังคิดและทำงานต่อในคิวเบื้องหลัง..."}
        for row in rows:
            data = dict(row.data or {})
            if not data.get("pending") and data.get("status") not in {"queued", "sending"}:
                continue
            job = terminal_jobs.get(row.id)
            if not job:
                continue
            if job.status == "cancelled":
                status = "cancelled"
                code = "cancelled"
                detail = job.last_error or "background chat generation was cancelled"
                fallback = "หยุดการตอบแล้ว"
            else:
                status = "failed"
                code = "background_reply_failed"
                detail = job.last_error or "background chat generation ended without finalizing the reply"
                fallback = "งานตอบกลับก่อนหน้าล้มเหลวในคิวเบื้องหลัง"
            text = str(data.get("text") or "").strip()
            data.update({
                "pending": False,
                "status": status,
                "text": text if text not in generic_pending else fallback,
                "error": {"code": code, "detail": detail, "retryable": True},
            })
            row.data = ensure_rendering_metadata(data)
            repaired += 1
        return repaired

    # ---------- activity ----------

    async def add_activity(self, ev: dict) -> None:
        ev = self._normalize_activity_event(ev)
        self.s.add(T.Activity(id=ev["id"], ts=ev["ts"], department_id=ev.get("departmentId"), data=ev))
        if self._activity_should_add_chat_line(ev):
            await self.add_message(system_chat_message(
                _activity_chat_thread_id(ev),
                ev["text"],
                activity=ev,
                department_id=ev.get("departmentId"),
                severity=ev.get("severity") or "info",
                ts=ev["ts"],
            ))

    @staticmethod
    def _normalize_activity_event(ev: dict) -> dict:
        data = dict(ev or {})
        data["text"] = str(data.get("text") or data.get("detail") or data.get("summary") or data.get("type") or "activity")
        data["severity"] = str(data.get("severity") or "info")
        return data

    @staticmethod
    def _activity_should_add_chat_line(ev: dict) -> bool:
        if ev.get("chatVisible") is False or ev.get("visibleInChat") is False:
            return False
        if ev.get("chatMessageId") or ev.get("activityId"):
            return False
        return str(ev.get("type") or "") in CHAT_VISIBLE_ACTIVITY_TYPES

    async def recent_activity(self, limit: int = MAX_ACTIVITY) -> list[dict]:
        rows = (
            await self.s.execute(select(T.Activity).order_by(T.Activity.ts.desc()).limit(limit))
        ).scalars().all()
        return [self._normalize_activity_event(r.data) for r in rows]

    async def activity_since(
        self,
        since: int | None = None,
        after_id: str | None = None,
        limit: int = MAX_ACTIVITY,
    ) -> list[dict]:
        q = select(T.Activity)
        if after_id:
            row = await self.s.get(T.Activity, after_id)
            if row is not None:
                # Timestamps are millisecond precision; returning same-ms peers is
                # preferable to losing an event during reconnect replay.
                q = q.where(T.Activity.ts >= row.ts, T.Activity.id != after_id)
        if since is not None:
            q = q.where(T.Activity.ts >= since)
        q = q.order_by(T.Activity.ts).limit(limit)
        rows = (await self.s.execute(q)).scalars().all()
        return [self._normalize_activity_event(r.data) for r in rows]

    # ---------- approvals ----------

    async def add_approval(self, ap: dict) -> None:
        from ..memory.ledger import record_approval_ledger

        policy = await self.get_permission_policy()
        if ap.get("status") == "pending" and policy.get("mode") == "full_auto" and not ap.get("action"):
            ts = now_ms()
            ap["status"] = "approved"
            ap["resolvedAt"] = ts
            ap["resolvedBy"] = ap.get("resolvedBy") or "full_auto"
            ap["autoApprovedBy"] = "full_auto"
        self.s.add(T.Approval(id=ap["id"], status=ap["status"], ts=ap["ts"], data=ap))
        await record_approval_ledger(self, ap, event_type="approval.record")

    async def list_approvals(self, *, status: str | None = None, limit: int | None = None) -> list[dict]:
        q = select(T.Approval)
        if status is not None:
            q = q.where(T.Approval.status == status)
        q = q.order_by(T.Approval.ts.desc())
        if limit is not None:
            q = q.limit(limit)
        rows = (await self.s.execute(q)).scalars().all()
        return [r.data for r in rows]

    async def count_approvals(self) -> int:
        return int((await self.s.execute(select(func.count()).select_from(T.Approval))).scalar_one() or 0)

    async def list_pending_approvals(self, *, limit: int | None = None) -> list[dict]:
        q = (
            select(T.Approval)
            .where(T.Approval.status == "pending")
            .order_by(T.Approval.ts.desc())
        )
        if limit is not None:
            q = q.limit(limit)
        rows = (await self.s.execute(q)).scalars().all()
        return [r.data for r in rows]

    async def snapshot_approvals(self, limit: int) -> list[dict]:
        if limit <= 0:
            return []
        pending_rows = (
            await self.s.execute(
                select(T.Approval)
                .where(T.Approval.status == "pending")
                .order_by(T.Approval.ts.desc())
                .limit(limit)
            )
        ).scalars().all()
        remaining = max(0, limit - len(pending_rows))
        resolved_rows = []
        if remaining:
            resolved_rows = (
                await self.s.execute(
                    select(T.Approval)
                    .where(T.Approval.status != "pending")
                    .order_by(T.Approval.ts.desc())
                    .limit(remaining)
                )
            ).scalars().all()
        rows = [*pending_rows, *resolved_rows]
        return [_compact_approval_for_snapshot(r.data or {}) for r in rows]

    async def get_approval(self, ap_id: str) -> Optional[dict]:
        row = await self.s.get(T.Approval, ap_id)
        return row.data if row else None

    async def save_approval(self, ap: dict) -> None:
        from ..memory.ledger import record_approval_ledger

        row = await self.s.get(T.Approval, ap["id"])
        if row:
            row.status = ap["status"]
            row.data = ap
            await record_approval_ledger(self, ap, event_type="approval.update")

    # ---------- objectives ----------

    async def list_objectives(self) -> list[dict]:
        rows = (await self.s.execute(select(T.Objective))).scalars().all()
        return [r.data for r in rows]

    async def add_objective(self, obj: dict) -> None:
        self.s.add(T.Objective(id=obj["id"], data=obj))

    async def get_objective(self, obj_id: str) -> Optional[dict]:
        row = await self.s.get(T.Objective, obj_id)
        return row.data if row else None

    async def save_objective(self, obj: dict) -> None:
        row = await self.s.get(T.Objective, obj["id"])
        if row:
            row.data = obj

    # ---------- cost ----------

    async def add_cost(
        self,
        record_id: str,
        ts: int,
        department_id: str,
        category: str,
        usd: float,
        detail: Optional[str] = None,
        provider_id: Optional[str] = None,
        model: Optional[str] = None,
        speed: Optional[str] = None,
        tokens_in: Optional[int] = None,
        tokens_out: Optional[int] = None,
    ) -> None:
        from ..memory.ledger import record_cost_ledger

        ts_int = int(ts)
        normalized_category, raw_category = normalize_cost_category(category)
        detail_text = detail
        if raw_category:
            suffix = f"rawCategory={raw_category}"
            detail_text = f"{detail_text}; {suffix}" if detail_text else suffix
        self.s.add(
            T.CostRecordRow(
                id=record_id, ts=ts_int, department_id=department_id, category=normalized_category,
                usd=round(usd, 6), detail=detail_text, provider_id=provider_id, model=model,
                speed=speed,
                tokens_in=tokens_in, tokens_out=tokens_out,
            )
        )
        await record_cost_ledger(
            self,
            record_id=record_id,
            ts=ts_int,
            department_id=department_id,
            category=normalized_category,
            usd=usd,
            detail=detail_text,
            provider_id=provider_id,
            model=model,
            speed=speed,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

    async def spent_today(self) -> float:
        now = now_ms()
        today_start = now - (now % DAY_MS)
        total = (
            await self.s.execute(
                select(func.coalesce(func.sum(T.CostRecordRow.usd), 0.0)).where(T.CostRecordRow.ts >= today_start)
            )
        ).scalar_one()
        return round(float(total), 4)

    async def department_spent_today(self, dept_id: str) -> float:
        now = now_ms()
        today_start = now - (now % DAY_MS)
        total = (
            await self.s.execute(
                select(func.coalesce(func.sum(T.CostRecordRow.usd), 0.0)).where(
                    T.CostRecordRow.ts >= today_start,
                    T.CostRecordRow.department_id == dept_id,
                )
            )
        ).scalar_one()
        return round(float(total), 4)

    async def cost_report(self, scope: str, dept_id: Optional[str] = None) -> dict:
        """Build the 7-day cost series plus byCategory/byAgent, forecast, and anomalies."""
        now = now_ms()
        today_start = now - (now % DAY_MS)
        window_start = today_start - 6 * DAY_MS

        q = select(T.CostRecordRow).where(T.CostRecordRow.ts >= window_start)
        if dept_id:
            q = q.where(T.CostRecordRow.department_id == dept_id)
        recs = [
            r
            for r in (await self.s.execute(q)).scalars().all()
            if isinstance(r.ts, int)
        ]

        # 7-day series (oldest -> newest)
        series: list[dict] = []
        day_totals: dict[str, float] = {}
        for i in range(6, -1, -1):
            key = day_key(today_start - i * DAY_MS)
            day_totals[key] = 0.0
            series.append({"day": key, "usd": 0.0})
        by_category: dict[str, float] = {c: 0.0 for c in ("work", "chat", "meeting", "autonomous", "memory", "tool")}
        by_agent: dict[str, float] = {}
        for r in recs:
            key = day_key(r.ts)
            if key in day_totals:
                day_totals[key] += r.usd
            by_category[r.category] = round(by_category.get(r.category, 0.0) + r.usd, 4)
            if r.department_id != "exec":
                by_agent[r.department_id] = round(by_agent.get(r.department_id, 0.0) + r.usd, 4)
        for s in series:
            s["usd"] = round(day_totals.get(s["day"], 0.0), 4)

        # today's spend (scoped)
        if dept_id:
            spent = round(sum(r.usd for r in recs if isinstance(r.ts, int) and r.ts >= today_start), 4)
        else:
            spent = await self.spent_today()

        elapsed = max(now - today_start, 1)
        day_fraction = min(elapsed / DAY_MS, 1.0)
        forecast = round(spent / day_fraction, 2) if day_fraction else spent

        anomalies = await self._detect_anomalies(today_start, window_start, dept_id)
        return {
            "scope": scope,
            "spentUsd": spent,
            "forecastUsd": forecast,
            "byCategory": by_category,
            "byAgent": by_agent,
            "series": series,
            "anomalies": anomalies,
        }

    async def _detect_anomalies(self, today_start: int, window_start: int, dept_id: Optional[str]) -> list[str]:
        out: list[str] = []
        dept_names: dict[str, str] = {}
        if dept_id:
            ids = [dept_id]
        else:
            depts = await self.list_departments()
            dept_names = {str(d["id"]): str(d.get("name") or d["id"]) for d in depts if d["id"] != "exec"}
            ids = list(dept_names.keys())
        if not ids:
            return out
        q = (
            select(T.CostRecordRow.department_id, T.CostRecordRow.ts, T.CostRecordRow.usd)
            .where(T.CostRecordRow.ts >= window_start)
        )
        if dept_id:
            q = q.where(T.CostRecordRow.department_id == dept_id)
        else:
            q = q.where(T.CostRecordRow.department_id.in_(ids))
        stats: dict[str, dict[str, float]] = {did: {"today": 0.0, "prior": 0.0} for did in ids}
        for did, ts, usd in (await self.s.execute(q)).all():
            did = str(did)
            if did == "exec":
                continue
            try:
                ts_int = int(ts)
                usd_float = float(usd)
            except (TypeError, ValueError):
                continue
            bucket = stats.setdefault(did, {"today": 0.0, "prior": 0.0})
            if ts_int >= today_start:
                bucket["today"] += usd_float
            else:
                bucket["prior"] += usd_float
        for did in ids:
            bucket = stats.get(did) or {"today": 0.0, "prior": 0.0}
            if bucket["prior"] <= 0:
                continue
            today = bucket["today"]
            avg = bucket["prior"] / 6
            if avg > 0 and today > avg * 3:
                name = dept_names.get(did)
                if not name:
                    d = await self.get_department(did)
                    name = d["name"] if d else did
                out.append(
                    f"ฝ่าย{name} ใช้จ่ายวันนี้ ${today:.2f} สูงกว่าค่าเฉลี่ย 7 วัน (~${avg:.2f}/วัน) เกิน 3 เท่า"
                )
        return out

    # ---------- memory: archive ----------

    async def add_archive(self, dept_id: str, entry: dict) -> None:
        self.s.add(T.MemoryArchive(id=entry["id"], department_id=dept_id, ts=entry["ts"], data=entry))

    async def list_archive(self, dept_id: str, limit: int = 50) -> list[dict]:
        rows = (
            await self.s.execute(
                select(T.MemoryArchive).where(T.MemoryArchive.department_id == dept_id)
                .order_by(T.MemoryArchive.ts.desc()).limit(limit)
            )
        ).scalars().all()
        return [r.data for r in rows]

    async def count_archive(self, dept_id: str) -> int:
        return int(
            (await self.s.execute(
                select(func.count()).select_from(T.MemoryArchive).where(T.MemoryArchive.department_id == dept_id)
            )).scalar_one()
        )

    async def refresh_department_memory_stats(self, dept_id: str) -> None:
        dept = await self.get_department(dept_id)
        if not dept:
            return
        old = dept.get("memory", {})
        graph_nodes, graph_edges = await self.count_graph(dept_id)
        dept["memory"] = {
            **old,
            "archiveChunks": await self.count_archive(dept_id),
            "ragEntries": len(await self.list_knowledge(dept_id)),
            "graphNodes": graph_nodes,
            "graphEdges": graph_edges,
        }
        await self.save_department(dept)

    # ---------- memory: knowledge (RAG) ----------

    async def _pgvector_status(self) -> dict[str, Any]:
        if not get_settings().is_postgres:
            return {"backend": "sqlite", "extension": False, "column": False, "vectorSearch": "python_cosine_json"}
        row = (await self.s.execute(text("""
            SELECT
              current_schema() AS schema,
              to_regtype('vector') IS NOT NULL AS extension,
              EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'memory_knowledge'
                  AND column_name = 'embedding_vector'
              ) AS column,
              EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name = 'alembic_version'
              ) AS alembic_version_table
        """))).mappings().first()
        extension = bool(row and row.get("extension"))
        column = bool(row and row.get("column"))
        return {
            "backend": "postgres",
            "schema": str(row.get("schema") if row else "public"),
            "extension": extension,
            "column": column,
            "alembicVersionTable": bool(row and row.get("alembic_version_table")),
            "vectorSearch": "pgvector" if extension and column else "python_cosine_json",
        }

    async def memory_runtime_health(self, *, resolve_embeddings: bool = True) -> dict[str, Any]:
        settings = get_settings()
        vector = await self._pgvector_status()
        embedder = None
        embedder_error = ""
        if resolve_embeddings:
            from ..memory.embeddings import resolve_embedder

            try:
                embedder = await resolve_embedder(settings)
            except Exception as exc:
                embedder = None
                embedder_error = f"{type(exc).__name__}: {str(exc)[:240]}"
        if embedder is None and settings.openai_embeddings_enabled:
            dim = settings.effective_openai_embedding_dimensions or (
                3072 if settings.openai_embedding_model == "text-embedding-3-large" else 1536
            )
            embedder = type("ConfiguredEmbedder", (), {
                "name": f"openai:{settings.openai_embedding_model}",
                "model": settings.openai_embedding_model,
                "dim": dim,
            })()
        elif embedder is None and settings.prefer_ollama_embeddings:
            embedder = type("ConfiguredEmbedder", (), {
                "name": f"ollama:{settings.ollama_embedding_model}",
                "model": settings.ollama_embedding_model,
                "dim": settings.embedding_dim,
            })()
        elif embedder is None and settings.live_embeddings:
            embedder = type("ConfiguredEmbedder", (), {
                "name": f"voyage:{settings.voyage_model}",
                "model": settings.voyage_model,
                "dim": settings.embedding_dim,
            })()
        return {
            "storage": vector["backend"],
            "vectorSearch": vector["vectorSearch"],
            "pgvector": {
                "schema": vector.get("schema"),
                "extension": vector["extension"],
                "column": vector["column"],
                "alembicVersionTable": vector.get("alembicVersionTable"),
            },
            "embeddings": {
                "mode": settings.embedding_provider_mode,
                "provider": getattr(embedder, "name", f"hash-{settings.embedding_dim}"),
                "model": getattr(embedder, "model", getattr(embedder, "name", f"hash-{settings.embedding_dim}")),
                "dim": getattr(embedder, "dim", settings.embedding_dim),
                "fallback": bool(getattr(embedder, "name", "").startswith("hash-")) if embedder else True,
                "error": embedder_error or None,
            },
            "graph": settings.graph_backend,
        }

    async def database_schema_health(self) -> dict[str, Any]:
        settings = get_settings()
        if not settings.is_postgres:
            conn = await self.s.connection()
            return await sqlite_schema_status(conn)
        row = (await self.s.execute(text("""
            SELECT
              current_schema() AS schema,
              EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name = 'alembic_version'
              ) AS alembic_version_table
        """))).mappings().first()
        alembic_revision = ""
        if row and row.get("alembic_version_table"):
            alembic_revision = str((await self.s.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))).scalar() or "")
        return {
            "backend": "postgres",
            "schema": str(row.get("schema") if row else "public"),
            "alembicVersionTable": bool(row and row.get("alembic_version_table")),
            "alembicRevision": alembic_revision,
        }

    async def _set_pgvector_embedding(self, knowledge_id: str, embedding: Optional[list[float]]) -> None:
        status = await self._pgvector_status()
        if status["vectorSearch"] != "pgvector":
            return
        if embedding:
            await self.s.execute(
                text('UPDATE "memory_knowledge" SET embedding_vector = CAST(:embedding AS vector) WHERE id = :id'),
                {"embedding": _vector_literal(embedding), "id": knowledge_id},
            )
        else:
            await self.s.execute(
                text('UPDATE "memory_knowledge" SET embedding_vector = NULL WHERE id = :id'),
                {"id": knowledge_id},
            )

    async def add_knowledge(
        self,
        dept_id: str,
        entry: dict,
        embedding: Optional[list[float]] = None,
        source: Optional[str] = None,
        embedding_meta: Optional[dict[str, Any]] = None,
    ) -> None:
        meta = embedding_meta or {}
        embedding_dim = meta.get("dim") or entry.get("embeddingDim") or entry.get("embedding_dim")
        if embedding and not embedding_dim:
            embedding_dim = len(embedding)
        embedding_provider = meta.get("provider") or entry.get("embeddingProvider") or entry.get("embedding_provider")
        embedding_model = meta.get("model") or entry.get("embeddingModel") or entry.get("embedding_model")
        embedding_ts = entry.get("embeddingTs") or entry.get("embedding_ts") or (now_ms() if embedding else None)
        row = T.MemoryKnowledge(
            id=entry["id"], department_id=dept_id, ts=entry["ts"], title=entry["title"],
            text=entry["text"], score=entry.get("score", 0.7), tags=entry.get("tags", []),
            embedding=embedding,
            embedding_provider=str(embedding_provider) if embedding_provider else None,
            embedding_model=str(embedding_model) if embedding_model else None,
            embedding_dim=int(embedding_dim) if embedding_dim else None,
            embedding_ts=int(embedding_ts) if embedding_ts else None,
            source=source,
        )
        self.s.add(row)
        await self.s.flush()
        await self._set_pgvector_embedding(row.id, embedding)

    async def _search_knowledge_pgvector(self, dept_id: str, query_vec: list[float], k: int) -> list[dict]:
        status = await self._pgvector_status()
        if status["vectorSearch"] != "pgvector":
            return []
        rows = (await self.s.execute(
            text("""
                SELECT id, title, ts, score, text, tags, source,
                       embedding_provider, embedding_model, embedding_dim, embedding_ts,
                       1 - (embedding_vector <=> CAST(:query AS vector)) AS similarity
                FROM memory_knowledge
                WHERE department_id = :dept_id
                  AND embedding_vector IS NOT NULL
                  AND vector_dims(embedding_vector) = :dims
                ORDER BY embedding_vector <=> CAST(:query AS vector)
                LIMIT :limit
            """),
            {
                "dept_id": dept_id,
                "query": _vector_literal(query_vec),
                "dims": len(query_vec),
                "limit": max(1, min(int(k), 50)),
            },
        )).mappings().all()
        out: list[dict] = []
        for row in rows:
            tags = row["tags"] if isinstance(row["tags"], list) else []
            out.append({
                "id": row["id"],
                "title": row["title"],
                "ts": row["ts"],
                "score": round(max(0.0, min(1.0, float(row["similarity"] or 0.0))), 4),
                "text": row["text"],
                "tags": tags,
                "source": row["source"],
                "embeddingProvider": row["embedding_provider"],
                "embeddingModel": row["embedding_model"],
                "embeddingDim": row["embedding_dim"],
                "embeddingTs": row["embedding_ts"],
                "retrievalBackend": "pgvector",
            })
        return out

    async def list_knowledge(self, dept_id: str, limit: int = 200) -> list[dict]:
        rows = (
            await self.s.execute(
                select(T.MemoryKnowledge).where(T.MemoryKnowledge.department_id == dept_id)
                .order_by(T.MemoryKnowledge.ts.desc()).limit(limit)
            )
        ).scalars().all()
        return [self._knowledge_dict(r) for r in rows]

    async def _knowledge_rows(self, dept_id: str) -> list[T.MemoryKnowledge]:
        return list((
            await self.s.execute(select(T.MemoryKnowledge).where(T.MemoryKnowledge.department_id == dept_id))
        ).scalars().all())

    @staticmethod
    def _knowledge_dict(r: T.MemoryKnowledge) -> dict:
        return {
            "id": r.id,
            "title": r.title,
            "ts": r.ts,
            "score": r.score,
            "text": r.text,
            "tags": r.tags or [],
            "source": r.source,
            "embeddingProvider": r.embedding_provider,
            "embeddingModel": r.embedding_model,
            "embeddingDim": r.embedding_dim,
            "embeddingTs": r.embedding_ts,
        }

    @staticmethod
    def _knowledge_embedding_stale(
        row: T.MemoryKnowledge,
        *,
        expected_provider: str | None,
        expected_model: str | None,
        expected_dim: int | None,
    ) -> bool:
        if not row.text:
            return False
        if not row.embedding:
            return True
        if expected_provider and row.embedding_provider != expected_provider:
            return True
        if expected_model and row.embedding_model != expected_model:
            return True
        if expected_dim and int(row.embedding_dim or 0) != int(expected_dim):
            return True
        return False

    async def knowledge_embedding_migration_status(self, *, limit: int = 20) -> dict[str, Any]:
        from ..memory.embeddings import embedding_metadata, resolve_embedder

        embedder = await resolve_embedder()
        expected = embedding_metadata(embedder)
        expected_provider = str(expected.get("provider") or "")
        expected_model = str(expected.get("model") or "")
        expected_dim = int(expected.get("dim") or get_settings().embedding_dim)
        rows = list((await self.s.execute(select(T.MemoryKnowledge))).scalars().all())
        stale_rows = [
            row
            for row in rows
            if self._knowledge_embedding_stale(
                row,
                expected_provider=expected_provider,
                expected_model=expected_model,
                expected_dim=expected_dim,
            )
        ]
        pgvector_null = None
        vector = await self._pgvector_status()
        if vector["vectorSearch"] == "pgvector":
            pgvector_null = int((await self.s.execute(text("""
                SELECT COUNT(*)
                FROM memory_knowledge
                WHERE text IS NOT NULL
                  AND text <> ''
                  AND embedding_vector IS NULL
            """))).scalar() or 0)
        return {
            "expected": {
                "provider": expected_provider,
                "model": expected_model,
                "dim": expected_dim,
            },
            "total": len(rows),
            "current": len(rows) - len(stale_rows),
            "stale": len(stale_rows),
            "pgvectorNull": pgvector_null,
            "staleSamples": [
                {
                    "id": row.id,
                    "departmentId": row.department_id,
                    "provider": row.embedding_provider,
                    "model": row.embedding_model,
                    "dim": row.embedding_dim,
                    "hasEmbedding": bool(row.embedding),
                }
                for row in stale_rows[: max(0, int(limit))]
            ],
        }

    async def reembed_stale_knowledge(self, *, limit: int = 1000, batch_size: int = 8) -> dict[str, Any]:
        from ..memory.embeddings import embedding_metadata, resolve_embedder

        embedder = await resolve_embedder()
        expected = embedding_metadata(embedder)
        expected_provider = str(expected.get("provider") or "")
        expected_model = str(expected.get("model") or "")
        expected_dim = int(expected.get("dim") or get_settings().embedding_dim)
        rows = list((await self.s.execute(select(T.MemoryKnowledge))).scalars().all())
        stale_rows = [
            row
            for row in rows
            if self._knowledge_embedding_stale(
                row,
                expected_provider=expected_provider,
                expected_model=expected_model,
                expected_dim=expected_dim,
            )
        ][: max(0, int(limit))]
        now = now_ms()
        updated: list[str] = []
        batch_size = max(1, min(int(batch_size), 64))
        for start in range(0, len(stale_rows), batch_size):
            batch = stale_rows[start : start + batch_size]
            vectors = await embedder.embed([row.text[:8000] for row in batch]) if batch else []
            for row, vector in zip(batch, vectors, strict=False):
                meta = embedding_metadata(embedder, vector)
                row.embedding = vector
                row.embedding_provider = str(meta.get("provider") or "")
                row.embedding_model = str(meta.get("model") or "")
                row.embedding_dim = int(meta.get("dim") or len(vector))
                row.embedding_ts = now
                await self._set_pgvector_embedding(row.id, vector)
                updated.append(row.id)
        return {
            "expected": {
                "provider": expected_provider,
                "model": expected_model,
                "dim": expected_dim,
            },
            "scanned": len(rows),
            "staleBefore": len(stale_rows),
            "updated": len(updated),
            "updatedIds": updated[:20],
            "limit": limit,
            "batchSize": batch_size,
        }

    async def get_knowledge(self, dept_id: str, kid: str) -> Optional[dict]:
        row = await self.s.get(T.MemoryKnowledge, kid)
        if row and row.department_id == dept_id:
            return self._knowledge_dict(row)
        return None

    async def delete_knowledge(self, dept_id: str, kid: str) -> bool:
        row = await self.s.get(T.MemoryKnowledge, kid)
        if row and row.department_id == dept_id:
            await self.s.delete(row)
            return True
        return False

    async def edit_knowledge(self, dept_id: str, kid: str, patch: dict) -> bool:
        row = await self.s.get(T.MemoryKnowledge, kid)
        if not row or row.department_id != dept_id:
            return False
        if patch.get("title") is not None:
            row.title = patch["title"]
        if patch.get("text") is not None:
            next_text = patch["text"]
            if next_text != row.text:
                row.text = next_text
                row.embedding = None
                row.embedding_provider = None
                row.embedding_model = None
                row.embedding_dim = None
                row.embedding_ts = None
                await self._set_pgvector_embedding(row.id, None)
        if patch.get("tags") is not None:
            row.tags = patch["tags"]
        row.ts = now_ms()
        return True

    async def search_knowledge(self, dept_id: str, query_vec: list[float], k: int = 5) -> list[dict]:
        pgvector = await self._search_knowledge_pgvector(dept_id, query_vec, k)
        if pgvector:
            return pgvector
        rows = await self._knowledge_rows(dept_id)
        scored: list[tuple[float, T.MemoryKnowledge]] = []
        for r in rows:
            if not r.embedding:
                continue
            scored.append((_cosine(query_vec, r.embedding), r))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for score, r in scored[:k]:
            d = self._knowledge_dict(r)
            d["score"] = round(max(0.0, min(1.0, score)), 4)
            d["retrievalBackend"] = "python_cosine_json"
            out.append(d)
        return out

    # ---------- memory: graph ----------

    async def add_graph_node(
        self,
        dept_id: str,
        node_id: str,
        label: str,
        ntype: str,
        x: float = 0.5,
        y: float = 0.5,
        *,
        valid_from: int | None = None,
        valid_to: int | None = None,
        confidence: float = 0.7,
        source: str | None = None,
    ) -> None:
        storage_id = _graph_scoped_id(dept_id, node_id)
        valid_from = now_ms() if valid_from is None else valid_from
        confidence = max(0.0, min(1.0, float(confidence)))
        existing = await self.s.get(T.GraphNode, storage_id)
        if existing:
            existing.department_id = dept_id
            existing.label = label
            existing.type = ntype
            existing.x = x
            existing.y = y
            existing.valid_from = existing.valid_from or valid_from
            existing.valid_to = valid_to
            existing.confidence = confidence
            existing.source = source or existing.source
            get_graph_mirror().upsert_node(dept_id, storage_id, label, ntype, x, y)
            return
        self.s.add(T.GraphNode(
            id=storage_id,
            department_id=dept_id,
            label=label,
            type=ntype,
            x=x,
            y=y,
            valid_from=valid_from,
            valid_to=valid_to,
            confidence=confidence,
            source=source,
        ))
        get_graph_mirror().upsert_node(dept_id, storage_id, label, ntype, x, y)

    async def add_graph_edge(
        self,
        dept_id: str,
        from_id: str,
        to_id: str,
        rel: str,
        *,
        valid_from: int | None = None,
        valid_to: int | None = None,
        confidence: float = 0.7,
        source: str | None = None,
    ) -> None:
        storage_from = _graph_scoped_id(dept_id, from_id)
        storage_to = _graph_scoped_id(dept_id, to_id)
        confidence = max(0.0, min(1.0, float(confidence)))
        valid_from = now_ms() if valid_from is None else valid_from
        existing = (
            await self.s.execute(
                select(T.GraphEdge).where(
                    T.GraphEdge.department_id == dept_id,
                    T.GraphEdge.from_id == storage_from,
                    T.GraphEdge.to_id == storage_to,
                    T.GraphEdge.rel == rel,
                ).limit(1)
            )
        ).scalars().first()
        if existing:
            existing.valid_from = existing.valid_from or valid_from
            existing.valid_to = valid_to
            existing.confidence = max(float(existing.confidence or 0.0), confidence)
            existing.source = source or existing.source
            return
        self.s.add(T.GraphEdge(
            department_id=dept_id,
            from_id=storage_from,
            to_id=storage_to,
            rel=rel,
            valid_from=valid_from,
            valid_to=valid_to,
            confidence=confidence,
            source=source,
        ))
        get_graph_mirror().add_edge(dept_id, storage_from, storage_to, rel)

    async def graph(self, dept_id: str) -> dict:
        nodes = (
            await self.s.execute(select(T.GraphNode).where(T.GraphNode.department_id == dept_id))
        ).scalars().all()
        edges = (
            await self.s.execute(select(T.GraphEdge).where(T.GraphEdge.department_id == dept_id))
        ).scalars().all()
        return {
            "nodes": [
                {
                    "id": n.id,
                    "label": n.label,
                    "type": n.type,
                    "x": n.x,
                    "y": n.y,
                    "validFrom": n.valid_from,
                    "validTo": n.valid_to,
                    "confidence": n.confidence,
                    "source": n.source,
                }
                for n in nodes
            ],
            "edges": [
                {
                    "from": e.from_id,
                    "to": e.to_id,
                    "rel": e.rel,
                    "validFrom": e.valid_from,
                    "validTo": e.valid_to,
                    "confidence": e.confidence,
                    "source": e.source,
                }
                for e in edges
            ],
        }

    async def count_graph(self, dept_id: str) -> tuple[int, int]:
        n = int((await self.s.execute(
            select(func.count()).select_from(T.GraphNode).where(T.GraphNode.department_id == dept_id)
        )).scalar_one())
        e = int((await self.s.execute(
            select(func.count()).select_from(T.GraphEdge).where(T.GraphEdge.department_id == dept_id)
        )).scalar_one())
        return n, e

    async def department_memory(self, dept_id: str) -> dict:
        return {
            "departmentId": dept_id,
            "archive": await self.list_archive(dept_id),
            "knowledge": await self.list_knowledge(dept_id),
            "graph": await self.graph(dept_id),
        }

    # ---------- jobs (durable queue) ----------

    async def enqueue(self, job_id: str, kind: str, payload: dict, run_after: int, priority: int = 5) -> None:
        now = now_ms()
        self.s.add(
            T.Job(id=job_id, kind=kind, status="queued", run_after=run_after, priority=priority,
                  payload=payload, attempts=0, created_at=now, updated_at=now)
        )

    async def enqueue_once(
        self,
        job_id: str,
        kind: str,
        payload: dict,
        run_after: int,
        priority: int = 5,
        *,
        dedupe_key: str,
    ) -> str:
        """Queue one future scheduled job and cancel duplicate queued copies."""
        rows = (
            await self.s.execute(
                select(T.Job)
                .where(T.Job.kind == kind, T.Job.status == "queued")
                .order_by(T.Job.run_after, T.Job.created_at)
            )
        ).scalars().all()
        matches: list[T.Job] = []
        for row in rows:
            row_payload = dict(row.payload or {})
            if row_payload.get("_dedupeKey") == dedupe_key:
                matches.append(row)
                continue
            if dedupe_key == f"scheduled:{kind}" and row_payload.get("reason") == "scheduled":
                matches.append(row)
        if matches:
            now = now_ms()
            keep = matches[0]
            for duplicate in matches[1:]:
                duplicate.status = "cancelled"
                duplicate.updated_at = now
                duplicate.last_error = f"deduped by queued job {keep.id}"
            return keep.id
        unique_payload = dict(payload or {})
        unique_payload["_dedupeKey"] = dedupe_key
        await self.enqueue(job_id, kind, unique_payload, run_after, priority=priority)
        return job_id

    async def due_jobs(
        self,
        now: int,
        limit: int = 8,
        kind: str | None = None,
        exclude_kinds: set[str] | None = None,
    ) -> list[T.Job]:
        q = (
            select(T.Job)
            .where(T.Job.status == "queued", T.Job.run_after <= now)
            .order_by(T.Job.priority, T.Job.run_after)
        )
        if kind:
            q = q.where(T.Job.kind == kind)
        if exclude_kinds:
            q = q.where(T.Job.kind.not_in(sorted(exclude_kinds)))
        q = q.limit(limit)
        rows = (await self.s.execute(q)).scalars().all()
        return list(rows)

    async def claim_due_jobs(
        self,
        now: int,
        limit: int = 8,
        kind: str | None = None,
        exclude_kinds: set[str] | None = None,
    ) -> list[T.Job]:
        """Atomically move due queued jobs to running and return the claimed rows."""
        q = (
            select(T.Job.id)
            .where(T.Job.status == "queued", T.Job.run_after <= now)
            .order_by(T.Job.priority, T.Job.run_after)
            .limit(limit)
        )
        if kind:
            q = q.where(T.Job.kind == kind)
        if exclude_kinds:
            q = q.where(T.Job.kind.not_in(sorted(exclude_kinds)))
        if get_settings().is_postgres:
            q = q.with_for_update(skip_locked=True)
        ids = [str(row_id) for row_id in (await self.s.execute(q)).scalars().all()]
        if not ids:
            return []
        claimed: list[T.Job] = []
        claim_now = now_ms()
        for job_id in ids:
            result = await self.s.execute(
                update(T.Job)
                .where(T.Job.id == job_id, T.Job.status == "queued")
                .values(status="running", updated_at=claim_now)
            )
            if int(result.rowcount or 0) != 1:
                continue
            row = await self.s.get(T.Job, job_id)
            if row:
                claimed.append(row)
        return claimed

    async def mark_job(self, job_id: str, status: str, error: Optional[str] = None, run_after: Optional[int] = None) -> None:
        row = await self.s.get(T.Job, job_id)
        if not row:
            return
        await self.s.refresh(row)
        if row.status == "cancelled" and status != "cancelled":
            return
        row.status = status
        row.updated_at = now_ms()
        if error is not None:
            row.last_error = error
            row.attempts += 1
        if run_after is not None:
            row.run_after = run_after

    async def job_runtime_summary(
        self,
        now: int,
        *,
        stale_after_ms: int = 120_000,
        retry_visibility_attempts: int = 5,
    ) -> dict[str, Any]:
        by_status = {
            str(status): int(count or 0)
            for status, count in (await self.s.execute(select(T.Job.status, func.count()).group_by(T.Job.status))).all()
        }
        by_kind = {
            str(kind): int(count or 0)
            for kind, count in (await self.s.execute(select(T.Job.kind, func.count()).group_by(T.Job.kind))).all()
        }
        oldest_due_queued_at = (
            await self.s.execute(
                select(func.min(T.Job.run_after)).where(T.Job.status == "queued", T.Job.run_after <= now)
            )
        ).scalar_one()
        scheduled_queued = int((
            await self.s.execute(
                select(func.count()).select_from(T.Job).where(T.Job.status == "queued", T.Job.run_after > now)
            )
        ).scalar_one() or 0)
        oldest_running_at = (
            await self.s.execute(
                select(func.min(T.Job.updated_at)).where(T.Job.status == "running")
            )
        ).scalar_one()
        stale_rows = (
            await self.s.execute(
                select(T.Job.id, T.Job.kind, T.Job.updated_at, T.Job.attempts, T.Job.last_error)
                .where(T.Job.status == "running", T.Job.updated_at <= now - stale_after_ms)
                .order_by(T.Job.updated_at.asc())
                .limit(20)
            )
        ).all()
        stale_running = [
            {
                "id": row.id,
                "kind": row.kind,
                "ageMs": max(0, now - int(row.updated_at or now)),
                "attempts": row.attempts,
                "lastError": row.last_error,
            }
            for row in stale_rows
        ]
        retry_visibility_threshold = max(1, int(retry_visibility_attempts or 1))
        high_attempt_rows = (
            await self.s.execute(
                select(T.Job.id, T.Job.kind, T.Job.status, T.Job.run_after, T.Job.updated_at, T.Job.attempts, T.Job.last_error)
                .where(
                    T.Job.status.in_(("queued", "running")),
                    T.Job.attempts >= retry_visibility_threshold,
                )
                .order_by(T.Job.attempts.desc(), T.Job.updated_at.asc())
                .limit(20)
            )
        ).all()
        high_attempt_jobs = [
            {
                "id": row.id,
                "kind": row.kind,
                "status": row.status,
                "runAfter": row.run_after,
                "ageMs": max(0, now - int(row.updated_at or now)),
                "attempts": row.attempts,
                "lastError": row.last_error,
            }
            for row in high_attempt_rows
        ]
        total = sum(by_status.values())
        return {
            "total": total,
            "byStatus": by_status,
            "byKind": by_kind,
            "oldestQueuedAgeMs": None if oldest_due_queued_at is None else max(0, now - oldest_due_queued_at),
            "scheduledQueued": scheduled_queued,
            "oldestRunningAgeMs": None if oldest_running_at is None else max(0, now - oldest_running_at),
            "staleRunning": stale_running,
            "retryVisibility": {
                "visibilityOnly": True,
                "thresholdAttempts": retry_visibility_threshold,
                "activeJobCount": len(high_attempt_jobs),
            },
            "highAttemptJobs": high_attempt_jobs,
        }

    async def requeue_stale_running_jobs(
        self,
        now: int,
        *,
        stale_after_ms: int = 120_000,
        limit: int = 50,
        exclude_kinds: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        cutoff = now - max(1, int(stale_after_ms))
        q = (
            select(T.Job)
            .where(T.Job.status == "running", T.Job.updated_at <= cutoff)
            .order_by(T.Job.updated_at.asc())
            .limit(max(1, int(limit)))
        )
        if exclude_kinds:
            q = q.where(T.Job.kind.not_in(sorted(exclude_kinds)))
        if get_settings().is_postgres:
            q = q.with_for_update(skip_locked=True)
        rows = (await self.s.execute(q)).scalars().all()
        requeued: list[dict[str, Any]] = []
        for row in rows:
            age_ms = max(0, now - int(row.updated_at or now))
            result = await self.s.execute(
                update(T.Job)
                .where(T.Job.id == row.id, T.Job.status == "running", T.Job.updated_at <= cutoff)
                .values(
                    status="queued",
                    run_after=now,
                    updated_at=now,
                    last_error=f"requeued stale running job after {age_ms}ms",
                )
            )
            if int(result.rowcount or 0) != 1:
                continue
            requeued.append({
                "id": row.id,
                "kind": row.kind,
                "ageMs": age_ms,
                "attempts": row.attempts,
            })
        return requeued

    async def active_jobs(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = (
            await self.s.execute(
                select(T.Job)
                .where(T.Job.status.in_(("queued", "running")))
                .order_by(T.Job.status.desc(), T.Job.priority, T.Job.run_after)
                .limit(limit)
            )
        ).scalars().all()
        return [
            {
                "id": row.id,
                "kind": row.kind,
                "status": row.status,
                "runAfter": row.run_after,
                "priority": row.priority,
                "payload": row.payload or {},
                "attempts": row.attempts,
                "lastError": row.last_error,
                "createdAt": row.created_at,
                "updatedAt": row.updated_at,
            }
            for row in rows
        ]

    async def executive_queue(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = (
            await self.s.execute(
                select(T.Job)
                .where(T.Job.status.in_(("queued", "running")), T.Job.kind.in_(sorted(EXECUTIVE_QUEUE_KINDS)))
                .order_by(T.Job.status.desc(), T.Job.priority, T.Job.run_after, T.Job.created_at)
                .limit(limit)
            )
        ).scalars().all()
        approval_rows = (
            await self.s.execute(
                select(T.Approval)
                .where(T.Approval.status == "pending")
                .order_by(T.Approval.ts)
                .limit(limit)
            )
        ).scalars().all()
        approval_items = [
            _queue_item_from_close_approval(row.data or {})
            for row in approval_rows
            if ((row.data or {}).get("action") or {}).get("action") == "close_task"
        ]
        items = [*[_queue_item_from_job(row) for row in rows], *approval_items]
        items.sort(key=lambda item: (
            0 if item.get("status") == "running" else 1,
            int(item.get("priority") or 0),
            int(item.get("runAfter") or 0),
            int(item.get("createdAt") or 0),
        ))
        return items[:limit]

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = await self.s.get(T.Job, job_id)
        if not row:
            return None
        return {
            "id": row.id,
            "kind": row.kind,
            "status": row.status,
            "runAfter": row.run_after,
            "priority": row.priority,
            "payload": row.payload or {},
            "attempts": row.attempts,
            "lastError": row.last_error,
            "createdAt": row.created_at,
            "updatedAt": row.updated_at,
        }

    async def cancel_chat_reply_jobs(self, thread_id: str, reply_message_id: str | None, reason: str) -> int:
        rows = (
            await self.s.execute(
                select(T.Job).where(T.Job.kind == "chat_reply", T.Job.status.in_(("queued", "running")))
            )
        ).scalars().all()
        now = now_ms()
        count = 0
        for row in rows:
            payload = dict(row.payload or {})
            if payload.get("threadId") != thread_id:
                continue
            if reply_message_id is not None and payload.get("replyMessageId") != reply_message_id:
                continue
            row.status = "cancelled"
            row.updated_at = now
            row.last_error = reason
            count += 1
        return count

    async def reset_stuck_jobs(self) -> None:
        """On boot, any job left 'running' (crash mid-flight) goes back to the queue."""
        now = now_ms()
        await self.s.execute(
            update(T.Job)
            .where(T.Job.status == "running")
            .values(status="queued", updated_at=now, last_error="requeued after service restart")
        )

    async def cancel_active_jobs(self, reason: str) -> int:
        now = now_ms()
        rows = (
            await self.s.execute(select(T.Job).where(T.Job.status.in_(("queued", "running"))))
        ).scalars().all()
        for row in rows:
            row.status = "cancelled"
            row.updated_at = now
            row.last_error = reason
        return len(rows)

    # ---------- generic v0.4 entities ----------

    async def put_entity(self, etype: str, obj: dict, *, dept: Optional[str] = None,
                         project: Optional[str] = None, status: Optional[str] = None, ts: Optional[int] = None) -> dict:
        from ..memory.ledger import record_entity_ledger

        eid = obj["id"]
        row = await self.s.get(T.Entity, (etype, eid))
        ts = ts if ts is not None else obj.get("ts") or obj.get("createdAt") or now_ms()
        written = False
        if row is None:
            self.s.add(T.Entity(type=etype, id=eid, dept=dept, project=project, status=status, ts=ts, data=obj))
            written = True
        else:
            if etype in IMMUTABLE_ENTITY_TYPES:
                return row.data
            row.dept, row.project, row.status, row.ts, row.data = dept, project, status, ts, obj
            written = True
        if written and etype != "conversation_ledger":
            await record_entity_ledger(
                self,
                entity_type=etype,
                entity=obj,
                department_id=dept,
                project_id=project,
                status=status,
            )
        return obj

    async def get_entity(self, etype: str, eid: str) -> Optional[dict]:
        row = await self.s.get(T.Entity, (etype, eid))
        return row.data if row else None

    async def list_entities(self, etype: str, *, dept: Optional[str] = None, project: Optional[str] = None,
                            status: Optional[str] = None, limit: int = 500) -> list[dict]:
        q = select(T.Entity).where(T.Entity.type == etype)
        if dept is not None:
            q = q.where(T.Entity.dept == dept)
        if project is not None:
            q = q.where(T.Entity.project == project)
        if status is not None:
            q = q.where(T.Entity.status == status)
        q = q.order_by(T.Entity.ts.desc()).limit(limit)
        rows = (await self.s.execute(q)).scalars().all()
        return [r.data for r in rows]

    async def delete_entity(self, etype: str, eid: str) -> None:
        await self.s.execute(delete(T.Entity).where(T.Entity.type == etype, T.Entity.id == eid))

    # ---------- snapshot ----------

    async def snapshot(self) -> dict:
        try:
            await self.reconcile_chat_reply_placeholders(limit=200)
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            await self.s.rollback()
        settings = get_settings()
        company = await self.get_company()
        departments = await self.list_departments()
        preferred_threads = ["executive", *[f"dept:{dept['id']}" for dept in departments if dept.get("id")]]
        tasks = sorted(await self.snapshot_tasks(settings.state_task_count_limit), key=_snapshot_task_sort_key)
        tasks = [_compact_task_for_snapshot(task) for task in tasks]
        return {
            "companyName": company.company_name if company else "ATRIUM",
            "now": now_ms(),
            "running": company.running if company else True,
            "departments": departments,
            "tasks": tasks,
            "threads": await self.all_threads(
                limit_per=settings.state_thread_message_limit,
                max_threads=settings.state_thread_count_limit,
                preferred_thread_ids=preferred_threads,
            ),
            "activity": await self.recent_activity(),
            "approvals": await self.snapshot_approvals(settings.state_approval_count_limit),
            "objectives": await self.list_objectives(),
            "executiveQueue": await self.executive_queue(limit=30),
            "budget": await self.get_budget(),
            "permissionPolicy": await self.get_permission_policy(),
        }


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
