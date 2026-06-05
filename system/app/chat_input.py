"""Backend support for chat input affordances.

This module keeps parsing and lightweight suggestions deterministic so the REST
API, background engine, and future UI autocomplete all agree on the same input
contract.
"""
from __future__ import annotations

import base64
import json
import math
import re
from typing import Any

from .config import get_settings
from .file_intake import bytes_from_uri, extract_text_from_uri, guess_mime
from .threads import is_exec, thread_id_for

MAX_ATTACHMENT_CONTEXT_CHARS = 10_000
VIDEO_CONTEXT_KEYS = (
    "projectId",
    "videoProjectId",
    "assetId",
    "timelineId",
    "timelineVersion",
    "renderId",
    "mediaHandle",
    "contextTool",
)

_COMMAND_RE = re.compile(r"^/([A-Za-z][A-Za-z0-9_-]*)(?:\s+(.*))?$", re.DOTALL)
_MENTION_RE = re.compile(r"(?<![\w@])@([0-9A-Za-z_\-\u0E00-\u0E7F]{2,64})")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+", re.UNICODE)
_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")

COMMAND_SPECS: list[dict[str, Any]] = [
    {
        "name": "assign",
        "aliases": ["task"],
        "description": "Create an assigned task for a department without calling the model.",
        "usage": "/assign @department Task title - optional detail",
        "examples": ["/assign @engineering ตรวจ API ส่งข้อความ"],
    },
    {
        "name": "summarize",
        "aliases": ["summary"],
        "description": "Summarize recent messages in the current thread.",
        "usage": "/summarize [message_count]",
        "examples": ["/summarize 12"],
    },
    {
        "name": "cost",
        "aliases": ["spend", "budget"],
        "description": "Show current spend and forecast for this chat's responding department.",
        "usage": "/cost",
        "examples": ["/cost"],
    },
    {
        "name": "memory",
        "aliases": ["mem"],
        "description": "Show memory/RAG counts for this department.",
        "usage": "/memory",
        "examples": ["/memory"],
    },
    {
        "name": "clear",
        "aliases": ["reset"],
        "description": "Clear messages in the current thread and leave an audit line.",
        "usage": "/clear",
        "examples": ["/clear"],
    },
]

_COMMAND_BY_ALIAS: dict[str, dict[str, Any]] = {}
for spec in COMMAND_SPECS:
    _COMMAND_BY_ALIAS[str(spec["name"]).lower()] = spec
    for alias in spec.get("aliases", []):
        _COMMAND_BY_ALIAS[str(alias).lower()] = spec


def parse_slash_command(text: str) -> dict[str, str] | None:
    stripped = str(text or "").strip()
    if not stripped.startswith("/") or stripped.startswith("//"):
        return None
    match = _COMMAND_RE.match(stripped)
    if not match:
        return {"name": "", "rawName": "", "args": stripped[1:].strip()}
    raw_name = match.group(1).lower()
    spec = _COMMAND_BY_ALIAS.get(raw_name)
    return {
        "name": str(spec["name"]) if spec else raw_name,
        "rawName": raw_name,
        "args": (match.group(2) or "").strip(),
    }


def estimate_input(text: str, attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    value = str(text or "")
    attachments = attachments or []
    settings = get_settings()
    max_input_chars = settings.chat_max_input_char_limit
    max_recommended_tokens = settings.chat_recommended_input_token_limit
    chars = len(value)
    thai_chars = len(_THAI_RE.findall(value))
    words = len(_WORD_RE.findall(value))
    non_thai_chars = max(chars - thai_chars, 0)
    estimated = max(1 if value.strip() else 0, math.ceil(thai_chars / 2.2 + non_thai_chars / 4.0))
    estimated += len(attachments) * 120
    warnings: list[str] = []
    if max_input_chars > 0 and chars > max_input_chars:
        warnings.append(f"input exceeds {max_input_chars} characters")
    if max_recommended_tokens > 0 and estimated > max_recommended_tokens:
        warnings.append(f"estimated input is above {max_recommended_tokens} tokens")
    within_limit = (
        (max_input_chars <= 0 or chars <= max_input_chars)
        and (max_recommended_tokens <= 0 or estimated <= max_recommended_tokens)
    )
    return {
        "characters": chars,
        "words": words,
        "estimatedTokens": estimated,
        "maxRecommendedTokens": max_recommended_tokens,
        "withinLimit": within_limit,
        "warnings": warnings,
        "attachmentCount": len(attachments),
    }


def input_character_limit_exceeded(estimate: dict[str, Any]) -> bool:
    limit = get_settings().chat_max_input_char_limit
    return limit > 0 and int(estimate.get("characters") or 0) > limit


def input_character_limit_detail() -> str:
    limit = get_settings().chat_max_input_char_limit
    if limit <= 0:
        return "message text exceeds configured character limit"
    return f"message text exceeds {limit} characters"


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("@"):
        text = text[1:]
    if text.startswith("ฝ่าย"):
        text = text[4:]
    return re.sub(r"[\s_\-]+", "", text)


def _department_keys(dept: dict[str, Any]) -> set[str]:
    values = {
        dept.get("id"),
        dept.get("name"),
        dept.get("agentName"),
        f"ฝ่าย{dept.get('name', '')}",
    }
    return {key for key in (_norm(value) for value in values) if key}


def resolve_department_reference(raw: str, departments: list[dict[str, Any]]) -> dict[str, Any] | None:
    key = _norm(raw)
    if not key:
        return None
    for dept in departments:
        if key in _department_keys(dept):
            return dept
    for dept in departments:
        if any(key and key in dept_key for dept_key in _department_keys(dept)):
            return dept
    return None


def resolve_department_mentions(text: str, departments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _MENTION_RE.finditer(str(text or "")):
        raw_target = match.group(1)
        raw = f"@{raw_target}"
        dept = resolve_department_reference(raw_target, departments)
        key = dept["id"] if dept else raw.lower()
        if key in seen:
            continue
        seen.add(key)
        target = {
            "raw": raw,
            "departmentId": dept.get("id") if dept else None,
            "threadId": thread_id_for(dept["id"]) if dept else None,
            "displayName": dept.get("name") if dept else raw_target,
            "agentName": dept.get("agentName") if dept else None,
            "matchedBy": "mention" if dept else "unresolved",
        }
        out.append(target)
    return out


def autocomplete_mentions(query: str, departments: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    q = _norm(query)
    candidates = list(departments)
    if q:
        candidates = [
            dept
            for dept in candidates
            if any(q in key for key in _department_keys(dept))
        ]
    return [
        {
            "raw": f"@{dept.get('name') or dept['id']}",
            "departmentId": dept["id"],
            "threadId": thread_id_for(dept["id"]),
            "displayName": dept.get("name") or dept["id"],
            "agentName": dept.get("agentName"),
            "matchedBy": "autocomplete",
        }
        for dept in candidates[:limit]
    ]


def choose_mentioned_responder(
    current_dept: dict[str, Any],
    mentions: list[dict[str, Any]],
    departments: list[dict[str, Any]],
) -> dict[str, Any]:
    if not is_exec(str(current_dept.get("id") or "")):
        return current_dept
    mentioned_ids = [
        mention.get("departmentId")
        for mention in mentions
        if mention.get("departmentId") and not is_exec(str(mention.get("departmentId")))
    ]
    if len(mentioned_ids) != 1:
        return current_dept
    target = next((dept for dept in departments if dept.get("id") == mentioned_ids[0]), None)
    return target or current_dept


def split_assign_command(args: str, departments: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str, str]:
    raw = str(args or "").strip()
    if not raw:
        return None, "", ""
    target: dict[str, Any] | None = None
    title = raw
    first_mention = _MENTION_RE.search(raw)
    if first_mention:
        target = resolve_department_reference(first_mention.group(1), departments)
        title = (raw[: first_mention.start()] + raw[first_mention.end() :]).strip()
    if target is None:
        parts = raw.split(maxsplit=1)
        if parts:
            target = resolve_department_reference(parts[0], departments)
            if target is not None:
                title = parts[1].strip() if len(parts) > 1 else ""
    detail = title
    for separator in (" -- ", " — ", " - "):
        if separator in title:
            head, tail = title.split(separator, 1)
            title = head.strip()
            detail = tail.strip() or title
            break
    return target, title.strip(), detail.strip()


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _video_media_context(attachment: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    out = {
        "artifactId": _first_present(artifact.get("id"), attachment.get("artifactId"), attachment.get("artifact_id")),
        "projectId": _first_present(artifact.get("projectId"), artifact.get("project_id"), attachment.get("projectId"), attachment.get("project_id")),
        "videoProjectId": _first_present(artifact.get("videoProjectId"), artifact.get("video_project_id"), attachment.get("videoProjectId"), attachment.get("video_project_id")),
        "assetId": _first_present(artifact.get("assetId"), artifact.get("asset_id"), attachment.get("assetId"), attachment.get("asset_id")),
        "timelineId": _first_present(artifact.get("timelineId"), artifact.get("timeline_id"), attachment.get("timelineId"), attachment.get("timeline_id")),
        "timelineVersion": _first_present(artifact.get("timelineVersion"), artifact.get("timeline_version"), attachment.get("timelineVersion"), attachment.get("timeline_version")),
        "renderId": _first_present(artifact.get("renderId"), artifact.get("render_id"), attachment.get("renderId"), attachment.get("render_id")),
        "mediaHandle": _first_present(artifact.get("mediaHandle"), artifact.get("media_handle"), attachment.get("mediaHandle"), attachment.get("media_handle")),
        "contextTool": _first_present(artifact.get("contextTool"), artifact.get("context_tool"), attachment.get("contextTool"), attachment.get("context_tool")),
        "contextArgs": _first_present(artifact.get("contextArgs"), artifact.get("context_args"), attachment.get("contextArgs"), attachment.get("context_args")),
    }
    if not out.get("videoProjectId") and out.get("projectId") and (out.get("mediaHandle") or out.get("renderId") or out.get("timelineId")):
        out["videoProjectId"] = out["projectId"]
    if not out.get("contextTool") and out.get("projectId") and (out.get("mediaHandle") or out.get("renderId") or out.get("timelineId")):
        out["contextTool"] = "video.context_packet"
    if not isinstance(out.get("contextArgs"), dict) and out.get("projectId") and out.get("contextTool") == "video.context_packet":
        context_args = {"projectId": out["projectId"]}
        if out.get("timelineId"):
            context_args["timelineId"] = out["timelineId"]
        if out.get("timelineVersion") is not None:
            context_args["version"] = out["timelineVersion"]
            context_args["timelineVersion"] = out["timelineVersion"]
        if out.get("renderId"):
            context_args["renderId"] = out["renderId"]
        if out.get("artifactId"):
            context_args["artifactId"] = out["artifactId"]
        out["contextArgs"] = context_args
    return {key: value for key, value in out.items() if value is not None and value != ""}


def _video_media_context_text(attachment: dict[str, Any], artifact: dict[str, Any], *, label: str) -> str:
    context = _video_media_context(attachment, artifact)
    if not (context.get("mediaHandle") or context.get("videoProjectId") or context.get("renderId") or context.get("timelineId")):
        return ""
    lines = [f"Video attachment {context.get('artifactId') or attachment.get('artifactId') or '-'} ({label}) media context:"]
    for key in VIDEO_CONTEXT_KEYS:
        if context.get(key) is not None:
            lines.append(f"- {key}={context[key]}")
    context_args = context.get("contextArgs")
    if isinstance(context_args, dict) and context_args:
        lines.append(f"- contextArgs={json.dumps(context_args, ensure_ascii=False, sort_keys=True)}")
    lines.append("Use run_owner_tool with tool=contextTool and args=contextArgs to inspect the current video context; use the same projectId/timelineId/version/renderId when planning, patching, re-rendering, QA, or requesting review.")
    return "\n".join(lines)


def prompt_starters_for_thread(
    thread_id: str,
    dept: dict[str, Any],
    departments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if is_exec(str(dept.get("id") or "")):
        first_dept = next((d for d in departments if not is_exec(str(d.get("id") or ""))), None)
        mention = f"@{first_dept['name']} " if first_dept else ""
        starters = [
            ("assign", "มอบหมายงาน", f"/assign {mention}ตรวจความพร้อมของฟีเจอร์แชต input"),
            ("cost", "ดูค่าใช้จ่าย", "/cost"),
            ("summary", "สรุปบทสนทนา", "/summarize 12"),
        ]
    else:
        starters = [
            ("status", "ขอสถานะ", f"สรุปสถานะงานของฝ่าย{dept.get('name')}ตอนนี้ให้หน่อย"),
            ("memory", "ดูความจำ", "/memory"),
            ("handoff", "ถามอีกฝ่าย", "@ฝ่ายที่เกี่ยวข้อง ขอข้อมูลที่ยังขาดสำหรับงานนี้"),
        ]
    return [
        {
            "id": f"{thread_id}:{key}",
            "threadId": thread_id,
            "departmentId": dept.get("id"),
            "label": label,
            "text": text,
            "category": key,
        }
        for key, label, text in starters
    ]


def suggested_followups_for_response(
    dept: dict[str, Any],
    user_text: str,
    reply_text: str,
    *,
    command_name: str | None = None,
    mentions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    del reply_text
    mentions = mentions or []
    suggestions: list[tuple[str, str, str]] = []
    if command_name == "assign":
        suggestions.append(("task-board", "เปิดดูงานที่สร้าง", "แสดงรายการงานล่าสุดของฝ่ายนี้"))
        suggestions.append(("brief", "เขียน brief เพิ่ม", "ช่วยแตก brief ของงานนี้ให้ละเอียดขึ้น"))
    elif command_name == "clear":
        suggestions.append(("starter", "เริ่มโจทย์ใหม่", "ช่วยวางแผนงานใหม่จากศูนย์"))
    elif command_name == "cost":
        suggestions.append(("optimize", "ลดค่าใช้จ่าย", "เสนอวิธีลดค่าใช้จ่ายของแชตและงานเอเจนต์"))
    elif mentions:
        suggestions.append(("handoff", "สรุปให้ฝ่ายที่ถูก mention", "ช่วยสรุปบริบทให้ฝ่ายที่ถูก mention ต่อได้ทันที"))
    else:
        suggestions.append(("next", "แตกขั้นถัดไป", "จากคำตอบนี้ ควรทำอะไรต่อเป็นลำดับแรก"))
    if not is_exec(str(dept.get("id") or "")):
        suggestions.append(("memory", "บันทึกเข้าความจำ", f"บันทึกข้อสรุปนี้เข้าความจำของฝ่าย{dept.get('name')}"))
    if len(suggestions) < 3 and "/assign" not in user_text:
        suggestions.append(("assign", "มอบหมายเป็นงาน", f"/assign @{dept.get('name') or dept.get('id')} {str(user_text or '').strip()[:80]}"))
    return [
        {"id": key, "label": label, "text": text}
        for key, label, text in suggestions[:3]
        if text.strip()
    ]


def summarize_messages(messages: list[dict[str, Any]], *, limit: int = 12) -> str:
    recent = [m for m in messages if not m.get("pending")][-max(1, min(limit, 40)) :]
    if not recent:
        return "ยังไม่มีข้อความให้สรุป"
    lines = ["สรุปบทสนทนาล่าสุด:"]
    for msg in recent:
        author = msg.get("authorName") or msg.get("role") or "unknown"
        text = str(msg.get("text") or "").strip().replace("\n", " ")
        if not text:
            continue
        lines.append(f"- {author}: {text[:220]}")
    return "\n".join(lines)


def _attachment_context_body(text: str, *, limit: int = MAX_ATTACHMENT_CONTEXT_CHARS) -> str:
    body = str(text or "")
    if len(body) <= limit:
        return body
    return (
        body[:limit]
        + f"\n[attachment context truncated at {limit} chars; use the artifact id, uri, or sourcePath to inspect the full file]"
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "full"}


def _positive_int(*values: Any) -> int | None:
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def _attachment_context_limit(attachment: dict[str, Any], artifact: dict[str, Any]) -> int:
    explicit = _positive_int(attachment.get("contextMaxChars"), attachment.get("context_max_chars"))
    if explicit is not None:
        return explicit
    include_full = _truthy(
        _first_present(
            attachment.get("includeFullContext"),
            attachment.get("include_full_context"),
            artifact.get("includeFullContext"),
            artifact.get("include_full_context"),
        )
    )
    if include_full:
        known_size = _positive_int(
            artifact.get("sourceSizeBytes"),
            artifact.get("source_size_bytes"),
            artifact.get("contentSizeBytes"),
            artifact.get("content_size_bytes"),
            attachment.get("sourceSizeBytes"),
            attachment.get("source_size_bytes"),
            attachment.get("sizeBytes"),
            attachment.get("size_bytes"),
        )
        if known_size is not None:
            return max(MAX_ATTACHMENT_CONTEXT_CHARS + 1, known_size + 1)
        return MAX_ATTACHMENT_CONTEXT_CHARS * 20
    return MAX_ATTACHMENT_CONTEXT_CHARS


async def attachment_context(repo, attachments: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for attachment in attachments:
        artifact_id = attachment.get("artifactId") or attachment.get("artifact_id")
        if not artifact_id:
            continue
        artifact = await repo.get_entity("artifact", str(artifact_id))
        if not artifact:
            continue
        uri = None
        preview = artifact.get("preview")
        if isinstance(preview, dict):
            uri = preview.get("uri")
        uri = uri or artifact.get("uri")
        label = artifact.get("name") or artifact_id
        mime = artifact.get("contentMime") or artifact.get("mime") or attachment.get("mime")
        context_limit = _attachment_context_limit(attachment, artifact)
        video_context = _video_media_context_text(attachment, artifact, label=str(label))
        if video_context:
            parts.append(video_context)
        audio_transcription = artifact.get("audioTranscription") if isinstance(artifact.get("audioTranscription"), dict) else None
        transcript = str((audio_transcription or {}).get("text") or "").strip()
        if transcript:
            model = str((audio_transcription or {}).get("model") or "unknown")
            parts.append(
                f"Attachment {artifact_id} ({label}) audio transcript"
                f" [model={model}]:\n{_attachment_context_body(transcript, limit=context_limit)}"
            )
            continue
        text = extract_text_from_uri(uri, filename=str(label), mime=mime, limit=context_limit + 1)
        if text:
            parts.append(f"Attachment {artifact_id} ({label}):\n{_attachment_context_body(text, limit=context_limit)}")
        elif not video_context:
            source_path = artifact.get("sourcePath") or attachment.get("sourcePath") or attachment.get("source_path")
            reference = f" sourcePath={source_path}" if source_path else ""
            copy_status = artifact.get("copyStatus") or attachment.get("copyStatus") or attachment.get("copy_status")
            copy_note = f" copyStatus={copy_status}" if copy_status else ""
            parts.append(
                f"Attachment {artifact_id} ({label}): no text preview available; "
                f"kind={artifact.get('kind') or attachment.get('kind') or 'file'} "
                f"mime={mime or 'unknown'} size={artifact.get('contentSizeBytes') or attachment.get('sizeBytes') or 'unknown'}"
                f"{reference}{copy_note}"
            )
    return "\n\n".join(parts)


def attachment_image_blocks(
    attachments: list[dict[str, Any]],
    *,
    max_images: int,
    max_bytes: int | None,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    image_count = 0
    for attachment in attachments:
        if image_count >= max(0, max_images):
            break
        if not isinstance(attachment, dict):
            continue
        name = str(attachment.get("name") or attachment.get("artifactId") or "attachment")
        mime = str(attachment.get("mime") or "").split(";", 1)[0].strip().lower()
        if not mime:
            mime = guess_mime(name)
        if not mime.startswith("image/"):
            continue
        uri = attachment.get("uri")
        byte_limit = None if max_bytes is None or max_bytes <= 0 else max_bytes
        data = bytes_from_uri(uri, max_bytes=byte_limit)
        if data is None:
            continue
        artifact_id = str(attachment.get("artifactId") or attachment.get("artifact_id") or "").strip()
        size = attachment.get("sizeBytes") or attachment.get("size_bytes") or len(data)
        location_bits = []
        if artifact_id:
            location_bits.append(f"artifactId={artifact_id}")
            location_bits.append(f"download=/api/artifacts/{artifact_id}/download")
        if uri:
            location_bits.append(f"uri={uri}")
        blocks.append({
            "type": "text",
            "text": (
                f"Attached image {image_count + 1}: {name}; mime={mime}; size={size}; "
                + "; ".join(location_bits)
            ).strip(),
        })
        encoded = base64.b64encode(data).decode("ascii")
        blocks.append({"type": "input_image", "image_url": f"data:{mime};base64,{encoded}", "detail": "auto"})
        image_count += 1
    return blocks


def message_content_with_attachment_images(
    text: str,
    attachments: list[dict[str, Any]],
    *,
    max_images: int,
    max_bytes: int | None,
) -> str | list[dict[str, Any]]:
    image_blocks = attachment_image_blocks(attachments, max_images=max_images, max_bytes=max_bytes)
    if not image_blocks:
        return text
    base = text.strip() or "Analyze the attached file(s)."
    return [{"type": "text", "text": base}, *image_blocks]


def attachment_reference_text(attachments: list[dict[str, Any]], *, limit: int = 12) -> str:
    refs: list[str] = []
    for attachment in attachments[: max(0, limit)]:
        if not isinstance(attachment, dict):
            continue
        artifact_id = str(attachment.get("artifactId") or attachment.get("artifact_id") or "").strip()
        name = str(attachment.get("name") or artifact_id or "attachment")
        mime = str(attachment.get("mime") or "").strip()
        size = attachment.get("sizeBytes") or attachment.get("size_bytes")
        uri = str(attachment.get("uri") or "").strip()
        source_path = str(attachment.get("sourcePath") or attachment.get("source_path") or "").strip()
        copy_status = str(attachment.get("copyStatus") or attachment.get("copy_status") or "").strip()
        reference_kind = str(attachment.get("referenceKind") or attachment.get("reference_kind") or "").strip()
        context_max_chars = attachment.get("contextMaxChars") or attachment.get("context_max_chars")
        include_full_context = attachment.get("includeFullContext")
        if include_full_context is None:
            include_full_context = attachment.get("include_full_context")
        video_context = _video_media_context(attachment, attachment)
        bits = [name]
        if artifact_id:
            bits.append(f"artifactId={artifact_id}")
            bits.append(f"download=/api/artifacts/{artifact_id}/download")
        for key in ("mediaHandle", "projectId", "videoProjectId", "timelineId", "timelineVersion", "renderId", "contextTool"):
            if video_context.get(key) is not None:
                bits.append(f"{key}={video_context[key]}")
        context_args = video_context.get("contextArgs")
        if isinstance(context_args, dict) and context_args:
            bits.append(f"contextArgs={json.dumps(context_args, ensure_ascii=False, sort_keys=True)}")
        if mime:
            bits.append(f"mime={mime}")
        if size:
            bits.append(f"size={size}")
        if uri:
            bits.append(f"uri={uri}")
        if source_path and source_path != uri:
            bits.append(f"sourcePath={source_path}")
        if reference_kind:
            bits.append(f"referenceKind={reference_kind}")
        if copy_status:
            bits.append(f"copyStatus={copy_status}")
        if context_max_chars:
            bits.append(f"contextMaxChars={context_max_chars}")
        if include_full_context is not None:
            bits.append(f"includeFullContext={bool(_truthy(include_full_context))}")
        refs.append("; ".join(bits))
    if not refs:
        return ""
    more = "" if len(attachments) <= limit else f"\n- ...and {len(attachments) - limit} more attachment(s)"
    return "Attachments available for later tool use:\n- " + "\n- ".join(refs) + more


def message_text_with_attachment_refs(text: str, attachments: list[dict[str, Any]]) -> str:
    refs = attachment_reference_text(attachments)
    if not refs:
        return text
    return f"{text}\n\n{refs}".strip()
