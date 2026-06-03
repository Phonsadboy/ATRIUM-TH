"""Chat message rendering metadata.

The frontend owns visual rendering, but the backend is the durable source of
facts that make rendering deterministic across send responses, snapshots, and
thread replay.
"""
from __future__ import annotations

import re
from typing import Any

_CODE_FENCE_RE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_URL_RE = re.compile(r"https?://[^\s<>\[\]{}\"']+")
_MENTION_RE = re.compile(r"(?<![\w@])@([0-9A-Za-z_\-\u0E00-\u0E7F]{2,64})")
_INPUT_STATUSES = {"sent", "executed", "failed", "draft", "pending_approval"}
_LEGACY_INPUT_STATUS_ALIASES = {
    "queued": "sent",
    "sending": "sent",
    "cancelled": "failed",
    "blocked": "failed",
}


def _clip(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _clean_url(raw: str) -> str:
    return raw.rstrip(".,;:!?)")


def _code_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for index, match in enumerate(_CODE_FENCE_RE.finditer(text)):
        raw_lang = match.group(1).strip()
        language = raw_lang.split()[0].strip("{}").lower() if raw_lang else None
        blocks.append(
            {
                "index": index,
                "language": language or None,
                "text": match.group(2).strip("\n"),
            }
        )
    return blocks


def _links(text: str) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for match in _URL_RE.finditer(text):
        url = _clean_url(match.group(0))
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "label": url})
    return out


def _mentions(text: str) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for match in _MENTION_RE.finditer(text):
        target = match.group(1)
        key = target.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"raw": f"@{target}", "target": target})
    return out


def _looks_markdown(text: str) -> bool:
    if "```" in text or "`" in text:
        return True
    for pattern in (
        r"(?m)^\s{0,3}#{1,6}\s+\S",
        r"(?m)^\s*[-*+]\s+\S",
        r"(?m)^\s*\d+\.\s+\S",
        r"\*\*[^*]+\*\*",
        r"\[[^\]]+\]\([^)]+\)",
        r"(?m)^\s*\|.+\|\s*$",
    ):
        if re.search(pattern, text):
            return True
    return False


def _content_format(role: str, text: str) -> str:
    if role in {"agent", "executive"}:
        return "markdown"
    return "markdown" if _looks_markdown(text) else "plain"


def citation_chips(memory_hits: list[dict[str, Any]], *, department_id: str | None = None) -> list[dict[str, Any]]:
    chips: list[dict[str, Any]] = []
    for index, hit in enumerate(memory_hits, start=1):
        memory_id = str(hit.get("id") or f"memory#{index}")
        title = str(hit.get("title") or memory_id)
        score = hit.get("score")
        chips.append(
            {
                "id": memory_id,
                "kind": "knowledge",
                "label": title or f"memory#{index}",
                "title": title,
                "source": hit.get("source"),
                "score": float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
                "snippet": _clip(hit.get("text")),
                "departmentId": department_id,
            }
        )
    return chips


def ensure_rendering_metadata(
    message: dict[str, Any],
    *,
    usage: dict[str, Any] | None = None,
    citations: list[dict[str, Any]] | None = None,
    notices: list[str] | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    out = dict(message)
    text = str(out.get("text") or "")
    role = str(out.get("role") or "")
    existing = dict(out.get("render") or {})
    usage = usage or {}
    if not out.get("status"):
        if out.get("approvalId") and out.get("approvalStatus") == "pending":
            out["status"] = "pending_approval"
        elif out.get("pending"):
            out["status"] = "sending"
        else:
            out["status"] = "sent"

    content_format = str(out.get("contentFormat") or existing.get("format") or _content_format(role, text))
    compact_enqueued = bool(usage.get("compactEnqueued", existing.get("compactEnqueued", False)))

    next_notices = list(existing.get("notices") or [])
    for notice in notices or []:
        if notice not in next_notices:
            next_notices.append(notice)
    if (
        "approval_required" in next_notices
        and out.get("approvalId")
        and out.get("approvalStatus") != "pending"
    ):
        next_notices = [notice for notice in next_notices if notice != "approval_required"]
    lower_text = text.lower()
    if ("budget guardrail" in lower_text or "ติด budget guardrail" in text) and "budget_guardrail" not in next_notices:
        next_notices.append("budget_guardrail")
        severity = severity or "warn"
    if compact_enqueued and "context_compacted" not in next_notices:
        next_notices.append("context_compacted")

    render = {
        **existing,
        "format": content_format,
        "codeBlocks": existing.get("codeBlocks") or _code_blocks(text),
        "links": existing.get("links") or _links(text),
        "mentions": existing.get("mentions") or _mentions(text),
        "citations": citations if citations is not None else existing.get("citations", []),
        "costUsd": usage.get("usd", existing.get("costUsd")),
        "compactEnqueued": compact_enqueued,
        "notices": next_notices,
        "severity": severity or existing.get("severity"),
    }
    out["contentFormat"] = content_format
    out["render"] = render
    input_meta = out.get("input")
    if isinstance(input_meta, dict):
        input_meta = dict(input_meta)
        status = input_meta.get("status")
        if status not in _INPUT_STATUSES:
            mapped = _LEGACY_INPUT_STATUS_ALIASES.get(str(status or ""))
            if mapped:
                input_meta["status"] = mapped
            else:
                input_meta.pop("status", None)
        out["input"] = input_meta
    return out
