"""Audio transcription provider subsystem.

This intentionally stays separate from chat/Responses providers. Batch STT uses
OpenAI-compatible `/audio/transcriptions` with an API-key-shaped bearer token;
OAuth can be opted in only for routes that are known to accept it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import Settings, get_settings

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


class AudioTranscriptionError(RuntimeError):
    pass


class AudioTranscriptionNotConfigured(AudioTranscriptionError):
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
