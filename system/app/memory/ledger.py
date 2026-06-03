"""Append-only conversation ledger (messages, tools, compaction links)."""
from __future__ import annotations

import json
import math
import re
from typing import Any

from ..clock import now_ms
from ..ids import uid
from ..threads import thread_id_for
from .archive import transcript_digest

_TOKEN_RE = re.compile(r"[0-9A-Za-z฀-๿]+")


async def append_ledger_event(
    repo: Any,
    *,
    event_type: str,
    thread_id: str,
    department_id: str | None = None,
    payload: dict[str, Any] | None = None,
    refs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now_ms()
    entry = {
        "id": uid("led"),
        "eventType": event_type,
        "threadId": thread_id,
        "departmentId": department_id,
        "ts": now,
        "payload": payload or {},
        "refs": refs or {},
    }
    await repo.put_entity(
        "conversation_ledger",
        entry,
        dept=department_id,
        status=event_type,
        ts=now,
    )
    return entry


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def ledger_search_text(row: dict[str, Any]) -> str:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    refs = row.get("refs") if isinstance(row.get("refs"), dict) else {}
    parts = [
        str(row.get("eventType") or ""),
        str(row.get("threadId") or ""),
        str(row.get("departmentId") or ""),
        _json_text(refs),
        _json_text(payload),
    ]
    return "\n".join(part for part in parts if part)


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("payload") if isinstance(row.get("payload"), dict) else {}


def _refs(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("refs") if isinstance(row.get("refs"), dict) else {}


def _entity(row: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(row)
    entity = payload.get("entity")
    return entity if isinstance(entity, dict) else {}


def _run(row: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(row)
    run = payload.get("run")
    return run if isinstance(run, dict) else {}


def _message(row: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(row)
    message = payload.get("message")
    return message if isinstance(message, dict) else {}


def _message_citations(msg: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidates: list[Any] = []
    if isinstance(msg.get("citations"), list):
        candidates.extend(msg.get("citations") or [])
    render = msg.get("render") if isinstance(msg.get("render"), dict) else {}
    if isinstance(render.get("citations"), list):
        candidates.extend(render.get("citations") or [])
    for item in candidates:
        if not isinstance(item, dict):
            continue
        key = _json_text(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(item))
    return out


def _matches_any(value: str, *candidates: Any) -> bool:
    if not value:
        return True
    return any(_norm(candidate) == value for candidate in candidates)


def _citation_matches(row: dict[str, Any], citation_source: str | None) -> bool:
    needle = (citation_source or "").casefold().strip()
    if not needle:
        return True
    payload = _payload(row)
    citations = payload.get("citations")
    if not isinstance(citations, list):
        citations = _message_citations(_message(row))
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        text = " ".join(
            str(citation.get(key) or "")
            for key in ("source", "title", "label", "snippet", "kind")
        ).casefold()
        if needle in text:
            return True
    return False


def ledger_row_matches(
    row: dict[str, Any],
    *,
    thread_id: str | None = None,
    department_id: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
    artifact_id: str | None = None,
    decision_id: str | None = None,
    tool_run_id: str | None = None,
    message_id: str | None = None,
    branch_thread_id: str | None = None,
    source_thread_id: str | None = None,
    citation_source: str | None = None,
    event_type: str | None = None,
    after: int | None = None,
    before: int | None = None,
) -> bool:
    payload = _payload(row)
    refs = _refs(row)
    entity = _entity(row)
    run = _run(row)
    message = _message(row)
    ts = int(row.get("ts") or 0)
    if after is not None and ts < after:
        return False
    if before is not None and ts > before:
        return False
    if event_type and row.get("eventType") != event_type:
        return False
    if thread_id and not _matches_any(thread_id, row.get("threadId"), payload.get("threadId"), refs.get("threadId"), entity.get("threadId")):
        return False
    if department_id and not _matches_any(
        department_id,
        row.get("departmentId"),
        payload.get("departmentId"),
        entity.get("departmentId"),
        entity.get("dept"),
        entity.get("ownerDept"),
        run.get("departmentId"),
    ):
        return False
    if project_id and not _matches_any(project_id, refs.get("projectId"), payload.get("projectId"), entity.get("projectId"), entity.get("project")):
        return False
    if task_id and not _matches_any(task_id, refs.get("taskId"), payload.get("taskId"), entity.get("taskId"), entity.get("id") if payload.get("entityType") == "task" else None):
        return False
    if artifact_id and not _matches_any(
        artifact_id,
        refs.get("artifactId"),
        payload.get("artifactId"),
        entity.get("artifactId"),
        entity.get("id") if payload.get("entityType") == "artifact" else None,
    ):
        return False
    if decision_id and not _matches_any(
        decision_id,
        refs.get("decisionId"),
        payload.get("decisionId"),
        entity.get("decisionId"),
        entity.get("id") if payload.get("entityType") == "decision" else None,
    ):
        return False
    if tool_run_id and not _matches_any(
        tool_run_id,
        refs.get("toolRunId"),
        payload.get("toolRunId"),
        payload.get("runId"),
        payload.get("entityId") if payload.get("entityType") in {"tool_run", "agent_tool_run"} else None,
        run.get("id"),
    ):
        return False
    if message_id and not _matches_any(
        message_id,
        refs.get("messageId"),
        payload.get("messageId"),
        message.get("id"),
        refs.get("branchPointMessageId"),
        refs.get("branchCopyOfMessageId"),
    ):
        return False
    if branch_thread_id and not _matches_any(
        branch_thread_id,
        row.get("threadId"),
        refs.get("branchThreadId"),
        payload.get("branchThreadId"),
        message.get("threadId"),
    ):
        return False
    if source_thread_id and not _matches_any(
        source_thread_id,
        refs.get("sourceThreadId"),
        payload.get("sourceThreadId"),
        message.get("branchFromThreadId"),
    ):
        return False
    if citation_source and not _citation_matches(row, citation_source):
        return False
    return True


def _lexical_score(query: str, text: str) -> float:
    q = query.casefold().strip()
    if not q:
        return 0.0
    hay = text.casefold()
    score = 1.0 if q in hay else 0.0
    terms = _TOKEN_RE.findall(q)
    if terms:
        hits = sum(1 for term in terms if term in hay)
        score += hits / len(terms)
    return score


def _cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(a[i] * a[i] for i in range(n)))
    nb = math.sqrt(sum(b[i] * b[i] for i in range(n)))
    return dot / (na * nb) if na and nb else 0.0


async def rank_ledger_rows(
    rows: list[dict[str, Any]],
    query: str | None,
    *,
    semantic: bool = True,
    max_semantic_rows: int = 200,
) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return sorted(rows, key=lambda row: (int(row.get("ts") or 0), str(row.get("id") or "")))
    scored: list[tuple[float, int, dict[str, Any], str]] = []
    for idx, row in enumerate(rows):
        text = ledger_search_text(row)
        scored.append((_lexical_score(q, text), idx, row, text[:6000]))
    scored.sort(key=lambda item: (item[0], int(item[2].get("ts") or 0)), reverse=True)
    selected = scored[: max(1, min(max_semantic_rows, len(scored)))]
    combined: dict[int, float] = {idx: score for score, idx, _row, _text in scored}
    semantic_backend = "none"
    if semantic and selected:
        try:
            from .embeddings import HashEmbedder

            # Ledger lookup is latency-sensitive and can scan many rows. Use the
            # deterministic local embedder here so search never blocks on the
            # runtime embedding service used by RAG/pgvector.
            embedder = HashEmbedder(256)
            vectors = await embedder.embed([q, *[text for _score, _idx, _row, text in selected]])
            query_vec = vectors[0]
            for vector, (lex_score, idx, _row, _text) in zip(vectors[1:], selected, strict=False):
                semantic_score = max(0.0, _cosine(query_vec, vector))
                combined[idx] = max(lex_score, semantic_score + min(lex_score, 1.0) * 0.25)
            semantic_backend = embedder.name
        except Exception:
            pass
    ranked: list[dict[str, Any]] = []
    for _score, idx, row, _text in scored:
        score = combined.get(idx, 0.0)
        if score <= 0:
            continue
        out = dict(row)
        out["search"] = {
            "query": q,
            "score": round(float(score), 4),
            "semantic": bool(semantic),
            "backend": semantic_backend if semantic else "lexical",
        }
        ranked.append(out)
    ranked.sort(key=lambda row: (float((row.get("search") or {}).get("score") or 0.0), int(row.get("ts") or 0)), reverse=True)
    return ranked


async def record_message_ledger(repo: Any, msg: dict[str, Any]) -> None:
    thread_id = str(msg.get("threadId") or "")
    if not thread_id:
        return
    role = str(msg.get("role") or "unknown")
    event_type = f"message.{role}"
    await append_ledger_event(
        repo,
        event_type=event_type,
        thread_id=thread_id,
        department_id=msg.get("departmentId"),
        payload={
            "messageId": msg.get("id"),
            "authorName": msg.get("authorName"),
            "textPreview": str(msg.get("text") or "")[:2000],
            "status": msg.get("status"),
            "pending": bool(msg.get("pending")),
            "message": msg,
        },
        refs={
            "messageId": msg.get("id"),
            "parentMessageId": msg.get("parentMessageId"),
            "replyToMessageId": msg.get("replyToMessageId"),
            "quoteMessageId": msg.get("quoteMessageId"),
            "regeneratedFromMessageId": msg.get("regeneratedFromMessageId"),
            "editedFromMessageId": msg.get("editedFromMessageId"),
            "branchThreadId": thread_id if msg.get("branchFromThreadId") else None,
            "sourceThreadId": msg.get("branchFromThreadId"),
            "branchPointMessageId": msg.get("branchPointMessageId"),
            "branchCopyOfMessageId": msg.get("branchCopyOfMessageId"),
        },
    )
    if msg.get("branchFromThreadId") or msg.get("branchPointMessageId") or msg.get("branchCopyOfMessageId"):
        await append_ledger_event(
            repo,
            event_type="branch.message",
            thread_id=thread_id,
            department_id=msg.get("departmentId"),
            payload={
                "messageId": msg.get("id"),
                "branchThreadId": thread_id,
                "sourceThreadId": msg.get("branchFromThreadId"),
                "branchPointMessageId": msg.get("branchPointMessageId"),
                "branchCopyOfMessageId": msg.get("branchCopyOfMessageId"),
                "editedFromMessageId": msg.get("editedFromMessageId"),
                "message": msg,
            },
            refs={
                "messageId": msg.get("id"),
                "branchThreadId": thread_id,
                "sourceThreadId": msg.get("branchFromThreadId"),
                "branchPointMessageId": msg.get("branchPointMessageId"),
                "branchCopyOfMessageId": msg.get("branchCopyOfMessageId"),
            },
        )
    citations = _message_citations(msg)
    if citations:
        await append_ledger_event(
            repo,
            event_type="citation.record",
            thread_id=thread_id,
            department_id=msg.get("departmentId"),
            payload={
                "messageId": msg.get("id"),
                "citations": citations,
                "message": msg,
            },
            refs={
                "messageId": msg.get("id"),
                "citationCount": len(citations),
                "citationSources": [
                    citation.get("source") or citation.get("title") or citation.get("label")
                    for citation in citations
                    if isinstance(citation, dict)
                ],
            },
        )


async def record_tool_ledger(
    repo: Any,
    *,
    thread_id: str,
    department_id: str,
    run: dict[str, Any],
) -> None:
    status = str(run.get("status") or "")
    event_type = "tool.result" if status in {"completed", "succeeded", "failed", "cancelled", "blocked"} else "tool.call"
    await append_ledger_event(
        repo,
        event_type=event_type,
        thread_id=thread_id,
        department_id=department_id,
        payload={
            "tool": run.get("tool"),
            "status": run.get("status"),
            "summary": run.get("summary"),
            "runId": run.get("id"),
            "run": run,
        },
        refs={"toolRunId": run.get("id"), "messageId": run.get("messageId")},
    )


async def record_approval_ledger(repo: Any, approval: dict[str, Any], *, event_type: str = "approval.record") -> None:
    dept_id = approval.get("departmentId")
    thread_id = str(approval.get("threadId") or f"dept:{dept_id or 'company'}")
    await append_ledger_event(
        repo,
        event_type=event_type,
        thread_id=thread_id,
        department_id=dept_id,
        payload={
            "approvalId": approval.get("id"),
            "kind": approval.get("kind"),
            "status": approval.get("status"),
            "approval": approval,
        },
        refs={
            "approvalId": approval.get("id"),
            "toolRunId": (approval.get("action") or {}).get("toolRunId") if isinstance(approval.get("action"), dict) else None,
        },
    )


async def record_cost_ledger(
    repo: Any,
    *,
    record_id: str,
    ts: int,
    department_id: str,
    category: str,
    usd: float,
    detail: str | None = None,
    provider_id: str | None = None,
    model: str | None = None,
    speed: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> None:
    await append_ledger_event(
        repo,
        event_type="cost.telemetry",
        thread_id=f"dept:{department_id}",
        department_id=department_id,
        payload={
            "costId": record_id,
            "ts": ts,
            "departmentId": department_id,
            "category": category,
            "usd": round(float(usd), 6),
            "detail": detail,
            "providerId": provider_id,
            "model": model,
            "speed": speed,
            "tokensIn": tokens_in,
            "tokensOut": tokens_out,
        },
        refs={"costId": record_id},
    )


async def record_runtime_event_ledger(
    repo: Any,
    event: Any,
    *,
    thread_id: str,
    department_id: str | None = None,
    message_id: str | None = None,
    source: str | None = None,
    category: str | None = None,
) -> None:
    kind = str(getattr(event, "kind", "") or "runtime_event")
    data = getattr(event, "data", {})
    if not isinstance(data, dict):
        data = {"value": data}
    await append_ledger_event(
        repo,
        event_type="runtime.event",
        thread_id=thread_id,
        department_id=department_id,
        payload={
            "kind": kind,
            "data": data,
            "messageId": message_id,
            "source": source,
            "category": category,
            "runtimeEvent": {
                "kind": kind,
                "data": data,
            },
        },
        refs={
            "messageId": message_id,
            "toolRunId": (data.get("run") or {}).get("id") if isinstance(data.get("run"), dict) else data.get("id"),
        },
    )


LEDGER_ENTITY_TYPES = {
    "agent_tool_run",
    "artifact",
    "artifact_version",
    "audit_note",
    "checkpoint",
    "critique_report",
    "decision",
    "decision_event",
    "evidence_pack",
    "handoff_message",
    "meeting",
    "org_checkpoint",
    "project",
    "runtime_checkpoint",
    "tool_run",
    "war_room",
}


def _entity_thread_id(etype: str, obj: dict[str, Any], dept_id: str | None) -> str:
    if obj.get("threadId"):
        return str(obj["threadId"])
    if etype == "handoff_message" and obj.get("handoffId"):
        return f"handoff:{obj['handoffId']}"
    if obj.get("projectId"):
        return f"project:{obj['projectId']}"
    if obj.get("project"):
        return f"project:{obj['project']}"
    return f"dept:{dept_id or obj.get('departmentId') or obj.get('ownerDept') or 'company'}"


async def record_entity_ledger(
    repo: Any,
    *,
    entity_type: str,
    entity: dict[str, Any],
    department_id: str | None = None,
    project_id: str | None = None,
    status: str | None = None,
) -> None:
    if entity_type not in LEDGER_ENTITY_TYPES:
        return
    entity_id = entity.get("id")
    dept_id = department_id or entity.get("departmentId") or entity.get("dept") or entity.get("ownerDept")
    event_type = f"entity.{entity_type}"
    if entity_type in {"tool_run", "agent_tool_run"}:
        run_status = str(entity.get("status") or status or "")
        event_type = "tool.result" if run_status in {"completed", "succeeded", "failed", "cancelled", "blocked"} else "tool.call"
    elif entity_type in {"checkpoint", "org_checkpoint", "runtime_checkpoint"}:
        event_type = "checkpoint.record"
    elif entity_type in {"artifact", "artifact_version"}:
        event_type = "artifact.record"
    elif entity_type == "decision":
        event_type = "decision.record"
    await append_ledger_event(
        repo,
        event_type=event_type,
        thread_id=_entity_thread_id(entity_type, entity, dept_id),
        department_id=dept_id,
        payload={
            "entityType": entity_type,
            "entityId": entity_id,
            "status": status or entity.get("status"),
            "projectId": project_id or entity.get("projectId") or entity.get("project"),
            "entity": entity,
        },
        refs={
            "entityType": entity_type,
            "entityId": entity_id,
            "projectId": project_id or entity.get("projectId") or entity.get("project"),
            "toolRunId": entity_id if entity_type in {"tool_run", "agent_tool_run"} else None,
            "artifactId": entity.get("artifactId") or (entity_id if entity_type == "artifact" else None),
            "decisionId": entity_id if entity_type == "decision" else None,
        },
    )


async def record_compaction_ledger(
    repo: Any,
    *,
    department_id: str,
    thread_id: str | None,
    archive_id: str,
    transcript: str | None,
    summary: str,
) -> None:
    digest = transcript_digest(transcript) if transcript else None
    await append_ledger_event(
        repo,
        event_type="compaction.summary",
        thread_id=thread_id or thread_id_for(department_id),
        department_id=department_id,
        payload={
            "archiveId": archive_id,
            "summaryPreview": summary[:1200],
            "transcriptDigest": digest,
        },
        refs={"archiveId": archive_id, "threadId": thread_id},
    )
