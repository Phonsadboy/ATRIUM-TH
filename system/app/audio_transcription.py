"""Audio transcription provider subsystem.

This intentionally stays separate from chat/Responses providers. Batch STT uses
OpenAI-compatible `/audio/transcriptions` with an API-key-shaped bearer token;
OAuth can be opted in only for routes that are known to accept it.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import Settings, get_settings
from .clock import now_ms
from .file_intake import bytes_from_uri, guess_mime, safe_filename
from .ids import uid
from .schema import Artifact, ArtifactVersion

DEFAULT_OPENAI_AUDIO_MODEL = "gpt-4o-transcribe"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
AUDIO_SUFFIXES = {
    ".aac",
    ".aiff",
    ".flac",
    ".m4a",
    ".mp3",
    ".oga",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}
TOOL_RESULT_TEXT_LIMIT = 16_000


class AudioTranscriptionError(RuntimeError):
    pass


class AudioTranscriptionNotConfigured(AudioTranscriptionError):
    pass


class AudioTranscriptionSourceError(AudioTranscriptionError):
    pass


@dataclass(frozen=True)
class AudioAuthToken:
    token: str
    source: str
    kind: str


@dataclass(frozen=True)
class AudioTranscriptionResult:
    text: str
    model: str
    provider: str
    source: str
    mime: str
    filename: str
    duration_seconds: float | None = None
    raw: dict[str, Any] | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": "succeeded",
            "provider": self.provider,
            "model": self.model,
            "source": self.source,
            "text": self.text,
            "mime": self.mime,
            "filename": self.filename,
            "durationSeconds": self.duration_seconds,
        }


def is_audio_file(filename: str | None, mime: str | None = None) -> bool:
    normalized_mime = str(mime or "").split(";", 1)[0].strip().lower()
    if normalized_mime.startswith("audio/"):
        return True
    suffix = Path(str(filename or "")).suffix.lower()
    return suffix in AUDIO_SUFFIXES


def effective_openai_audio_base_url(settings: Settings) -> str:
    base_url = (
        str(getattr(settings, "audio_transcription_base_url", "") or "").strip()
        or str(getattr(settings, "openai_base_url", "") or "").strip()
        or DEFAULT_OPENAI_BASE_URL
    )
    return base_url.rstrip("/")


def effective_audio_auth_order(settings: Settings, *, allow_oauth: bool | None = None) -> list[str]:
    raw = str(getattr(settings, "audio_transcription_auth_order", "") or "api_key")
    order = []
    for part in raw.split(","):
        item = part.strip().lower().replace("-", "_")
        if item in {"api_key", "chatgpt_oauth"} and item not in order:
            order.append(item)
    if not order:
        order = ["api_key"]
    if allow_oauth is False:
        order = [item for item in order if item != "chatgpt_oauth"]
    return order or ["api_key"]


def _openai_api_key(settings: Settings) -> str:
    return str(getattr(settings, "openai_api_key", "") or "").strip()


async def resolve_audio_auth_token(
    settings: Settings | None = None,
    *,
    allow_oauth: bool | None = None,
) -> AudioAuthToken:
    settings = settings or get_settings()
    for item in effective_audio_auth_order(settings, allow_oauth=allow_oauth):
        if item == "api_key":
            key = _openai_api_key(settings)
            if key:
                return AudioAuthToken(token=key, source="ATRIUM_OPENAI_API_KEY", kind="api_key")
            continue
        if item == "chatgpt_oauth":
            try:
                from .provider.chatgpt_oauth import ChatGPTCodexOAuthTokenProvider

                token = await ChatGPTCodexOAuthTokenProvider(settings).access_token()
            except Exception:
                token = ""
            if token:
                return AudioAuthToken(token=token, source="chatgpt_account_oauth", kind="chatgpt_oauth")
    raise AudioTranscriptionNotConfigured(
        "OpenAI audio transcription is not configured; set ATRIUM_OPENAI_API_KEY "
        "or opt in to chatgpt_oauth for a route that supports it."
    )


def audio_transcription_status(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    oauth_ready = False
    try:
        from .provider.chatgpt_oauth import chatgpt_oauth_status

        oauth_ready = bool(chatgpt_oauth_status(settings).get("ready"))
    except Exception:
        oauth_ready = False
    auth_order = effective_audio_auth_order(settings)
    api_key_ready = bool(_openai_api_key(settings))
    return {
        "enabled": bool(getattr(settings, "audio_transcription_enabled", True)),
        "configured": api_key_ready or ("chatgpt_oauth" in auth_order and oauth_ready),
        "provider": str(getattr(settings, "audio_transcription_provider", "openai") or "openai"),
        "baseUrl": effective_openai_audio_base_url(settings),
        "defaultModel": str(getattr(settings, "audio_transcription_model", "") or DEFAULT_OPENAI_AUDIO_MODEL),
        "timeoutSeconds": float(getattr(settings, "audio_transcription_timeout_s", 120.0) or 120.0),
        "maxBytes": int(getattr(settings, "audio_transcription_max_bytes", 20 * 1024 * 1024) or 0),
        "autoOnUpload": bool(getattr(settings, "audio_transcription_auto_on_upload", True)),
        "auth": {
            "apiKeyConfigured": api_key_ready,
            "chatgptAccountOAuthReady": oauth_ready,
            "authOrder": auth_order,
            "oauthAllowedWhenConfigured": "chatgpt_oauth" in auth_order,
        },
        "route": "/api/audio/transcribe",
        "storesTranscriptPreview": True,
    }


def format_audio_transcript_preview(result: AudioTranscriptionResult) -> str:
    lines = [
        "# Audio transcript",
        f"- File: {result.filename}",
        f"- MIME: {result.mime}",
        f"- Provider: {result.provider}",
        f"- Model: {result.model}",
        "",
        "## Transcript",
        result.text.strip(),
    ]
    return "\n".join(lines).strip()


def _clip_text(text: str, limit: int = TOOL_RESULT_TEXT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}
    return False


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


async def _resolve_audio_tool_source(
    repo: Any,
    args: dict[str, Any],
    *,
    max_bytes: int,
) -> tuple[bytes, str, str, dict[str, Any] | None, str]:
    artifact_id = str(args.get("artifactId") or args.get("artifact_id") or "").strip()
    if artifact_id:
        artifact = await repo.get_entity("artifact", artifact_id)
        if not artifact:
            raise AudioTranscriptionSourceError(f"artifact not found: {artifact_id}")
        filename = safe_filename(args.get("filename") or artifact.get("name") or f"{artifact_id}.audio")
        mime = guess_mime(filename, artifact.get("contentMime") or artifact.get("mime"))
        data = bytes_from_uri(artifact.get("uri"), max_bytes=max_bytes if max_bytes > 0 else None)
        if data is None:
            raise AudioTranscriptionSourceError(
                "artifact bytes are unavailable or exceed the configured transcription limit"
            )
        return data, filename, mime, artifact, f"artifact:{artifact_id}"

    raw_uri = str(args.get("uri") or "").strip()
    if raw_uri:
        filename = safe_filename(args.get("filename") or args.get("name") or "audio-upload")
        mime = guess_mime(filename, args.get("mime") or args.get("contentType"))
        data = bytes_from_uri(raw_uri, max_bytes=max_bytes if max_bytes > 0 else None)
        if data is None:
            raise AudioTranscriptionSourceError("uri bytes are unavailable or exceed the configured transcription limit")
        return data, filename, mime, None, raw_uri

    raw_path = str(args.get("sourcePath") or args.get("source_path") or args.get("path") or "").strip()
    if not raw_path:
        raise AudioTranscriptionSourceError("audio.transcribe requires artifactId, uri, or sourcePath")
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise AudioTranscriptionSourceError(f"audio source file not found: {path}")
    if max_bytes > 0 and path.stat().st_size > max_bytes:
        raise AudioTranscriptionSourceError(f"audio file exceeds transcription limit of {max_bytes} bytes")
    filename = safe_filename(args.get("filename") or args.get("name") or path.name)
    mime = guess_mime(filename, args.get("mime") or args.get("contentType"))
    return path.read_bytes(), filename, mime, None, str(path)


def _transcript_artifact_name(source_filename: str, requested: Any = None) -> str:
    raw = safe_filename(str(requested or "").strip() or f"{Path(source_filename).stem or source_filename} transcript.md")
    if Path(raw).suffix.lower() not in {".md", ".txt"}:
        raw = f"{raw}.md"
    return raw


def _write_transcript_text(
    text: str,
    *,
    owner_dept: str,
    artifact_id: str,
    settings: Settings,
) -> tuple[str, str, str, int]:
    mime = "text/markdown; charset=utf-8"
    if bool(getattr(settings, "object_store_enabled", True)):
        from .storage.object_store import get_object_store

        stored = get_object_store(settings).put_text(text, mime=mime)
        return stored.uri, "object_store", stored.content_hash, stored.size_bytes
    path = (settings.workspace_dir / owner_dept / "artifacts" / artifact_id / "v1.md").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    encoded = text.encode("utf-8")
    return str(path), "filesystem", hashlib.sha256(encoded).hexdigest(), len(encoded)


async def execute_audio_transcription_tool(repo: Any, run: dict[str, Any]) -> dict[str, Any]:
    """Transcribe an ATRIUM artifact/URI/local audio path and persist a transcript artifact."""
    args = run.get("args") or {}
    settings = get_settings()
    max_bytes = int(getattr(settings, "audio_transcription_max_bytes", 20 * 1024 * 1024) or 0)
    data, filename, mime, source_artifact, source_ref = await _resolve_audio_tool_source(
        repo,
        args,
        max_bytes=max_bytes,
    )
    if not is_audio_file(filename, mime):
        raise AudioTranscriptionSourceError("audio.transcribe source is not an audio file")

    allow_oauth_raw = args.get("allowOAuth") if "allowOAuth" in args else args.get("allow_oauth")
    allow_oauth = None if allow_oauth_raw is None else _truthy(allow_oauth_raw)
    result = await transcribe_audio_bytes(
        data,
        filename=filename,
        mime=mime,
        settings=settings,
        model=args.get("model"),
        language=args.get("language"),
        prompt=args.get("prompt"),
        allow_oauth=allow_oauth,
    )
    owner_dept = str(
        args.get("ownerDept")
        or args.get("owner_dept")
        or args.get("targetDept")
        or args.get("target_dept")
        or (source_artifact or {}).get("ownerDept")
        or run.get("departmentId")
        or "executive"
    )
    project_id = (
        args.get("projectId")
        or args.get("project_id")
        or (source_artifact or {}).get("projectId")
    )
    requested_by = str(run.get("requestedBy") or args.get("requestedBy") or owner_dept)
    transcript_md = format_audio_transcript_preview(result)
    artifact_id = uid("art")
    artifact_name = _transcript_artifact_name(
        filename,
        args.get("transcriptArtifactName") or args.get("transcript_artifact_name") or args.get("artifactName"),
    )
    uri, storage, content_hash, content_size = _write_transcript_text(
        transcript_md,
        owner_dept=owner_dept,
        artifact_id=artifact_id,
        settings=settings,
    )
    now = now_ms()
    tags = _string_list(args.get("tags"))
    for tag in ("audio", "transcript", "transcribed"):
        if tag not in tags:
            tags.append(tag)
    source_artifact_id = str((source_artifact or {}).get("id") or "")
    if source_artifact_id and source_artifact_id not in tags:
        tags.append(source_artifact_id)
    links = _string_list(args.get("links"))
    if source_ref and source_ref not in links:
        links.append(source_ref)
    preview_record = {"kind": "md", "uri": uri}
    artifact = Artifact(
        id=artifact_id,
        name=artifact_name,
        kind="report",
        mime="text/markdown; charset=utf-8",
        owner_dept=owner_dept,
        task_ids=[],
        project_id=str(project_id) if project_id else None,
        version=1,
        status="approved",
        uri=uri,
        storage=storage,  # type: ignore[arg-type]
        content_hash=content_hash,
        content_size_bytes=content_size,
        content_mime="text/markdown; charset=utf-8",
        tags=tags,
        links=links,
        preview=preview_record,
        created_at=now,
        created_by=requested_by,
        updated_at=now,
        updated_by=requested_by,
    ).dump()
    artifact["audioSource"] = {
        "artifactId": source_artifact_id or None,
        "source": source_ref,
        "filename": filename,
        "mime": mime,
    }
    metadata_transcription = result.public_dict()
    metadata_transcription.pop("text", None)
    artifact["transcription"] = metadata_transcription
    version = ArtifactVersion(
        artifact_id=artifact_id,
        version=1,
        author=requested_by,
        ts=now,
        note=f"audio transcript for {filename}",
        parent=None,
        uri=uri,
        storage=storage,  # type: ignore[arg-type]
        content_hash=content_hash,
        content_size_bytes=content_size,
        content_mime="text/markdown; charset=utf-8",
        preview=preview_record,
    ).dump()
    version["transcription"] = metadata_transcription
    await repo.put_entity("artifact", artifact, dept=owner_dept, project=str(project_id) if project_id else None, status="approved", ts=now)
    await repo.put_entity(
        "artifact_version",
        {**version, "id": f"{artifact_id}:1"},
        dept=owner_dept,
        project=str(project_id) if project_id else None,
        status="approved",
        ts=now,
    )
    await repo.add_knowledge(
        owner_dept,
        {
            "id": uid("kn"),
            "title": f"Transcript: {filename}",
            "ts": now,
            "score": 0.82,
            "text": transcript_md[:10_000],
            "tags": [*tags, artifact_id],
            "source": artifact_id,
        },
        source=artifact_id,
    )

    if source_artifact:
        updated_source = {**source_artifact}
        source_tags = list(updated_source.get("tags") or [])
        for tag in ("audio", "transcribed"):
            if tag not in source_tags:
                source_tags.append(tag)
        updated_source["tags"] = source_tags
        updated_source["audioTranscription"] = result.public_dict()
        updated_source["transcriptArtifactId"] = artifact_id
        updated_source["preview"] = preview_record
        updated_source["extraction"] = {"status": "audio_transcription", "warnings": []}
        updated_source["updatedAt"] = now
        updated_source["updatedBy"] = requested_by
        await repo.put_entity(
            "artifact",
            updated_source,
            dept=updated_source.get("ownerDept") or owner_dept,
            project=updated_source.get("projectId") or project_id,
            status=updated_source.get("status") or "approved",
            ts=now,
        )

    await repo.add_activity(
        {
            "id": uid("ev"),
            "ts": now,
            "type": "system",
            "departmentId": owner_dept,
            "text": f"audio.transcribe: {filename} -> {artifact_id}",
            "severity": "good",
        }
    )
    public_transcription = result.public_dict()
    full_text = str(public_transcription.pop("text", result.text) or result.text)
    text_preview = _clip_text(full_text)
    return {
        "ok": True,
        "status": "succeeded",
        "tool": "audio.transcribe",
        "summary": f"transcribed {filename} to artifact {artifact_id}",
        "transcription": public_transcription,
        "text": text_preview,
        "textTruncated": len(full_text) > TOOL_RESULT_TEXT_LIMIT,
        "artifact": artifact,
        "transcriptArtifact": artifact,
        "sourceArtifactId": source_artifact_id or None,
        "source": source_ref,
        "preview": {
            "kind": "md",
            "uri": uri,
            "text": text_preview,
            "download": f"/api/artifacts/{artifact_id}/download",
        },
    }


async def transcribe_audio_bytes(
    data: bytes,
    *,
    filename: str,
    mime: str | None = None,
    settings: Settings | None = None,
    model: str | None = None,
    language: str | None = None,
    prompt: str | None = None,
    allow_oauth: bool | None = None,
) -> AudioTranscriptionResult:
    settings = settings or get_settings()
    if not bool(getattr(settings, "audio_transcription_enabled", True)):
        raise AudioTranscriptionNotConfigured("OpenAI audio transcription is disabled")
    if not is_audio_file(filename, mime):
        raise AudioTranscriptionError("uploaded file is not an audio file")
    max_bytes = int(getattr(settings, "audio_transcription_max_bytes", 20 * 1024 * 1024) or 0)
    if max_bytes > 0 and len(data) > max_bytes:
        raise AudioTranscriptionError(f"audio file exceeds transcription limit of {max_bytes} bytes")

    auth = await resolve_audio_auth_token(settings, allow_oauth=allow_oauth)
    provider = str(getattr(settings, "audio_transcription_provider", "openai") or "openai")
    if provider != "openai":
        raise AudioTranscriptionError(f"unsupported audio transcription provider: {provider}")
    selected_model = str(model or getattr(settings, "audio_transcription_model", "") or DEFAULT_OPENAI_AUDIO_MODEL).strip()
    selected_model = selected_model or DEFAULT_OPENAI_AUDIO_MODEL
    selected_mime = str(mime or "application/octet-stream").split(";", 1)[0].strip() or "application/octet-stream"
    base_url = effective_openai_audio_base_url(settings)
    timeout = float(getattr(settings, "audio_transcription_timeout_s", 120.0) or 120.0)

    fields: dict[str, str] = {"model": selected_model}
    language_value = str(language or getattr(settings, "audio_transcription_language", "") or "").strip()
    prompt_value = str(prompt or getattr(settings, "audio_transcription_prompt", "") or "").strip()
    if language_value:
        fields["language"] = language_value
    if prompt_value:
        fields["prompt"] = prompt_value

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {auth.token}"},
                data=fields,
                files={"file": (filename, data, selected_mime)},
            )
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:800]
        raise AudioTranscriptionError(
            f"OpenAI audio transcription failed with HTTP {exc.response.status_code}: {detail}"
        ) from exc
    except httpx.RequestError as exc:
        raise AudioTranscriptionError(
            f"OpenAI audio transcription request failed: {type(exc).__name__}: {exc}"
        ) from exc
    except ValueError as exc:
        raise AudioTranscriptionError("OpenAI audio transcription returned invalid JSON") from exc

    text = str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""
    if not text:
        raise AudioTranscriptionError("OpenAI audio transcription response missing text")
    duration = payload.get("duration") if isinstance(payload, dict) else None
    try:
        duration_seconds = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_seconds = None
    return AudioTranscriptionResult(
        text=text,
        model=selected_model,
        provider=provider,
        source=auth.source,
        mime=selected_mime,
        filename=filename,
        duration_seconds=duration_seconds,
        raw=payload if isinstance(payload, dict) else None,
    )
