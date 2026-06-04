"""OpenAI image generation/editing integration for ATRIUM artifacts."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from .atrium_domain import agent_message_metadata
from .clock import now_ms
from .config import Settings, get_settings
from .db.repo import Repo
from .events import hub
from .file_intake import bytes_from_uri, guess_mime, message_attachment_from_artifact, safe_filename
from .ids import uid
from .provider.chatgpt_oauth import (
    ChatGPTCodexOAuthTokenProvider,
    chatgpt_oauth_status,
)
from .schema import Artifact, ArtifactVersion
from .storage.object_store import get_object_store
from .threads import is_exec


IMAGE_MIME_BY_FORMAT = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "webp": "image/webp",
}
IMAGE_EXT_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
IMAGE_BASE64_KEYS = ("b64_json", "base64", "b64", "image_base64", "imageBase64")
IMAGE_URL_KEYS = ("url", "image_url", "imageUrl", "download_url", "downloadUrl", "file_url", "fileUrl")
IMAGE_FILE_ID_KEYS = ("file_id", "fileId", "openai_file_id", "openaiFileId")
IMAGE_NESTED_KEYS = ("image", "artifact", "file")
IMAGE_RESULT_KEYS = ("result", "data")
VALID_QUALITIES = {"low", "medium", "high", "auto"}
VALID_OUTPUT_FORMATS = {"png", "jpeg", "webp"}
VALID_BACKGROUNDS = {"auto", "opaque", "transparent"}
VALID_MODERATIONS = {"auto", "low"}
GPT_IMAGE_MODELS = {
    "gpt-image-2",
    "gpt-image-2-2026-04-21",
    "gpt-image-1.5",
    "gpt-image-1",
    "gpt-image-1-mini",
}
PRIMARY_IMAGE_MODEL = "gpt-image-2"
FALLBACK_IMAGE_MODEL = "gpt-image-1.5"
STANDARD_GPT_IMAGE_SIZES = {"auto", "1024x1024", "1536x1024", "1024x1536"}
GPT_IMAGE_2_MIN_PIXELS = 655_360
GPT_IMAGE_2_MAX_PIXELS = 8_294_400
GPT_IMAGE_2_MAX_EDGE = 3840
IMAGE_RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524}
IMAGE_GENERATION_MAX_ATTEMPTS = 3
IMAGE_GENERATION_RETRY_DELAYS_MS = (60_000, 180_000, 300_000)
IMAGE_GENERATION_TIMEOUT_DEFAULT_S = 600.0
CHATGPT_OAUTH_IMAGE_RESPONSES_MODEL = "gpt-5.4-mini"


class ImageGenerationError(RuntimeError):
    """Raised for upstream image API or artifact preparation failures."""


class RetryableImageGenerationError(ImageGenerationError):
    """Raised when a transient image provider failure should be retried by the worker."""

    def __init__(self, message: str, *, delay_ms: int = 60_000) -> None:
        super().__init__(message)
        self.delay_ms = max(1_000, int(delay_ms))


class ChatGPTOAuthImageGenerationExhausted(RetryableImageGenerationError):
    """Raised after the ChatGPT OAuth image route exhausts its local attempts."""


@dataclass(frozen=True)
class ImageReference:
    artifact_id: str
    name: str
    mime: str
    data: bytes
    uri: str


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    mime: str
    revised_prompt: str | None = None


@dataclass(frozen=True)
class _ImagePayloadCandidate:
    value: Any
    kind: str
    revised_prompt: str | None = None


@dataclass(frozen=True)
class ImageApiResult:
    images: list[GeneratedImage]
    model: str
    usage: dict[str, Any] | None
    request_id: str | None
    raw_created: int | None = None
    provider: str = "openai"


def _url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}{path}"
    return f"{base}/v1{path}"


def _clip(value: Any, limit: int = 8000) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit].rstrip() + "\n...[truncated]"


def _coerce_quality(value: Any) -> str:
    quality = str(value or "auto").strip().lower()
    aliases = {
        "draft": "low",
        "fast": "low",
        "quick": "low",
        "standard": "medium",
        "normal": "medium",
        "final": "high",
        "sharp": "high",
        "crisp": "high",
        "max": "high",
        "สูง": "high",
        "ชัด": "high",
        "กลาง": "medium",
        "เร็ว": "low",
    }
    quality = aliases.get(quality, quality)
    if quality not in VALID_QUALITIES:
        raise ImageGenerationError("quality must be one of low, medium, high, or auto")
    return quality


def _coerce_output_format(value: Any) -> str:
    output_format = str(value or "png").strip().lower()
    if output_format == "jpg":
        output_format = "jpeg"
    if output_format not in VALID_OUTPUT_FORMATS:
        raise ImageGenerationError("outputFormat must be one of png, jpeg, or webp")
    return output_format


def _coerce_background(value: Any) -> str:
    background = str(value or "auto").strip().lower()
    if background not in VALID_BACKGROUNDS:
        raise ImageGenerationError("background must be one of auto, opaque, or transparent")
    return background


def _coerce_moderation(value: Any) -> str:
    moderation = str(value or "auto").strip().lower()
    if moderation not in VALID_MODERATIONS:
        raise ImageGenerationError("moderation must be one of auto or low")
    return moderation


def _raw_value(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value is not None and value != "":
            return value
    return None


def _is_gpt_image_2(model: str) -> bool:
    return model.startswith("gpt-image-2")


def _is_primary_family(model: str, primary: str) -> bool:
    if model == primary:
        return True
    return model.startswith("gpt-image-2") and primary.startswith("gpt-image-2")


def _parse_int(value: Any, *, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ImageGenerationError(f"{field} must be an integer") from exc


def _coerce_n(value: Any) -> int:
    n = _parse_int(value, field="n") or 1
    if n < 1 or n > 10:
        raise ImageGenerationError("n must be between 1 and 10")
    return n


def _coerce_output_compression(value: Any, *, output_format: str) -> int | None:
    compression = _parse_int(value, field="outputCompression")
    if compression is None:
        return None
    if compression < 0 or compression > 100:
        raise ImageGenerationError("outputCompression must be between 0 and 100")
    if output_format not in {"jpeg", "webp"}:
        raise ImageGenerationError("outputCompression is supported only when outputFormat is jpeg or webp")
    return compression


def _parse_aspect_ratio(value: Any) -> tuple[int, int] | None:
    if value is None or value == "":
        return None
    text = str(value).strip().lower().replace(" ", "")
    aliases = {
        "square": "1:1",
        "จตุรัส": "1:1",
        "portrait": "2:3",
        "vertical": "9:16",
        "story": "9:16",
        "reel": "9:16",
        "landscape": "16:9",
        "horizontal": "16:9",
        "wide": "16:9",
    }
    text = aliases.get(text, text)
    match = re.match(r"^(\d+(?:\.\d+)?)[x:/](\d+(?:\.\d+)?)$", text)
    if not match:
        raise ImageGenerationError("aspectRatio must look like 1:1, 16:9, 9:16, portrait, landscape, or square")
    left = float(match.group(1))
    right = float(match.group(2))
    if left <= 0 or right <= 0:
        raise ImageGenerationError("aspectRatio values must be positive")
    # Keep the numbers compact but preserve orientation and approximate ratio.
    scale = 1000
    return max(1, round(left * scale)), max(1, round(right * scale))


def _round_to_multiple(value: float, multiple: int = 16) -> int:
    return max(multiple, int(round(value / multiple)) * multiple)


def _size_from_aspect(model: str, aspect_ratio: Any, resolution: Any) -> str:
    ratio = _parse_aspect_ratio(aspect_ratio)
    if ratio is None:
        return "auto"
    w_ratio, h_ratio = ratio
    landscape = w_ratio > h_ratio
    portrait = h_ratio > w_ratio
    if not _is_gpt_image_2(model):
        if landscape:
            return "1536x1024"
        if portrait:
            return "1024x1536"
        return "1024x1024"

    preset = str(resolution or "").strip().lower()
    if "4k" in preset or "uhd" in preset:
        long_edge = 3840
    elif "2k" in preset or "qhd" in preset:
        long_edge = 2048
    elif "hd" in preset:
        long_edge = 1536
    else:
        long_edge = 1536 if landscape else 1024 if portrait else 1024
    if landscape:
        width = long_edge
        height = _round_to_multiple(width * h_ratio / w_ratio)
    elif portrait:
        height = long_edge
        width = _round_to_multiple(height * w_ratio / h_ratio)
    else:
        width = height = min(long_edge, 2048)
    size = f"{width}x{height}"
    _validate_size_for_model(size, model)
    return size


def _validate_size_for_model(size: str, model: str) -> None:
    if size == "auto":
        return
    match = re.match(r"^(\d+)x(\d+)$", size)
    if not match:
        raise ImageGenerationError("size must be auto or WIDTHxHEIGHT, for example 1536x864")
    width = int(match.group(1))
    height = int(match.group(2))
    if _is_gpt_image_2(model):
        if width % 16 or height % 16:
            raise ImageGenerationError("gpt-image-2 size width and height must both be divisible by 16")
        if width > GPT_IMAGE_2_MAX_EDGE or height > GPT_IMAGE_2_MAX_EDGE:
            raise ImageGenerationError("gpt-image-2 size edge length must be <= 3840px")
        ratio = max(width, height) / max(1, min(width, height))
        if ratio > 3:
            raise ImageGenerationError("gpt-image-2 size aspect ratio must be between 1:3 and 3:1")
        pixels = width * height
        if pixels < GPT_IMAGE_2_MIN_PIXELS or pixels > GPT_IMAGE_2_MAX_PIXELS:
            raise ImageGenerationError("gpt-image-2 size total pixels must be between 655360 and 8294400")
        return
    if size not in STANDARD_GPT_IMAGE_SIZES:
        raise ImageGenerationError(
            f"{model} supports size auto, 1024x1024, 1536x1024, or 1024x1536. "
            "Use gpt-image-2 for arbitrary WIDTHxHEIGHT sizes."
        )


def _coerce_size(raw: dict[str, Any], model: str) -> str:
    width = _parse_int(_raw_value(raw, "width", "outputWidth", "output_width"), field="width")
    height = _parse_int(_raw_value(raw, "height", "outputHeight", "output_height"), field="height")
    if width is not None or height is not None:
        if width is None or height is None:
            raise ImageGenerationError("width and height must be provided together")
        size = f"{width}x{height}"
    else:
        explicit = _raw_value(raw, "size", "dimensions", "resolutionSize", "resolution_size")
        if explicit is not None:
            size = str(explicit).strip().lower().replace(" ", "")
        else:
            aspect = _raw_value(raw, "aspectRatio", "aspect_ratio", "ratio")
            resolution = _raw_value(raw, "resolution", "resolutionPreset", "resolution_preset")
            size = _size_from_aspect(model, aspect, resolution) if aspect is not None else "auto"
    _validate_size_for_model(size, model)
    return size


def _image_request_options(raw: dict[str, Any], model: str) -> dict[str, Any]:
    output_format = _coerce_output_format(_raw_value(raw, "outputFormat", "output_format", "format", "fileFormat", "file_format"))
    quality = _coerce_quality(_raw_value(raw, "quality", "clarity", "detailLevel", "detail_level"))
    background = _coerce_background(_raw_value(raw, "background", "backgroundMode", "background_mode"))
    if _is_gpt_image_2(model) and background == "transparent":
        raise ImageGenerationError("gpt-image-2 does not currently support background=transparent; use auto or opaque")
    if background == "transparent" and output_format not in {"png", "webp"}:
        raise ImageGenerationError("transparent background requires outputFormat png or webp")
    options = {
        "n": _coerce_n(_raw_value(raw, "n", "count", "imageCount", "image_count")),
        "size": _coerce_size(raw, model),
        "quality": quality,
        "outputFormat": output_format,
        "outputCompression": _coerce_output_compression(
            _raw_value(raw, "outputCompression", "output_compression", "compression"),
            output_format=output_format,
        ),
        "background": background,
        "moderation": _coerce_moderation(_raw_value(raw, "moderation")),
    }
    return options


def _image_mime(output_format: str) -> str:
    return IMAGE_MIME_BY_FORMAT.get(output_format, "image/png")


def _mime_from_image_bytes(data: bytes, fallback_mime: str) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return fallback_mime if fallback_mime.startswith("image/") else "image/png"


def _content_type_image_mime(content_type: str | None, fallback_mime: str) -> str:
    mime = str(content_type or "").split(";", 1)[0].strip().lower()
    if mime.startswith("image/"):
        return mime
    return fallback_mime if fallback_mime.startswith("image/") else "image/png"


def _decode_base64_bytes(value: Any, *, allow_short: bool = False) -> bytes | None:
    text = re.sub(r"\s+", "", str(value or ""))
    if not text:
        return None
    if not allow_short and len(text) < 32:
        return None
    if not re.fullmatch(r"[A-Za-z0-9+/=_-]+", text):
        return None
    padded = text + ("=" * (-len(text) % 4))
    try:
        return base64.b64decode(padded, validate=True)
    except Exception:
        with contextlib.suppress(Exception):
            return base64.urlsafe_b64decode(padded)
    return None


def _decode_data_url(value: Any, fallback_mime: str) -> GeneratedImage | None:
    text = str(value or "").strip()
    match = re.match(r"^data:([^;,]+)?(?:;[^,]*)?;base64,(.+)$", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    declared_mime = str(match.group(1) or "").strip().lower()
    raw = _decode_base64_bytes(match.group(2), allow_short=True)
    if raw is None:
        raise ImageGenerationError("image API returned an invalid base64 data URL")
    mime = declared_mime if declared_mime.startswith("image/") else fallback_mime
    return GeneratedImage(data=raw, mime=_mime_from_image_bytes(raw, mime))


def _is_http_url(value: str) -> bool:
    return urlparse(value).scheme in {"http", "https"}


def _image_timeout(settings: Settings) -> float | None:
    value = settings.image_generation_timeout_s
    if value is None or value <= 0:
        return IMAGE_GENERATION_TIMEOUT_DEFAULT_S
    return min(float(value), IMAGE_GENERATION_TIMEOUT_DEFAULT_S)


def image_generation_auth_status(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    chatgpt_status = chatgpt_oauth_status(settings)
    has_chatgpt_oauth = bool(chatgpt_status.get("ready"))
    has_api_key_fallback = bool(settings.image_generation_api_key)
    return {
        "configured": has_chatgpt_oauth,
        "primaryProvider": "chatgpt_account",
        "primaryConfigured": has_chatgpt_oauth,
        "primaryBaseUrl": settings.chatgpt_account_base_url,
        "fallbackProvider": "openai",
        "fallbackConfigured": has_api_key_fallback,
        "fallbackBaseUrl": settings.image_generation_base_url,
        "timeoutS": _image_timeout(settings),
        "chatgptAccountOAuth": chatgpt_status,
        "usesDedicatedImageKey": has_api_key_fallback,
    }


def _sse_json_events(body: str):
    for line in str(body or "").splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                yield parsed


def _walk_event_values(value: Any, path: str = "$"):
    if isinstance(value, dict):
        for key, nested in value.items():
            nested_path = f"{path}.{key}"
            yield nested_path, key, nested
            yield from _walk_event_values(nested, nested_path)
        return
    if isinstance(value, list | tuple):
        for idx, nested in enumerate(value):
            yield from _walk_event_values(nested, f"{path}[{idx}]")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "approved", "อนุมัติ"}


def _image_model_policy(raw: dict[str, Any], settings: Settings) -> tuple[str, dict[str, Any]]:
    primary = PRIMARY_IMAGE_MODEL
    fallback = FALLBACK_IMAGE_MODEL
    requested = str(raw.get("model") or primary).strip() or primary
    supported = sorted({*GPT_IMAGE_MODELS, primary, fallback})
    approved = _truthy(
        raw.get("modelOverrideApproved")
        or raw.get("model_override_approved")
        or raw.get("userApprovedModelOverride")
        or raw.get("user_approved_model_override")
    )
    fallback_from = str(raw.get("fallbackFromModel") or raw.get("fallback_from_model") or "").strip()
    primary_error = str(raw.get("primaryModelError") or raw.get("primary_model_error") or "").strip()
    reason = str(raw.get("modelOverrideReason") or raw.get("model_override_reason") or "").strip()
    policy = {
        "primaryModel": primary,
        "fallbackModel": fallback,
        "requestedModel": requested,
        "selectedModel": requested,
        "modelOverrideApproved": approved,
        "fallbackFromModel": fallback_from or None,
        "primaryModelError": _clip(primary_error, 1200) if primary_error else None,
        "modelOverrideReason": _clip(reason, 1200) if reason else None,
        "supportedModels": supported,
    }
    if _is_primary_family(requested, primary):
        policy["selection"] = "primary"
        return requested, policy
    if requested in supported:
        if approved:
            policy["selection"] = "user_approved_non_primary"
            return requested, policy
        if fallback_from == primary and primary_error:
            policy["selection"] = "primary_failed_non_primary"
            return requested, policy
        raise ImageGenerationError(
            f"{requested} requires explicit user approval or a prior {primary} failure. "
            f"Use {primary} first; if it fails, retry with fallbackFromModel={primary} and primaryModelError."
        )
    raise ImageGenerationError(f"unsupported image model: {requested}; supported models are {', '.join(supported)}")


def _filename_timestamp(ts_ms: int) -> str:
    try:
        tz = ZoneInfo("Asia/Bangkok")
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    return datetime.fromtimestamp(ts_ms / 1000, tz).strftime("%y%m%d_%H%M")


def _display_timestamp(ts_ms: int | None) -> str:
    if not ts_ms:
        return "-"
    try:
        tz = ZoneInfo("Asia/Bangkok")
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    return datetime.fromtimestamp(int(ts_ms) / 1000, tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def _duration_label(elapsed_ms: int | None) -> str:
    if elapsed_ms is None:
        return "-"
    seconds = max(0.0, float(elapsed_ms) / 1000.0)
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, sec = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _image_retry_delay_ms(attempt: int) -> int:
    index = min(max(0, int(attempt) - 1), len(IMAGE_GENERATION_RETRY_DELAYS_MS) - 1)
    return IMAGE_GENERATION_RETRY_DELAYS_MS[index]


def _strip_extension(value: str) -> str:
    suffix = Path(value).suffix
    if suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        return value[: -len(suffix)]
    return value


def _filename_segment(value: str | None, *, fallback: str, limit: int) -> str:
    text = _strip_extension(str(value or "").strip())
    text = re.sub(r"[\x00-\x1f/\\:]+", "_", text)
    text = re.sub(r"[\"'`“”‘’<>|?*]+", "", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._- ")
    if not text:
        text = fallback
    return text[:limit].strip("._- ") or fallback


def _subject_from_prompt(prompt: str) -> str:
    text = re.sub(r"\s+", " ", str(prompt or "")).strip()
    text = re.split(r"[\n\r,.;:!?，。]", text, maxsplit=1)[0].strip()
    text = re.sub(
        r"^(ช่วย)?\s*(สร้าง|วาด|ทำ|ออกแบบ|generate|create|make|draw|design)\s*"
        r"(ภาพ|รูป|รูปภาพ|image|picture|photo|illustration)?\s*(ของ|ให้|เป็น|of)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    if re.search(r"[ก-๙]", text) and not text.startswith(("ภาพ", "รูป")):
        text = "ภาพ" + text
    return text or "generated-image"


def _safe_image_name(
    name: str | None,
    artifact_id: str,
    *,
    prompt: str,
    requester: str,
    ts_ms: int,
    index: int,
    total: int,
    mime: str,
) -> str:
    suffix = IMAGE_EXT_BY_MIME.get(mime, ".png")
    subject_source = name or _subject_from_prompt(prompt)
    subject = _filename_segment(subject_source, fallback=f"generated-image-{artifact_id}", limit=72)
    requester_segment = _filename_segment(requester, fallback="user", limit=36)
    stamp = _filename_timestamp(ts_ms)
    stem = f"{subject}_{requester_segment}_{stamp}"
    if total <= 1:
        return safe_filename(f"{stem}{suffix}")
    return safe_filename(f"{stem}-{index + 1}{suffix}")


def artifact_location(artifact: dict[str, Any]) -> dict[str, Any]:
    artifact_id = str(artifact.get("id") or "")
    uri = str(artifact.get("uri") or "")
    local_path = None
    if uri.startswith("atrium-object://"):
        with contextlib.suppress(Exception):
            path = get_object_store().resolve_uri(uri)
            if path:
                local_path = str(path)
    elif uri and not uri.startswith("atrium://"):
        local_path = uri.removeprefix("file://")
    return {
        "artifactId": artifact_id,
        "name": str(artifact.get("name") or artifact_id),
        "mime": artifact.get("contentMime") or artifact.get("mime"),
        "uri": uri,
        "storage": artifact.get("storage"),
        "contentHash": artifact.get("contentHash"),
        "contentSizeBytes": artifact.get("contentSizeBytes"),
        "downloadUrl": f"/api/artifacts/{quote(artifact_id, safe='')}/download",
        "previewUrl": f"/api/artifacts/{quote(artifact_id, safe='')}/preview",
        "localPath": local_path,
    }


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list | tuple | set):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


async def _prepared_image_request(
    repo: Repo,
    raw: dict[str, Any],
    *,
    fallback_owner_dept: str,
    requested_by: str,
) -> dict[str, Any]:
    settings = get_settings()
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        raise ImageGenerationError("prompt is required")
    owner_dept = str(raw.get("ownerDept") or raw.get("owner_dept") or fallback_owner_dept).strip()
    dept = await repo.get_department(owner_dept)
    if not dept:
        raise ImageGenerationError(f"owner department not found: {owner_dept}")
    project_id = raw.get("projectId") or raw.get("project_id")
    if project_id and not await repo.get_entity("project", str(project_id)):
        raise ImageGenerationError(f"project not found: {project_id}")
    task_ids = _str_list(raw.get("taskIds") or raw.get("task_ids"))
    for task_id in task_ids:
        if not await repo.get_task(task_id):
            raise ImageGenerationError(f"task not found: {task_id}")
    reference_artifact_ids = _str_list(
        raw.get("referenceArtifactIds") or raw.get("reference_artifact_ids") or raw.get("references")
    )
    mask_artifact_id = str(raw.get("maskArtifactId") or raw.get("mask_artifact_id") or "").strip() or None
    # Validate references before queueing so bad artifact ids fail immediately,
    # while the slow provider call still happens in the background worker.
    if reference_artifact_ids:
        await resolve_image_references(repo, reference_artifact_ids)
    if mask_artifact_id:
        await resolve_image_reference(repo, mask_artifact_id, purpose="mask")
    selected_model, model_policy = _image_model_policy(raw, settings)
    options = _image_request_options(raw, selected_model)
    mode = "edit" if reference_artifact_ids or mask_artifact_id else "generate"
    wake_on_complete = _image_wake_enabled(raw)
    display_requester = str(
        raw.get("requestedBy")
        or raw.get("requested_by")
        or raw.get("requesterName")
        or raw.get("requester_name")
        or raw.get("createdBy")
        or raw.get("created_by")
        or requested_by
        or owner_dept
    )
    created_by = str(raw.get("createdBy") or raw.get("created_by") or display_requester or owner_dept)
    request = dict(raw or {})
    request.update({
        "prompt": prompt,
        "ownerDept": owner_dept,
        "projectId": str(project_id) if project_id else None,
        "taskIds": task_ids,
        "artifactName": raw.get("artifactName") or raw.get("artifact_name"),
        "referenceArtifactIds": reference_artifact_ids,
        "maskArtifactId": mask_artifact_id,
        "model": selected_model,
        "modelOverrideApproved": bool(model_policy.get("modelOverrideApproved")),
        "modelOverrideReason": model_policy.get("modelOverrideReason"),
        "fallbackFromModel": model_policy.get("fallbackFromModel"),
        "primaryModelError": model_policy.get("primaryModelError"),
        "requestedBy": display_requester,
        "requesterName": raw.get("requesterName") or raw.get("requester_name"),
        "createdBy": created_by,
        "tags": _str_list(raw.get("tags")),
        "n": options["n"],
        "size": options["size"],
        "quality": options["quality"],
        "outputFormat": options["outputFormat"],
        "outputCompression": options["outputCompression"],
        "background": options["background"],
        "moderation": options["moderation"],
        "wakeOnComplete": wake_on_complete,
    })
    request = {key: value for key, value in request.items() if value is not None}
    return {
        "request": request,
        "department": dept,
        "ownerDept": owner_dept,
        "projectId": str(project_id) if project_id else None,
        "taskIds": task_ids,
        "mode": mode,
        "model": selected_model,
        "modelPolicy": {**model_policy, "selectedModel": selected_model},
        "options": options,
        "referenceArtifactIds": reference_artifact_ids,
        "maskArtifactId": mask_artifact_id,
        "requestedBy": display_requester,
        "createdBy": created_by,
        "prompt": prompt,
        "wakeOnComplete": wake_on_complete,
    }


def _image_job_options_label(run: dict[str, Any]) -> str:
    options = run.get("options") if isinstance(run.get("options"), dict) else {}
    parts = [
        f"model={run.get('model') or '-'}",
        f"size={options.get('size') or '-'}",
        f"quality={options.get('quality') or '-'}",
        f"format={options.get('outputFormat') or '-'}",
        f"n={options.get('n') or 1}",
    ]
    return " · ".join(parts)


def _image_job_message_text(
    run: dict[str, Any],
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> str:
    status = str(run.get("status") or "queued")
    status_label = {
        "queued": "อยู่ในคิวสร้างภาพ",
        "running": "กำลังเรียกโมเดลสร้างภาพ",
        "succeeded": "สร้างภาพเสร็จแล้ว",
        "failed": "สร้างภาพไม่สำเร็จ",
    }.get(status, status)
    lines = [
        f"{status_label} ({run.get('jobId')})",
        f"เวลาเข้าคิว: {_display_timestamp(run.get('queuedAt'))}",
    ]
    if run.get("startedAt"):
        lines.append(f"เวลาเริ่มสร้าง: {_display_timestamp(run.get('startedAt'))}")
    if run.get("completedAt"):
        lines.append(f"เวลาเสร็จ: {_display_timestamp(run.get('completedAt'))} · ใช้เวลา {_duration_label(run.get('elapsedMs'))}")
    lines.extend([
        f"ตัวเลือก: {_image_job_options_label(run)}",
        f"โหมด: {run.get('mode') or '-'}",
        f"สถานะ API: /api/images/jobs/{run.get('jobId')}",
    ])
    attempts = int(run.get("attempts") or 0)
    max_attempts = int(run.get("maxAttempts") or IMAGE_GENERATION_MAX_ATTEMPTS)
    if attempts > 0:
        lines.append(f"ครั้งที่ลอง: {attempts}/{max_attempts}")
    if status == "queued" and run.get("retryAfter"):
        lines.append(f"จะลองใหม่: {_display_timestamp(run.get('retryAfter'))}")
    prompt = _clip(run.get("prompt") or "", 420)
    if prompt:
        lines.append(f"พรอมป์: {prompt}")
    if status in {"queued", "running"}:
        lines.append("AI ทำงานอื่นต่อได้ระหว่างรอ และจะเห็นผลลัพธ์จากข้อความนี้เมื่อภาพเสร็จ")
    if result:
        locations = result.get("locations") if isinstance(result.get("locations"), list) else []
        if locations:
            lines.append("ไฟล์ที่ได้:")
            for location in locations[:10]:
                if not isinstance(location, dict):
                    continue
                path = location.get("localPath") or location.get("downloadUrl") or location.get("uri")
                lines.append(f"- {location.get('artifactId')}: {location.get('name')} ({path})")
    if error:
        lines.append(f"ข้อผิดพลาด: {_clip(error, 1200)}")
    return "\n".join(lines)


ACTIVE_IMAGE_WAKE_REPLY_STATUSES = {"queued", "sending", "pending_approval"}


def _image_wake_enabled(raw: dict[str, Any]) -> bool:
    explicit = _raw_value(raw, "wakeOnComplete", "wake_on_complete", "notifyOnComplete", "notify_on_complete")
    return True if explicit is None else _truthy(explicit)


def _image_job_message(
    run: dict[str, Any],
    dept: dict[str, Any],
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    thread_id = str(run.get("threadId") or "")
    message_id = str(run.get("messageId") or uid("msg"))
    ts = int(run.get("completedAt") or run.get("startedAt") or run.get("queuedAt") or now_ms())
    return {
        "id": message_id,
        "threadId": thread_id,
        "role": "executive" if is_exec(str(dept.get("id") or "")) else "agent",
        "authorName": dept.get("agentName") or dept.get("name") or run.get("departmentId") or "ATRIUM",
        "text": _image_job_message_text(run, result=result, error=error),
        "ts": ts,
        "pending": False,
        "status": "sent",
        "contentFormat": "plain",
        "attachments": attachments or [],
        "imageGeneration": {
            "runId": run.get("id"),
            "jobId": run.get("jobId"),
            "status": run.get("status"),
            "queuedAt": run.get("queuedAt"),
            "startedAt": run.get("startedAt"),
            "completedAt": run.get("completedAt"),
            "elapsedMs": run.get("elapsedMs"),
            "model": run.get("model"),
            "mode": run.get("mode"),
            "options": run.get("options") or {},
            "statusUrl": f"/api/images/jobs/{run.get('jobId')}",
        },
        **agent_message_metadata(dept),
    }


async def _upsert_image_job_message(
    repo: Repo,
    run: dict[str, Any],
    dept: dict[str, Any],
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not run.get("threadId") or not run.get("messageId"):
        return None
    msg = _image_job_message(run, dept, result=result, error=error, attachments=attachments)
    existing = await repo.get_message(str(run["messageId"]), thread_id=str(run["threadId"]))
    if existing:
        await repo.update_message({**existing, **msg})
    else:
        await repo.add_message(msg)
    hub.pulse({
        "kind": "image_generation_status",
        "threadId": run.get("threadId"),
        "msgId": run.get("messageId"),
        "departmentId": run.get("departmentId"),
        "jobId": run.get("jobId"),
        "status": run.get("status"),
        "message": msg,
    })
    return msg


def _active_image_wake_reply(
    messages: list[dict[str, Any]],
    *,
    run_id: str,
    image_message_id: str,
) -> dict[str, Any] | None:
    for msg in reversed(messages):
        if msg.get("id") == image_message_id:
            continue
        wake = msg.get("imageGenerationWake") if isinstance(msg.get("imageGenerationWake"), dict) else {}
        if wake.get("runId") == run_id:
            return msg
        if msg.get("role") != "user" and (
            bool(msg.get("pending")) or str(msg.get("status") or "") in ACTIVE_IMAGE_WAKE_REPLY_STATUSES
        ):
            return msg
    return None


async def _record_image_wake_state(repo: Repo, run: dict[str, Any], wake: dict[str, Any]) -> dict[str, Any]:
    updated = {**run, "wake": wake}
    await repo.put_entity(
        "image_generation_run",
        updated,
        dept=updated.get("ownerDept"),
        project=updated.get("projectId"),
        status=updated.get("status"),
        ts=int(updated.get("completedAt") or updated.get("updatedAt") or now_ms()),
    )
    return updated


async def _maybe_enqueue_image_completion_wake(
    repo: Repo,
    run: dict[str, Any],
    dept: dict[str, Any],
    image_message: dict[str, Any] | None,
    *,
    attachments: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    thread_id = str(run.get("threadId") or "")
    image_message_id = str((image_message or {}).get("id") or run.get("messageId") or "")
    if not thread_id or not image_message_id:
        await _record_image_wake_state(repo, run, {"status": "skipped", "reason": "no_thread", "checkedAt": now_ms()})
        return None
    request = run.get("request") if isinstance(run.get("request"), dict) else {}
    if not _image_wake_enabled(request):
        await _record_image_wake_state(repo, run, {"status": "disabled", "reason": "wakeOnComplete=false", "checkedAt": now_ms()})
        return None
    messages = await repo.thread_messages(thread_id, limit=80)
    active = _active_image_wake_reply(messages, run_id=str(run.get("id") or ""), image_message_id=image_message_id)
    if active:
        await _record_image_wake_state(
            repo,
            run,
            {
                "status": "skipped",
                "reason": "active_reply",
                "activeMessageId": active.get("id"),
                "checkedAt": now_ms(),
            },
        )
        return None

    now = now_ms()
    reply_id = uid("msg")
    wake_job_id = uid("job")
    terminal = str(run.get("status") or "")
    reply = {
        "id": reply_id,
        "threadId": thread_id,
        "role": "executive" if is_exec(str(dept.get("id") or "")) else "agent",
        "authorName": dept.get("agentName") or dept.get("name") or "ATRIUM",
        "text": (
            "ภาพสร้างเสร็จแล้ว กำลังให้ AI กลับมาตรวจผลลัพธ์และบอกตำแหน่งไฟล์..."
            if terminal == "succeeded"
            else "งานสร้างภาพจบด้วยข้อผิดพลาด กำลังให้ AI กลับมาตรวจและแนะนำขั้นตอนถัดไป..."
        ),
        "ts": now,
        "pending": True,
        "status": "queued",
        "replyToMessageId": image_message_id,
        "imageGenerationWake": {
            "runId": run.get("id"),
            "jobId": run.get("jobId"),
            "status": terminal,
            "imageMessageId": image_message_id,
            "queuedAt": now,
        },
        **agent_message_metadata(dept),
    }
    await repo.add_message(reply)
    await repo.enqueue(
        wake_job_id,
        "chat_reply",
        {
            "threadId": thread_id,
            "departmentId": dept.get("id") or run.get("departmentId") or run.get("ownerDept"),
            "userMessageId": image_message_id,
            "replyMessageId": reply_id,
            "text": str((image_message or {}).get("text") or ""),
            "userTs": int((image_message or {}).get("ts") or run.get("completedAt") or now),
            "replyTs": now,
            "thinkingEffort": "low",
            "speed": "fast",
            "attachments": attachments or (image_message or {}).get("attachments") or [],
            "imageGenerationWake": {
                "runId": run.get("id"),
                "jobId": run.get("jobId"),
                "status": terminal,
                "error": error,
            },
        },
        now,
        priority=1,
    )
    await repo.add_activity({
        "id": uid("act"),
        "ts": now,
        "type": "message",
        "severity": "info",
        "departmentId": run.get("ownerDept") or dept.get("id"),
        "detail": f"ปลุก AI ให้ตอบหลังงานสร้างภาพ {run.get('jobId')} จบ",
    })
    await _record_image_wake_state(
        repo,
        run,
        {
            "status": "queued",
            "jobId": wake_job_id,
            "replyMessageId": reply_id,
            "queuedAt": now,
        },
    )
    hub.pulse({
        "kind": "image_generation_wake_queued",
        "threadId": thread_id,
        "msgId": reply_id,
        "departmentId": dept.get("id"),
        "jobId": run.get("jobId"),
        "reply": reply,
    })
    hub.mark_dirty()
    return reply


async def queue_image_generation_assets(
    repo: Repo,
    raw: dict[str, Any],
    *,
    fallback_owner_dept: str,
    requested_by: str,
    thread_id: str | None = None,
    active_dept: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prepared = await _prepared_image_request(
        repo,
        raw,
        fallback_owner_dept=fallback_owner_dept,
        requested_by=requested_by,
    )
    queued_at = now_ms()
    job_id = uid("imgjob")
    run_id = uid("imgrun")
    dept = active_dept or prepared["department"]
    message_id = uid("msg") if thread_id else None
    run = {
        "id": run_id,
        "jobId": job_id,
        "threadId": thread_id,
        "messageId": message_id,
        "departmentId": dept.get("id") or prepared["ownerDept"],
        "ownerDept": prepared["ownerDept"],
        "projectId": prepared["projectId"],
        "taskIds": prepared["taskIds"],
        "requestedBy": prepared["requestedBy"],
        "createdBy": prepared["createdBy"],
        "prompt": prepared["prompt"],
        "mode": prepared["mode"],
        "model": prepared["model"],
        "modelPolicy": prepared["modelPolicy"],
        "options": prepared["options"],
        "referenceArtifactIds": prepared["referenceArtifactIds"],
        "maskArtifactId": prepared["maskArtifactId"],
        "wakeOnComplete": prepared["wakeOnComplete"],
        "status": "queued",
        "queuedAt": queued_at,
        "startedAt": None,
        "completedAt": None,
        "elapsedMs": None,
        "attempts": 0,
        "maxAttempts": IMAGE_GENERATION_MAX_ATTEMPTS,
        "retryAfter": None,
        "request": prepared["request"],
        "artifacts": [],
        "locations": [],
        "error": None,
    }
    await repo.put_entity("image_generation_run", run, dept=prepared["ownerDept"], project=prepared["projectId"], status="queued", ts=queued_at)
    if message_id and thread_id:
        await repo.add_message(_image_job_message(run, dept))
    await repo.enqueue(
        job_id,
        "image_generation",
        {
            "runId": run_id,
            "jobId": job_id,
            "threadId": thread_id,
            "messageId": message_id,
            "departmentId": dept.get("id") or prepared["ownerDept"],
            "ownerDept": prepared["ownerDept"],
            "requestedBy": prepared["requestedBy"],
            "request": prepared["request"],
            "queuedAt": queued_at,
        },
        queued_at,
        priority=1,
    )
    await repo.add_activity({
        "id": uid("act"),
        "ts": queued_at,
        "type": "system",
        "severity": "info",
        "departmentId": prepared["ownerDept"],
        "detail": f"คิวสร้างภาพ {job_id} ผ่าน {prepared['model']} ({prepared['options'].get('n', 1)} image)",
    })
    hub.pulse({
        "kind": "image_generation_queued",
        "threadId": thread_id,
        "msgId": message_id,
        "departmentId": prepared["ownerDept"],
        "jobId": job_id,
        "run": run,
    })
    hub.mark_dirty()
    return {
        "ok": True,
        "tool": "generate_image_asset",
        "async": True,
        "asyncMode": True,
        "status": "queued",
        "jobId": job_id,
        "runId": run_id,
        "messageId": message_id,
        "queuedAt": queued_at,
        "statusUrl": f"/api/images/jobs/{job_id}",
        "mode": prepared["mode"],
        "summary": (
            f"queued image generation job {job_id} via {prepared['model']}; "
            f"AI can continue working and poll /api/images/jobs/{job_id}"
        ),
        "model": prepared["model"],
        "provider": "openai",
        "artifacts": [],
        "artifact": None,
        "locations": [],
        "usage": None,
        "requestId": None,
        "options": prepared["options"],
        "referenceArtifactIds": prepared["referenceArtifactIds"],
        "maskArtifactId": prepared["maskArtifactId"],
        "modelPolicy": prepared["modelPolicy"],
        "wakeOnComplete": prepared["wakeOnComplete"],
        "warnings": [],
        "job": run,
        "jobs": [run],
    }


async def process_image_generation_job(repo: Repo, payload: dict[str, Any], now: int | None = None) -> dict[str, Any]:
    now = now or now_ms()
    run_id = str(payload.get("runId") or "")
    job_id = str(payload.get("jobId") or "")
    if not run_id or not job_id:
        raise ImageGenerationError("image generation job payload is missing runId or jobId")
    run = await repo.get_entity("image_generation_run", run_id)
    if not run:
        raise ImageGenerationError(f"image generation run not found: {run_id}")
    request = payload.get("request") if isinstance(payload.get("request"), dict) else run.get("request")
    if not isinstance(request, dict):
        raise ImageGenerationError(f"image generation run has no request: {run_id}")
    dept = await repo.get_department(str(payload.get("departmentId") or run.get("departmentId") or run.get("ownerDept") or ""))
    if not dept:
        dept = await repo.get_department(str(run.get("ownerDept") or ""))
    dept = dept or {"id": run.get("ownerDept") or "exec", "name": "ATRIUM", "agentName": "ATRIUM"}
    started_at = now_ms()
    attempt = int(run.get("attempts") or 0) + 1
    run = {
        **run,
        "status": "running",
        "startedAt": run.get("startedAt") or started_at,
        "lastAttemptStartedAt": started_at,
        "attempts": attempt,
        "maxAttempts": IMAGE_GENERATION_MAX_ATTEMPTS,
        "retryAfter": None,
        "updatedAt": started_at,
        "error": None,
    }
    await repo.put_entity("image_generation_run", run, dept=run.get("ownerDept"), project=run.get("projectId"), status="running", ts=started_at)
    await _upsert_image_job_message(repo, run, dept)
    await repo.add_activity({
        "id": uid("act"),
        "ts": started_at,
        "type": "system",
        "severity": "info",
        "departmentId": run.get("ownerDept"),
        "detail": f"เริ่มสร้างภาพ {job_id} ผ่าน {run.get('model')}",
    })
    hub.mark_dirty()
    await repo.s.commit()
    try:
        result = await generate_image_assets(
            repo,
            request,
            fallback_owner_dept=str(run.get("ownerDept") or payload.get("ownerDept") or "exec"),
            requested_by=str(payload.get("requestedBy") or run.get("requestedBy") or "image-worker"),
        )
    except Exception as exc:
        completed_at = now_ms()
        error = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, RetryableImageGenerationError) and attempt < IMAGE_GENERATION_MAX_ATTEMPTS:
            delay_ms = _image_retry_delay_ms(attempt)
            retry_after = completed_at + delay_ms
            retry_run = {
                **run,
                "status": "queued",
                "startedAt": None,
                "lastAttemptCompletedAt": completed_at,
                "lastAttemptElapsedMs": max(0, completed_at - int(run.get("lastAttemptStartedAt") or started_at)),
                "completedAt": None,
                "elapsedMs": None,
                "updatedAt": completed_at,
                "retryAfter": retry_after,
                "error": error,
                "lastError": error,
            }
            await repo.put_entity(
                "image_generation_run",
                retry_run,
                dept=retry_run.get("ownerDept"),
                project=retry_run.get("projectId"),
                status="queued",
                ts=completed_at,
            )
            await _upsert_image_job_message(repo, retry_run, dept, error=error)
            await repo.add_activity({
                "id": uid("act"),
                "ts": completed_at,
                "type": "system",
                "severity": "warn",
                "departmentId": retry_run.get("ownerDept"),
                "detail": (
                    f"สร้างภาพ {job_id} เจอ upstream timeout/temporary error; "
                    f"จะลองใหม่ครั้งที่ {attempt + 1}/{IMAGE_GENERATION_MAX_ATTEMPTS} "
                    f"ใน {_duration_label(delay_ms)}"
                ),
            })
            hub.mark_dirty()
            raise RetryableImageGenerationError(str(exc), delay_ms=delay_ms) from exc
        failed_run = {
            **run,
            "status": "failed",
            "completedAt": completed_at,
            "elapsedMs": max(0, completed_at - int(run.get("startedAt") or started_at)),
            "updatedAt": completed_at,
            "error": error,
        }
        await repo.put_entity(
            "image_generation_run",
            failed_run,
            dept=failed_run.get("ownerDept"),
            project=failed_run.get("projectId"),
            status="failed",
            ts=completed_at,
        )
        failed_message = await _upsert_image_job_message(repo, failed_run, dept, error=error)
        await repo.add_activity({
            "id": uid("act"),
            "ts": completed_at,
            "type": "system",
            "severity": "alert",
            "departmentId": failed_run.get("ownerDept"),
            "detail": f"สร้างภาพ {job_id} ไม่สำเร็จ: {_clip(error, 300)}",
        })
        await _maybe_enqueue_image_completion_wake(repo, failed_run, dept, failed_message, error=error)
        hub.mark_dirty()
        raise
    completed_at = now_ms()
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
    locations = result.get("locations") if isinstance(result.get("locations"), list) else []
    attachments = [message_attachment_from_artifact(artifact) for artifact in artifacts if isinstance(artifact, dict)]
    succeeded_run = {
        **run,
        "status": "succeeded",
        "completedAt": completed_at,
        "elapsedMs": max(0, completed_at - int(run.get("startedAt") or started_at)),
        "updatedAt": completed_at,
        "artifacts": artifacts,
        "artifactIds": [artifact.get("id") for artifact in artifacts if isinstance(artifact, dict) and artifact.get("id")],
        "locations": locations,
        "usage": result.get("usage"),
        "requestId": result.get("requestId"),
        "summary": result.get("summary"),
        "error": None,
    }
    await repo.put_entity(
        "image_generation_run",
        succeeded_run,
        dept=succeeded_run.get("ownerDept"),
        project=succeeded_run.get("projectId"),
        status="succeeded",
        ts=completed_at,
    )
    completed_message = await _upsert_image_job_message(repo, succeeded_run, dept, result=result, attachments=attachments)
    await repo.add_activity({
        "id": uid("act"),
        "ts": completed_at,
        "type": "task_done",
        "severity": "good",
        "departmentId": succeeded_run.get("ownerDept"),
        "detail": f"สร้างภาพ {job_id} เสร็จแล้ว ได้ {len(artifacts)} artifact",
    })
    hub.pulse({
        "kind": "image_generation_done",
        "threadId": succeeded_run.get("threadId"),
        "msgId": succeeded_run.get("messageId"),
        "departmentId": succeeded_run.get("ownerDept"),
        "jobId": job_id,
        "run": succeeded_run,
    })
    await _maybe_enqueue_image_completion_wake(repo, succeeded_run, dept, completed_message, attachments=attachments)
    hub.mark_dirty()
    return result


async def mark_image_generation_job_failed(repo: Repo, payload: dict[str, Any], error: str) -> None:
    run_id = str(payload.get("runId") or "")
    if not run_id:
        return
    run = await repo.get_entity("image_generation_run", run_id)
    if not run:
        return
    dept = await repo.get_department(str(payload.get("departmentId") or run.get("departmentId") or run.get("ownerDept") or ""))
    if not dept:
        dept = await repo.get_department(str(run.get("ownerDept") or ""))
    dept = dept or {"id": run.get("ownerDept") or "exec", "name": "ATRIUM", "agentName": "ATRIUM"}
    completed_at = now_ms()
    failed_run = {
        **run,
        "status": "failed",
        "completedAt": completed_at,
        "elapsedMs": max(0, completed_at - int(run.get("startedAt") or run.get("queuedAt") or completed_at)),
        "updatedAt": completed_at,
        "error": error,
    }
    await repo.put_entity(
        "image_generation_run",
        failed_run,
        dept=failed_run.get("ownerDept"),
        project=failed_run.get("projectId"),
        status="failed",
        ts=completed_at,
    )
    failed_message = await _upsert_image_job_message(repo, failed_run, dept, error=error)
    await repo.add_activity({
        "id": uid("act"),
        "ts": completed_at,
        "type": "system",
        "severity": "alert",
        "departmentId": failed_run.get("ownerDept"),
        "detail": f"สร้างภาพ {failed_run.get('jobId') or payload.get('jobId')} ไม่สำเร็จ: {_clip(error, 300)}",
    })
    await _maybe_enqueue_image_completion_wake(repo, failed_run, dept, failed_message, error=error)
    hub.mark_dirty()


async def resolve_image_reference(repo: Repo, artifact_id: str, *, purpose: str = "reference") -> ImageReference:
    artifact_id = str(artifact_id or "").strip()
    if not artifact_id:
        raise ImageGenerationError(f"{purpose} artifact id is required")
    artifact = await repo.get_entity("artifact", artifact_id)
    if not artifact:
        raise ImageGenerationError(f"{purpose} artifact not found: {artifact_id}")
    mime = guess_mime(str(artifact.get("name") or f"{artifact_id}.png"), artifact.get("contentMime") or artifact.get("mime"))
    if not mime.startswith("image/"):
        raise ImageGenerationError(f"{purpose} artifact is not an image: {artifact_id}")
    data = bytes_from_uri(artifact.get("uri"))
    if data is None:
        raise ImageGenerationError(f"{purpose} artifact bytes are not available: {artifact_id}")
    max_upload_bytes = int(get_settings().max_upload_bytes or 0)
    if max_upload_bytes > 0 and len(data) > max_upload_bytes:
        raise ImageGenerationError(f"{purpose} artifact exceeds max upload bytes: {artifact_id}")
    return ImageReference(
        artifact_id=artifact_id,
        name=str(artifact.get("name") or f"{artifact_id}.png"),
        mime=mime,
        data=data,
        uri=str(artifact.get("uri") or ""),
    )


async def resolve_image_references(repo: Repo, artifact_ids: list[str]) -> list[ImageReference]:
    refs: list[ImageReference] = []
    seen: set[str] = set()
    for artifact_id in artifact_ids:
        normalized = str(artifact_id or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        refs.append(await resolve_image_reference(repo, normalized))
    return refs


class ChatGPTOAuthImageClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._token_provider = ChatGPTCodexOAuthTokenProvider(self.settings)

    def ready(self) -> bool:
        return bool(chatgpt_oauth_status(self.settings).get("ready"))

    @staticmethod
    def _reference_data_url(ref: ImageReference) -> str:
        encoded = base64.b64encode(ref.data).decode("ascii")
        return f"data:{ref.mime};base64,{encoded}"

    def _input_content(
        self,
        *,
        prompt: str,
        references: list[ImageReference],
        mask: ImageReference | None,
        n: int,
        size: str,
        quality: str,
        output_format: str,
        background: str | None,
    ) -> list[dict[str, Any]]:
        instruction_parts = [
            prompt,
            "",
            "Generate the requested image with the image_generation tool.",
            f"Requested count: {_coerce_n(n)}.",
            f"Requested size/aspect setting: {size}.",
            f"Requested quality: {quality}.",
            f"Requested output format: {output_format}.",
        ]
        if background:
            instruction_parts.append(f"Requested background: {background}.")
        if references:
            instruction_parts.append("Use the attached reference image(s) as visual guidance.")
        if mask:
            instruction_parts.append("Use the attached mask image as edit guidance where possible.")
        content: list[dict[str, Any]] = [{"type": "input_text", "text": "\n".join(instruction_parts).strip()}]
        for ref in references:
            content.append({"type": "input_image", "image_url": self._reference_data_url(ref)})
        if mask:
            content.append({"type": "input_image", "image_url": self._reference_data_url(mask)})
        return content

    def _payload(
        self,
        *,
        prompt: str,
        references: list[ImageReference],
        mask: ImageReference | None,
        n: int,
        size: str,
        quality: str,
        output_format: str,
        background: str | None,
    ) -> dict[str, Any]:
        return {
            "model": CHATGPT_OAUTH_IMAGE_RESPONSES_MODEL,
            "instructions": (
                "Use the image_generation tool to generate image artifacts. "
                "Prioritize exact requested text and composition. Return only a short final note after generation."
            ),
            "input": [{
                "role": "user",
                "content": self._input_content(
                    prompt=prompt,
                    references=references,
                    mask=mask,
                    n=n,
                    size=size,
                    quality=quality,
                    output_format=output_format,
                    background=background,
                ),
            }],
            "tools": [{"type": "image_generation"}],
            "tool_choice": "auto",
            "store": False,
            "stream": True,
        }

    @staticmethod
    def _event_error_message(event: dict[str, Any]) -> str:
        error = event.get("error") if isinstance(event.get("error"), dict) else {}
        message = error.get("message") if isinstance(error, dict) else None
        return str(message or event.get("message") or "ChatGPT OAuth image generation failed")

    async def _post_once(self, payload: dict[str, Any]) -> ImageApiResult:
        token = await self._token_provider.access_token()
        timeout = _image_timeout(self.settings)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    self.settings.chatgpt_account_base_url.rstrip("/") + "/responses",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise RetryableImageGenerationError(f"ChatGPT OAuth image generation timed out after {timeout} seconds") from exc
        except httpx.RequestError as exc:
            raise RetryableImageGenerationError(f"ChatGPT OAuth image generation request failed: {exc}") from exc
        request_id = resp.headers.get("x-request-id")
        if resp.status_code >= 400:
            detail = resp.text[:1200]
            with contextlib.suppress(Exception):
                parsed = resp.json()
                error = parsed.get("error") if isinstance(parsed, dict) else None
                if isinstance(error, dict):
                    detail = str(error.get("message") or error)[:1200]
                elif isinstance(parsed, dict):
                    detail = str(parsed.get("detail") or parsed)[:1200]
            message = f"ChatGPT OAuth image generation error {resp.status_code}: {detail}"
            if resp.status_code in IMAGE_RETRYABLE_STATUS_CODES:
                raise RetryableImageGenerationError(message)
            raise ImageGenerationError(message)

        events = list(_sse_json_events(resp.text))
        images: list[GeneratedImage] = []
        seen: set[str] = set()
        final_model = CHATGPT_OAUTH_IMAGE_RESPONSES_MODEL
        usage: dict[str, Any] | None = None
        for event in events:
            event_type = str(event.get("type") or "")
            if event_type in {"response.failed", "response.incomplete", "error"}:
                raise ImageGenerationError(self._event_error_message(event))
            response = event.get("response") if isinstance(event.get("response"), dict) else None
            if response:
                final_model = str(response.get("model") or final_model)
                if isinstance(response.get("usage"), dict):
                    usage = response["usage"]
            for _path, key, value in _walk_event_values(event):
                if key not in {"result", "b64_json", "base64", "b64", "image_base64", "imageBase64"}:
                    continue
                text = str(value or "").strip() if isinstance(value, str) else ""
                if not text or text[:128] in seen:
                    continue
                data_url = _decode_data_url(text, "image/png")
                raw = data_url.data if data_url else _decode_base64_bytes(text, allow_short=True)
                if not raw:
                    continue
                seen.add(text[:128])
                mime = data_url.mime if data_url else _mime_from_image_bytes(raw, "image/png")
                images.append(GeneratedImage(data=raw, mime=mime))
        if not images:
            event_types = sorted({str(event.get("type") or "") for event in events if isinstance(event, dict)})
            raise ImageGenerationError(
                "ChatGPT OAuth image generation returned no parseable image data; "
                f"events={', '.join(event_types[:24]) or 'none'}"
            )
        return ImageApiResult(
            images=images,
            model=final_model,
            usage=usage,
            request_id=request_id,
            provider="chatgpt_account",
        )

    async def create(
        self,
        *,
        prompt: str,
        references: list[ImageReference],
        mask: ImageReference | None,
        model: str | None,
        n: int,
        size: str | None,
        quality: str | None,
        output_format: str | None,
        output_compression: int | None,
        background: str | None,
        moderation: str | None,
    ) -> ImageApiResult:
        del model, output_compression, moderation
        if not self.ready():
            raise ImageGenerationError(
                "ChatGPT account OAuth is not configured for image generation; sign in with ChatGPT OAuth first."
            )
        payload = self._payload(
            prompt=prompt,
            references=references,
            mask=mask,
            n=n,
            size=str(size or "auto").strip() or "auto",
            quality=_coerce_quality(quality),
            output_format=_coerce_output_format(output_format),
            background=background,
        )
        last_exc: Exception | None = None
        for attempt in range(1, IMAGE_GENERATION_MAX_ATTEMPTS + 1):
            try:
                return await self._post_once(payload)
            except RetryableImageGenerationError as exc:
                last_exc = exc
                if attempt >= IMAGE_GENERATION_MAX_ATTEMPTS:
                    raise ChatGPTOAuthImageGenerationExhausted(str(exc), delay_ms=exc.delay_ms) from exc
                await asyncio.sleep(min(_image_retry_delay_ms(attempt) / 1000.0, 10.0))
        if last_exc:
            raise ChatGPTOAuthImageGenerationExhausted(str(last_exc)) from last_exc
        raise ImageGenerationError("ChatGPT OAuth image generation failed before making a request")


class OpenAIImageClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def _headers(self) -> dict[str, str]:
        if not self.settings.image_generation_api_key:
            raise ImageGenerationError("ATRIUM_IMAGE_GENERATION_API_KEY is not configured")
        return {"Authorization": f"Bearer {self.settings.image_generation_api_key}"}

    async def create(
        self,
        *,
        prompt: str,
        references: list[ImageReference],
        mask: ImageReference | None,
        model: str | None,
        n: int,
        size: str | None,
        quality: str | None,
        output_format: str | None,
        output_compression: int | None,
        background: str | None,
        moderation: str | None,
    ) -> ImageApiResult:
        model = str(model or PRIMARY_IMAGE_MODEL).strip()
        size = str(size or "auto").strip()
        quality = _coerce_quality(quality)
        output_format = _coerce_output_format(output_format)
        if references or mask:
            return await self._edit(
                prompt=prompt,
                references=references,
                mask=mask,
                model=model,
                n=n,
                size=size,
                quality=quality,
                output_format=output_format,
                output_compression=output_compression,
                background=background,
                moderation=moderation,
            )
        return await self._generate(
            prompt=prompt,
            model=model,
            n=n,
            size=size,
            quality=quality,
            output_format=output_format,
            output_compression=output_compression,
            background=background,
            moderation=moderation,
        )

    async def _generate(
        self,
        *,
        prompt: str,
        model: str,
        n: int,
        size: str,
        quality: str,
        output_format: str,
        output_compression: int | None,
        background: str | None,
        moderation: str | None,
    ) -> ImageApiResult:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": _coerce_n(n),
            "size": size,
            "quality": quality,
        }
        optional = {
            "output_format": output_format,
            "output_compression": output_compression,
            "background": background,
            "moderation": moderation,
        }
        payload.update({key: value for key, value in optional.items() if value is not None and value != ""})
        try:
            async with httpx.AsyncClient(timeout=_image_timeout(self.settings)) as client:
                resp = await client.post(
                    _url(self.settings.image_generation_base_url, "/images/generations"),
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise RetryableImageGenerationError(
                f"image API timed out after {_image_timeout(self.settings) or 'unbounded'} seconds"
            ) from exc
        except httpx.RequestError as exc:
            raise RetryableImageGenerationError(f"image API request failed: {exc}") from exc
        return await self._parse_response(resp, model=model, fallback_mime=_image_mime(output_format))

    async def _edit(
        self,
        *,
        prompt: str,
        references: list[ImageReference],
        mask: ImageReference | None,
        model: str,
        n: int,
        size: str,
        quality: str,
        output_format: str,
        output_compression: int | None,
        background: str | None,
        moderation: str | None,
    ) -> ImageApiResult:
        if not references:
            raise ImageGenerationError("referenceArtifactIds is required when maskArtifactId is provided")
        data: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": str(_coerce_n(n)),
            "size": size,
            "quality": quality,
            "output_format": output_format,
        }
        optional = {
            "output_compression": output_compression,
            "background": background,
            "moderation": moderation,
        }
        data.update({key: str(value) for key, value in optional.items() if value is not None and value != ""})
        files = self._edit_files(references, mask, field_name="image[]")
        try:
            async with httpx.AsyncClient(timeout=_image_timeout(self.settings)) as client:
                resp = await client.post(
                    _url(self.settings.image_generation_base_url, "/images/edits"),
                    headers=self._headers(),
                    data=data,
                    files=files,
                )
                if resp.status_code == 400 and "image[]" in resp.text:
                    resp = await client.post(
                        _url(self.settings.image_generation_base_url, "/images/edits"),
                        headers=self._headers(),
                        data=data,
                        files=self._edit_files(references, mask, field_name="image"),
                    )
        except httpx.TimeoutException as exc:
            raise RetryableImageGenerationError(
                f"image API timed out after {_image_timeout(self.settings) or 'unbounded'} seconds"
            ) from exc
        except httpx.RequestError as exc:
            raise RetryableImageGenerationError(f"image API request failed: {exc}") from exc
        return await self._parse_response(resp, model=model, fallback_mime=_image_mime(output_format))

    @staticmethod
    def _edit_files(
        references: list[ImageReference],
        mask: ImageReference | None,
        *,
        field_name: str,
    ) -> list[tuple[str, tuple[str, bytes, str]]]:
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for idx, ref in enumerate(references):
            filename = safe_filename(ref.name or f"reference-{idx}.png")
            files.append((field_name, (filename, ref.data, ref.mime)))
        if mask:
            files.append(("mask", (safe_filename(mask.name or "mask.png"), mask.data, mask.mime)))
        return files

    @classmethod
    def _iter_image_candidates(
        cls,
        value: Any,
        *,
        kind: str = "auto",
        revised_prompt: str | None = None,
        depth: int = 0,
    ):
        if value is None or depth > 6:
            return
        if isinstance(value, str):
            yield _ImagePayloadCandidate(value=value, kind=kind, revised_prompt=revised_prompt)
            return
        if isinstance(value, list | tuple):
            for item in value:
                yield from cls._iter_image_candidates(
                    item,
                    kind=kind,
                    revised_prompt=revised_prompt,
                    depth=depth + 1,
                )
            return
        if not isinstance(value, dict):
            return

        item_prompt = (
            str(value.get("revised_prompt") or value.get("revisedPrompt") or revised_prompt or "").strip()
            or None
        )
        for key in IMAGE_BASE64_KEYS:
            if value.get(key) is not None:
                yield _ImagePayloadCandidate(value=value.get(key), kind="base64", revised_prompt=item_prompt)
        for key in IMAGE_URL_KEYS:
            if value.get(key) is None:
                continue
            nested = value.get(key)
            if isinstance(nested, dict | list | tuple):
                yield from cls._iter_image_candidates(
                    nested,
                    kind="url",
                    revised_prompt=item_prompt,
                    depth=depth + 1,
                )
            else:
                yield _ImagePayloadCandidate(value=nested, kind="url", revised_prompt=item_prompt)
        for key in IMAGE_FILE_ID_KEYS:
            if value.get(key) is not None:
                yield _ImagePayloadCandidate(value=value.get(key), kind="file_id", revised_prompt=item_prompt)
        file_value = value.get("file")
        if isinstance(file_value, str) and file_value.strip().startswith(("file-", "file_")):
            yield _ImagePayloadCandidate(value=file_value, kind="file_id", revised_prompt=item_prompt)
        for key in IMAGE_RESULT_KEYS:
            if key not in value:
                continue
            nested = value.get(key)
            nested_kind = "base64" if isinstance(nested, str) else "auto"
            yield from cls._iter_image_candidates(
                nested,
                kind=nested_kind,
                revised_prompt=item_prompt,
                depth=depth + 1,
            )
        for key in ("images", "output", "outputs", "results", "content", *IMAGE_NESTED_KEYS):
            if key in value:
                yield from cls._iter_image_candidates(
                    value.get(key),
                    kind="auto",
                    revised_prompt=item_prompt,
                    depth=depth + 1,
                )

    @staticmethod
    def _payload_key_summary(payload: Any) -> str:
        if isinstance(payload, list):
            if not payload:
                return "root=list(empty)"
            first = payload[0]
            if isinstance(first, dict):
                return "root=list; item[0]=" + ",".join(list(first.keys())[:16])
            return f"root=list; item[0]={type(first).__name__}"
        if not isinstance(payload, dict):
            return f"root={type(payload).__name__}"
        parts = ["root=" + ",".join(list(payload.keys())[:16])]
        for key in ("data", "images", "output", "outputs", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                if value and isinstance(value[0], dict):
                    parts.append(f"{key}[0]=" + ",".join(list(value[0].keys())[:16]))
                else:
                    parts.append(f"{key}=list({len(value)})")
            elif isinstance(value, dict):
                parts.append(f"{key}=" + ",".join(list(value.keys())[:16]))
            elif value is not None:
                parts.append(f"{key}={type(value).__name__}")
        return "; ".join(part for part in parts if part)

    @staticmethod
    def _safe_url_label(url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path[:160]
        return f"{parsed.scheme}://{parsed.netloc}{path}" if parsed.scheme and parsed.netloc else path or "<relative-url>"

    def _absolute_image_url(self, url: str) -> str:
        text = str(url or "").strip()
        if _is_http_url(text):
            return text
        return urljoin(self.settings.image_generation_base_url.rstrip("/") + "/", text)

    async def _download_image_url(
        self,
        url: str,
        *,
        fallback_mime: str,
        revised_prompt: str | None,
        headers: dict[str, str] | None = None,
    ) -> GeneratedImage:
        absolute_url = self._absolute_image_url(url)
        parsed = urlparse(absolute_url)
        if parsed.scheme not in {"http", "https"}:
            raise ImageGenerationError("image API returned an unsupported image URL")

        request_headers = headers or {}
        base_host = urlparse(self.settings.image_generation_base_url).netloc
        if not headers and base_host and parsed.netloc == base_host:
            request_headers = self._headers()
        try:
            async with httpx.AsyncClient(timeout=_image_timeout(self.settings), follow_redirects=True) as client:
                fetched = await client.get(absolute_url, headers=request_headers)
        except httpx.TimeoutException as exc:
            raise ImageGenerationError(
                f"image URL download timed out after {_image_timeout(self.settings) or 'unbounded'} seconds"
            ) from exc
        except httpx.RequestError as exc:
            raise ImageGenerationError(f"image URL download failed: {exc}") from exc
        if fetched.status_code >= 400:
            detail = fetched.text[:600]
            raise ImageGenerationError(
                f"image URL download error {fetched.status_code} from {self._safe_url_label(absolute_url)}: {detail}"
            )
        data = fetched.content
        if not data:
            raise ImageGenerationError(f"image URL download returned empty content from {self._safe_url_label(absolute_url)}")
        mime = _content_type_image_mime(fetched.headers.get("content-type"), fallback_mime)
        return GeneratedImage(data=data, mime=_mime_from_image_bytes(data, mime), revised_prompt=revised_prompt)

    async def _download_image_file(
        self,
        file_id: str,
        *,
        fallback_mime: str,
        revised_prompt: str | None,
    ) -> GeneratedImage:
        normalized = str(file_id or "").strip()
        if not normalized:
            raise ImageGenerationError("image API returned an empty file id")
        url = _url(self.settings.image_generation_base_url, f"/files/{quote(normalized, safe='')}/content")
        return await self._download_image_url(
            url,
            fallback_mime=fallback_mime,
            revised_prompt=revised_prompt,
            headers=self._headers(),
        )

    async def _image_from_candidate(
        self,
        candidate: _ImagePayloadCandidate,
        *,
        fallback_mime: str,
    ) -> GeneratedImage | None:
        text = str(candidate.value or "").strip()
        if not text:
            return None
        data_url = _decode_data_url(text, fallback_mime)
        if data_url:
            return GeneratedImage(data=data_url.data, mime=data_url.mime, revised_prompt=candidate.revised_prompt)
        if candidate.kind == "file_id":
            return await self._download_image_file(
                text,
                fallback_mime=fallback_mime,
                revised_prompt=candidate.revised_prompt,
            )
        if _is_http_url(text) or text.startswith("/") or candidate.kind == "url":
            return await self._download_image_url(
                text,
                fallback_mime=fallback_mime,
                revised_prompt=candidate.revised_prompt,
            )
        if candidate.kind in {"base64", "auto"}:
            raw = _decode_base64_bytes(text, allow_short=candidate.kind == "base64")
            if raw is not None:
                return GeneratedImage(
                    data=raw,
                    mime=_mime_from_image_bytes(raw, fallback_mime),
                    revised_prompt=candidate.revised_prompt,
                )
        return None

    async def _parse_response(self, resp: httpx.Response, *, model: str, fallback_mime: str) -> ImageApiResult:
        request_id = resp.headers.get("x-request-id")
        if resp.status_code >= 400:
            if resp.status_code == 524:
                detail = "upstream image gateway timed out before returning a generated image"
            else:
                detail = resp.text[:1200]
            with contextlib.suppress(Exception):
                parsed = resp.json()
                detail = str((parsed.get("error") or {}).get("message") or parsed)[:1200]
            message = f"image API error {resp.status_code}: {detail}"
            if resp.status_code in IMAGE_RETRYABLE_STATUS_CODES:
                raise RetryableImageGenerationError(message)
            raise ImageGenerationError(message)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ImageGenerationError(f"image API returned a non-JSON response: {resp.text[:1200]}") from exc
        images: list[GeneratedImage] = []
        seen_candidates: set[tuple[str, str]] = set()
        parse_errors: list[str] = []
        for candidate in self._iter_image_candidates(payload):
            value_key = str(candidate.value or "")
            candidate_key = (candidate.kind, value_key[:2048])
            if candidate_key in seen_candidates:
                continue
            seen_candidates.add(candidate_key)
            try:
                image = await self._image_from_candidate(candidate, fallback_mime=fallback_mime)
            except ImageGenerationError as exc:
                parse_errors.append(str(exc))
                continue
            if image is not None:
                images.append(image)
        if not images:
            detail = (
                "image API returned no parseable image data "
                "(accepted b64_json, base64 data URL, image URL, or file id)"
            )
            detail += f"; response keys: {self._payload_key_summary(payload)}"
            if parse_errors:
                detail += "; parse errors: " + " | ".join(parse_errors[:3])
            raise ImageGenerationError(detail)
        response_model = payload.get("model") if isinstance(payload, dict) else None
        usage = payload.get("usage") if isinstance(payload, dict) and isinstance(payload.get("usage"), dict) else None
        raw_created = payload.get("created") if isinstance(payload, dict) and isinstance(payload.get("created"), int) else None
        return ImageApiResult(
            images=images,
            model=str(response_model or model),
            usage=usage,
            request_id=request_id,
            raw_created=raw_created,
            provider="openai",
        )


async def _link_artifact_to_tasks(repo: Repo, artifact_id: str, task_ids: list[str]) -> None:
    for task_id in task_ids:
        task = await repo.get_task(task_id)
        if not task:
            continue
        deliverables = list(task.get("deliverables") or [])
        if artifact_id not in deliverables:
            deliverables.append(artifact_id)
        task["deliverables"] = deliverables
        task["updatedAt"] = now_ms()
        task["log"] = [*task.get("log", []), f"แนบ generated image artifact {artifact_id}"]
        await repo.save_task(task)


async def store_generated_image_artifact(
    repo: Repo,
    *,
    data: bytes,
    mime: str,
    owner_dept: str,
    prompt: str,
    created_by: str,
    requested_by: str,
    artifact_name: str | None,
    index: int,
    total: int,
    project_id: str | None,
    task_ids: list[str],
    tags: list[str],
    model: str,
    provider: str,
    mode: str,
    reference_artifact_ids: list[str],
    mask_artifact_id: str | None,
    usage: dict[str, Any] | None,
    revised_prompt: str | None,
    options: dict[str, Any] | None = None,
    model_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    now = now_ms()
    artifact_id = uid("art")
    filename = _safe_image_name(
        artifact_name,
        artifact_id,
        prompt=prompt,
        requester=requested_by or created_by,
        ts_ms=now,
        index=index,
        total=total,
        mime=mime,
    )
    storage = "filesystem"
    content_hash = None
    uri: str
    if settings.object_store_enabled:
        stored = get_object_store(settings).put_bytes(data, mime=mime)
        uri = stored.uri
        content_hash = stored.content_hash
        content_size = stored.size_bytes
        storage = "object_store"
    else:
        out_dir = (settings.workspace_dir / owner_dept / "artifacts" / artifact_id).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / filename
        path.write_bytes(data)
        uri = str(path)
        content_size = len(data)

    all_tags = []
    for tag in [*(tags or []), "image-generation", mode, model]:
        tag = str(tag or "").strip()[:48]
        if tag and tag not in all_tags:
            all_tags.append(tag)
    metadata = {
        "source": "chatgpt-oauth-image-generation" if provider == "chatgpt_account" else "openai-image-api",
        "provider": provider,
        "model": model,
        "mode": mode,
        "prompt": _clip(prompt),
        "revisedPrompt": revised_prompt,
        "requestedBy": requested_by,
        "referenceArtifactIds": reference_artifact_ids,
        "maskArtifactId": mask_artifact_id,
        "usage": usage,
        "options": options or {},
        "modelPolicy": model_policy or {},
    }
    artifact = Artifact(
        id=artifact_id,
        name=filename,
        kind="image",
        mime=mime,
        owner_dept=owner_dept,
        task_ids=task_ids,
        project_id=project_id,
        version=1,
        status="approved",
        uri=uri,
        storage=storage,  # type: ignore[arg-type]
        content_hash=content_hash,
        content_size_bytes=content_size,
        content_mime=mime,
        tags=all_tags,
        links=[f"atrium://artifact/{ref_id}" for ref_id in reference_artifact_ids],
        preview={"kind": "image", "uri": uri},
        created_at=now,
        created_by=created_by,
        updated_at=now,
        updated_by=created_by,
        approval_tier="department",
        approved_by=created_by,
        approved_at=now,
    ).dump()
    artifact["imageGeneration"] = metadata
    version = ArtifactVersion(
        artifact_id=artifact_id,
        version=1,
        author=created_by,
        ts=now,
        note=f"generated image via {model}",
        uri=uri,
        storage=storage,  # type: ignore[arg-type]
        content_hash=content_hash,
        content_size_bytes=content_size,
        content_mime=mime,
        preview={"kind": "image", "uri": uri},
    ).dump()
    version["imageGeneration"] = metadata
    await repo.put_entity("artifact", artifact, dept=owner_dept, project=project_id, status="approved", ts=now)
    await repo.put_entity(
        "artifact_version",
        {**version, "id": f"{artifact_id}:1"},
        dept=owner_dept,
        project=project_id,
        status="approved",
        ts=now,
    )
    await _link_artifact_to_tasks(repo, artifact_id, task_ids)
    knowledge = {
        "id": uid("kn"),
        "title": f"Generated image: {filename}",
        "ts": now,
        "score": 0.76,
        "text": "\n".join(
            [
                f"Generated image artifact: {artifact_id}",
                f"Name: {filename}",
                f"Model: {model}",
                f"Mode: {mode}",
                f"Prompt: {_clip(prompt, 4000)}",
                *( [f"Revised prompt: {_clip(revised_prompt, 2000)}"] if revised_prompt else [] ),
                *( [f"Reference artifacts: {', '.join(reference_artifact_ids)}"] if reference_artifact_ids else [] ),
                *( [f"Mask artifact: {mask_artifact_id}"] if mask_artifact_id else [] ),
                f"Download: /api/artifacts/{artifact_id}/download",
            ]
        ),
        "tags": [*all_tags, artifact_id],
        "source": artifact_id,
    }
    await repo.add_knowledge(owner_dept, knowledge, source=artifact_id)
    await repo.add_activity({
        "id": uid("act"),
        "ts": now,
        "type": "task_done",
        "severity": "good",
        "departmentId": owner_dept,
        "detail": f"สร้างภาพ artifact “{filename}” ผ่าน {model}",
    })
    return {"artifact": artifact, "version": version, "knowledge": knowledge}


async def generate_image_assets(
    repo: Repo,
    raw: dict[str, Any],
    *,
    fallback_owner_dept: str,
    requested_by: str,
) -> dict[str, Any]:
    settings = get_settings()
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        raise ImageGenerationError("prompt is required")
    owner_dept = str(raw.get("ownerDept") or raw.get("owner_dept") or fallback_owner_dept).strip()
    if not await repo.get_department(owner_dept):
        raise ImageGenerationError(f"owner department not found: {owner_dept}")
    project_id = raw.get("projectId") or raw.get("project_id")
    if project_id and not await repo.get_entity("project", str(project_id)):
        raise ImageGenerationError(f"project not found: {project_id}")
    task_ids = [str(item).strip() for item in (raw.get("taskIds") or raw.get("task_ids") or []) if str(item).strip()]
    for task_id in task_ids:
        if not await repo.get_task(task_id):
            raise ImageGenerationError(f"task not found: {task_id}")
    reference_artifact_ids = [
        str(item).strip()
        for item in (raw.get("referenceArtifactIds") or raw.get("reference_artifact_ids") or raw.get("references") or [])
        if str(item).strip()
    ]
    mask_artifact_id = str(raw.get("maskArtifactId") or raw.get("mask_artifact_id") or "").strip() or None
    references = await resolve_image_references(repo, reference_artifact_ids)
    mask = await resolve_image_reference(repo, mask_artifact_id, purpose="mask") if mask_artifact_id else None
    mode = "edit" if references or mask else "generate"
    selected_model, model_policy = _image_model_policy(raw, settings)
    options = _image_request_options(raw, selected_model)
    warnings: list[str] = []
    auth_status = image_generation_auth_status(settings)
    fallback_reason: str | None = None

    async def create_with_openai_fallback(reason: str) -> ImageApiResult:
        if not settings.image_generation_api_key:
            raise ImageGenerationError(
                f"{reason}; ATRIUM_IMAGE_GENERATION_API_KEY is not configured, so OpenAI /v1/images fallback is unavailable"
            )
        warnings.append("used OpenAI /v1/images fallback after ChatGPT OAuth image generation was unavailable")
        fallback_client = OpenAIImageClient(settings)
        return await fallback_client.create(
            prompt=prompt,
            references=references,
            mask=mask,
            model=selected_model,
            n=options["n"],
            size=options["size"],
            quality=options["quality"],
            output_format=options["outputFormat"],
            output_compression=options["outputCompression"],
            background=options["background"],
            moderation=options["moderation"],
        )

    if auth_status.get("primaryConfigured"):
        try:
            result = await ChatGPTOAuthImageClient(settings).create(
                prompt=prompt,
                references=references,
                mask=mask,
                model=selected_model,
                n=options["n"],
                size=options["size"],
                quality=options["quality"],
                output_format=options["outputFormat"],
                output_compression=options["outputCompression"],
                background=options["background"],
                moderation=options["moderation"],
            )
        except ChatGPTOAuthImageGenerationExhausted as exc:
            fallback_reason = f"ChatGPT OAuth image generation exhausted retries: {exc}"
            result = await create_with_openai_fallback(fallback_reason)
    else:
        raise ImageGenerationError(
            "ChatGPT OAuth image generation is not configured; "
            "OpenAI /v1/images fallback is used only after the ChatGPT OAuth route exhausts retries"
        )
    display_requester = str(
        raw.get("requestedBy")
        or raw.get("requested_by")
        or raw.get("requesterName")
        or raw.get("requester_name")
        or raw.get("createdBy")
        or raw.get("created_by")
        or requested_by
        or owner_dept
    )
    created_by = str(raw.get("createdBy") or raw.get("created_by") or display_requester or owner_dept)
    artifact_name = raw.get("artifactName") or raw.get("artifact_name")
    tags = [str(item).strip() for item in (raw.get("tags") or []) if str(item).strip()]
    stored = []
    total = len(result.images)
    for idx, image in enumerate(result.images):
        stored.append(
            await store_generated_image_artifact(
                repo,
                data=image.data,
                mime=image.mime,
                owner_dept=owner_dept,
                prompt=prompt,
                created_by=created_by,
                requested_by=display_requester,
                artifact_name=str(artifact_name) if artifact_name else None,
                index=idx,
                total=total,
                project_id=str(project_id) if project_id else None,
                task_ids=task_ids,
                tags=tags,
                model=result.model,
                provider=result.provider,
                mode=mode,
                reference_artifact_ids=[ref.artifact_id for ref in references],
                mask_artifact_id=mask.artifact_id if mask else None,
                usage=result.usage,
                revised_prompt=image.revised_prompt,
                options=options,
                model_policy={
                    **model_policy,
                    "selectedModel": result.model,
                    "primaryProvider": auth_status.get("primaryProvider"),
                    "fallbackProvider": auth_status.get("fallbackProvider"),
                    "fallbackReason": fallback_reason,
                },
            )
        )
    artifacts = [item["artifact"] for item in stored]
    locations = [artifact_location(artifact) for artifact in artifacts]
    primary = artifacts[0] if artifacts else None
    summary = (
        f"created {len(artifacts)} image artifact(s) via {result.provider}/{result.model}: "
        + ", ".join(location["artifactId"] for location in locations)
    )
    return {
        "ok": True,
        "tool": "generate_image_asset",
        "async": False,
        "asyncMode": False,
        "status": "succeeded",
        "mode": mode,
        "summary": summary,
        "model": result.model,
        "provider": result.provider,
        "artifacts": artifacts,
        "artifact": primary,
        "locations": locations,
        "usage": result.usage,
        "requestId": result.request_id,
        "options": options,
        "referenceArtifactIds": [ref.artifact_id for ref in references],
        "maskArtifactId": mask.artifact_id if mask else None,
        "modelPolicy": {
            **model_policy,
            "selectedModel": result.model,
            "primaryProvider": auth_status.get("primaryProvider"),
            "fallbackProvider": auth_status.get("fallbackProvider"),
            "fallbackReason": fallback_reason,
        },
        "warnings": warnings,
    }


def image_data_url_for_artifact(artifact: dict[str, Any], *, max_bytes: int | None = None) -> str | None:
    mime = str(artifact.get("contentMime") or artifact.get("mime") or "")
    if not mime.startswith("image/"):
        return None
    data = bytes_from_uri(artifact.get("uri"))
    if data is None:
        return None
    if max_bytes is not None and len(data) > max_bytes:
        return None
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"
