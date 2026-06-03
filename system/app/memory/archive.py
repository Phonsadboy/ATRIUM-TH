"""Archive audit helpers for compacted memory transcripts."""
from __future__ import annotations

import hashlib
from typing import Any


def canonical_transcript(transcript: Any) -> str:
    """Normalize transcript text before hashing for audit comparisons."""
    text = str(transcript or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines)


def transcript_digest(transcript: Any, *, length: int = 24) -> str:
    """Return a stable digest for transcript audit metadata."""
    digest = hashlib.sha256(canonical_transcript(transcript).encode("utf-8")).hexdigest()
    if length <= 0:
        return f"sha256:{digest}"
    return f"sha256:{digest[:length]}"


def transcript_audit_metadata(
    *,
    department_id: str,
    thread_id: str | None,
    message_count: int | None,
    transcript: Any,
    source: str,
) -> dict[str, Any]:
    """Build compact archive metadata without mutating the archive body."""
    canonical = canonical_transcript(transcript)
    return {
        "departmentId": department_id,
        "threadId": thread_id,
        "messageCount": int(message_count or 0),
        "source": source,
        "transcriptDigest": transcript_digest(canonical),
        "transcriptBytes": len(canonical.encode("utf-8")),
        "hasTranscript": bool(canonical),
    }


def attach_archive_audit(
    archive: dict[str, Any],
    *,
    department_id: str,
    source: str = "compact",
) -> dict[str, Any]:
    """Return an archive copy with deterministic audit metadata attached."""
    enriched = dict(archive)
    enriched["audit"] = transcript_audit_metadata(
        department_id=department_id,
        thread_id=enriched.get("threadId"),
        message_count=enriched.get("messageCount"),
        transcript=enriched.get("transcript"),
        source=source,
    )
    return enriched
