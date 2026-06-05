"""Telegram channel gateway.

This module keeps Telegram as a channel adapter around the existing ATRIUM chat
runtime. Inbound Telegram events become normal chat messages and queued
``chat_reply`` jobs; a progress job keeps Telegram updated while the reply is
pending, then outbound delivery edits that live message into the final answer
when possible.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import mimetypes
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .atrium_domain import agent_message_metadata, thread_cost_summary
from .chat_input import estimate_input, input_character_limit_exceeded, input_character_limit_detail
from .chat_rendering import ensure_rendering_metadata
from .clock import now_ms
from .config import Settings, get_settings
from .db.base import commit_and_release, session_scope
from .db.repo import Repo
from .events import hub
from .file_intake import message_attachment_from_artifact, safe_filename
from .ids import uid
from .seed import EXEC_ID
from .threads import dept_id_from_thread, is_exec, thread_id_for

TELEGRAM_AUTH_FILE = "telegram-gateway.json"
TELEGRAM_MAX_TEXT = 4096
TELEGRAM_DEFAULT_CHUNK = 3800
DEFAULT_DM_POLICY = "pairing"
DEFAULT_GROUP_POLICY = "configured"
DEFAULT_GROUP_REQUIRE_MENTION = True
DEFAULT_POLLING_INTERVAL_S = 5.0
DEFAULT_POLLING_BURST_INTERVAL_S = 2.0
DEFAULT_POLLING_BURST_WINDOW_S = 120.0
DEFAULT_MAX_FILE_BYTES = 20_000_000
DEFAULT_OUTBOUND_RETRY_ATTEMPTS = 3
DEFAULT_OUTBOUND_RETRY_DELAY_S = 8.0
DEFAULT_PROGRESS_TYPING_INTERVAL_S = 4.0
DEFAULT_PROGRESS_MAX_WINDOW_S = 600.0
DEFAULT_PROGRESS_PREVIEW_CHARS = 3200
DEFAULT_PROGRESS_STREAM_INTERVAL_S = 2.0
DEFAULT_PROGRESS_STREAM_TEXT_CHARS = 160
TELEGRAM_MEDIA_FIELDS = (
    "document",
    "audio",
    "voice",
    "video",
    "video_note",
    "animation",
    "sticker",
)

TelegramFileLoader = Callable[[dict[str, Any], str, Settings], Awaitable[dict[str, Any]]]
TelegramFileStore = Callable[..., Awaitable[dict[str, Any]]]
TelegramSender = Callable[[str, dict[str, Any], Settings], Awaitable[dict[str, Any]]]
_REGISTERED_FILE_STORE: TelegramFileStore | None = None


def set_telegram_file_store(store_file: TelegramFileStore | None) -> None:
    global _REGISTERED_FILE_STORE
    _REGISTERED_FILE_STORE = store_file


class TelegramGatewayError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retry_after_s: float | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s
        self.status_code = status_code


def _activity(text: str, *, department_id: str | None = None, severity: str = "info") -> dict[str, Any]:
    return {
        "id": uid("act"),
        "ts": now_ms(),
        "type": "system",
        "text": text,
        "departmentId": department_id,
        "severity": severity,
    }


def _message_error(code: str, detail: str, *, retryable: bool = True) -> dict[str, Any]:
    return {"code": code, "detail": detail, "retryable": retryable}


def telegram_auth_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.data_dir / "auth" / TELEGRAM_AUTH_FILE


def load_telegram_auth(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    auth: dict[str, Any] = {}
    path = telegram_auth_path(settings)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                auth.update(loaded)
        except Exception:
            auth["authError"] = f"failed to read {path}"
    return auth


def telegram_bot_token(settings: Settings | None = None) -> str:
    return str(load_telegram_auth(settings).get("botToken") or "").strip()


def telegram_webhook_secret(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    auth = load_telegram_auth(settings)
    return str(auth.get("webhookSecret") or "").strip()


def _telegram_max_file_bytes(settings: Settings, auth: dict[str, Any] | None = None) -> int:
    auth = auth or {}
    return int(
        auth.get("maxFileBytes")
        or DEFAULT_MAX_FILE_BYTES
    )


def _telegram_outbound_chunk_chars(settings: Settings, auth: dict[str, Any] | None = None) -> int:
    auth = auth or load_telegram_auth(settings)
    return int(
        auth.get("outboundChunkChars")
        or TELEGRAM_DEFAULT_CHUNK
    )


def _telegram_outbound_retry_attempts(settings: Settings, auth: dict[str, Any] | None = None) -> int:
    auth = auth or {}
    return int(
        auth.get("outboundRetryAttempts")
        or DEFAULT_OUTBOUND_RETRY_ATTEMPTS
    )


def _telegram_outbound_retry_delay_s(settings: Settings, auth: dict[str, Any] | None = None) -> float:
    auth = auth or {}
    return float(
        auth.get("outboundRetryDelayS")
        or DEFAULT_OUTBOUND_RETRY_DELAY_S
    )


def _telegram_progress_typing_interval_s(auth: dict[str, Any] | None = None) -> float:
    auth = auth or {}
    return float(auth.get("progressTypingIntervalS") or DEFAULT_PROGRESS_TYPING_INTERVAL_S)


def _telegram_progress_max_window_s(auth: dict[str, Any] | None = None) -> float:
    auth = auth or {}
    return float(auth.get("progressMaxWindowS") or DEFAULT_PROGRESS_MAX_WINDOW_S)


def _telegram_progress_messages_enabled(auth: dict[str, Any] | None = None) -> bool:
    auth = auth or {}
    value = auth.get("progressMessages", auth.get("progressMessage", True))
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "off", "no", "disabled"}


def _telegram_progress_preview_chars(auth: dict[str, Any] | None = None) -> int:
    auth = auth or {}
    return max(600, min(int(auth.get("progressPreviewChars") or DEFAULT_PROGRESS_PREVIEW_CHARS), TELEGRAM_MAX_TEXT - 500))


def _telegram_progress_stream_interval_s(auth: dict[str, Any] | None = None) -> float:
    auth = auth or {}
    raw = auth.get("progressStreamIntervalS", auth.get("progressLiveIntervalS"))
    if raw is None:
        return DEFAULT_PROGRESS_STREAM_INTERVAL_S
    return max(0.0, float(raw))


def _telegram_progress_stream_text_chars(auth: dict[str, Any] | None = None) -> int:
    auth = auth or {}
    return max(40, int(auth.get("progressStreamTextChars") or DEFAULT_PROGRESS_STREAM_TEXT_CHARS))


def _csv_values(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        source = raw
    else:
        source = re.split(r"[,;\s]+", str(raw or ""))
    out: list[str] = []
    for item in source:
        value = str(item or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _policy_value(raw: Any, default: str) -> str:
    value = str(raw or default).strip().lower()
    return value if value in {"pairing", "allowlist", "open", "configured", "disabled"} else default


def _telegram_user_id(message: dict[str, Any]) -> str:
    user = message.get("from") if isinstance(message.get("from"), dict) else {}
    return str(user.get("id") or "").strip()


def _telegram_author_name(message: dict[str, Any]) -> str:
    user = message.get("from") if isinstance(message.get("from"), dict) else {}
    parts = [
        str(user.get("first_name") or "").strip(),
        str(user.get("last_name") or "").strip(),
    ]
    name = " ".join(part for part in parts if part).strip()
    if name:
        return name[:120]
    username = str(user.get("username") or "").strip()
    return f"@{username}" if username else "Telegram"


def _telegram_chat_id(message: dict[str, Any]) -> str:
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    return str(chat.get("id") or "").strip()


def _telegram_chat_type(message: dict[str, Any]) -> str:
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    return str(chat.get("type") or "").strip().lower()


def _telegram_message_id(message: dict[str, Any]) -> str:
    return str(message.get("message_id") or "").strip()


def _telegram_thread_id(message: dict[str, Any]) -> str | None:
    value = message.get("message_thread_id")
    return None if value is None else str(value)


def _client_message_id(message: dict[str, Any], update_id: str | None = None) -> str:
    chat_id = _telegram_chat_id(message) or "chat"
    message_id = _telegram_message_id(message) or update_id or hashlib.sha1(
        json.dumps(message, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return f"telegram:{chat_id}:{message_id}"


def _message_from_update(update: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    for kind in ("message", "edited_message", "channel_post", "edited_channel_post"):
        message = update.get(kind)
        if isinstance(message, dict):
            return kind, message
    return "", None


def _bot_username(auth: dict[str, Any], public_record: dict[str, Any] | None = None) -> str:
    for source in (auth, public_record or {}):
        bot = source.get("bot") if isinstance(source.get("bot"), dict) else {}
        username = str(bot.get("username") or "").strip()
        if username:
            return username.lstrip("@")
    return ""


def _has_bot_mention(text: str, message: dict[str, Any], bot_username: str) -> bool:
    if not bot_username:
        return False
    needle = f"@{bot_username.lower()}"
    if needle in str(text or "").lower():
        return True
    for field in ("entities", "caption_entities"):
        for entity in message.get(field) or []:
            if not isinstance(entity, dict) or entity.get("type") != "mention":
                continue
            offset = int(entity.get("offset") or 0)
            length = int(entity.get("length") or 0)
            if str(text or "")[offset : offset + length].lower() == needle:
                return True
    return False


def _strip_bot_mention(text: str, bot_username: str) -> str:
    if not bot_username:
        return text
    return re.sub(rf"@{re.escape(bot_username)}\b", "", text, flags=re.IGNORECASE).strip()


def _message_text(message: dict[str, Any], *, bot_username: str = "", strip_mention: bool = False) -> str:
    text = str(message.get("text") or message.get("caption") or "").strip()
    if strip_mention:
        text = _strip_bot_mention(text, bot_username)
    reply = message.get("reply_to_message") if isinstance(message.get("reply_to_message"), dict) else None
    if reply:
        reply_author = _telegram_author_name(reply)
        reply_text = str(reply.get("text") or reply.get("caption") or "").strip()
        if reply_text:
            quoted = reply_text[:500]
            text = f"Replying to Telegram message from {reply_author}: {quoted}\n\n{text}".strip()
    return text


def _configured_groups(settings: Settings, auth: dict[str, Any], public_record: dict[str, Any] | None) -> dict[str, Any]:
    del settings
    groups: dict[str, Any] = {}
    for source in (auth.get("groups"), (public_record or {}).get("groups")):
        parsed = _json_object(source)
        for key, value in parsed.items():
            if isinstance(value, dict):
                groups[str(key)] = value
    return groups


def _list_from_config(*values: Any) -> list[str]:
    out: list[str] = []
    for value in values:
        for item in _csv_values(value):
            if item not in out:
                out.append(item)
    return out


def _is_allowed_user(user_id: str, allow_from: list[str], *, open_allowed: bool = False) -> bool:
    if not user_id:
        return False
    normalized = {str(item).strip().removeprefix("telegram:").removeprefix("tg:") for item in allow_from}
    if "*" in normalized:
        return True
    if open_allowed and not normalized:
        return True
    return user_id in normalized


def _canonical_thread(raw_thread: str | None, department_id: str | None = None) -> str:
    dept_id = str(department_id or "").strip()
    if dept_id:
        return thread_id_for(dept_id)
    thread = str(raw_thread or "").strip()
    if thread in {"", "exec", "executive", "company"}:
        return thread_id_for(EXEC_ID)
    return thread


async def _public_gateway_record(repo: Repo) -> dict[str, Any] | None:
    try:
        record = await repo.get_entity("telegram_gateway", "default")
    except Exception:
        return None
    return record if isinstance(record, dict) else None


async def resolve_telegram_route(
    repo: Repo,
    message: dict[str, Any],
    *,
    settings: Settings | None = None,
    auth: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    auth = auth if auth is not None else load_telegram_auth(settings)
    public_record = await _public_gateway_record(repo)
    chat_id = _telegram_chat_id(message)
    chat_type = _telegram_chat_type(message)
    user_id = _telegram_user_id(message)
    bot_username = _bot_username(auth, public_record)
    base_thread = str(
        auth.get("defaultThreadId")
        or (public_record or {}).get("defaultThreadId")
        or "executive"
    )
    if chat_type == "private":
        policy = _policy_value(auth.get("dmPolicy") or (public_record or {}).get("dmPolicy"), DEFAULT_DM_POLICY)
        allow_from = _list_from_config(auth.get("allowFrom"), (public_record or {}).get("allowFrom"))
        if policy == "disabled":
            return {"ok": False, "status": "blocked", "reason": "dm_disabled", "chatId": chat_id}
        if policy == "open" or _is_allowed_user(user_id, allow_from, open_allowed=False):
            thread_id = _canonical_thread(base_thread)
            return {
                "ok": True,
                "kind": "dm",
                "threadId": thread_id,
                "departmentId": dept_id_from_thread(thread_id),
                "botUsername": bot_username,
                "policy": policy,
            }
        code = pairing_code(chat_id, user_id)
        await repo.put_entity(
            "telegram_pairing",
            {
                "id": code,
                "provider": "telegram",
                "chatId": chat_id,
                "userId": user_id,
                "status": "pending",
                "createdAt": now_ms(),
            },
            dept=EXEC_ID,
            status="pending",
            ts=now_ms(),
        )
        return {
            "ok": False,
            "status": "blocked",
            "reason": "pairing_required" if policy == "pairing" else "user_not_allowed",
            "pairingCode": code,
            "chatId": chat_id,
        }

    groups = _configured_groups(settings, auth, public_record)
    group = groups.get(chat_id) or groups.get(str(chat_id))
    policy = _policy_value(auth.get("groupPolicy") or (public_record or {}).get("groupPolicy"), DEFAULT_GROUP_POLICY)
    if not isinstance(group, dict):
        if policy == "disabled" or policy == "configured":
            return {"ok": False, "status": "suppressed", "reason": "group_not_configured", "chatId": chat_id}
        group = {}
    if group.get("enabled") is False:
        return {"ok": False, "status": "suppressed", "reason": "group_disabled", "chatId": chat_id}
    allow_from = _list_from_config(group.get("allowFrom"), auth.get("groupAllowFrom"), (public_record or {}).get("groupAllowFrom"))
    if allow_from and not _is_allowed_user(user_id, allow_from):
        return {"ok": False, "status": "blocked", "reason": "group_user_not_allowed", "chatId": chat_id}
    text = _message_text(message, bot_username=bot_username)
    require_mention = bool(group.get("requireMention", auth.get("groupRequireMention", (public_record or {}).get("groupRequireMention", DEFAULT_GROUP_REQUIRE_MENTION))))
    if require_mention and not _has_bot_mention(text, message, bot_username):
        return {"ok": False, "status": "suppressed", "reason": "mention_required", "chatId": chat_id}
    topic_id = _telegram_thread_id(message)
    topic_cfg = {}
    topics = group.get("topics") if isinstance(group.get("topics"), dict) else {}
    if topic_id and isinstance(topics, dict):
        topic_cfg = topics.get(str(topic_id)) if isinstance(topics.get(str(topic_id)), dict) else {}
    thread_id = _canonical_thread(
        topic_cfg.get("threadId") or group.get("threadId") or f"telegram:group:{chat_id}",
        topic_cfg.get("departmentId") or group.get("departmentId"),
    )
    return {
        "ok": True,
        "kind": "group",
        "threadId": thread_id,
        "departmentId": dept_id_from_thread(thread_id) if thread_id.startswith("dept:") or thread_id == "executive" else str(group.get("departmentId") or EXEC_ID),
        "botUsername": bot_username,
        "policy": policy,
        "requireMention": require_mention,
        "stripMention": require_mention,
    }


def pairing_code(chat_id: str, user_id: str) -> str:
    digest = hashlib.sha256(f"{chat_id}:{user_id}".encode("utf-8")).hexdigest()[:6].upper()
    return f"TG-{digest}"


def _media_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    photos = message.get("photo") if isinstance(message.get("photo"), list) else []
    if photos:
        photo = max((p for p in photos if isinstance(p, dict)), key=lambda p: int(p.get("file_size") or 0), default=None)
        if photo:
            file_unique = str(photo.get("file_unique_id") or photo.get("file_id") or "photo")
            items.append({
                "kind": "photo",
                "fileId": photo.get("file_id"),
                "fileUniqueId": photo.get("file_unique_id"),
                "filename": safe_filename(f"telegram-photo-{file_unique[:16]}.jpg"),
                "mime": "image/jpeg",
                "sizeBytes": photo.get("file_size"),
            })
    for field in TELEGRAM_MEDIA_FIELDS:
        value = message.get(field)
        if not isinstance(value, dict):
            continue
        file_id = value.get("file_id")
        if not file_id:
            continue
        filename = value.get("file_name") or value.get("filename")
        if not filename:
            suffix = mimetypes.guess_extension(str(value.get("mime_type") or "")) or ".bin"
            filename = f"telegram-{field}-{str(value.get('file_unique_id') or file_id)[:16]}{suffix}"
        items.append({
            "kind": field,
            "fileId": file_id,
            "fileUniqueId": value.get("file_unique_id"),
            "filename": safe_filename(filename),
            "mime": value.get("mime_type") or "application/octet-stream",
            "sizeBytes": value.get("file_size"),
        })
    return items


def _telegram_retry_after_s(parsed: Any) -> float | None:
    if not isinstance(parsed, dict):
        return None
    parameters = parsed.get("parameters")
    if not isinstance(parameters, dict) or parameters.get("retry_after") is None:
        return None
    try:
        retry_after = float(parameters.get("retry_after"))
    except (TypeError, ValueError):
        return None
    return retry_after if retry_after > 0 else None


def _telegram_error_code(parsed: Any) -> int | None:
    if not isinstance(parsed, dict):
        return None
    try:
        return int(parsed.get("error_code"))
    except (TypeError, ValueError):
        return None


def _telegram_api_error(method: str, raw: str, *, http_code: int | None = None) -> TelegramGatewayError:
    parsed: Any
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    retry_after_s = _telegram_retry_after_s(parsed)
    status_code = http_code or _telegram_error_code(parsed)
    if isinstance(parsed, dict) and parsed.get("description"):
        detail = str(parsed.get("description"))
    else:
        detail = raw
    if http_code is not None:
        return TelegramGatewayError(
            f"Telegram API {method} returned {http_code}: {detail[:800]}",
            retry_after_s=retry_after_s,
            status_code=status_code,
        )
    return TelegramGatewayError(
        f"Telegram API {method} failed: {detail[:800]}",
        retry_after_s=retry_after_s,
        status_code=status_code,
    )


def _telegram_next_update_offset(update: dict[str, Any]) -> int | None:
    try:
        return int(update.get("update_id")) + 1
    except (TypeError, ValueError):
        return None


def _telegram_api_request(bot_token: str, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = get_settings()
    timeout = max(1.0, float(getattr(settings, "telegram_gateway_timeout_s", 20.0) or 20.0))
    url = f"https://api.telegram.org/bot{bot_token}/{method.lstrip('/')}"
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise _telegram_api_error(method, body, http_code=exc.code) from exc
    except urllib.error.URLError as exc:
        raise TelegramGatewayError(f"Telegram API {method} failed: {exc}") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise TelegramGatewayError(f"Telegram API {method} returned invalid JSON") from exc
    if not parsed.get("ok"):
        raise _telegram_api_error(method, body)
    return parsed


async def download_telegram_media(item: dict[str, Any], bot_token: str, settings: Settings) -> dict[str, Any]:
    auth = load_telegram_auth(settings)
    file_info = await asyncio.to_thread(_telegram_api_request, bot_token, "getFile", {"file_id": item["fileId"]})
    result = file_info.get("result") if isinstance(file_info.get("result"), dict) else {}
    file_path = str(result.get("file_path") or "")
    if not file_path:
        raise TelegramGatewayError("Telegram getFile did not return file_path")
    max_bytes = _telegram_max_file_bytes(settings, auth)
    url = f"https://api.telegram.org/file/bot{bot_token}/{urllib.parse.quote(file_path, safe='/')}"
    try:
        with urllib.request.urlopen(url, timeout=max(1.0, float(settings.telegram_gateway_timeout_s))) as resp:
            data = resp.read(max_bytes + 1)
    except urllib.error.URLError as exc:
        raise TelegramGatewayError(f"Telegram file download failed: {exc}") from exc
    if len(data) > max_bytes:
        raise TelegramGatewayError(f"Telegram file exceeds {max_bytes} bytes")
    filename = item.get("filename") or Path(file_path).name or "telegram-file.bin"
    return {
        "data": data,
        "filename": safe_filename(filename),
        "mime": item.get("mime") or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        "sizeBytes": len(data),
        "filePath": file_path,
    }


async def _import_media(
    repo: Repo,
    *,
    message: dict[str, Any],
    route: dict[str, Any],
    bot_token: str,
    settings: Settings,
    store_file: TelegramFileStore | None,
    file_loader: TelegramFileLoader | None,
) -> list[dict[str, Any]]:
    if not store_file:
        return []
    loader = file_loader or download_telegram_media
    attachments: list[dict[str, Any]] = []
    for item in _media_items(message):
        loaded = await loader(item, bot_token, settings)
        data = bytes(loaded.get("data") or b"")
        max_bytes = _telegram_max_file_bytes(settings)
        if len(data) > max_bytes:
            raise TelegramGatewayError(f"Telegram file exceeds {max_bytes} bytes")
        result = await store_file(
            repo,
            data=data,
            filename=str(loaded.get("filename") or item.get("filename") or "telegram-file.bin"),
            owner_dept=str(route.get("departmentId") or EXEC_ID),
            artifact_name=str(loaded.get("filename") or item.get("filename") or "Telegram file"),
            tags=["telegram", "channel"],
            links=[f"telegram://chat/{_telegram_chat_id(message)}/message/{_telegram_message_id(message)}"],
            created_by="telegram",
            status="approved",
            mime=str(loaded.get("mime") or item.get("mime") or "application/octet-stream"),
        )
        artifact = result.get("artifact") if isinstance(result, dict) else None
        if artifact:
            attachment = message_attachment_from_artifact(artifact)
            attachment["source"] = "telegram"
            attachment["telegram"] = {
                "fileId": item.get("fileId"),
                "fileUniqueId": item.get("fileUniqueId"),
                "kind": item.get("kind"),
            }
            attachments.append(attachment)
    return attachments


async def _record_update(repo: Repo, update_id: str, payload: dict[str, Any], *, status: str, dept_id: str | None = None) -> None:
    await repo.put_entity(
        "telegram_update",
        {
            "id": update_id,
            "provider": "telegram",
            "status": status,
            "updatedAt": now_ms(),
            **payload,
        },
        dept=dept_id or EXEC_ID,
        status=status,
        ts=now_ms(),
    )


async def handle_telegram_update(
    repo: Repo,
    update: dict[str, Any],
    *,
    settings: Settings | None = None,
    store_file: TelegramFileStore | None = None,
    file_loader: TelegramFileLoader | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    auth = load_telegram_auth(settings)
    bot_token = str(auth.get("botToken") or "").strip()
    if not bot_token:
        return {"ok": False, "status": "not_configured", "reason": "missing_bot_token"}

    update_id = str(update.get("update_id") or "").strip()
    update_key = f"telegram:update:{update_id}" if update_id else ""
    if update_key:
        existing_update = await repo.get_entity("telegram_update", update_key)
        if existing_update:
            return {"ok": True, "status": "duplicate", "updateId": update_id, "messageId": existing_update.get("messageId")}

    kind, message = _message_from_update(update)
    if not message:
        if update_key:
            await _record_update(repo, update_key, {"updateKind": "ignored"}, status="ignored")
        return {"ok": True, "status": "ignored", "reason": "unsupported_update"}

    route = await resolve_telegram_route(repo, message, settings=settings, auth=auth)
    if not route.get("ok"):
        if route.get("reason") == "pairing_required" and route.get("pairingCode"):
            pairing_text = (
                "Telegram pairing is required before this chat can reach ATRIUM.\n"
                f"Pairing code: {route['pairingCode']}\n"
                "Send this code to the ATRIUM executive chat owner for approval."
            )
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    _telegram_api_request,
                    bot_token,
                    "sendMessage",
                    {"chat_id": _telegram_chat_id(message), "text": pairing_text},
                )
            await repo.add_activity(_activity(
                f"Telegram DM pairing requested: {route['pairingCode']} from user {_telegram_user_id(message)}",
                department_id=EXEC_ID,
                severity="info",
            ))
        if update_key:
            await _record_update(
                repo,
                update_key,
                {
                    "updateKind": kind,
                    "chatId": _telegram_chat_id(message),
                    "telegramMessageId": _telegram_message_id(message),
                    "reason": route.get("reason"),
                    **({"pairingCode": route.get("pairingCode")} if route.get("pairingCode") else {}),
                },
                status=str(route.get("status") or "blocked"),
            )
        return {"ok": True, **route}

    thread_id = str(route["threadId"])
    dept_id = str(route.get("departmentId") or dept_id_from_thread(thread_id) or EXEC_ID)
    dept = await repo.get_department(dept_id) or await repo.get_department(EXEC_ID)
    if not dept:
        raise TelegramGatewayError("no department available for Telegram route")
    msg_id = _client_message_id(message, update_id=update_id)
    existing_msg = await repo.get_message(msg_id)
    if existing_msg:
        return {"ok": True, "status": "duplicate", "messageId": msg_id, "threadId": existing_msg.get("threadId")}

    text = _message_text(
        message,
        bot_username=str(route.get("botUsername") or ""),
        strip_mention=bool(route.get("stripMention")),
    )
    attachments = await _import_media(
        repo,
        message=message,
        route=route,
        bot_token=bot_token,
        settings=settings,
        store_file=store_file,
        file_loader=file_loader,
    )
    if not text.strip() and not attachments:
        if update_key:
            await _record_update(repo, update_key, {"updateKind": kind, "messageId": msg_id}, status="ignored", dept_id=dept_id)
        return {"ok": True, "status": "ignored", "reason": "empty_message"}

    estimate = estimate_input(text, attachments)
    now = now_ms()
    user_msg = {
        "id": msg_id,
        "threadId": thread_id,
        "role": "user",
        "authorName": _telegram_author_name(message),
        "text": text,
        "ts": now,
        "clientMessageId": msg_id,
        "status": "sent",
        "attachments": attachments,
        "input": {
            "status": "sent",
            "command": None,
            "commandArgs": None,
            "routedDepartmentId": None,
            "characterCount": estimate["characters"],
            "estimatedTokens": estimate["estimatedTokens"],
            "attachmentCount": estimate["attachmentCount"],
        },
        "channel": {
            "provider": "telegram",
            "direction": "inbound",
            "updateId": update_id or None,
            "updateKind": kind,
            "chatId": _telegram_chat_id(message),
            "chatType": _telegram_chat_type(message),
            "messageId": _telegram_message_id(message),
            "messageThreadId": _telegram_thread_id(message),
            "fromUserId": _telegram_user_id(message),
            "route": {
                "kind": route.get("kind"),
                "policy": route.get("policy"),
            },
        },
    }
    if input_character_limit_exceeded(estimate):
        user_msg["status"] = "blocked"
        user_msg["error"] = _message_error("input_too_large", input_character_limit_detail(), retryable=False)
        await repo.add_message(user_msg)
        reply = {
            "id": uid("msg"),
            "threadId": thread_id,
            "role": "system",
            "authorName": "Telegram Gateway",
            "text": input_character_limit_detail(),
            "ts": now_ms(),
            "status": "blocked",
            "replyToMessageId": user_msg["id"],
            "channelReply": _channel_reply_target(message, user_msg["id"]),
        }
        await repo.add_message(ensure_rendering_metadata(reply))
        await maybe_deliver_telegram_reply_for_message(repo, reply, settings=settings)
        return {"ok": True, "status": "blocked", "messageId": user_msg["id"], "threadId": thread_id}

    await repo.add_message(user_msg)
    reply = {
        "id": uid("msg"),
        "threadId": thread_id,
        "role": "executive" if is_exec(dept["id"]) else "agent",
        "authorName": dept.get("agentName") or "Executive",
        "text": "กำลังคิดและทำงานต่อในคิวเบื้องหลัง...",
        "ts": now_ms(),
        "pending": True,
        "status": "queued",
        "replyToMessageId": user_msg["id"],
        "channelReply": _channel_reply_target(message, user_msg["id"]),
        **agent_message_metadata(dept),
    }
    reply["threadCost"] = thread_cost_summary(thread_id, [user_msg, reply])
    await repo.add_message(ensure_rendering_metadata(reply))
    await repo.enqueue(
        uid("job"),
        "chat_reply",
        {
            "threadId": thread_id,
            "departmentId": dept["id"],
            "userMessageId": user_msg["id"],
            "replyMessageId": reply["id"],
            "text": text,
            "userTs": user_msg["ts"],
            "replyTs": reply["ts"],
            "attachments": attachments,
            "tokenEstimate": estimate,
            "channel": "telegram",
            "channelReply": reply["channelReply"],
        },
        now_ms(),
        priority=1,
    )
    progress_now = now_ms()
    await repo.enqueue(
        uid("job"),
        "telegram_progress",
        {
            "replyMessageId": reply["id"],
            "threadId": thread_id,
            "channelReply": reply["channelReply"],
            "expiresAt": progress_now + int(_telegram_progress_max_window_s(auth) * 1000),
        },
        progress_now + 500,
        priority=3,
    )
    if update_key:
        await _record_update(
            repo,
            update_key,
            {
                "updateKind": kind,
                "chatId": _telegram_chat_id(message),
                "telegramMessageId": _telegram_message_id(message),
                "messageId": user_msg["id"],
                "replyMessageId": reply["id"],
                "threadId": thread_id,
            },
            status="queued",
            dept_id=dept["id"],
        )
    await repo.add_activity(_activity("Telegram message queued into ATRIUM chat", department_id=dept["id"], severity="info"))
    return {
        "ok": True,
        "status": "queued",
        "threadId": thread_id,
        "messageId": user_msg["id"],
        "replyMessageId": reply["id"],
        "attachmentCount": len(attachments),
    }


def _channel_reply_target(message: dict[str, Any], user_message_id: str) -> dict[str, Any]:
    chat_id = _telegram_chat_id(message)
    telegram_msg_id = _telegram_message_id(message)
    return {
        "provider": "telegram",
        "status": "queued",
        "chatId": chat_id,
        "messageThreadId": _telegram_thread_id(message),
        "replyToTelegramMessageId": telegram_msg_id,
        "replyToMessageId": user_message_id,
        "dedupeKey": f"telegram:reply:{chat_id}:{telegram_msg_id or user_message_id}",
        "createdAt": now_ms(),
    }


def _split_text(text: str, limit: int) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []
    limit = max(500, min(int(limit or TELEGRAM_DEFAULT_CHUNK), TELEGRAM_MAX_TEXT))
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        chunks.append(rest)
    return chunks


def _telegram_target(channel_reply: dict[str, Any]) -> dict[str, Any]:
    target = {
        "chat_id": channel_reply.get("chatId"),
    }
    if channel_reply.get("messageThreadId"):
        with contextlib.suppress(ValueError):
            target["message_thread_id"] = int(channel_reply["messageThreadId"])
    if channel_reply.get("replyToTelegramMessageId"):
        with contextlib.suppress(ValueError):
            target["reply_parameters"] = {"message_id": int(channel_reply["replyToTelegramMessageId"])}
    return {key: value for key, value in target.items() if value not in (None, "")}


def _coerce_telegram_message_id(raw: Any) -> int | str | None:
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    if not text:
        return None
    with contextlib.suppress(ValueError):
        return int(text)
    return text


def _merge_telegram_channel_reply(base: dict[str, Any] | None, override: dict[str, Any] | None) -> dict[str, Any]:
    base = base if isinstance(base, dict) else {}
    override = override if isinstance(override, dict) else {}
    merged = {**base, **override}
    for key, value in base.items():
        if key.startswith("progress") and key not in override:
            merged[key] = value
    return merged


def _telegram_result_message_id(response: dict[str, Any]) -> str | None:
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    value = result.get("message_id")
    return None if value in (None, "") else str(value)


def _telegram_edit_target(channel_reply: dict[str, Any]) -> dict[str, Any]:
    return {
        "chat_id": channel_reply.get("chatId"),
        "message_id": _coerce_telegram_message_id(channel_reply.get("progressMessageId")),
    }


def _pending_reply_placeholder(text: str) -> bool:
    normalized = str(text or "").strip()
    return normalized in {
        "กำลังคิดและทำงานต่อในคิวเบื้องหลัง...",
        "กำลังคิดและทำงานต่อใน war room...",
        "กลับมาตรวจงานที่ตั้งเวลาไว้และกำลังทำต่อ...",
    }


def _trim_telegram_text(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)].rstrip()}…"


def _single_line(text: str, *, limit: int = 180) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return _trim_telegram_text(cleaned, limit)


def _provider_status_text(text: str) -> str:
    raw = str(text or "")
    if "สถานะ provider" not in raw:
        return ""
    cleaned = re.sub(r"\*+", "", raw)
    cleaned = cleaned.replace("สถานะ provider", "")
    return _single_line(cleaned, limit=150)


def _telegram_tool_name(run: dict[str, Any] | None) -> str:
    run = run if isinstance(run, dict) else {}
    name = str(
        run.get("tool")
        or run.get("name")
        or run.get("toolName")
        or run.get("function")
        or "tool"
    ).strip()
    return _single_line(name or "tool", limit=80)


def _telegram_progress_status_for_event(
    event_kind: str,
    *,
    text: str = "",
    run: dict[str, Any] | None = None,
) -> tuple[str, str] | None:
    kind = str(event_kind or "").strip()
    if kind == "thinking_delta":
        provider_status = _provider_status_text(text)
        label = f"กำลังคิด: {provider_status}" if provider_status else "กำลังคิด วิเคราะห์บริบทและร่างคำตอบ"
        return ("thinking", _single_line(label, limit=180))
    if kind in {"text_delta", "msg_delta"}:
        return ("drafting", "กำลังร่างคำตอบ")
    if kind == "tool_call":
        tool = _telegram_tool_name(run)
        return (f"tool_call:{tool}", f"กำลังเรียกเครื่องมือ: {tool}")
    if kind == "tool_result":
        tool = _telegram_tool_name(run)
        status = str((run or {}).get("status") or "").strip().lower()
        if status in {"failed", "error", "cancelled"}:
            return (f"tool_result:{tool}:{status}", f"เครื่องมือมีปัญหา: {tool}")
        return (f"tool_result:{tool}", f"เครื่องมือเสร็จแล้ว: {tool}")
    if kind in {"runtime_error", "error"}:
        return ("error", "เกิดข้อผิดพลาดระหว่างทำงาน")
    return None


def _telegram_progress_text(reply: dict[str, Any], auth: dict[str, Any]) -> str:
    status = str(reply.get("status") or ("sending" if reply.get("pending") else "sent")).strip()
    status_label = {
        "queued": "อยู่ในคิว กำลังเตรียมบริบท",
        "sending": "กำลังคิดและส่งคำตอบ",
        "pending_approval": "รออนุมัติเครื่องมือ",
        "failed": "เกิดข้อผิดพลาด",
        "cancelled": "ถูกยกเลิก",
    }.get(status, "กำลังทำงาน")
    channel_reply = reply.get("channelReply") if isinstance(reply.get("channelReply"), dict) else {}
    live_status = _single_line(str(channel_reply.get("progressStatusText") or ""), limit=180)
    if live_status:
        status_label = live_status
    raw_text = str(reply.get("text") or "").strip()
    preview = "" if _pending_reply_placeholder(raw_text) else raw_text
    lines = [
        "ATRIUM กำลังทำงาน",
        f"สถานะ: {status_label}",
    ]
    if preview:
        max_preview = _telegram_progress_preview_chars(auth)
        lines.extend(["", "คำตอบที่กำลังร่าง:", _trim_telegram_text(preview, max_preview)])
    else:
        lines.extend(["", "กำลังอ่านบริบท เรียกโมเดล หรือรอผลเครื่องมืออยู่..."])
    return _trim_telegram_text("\n".join(lines), TELEGRAM_MAX_TEXT)


async def maybe_stream_telegram_progress_event(
    repo: Repo,
    *,
    reply_message_id: str,
    thread_id: str | None = None,
    event_kind: str,
    text: str = "",
    run: dict[str, Any] | None = None,
    reply: dict[str, Any] | None = None,
    now: int | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Queue a one-shot Telegram progress refresh for live stream status events."""
    reply_id = str(reply_message_id or "").strip()
    if not reply_id:
        return {"ok": False, "status": "skipped", "reason": "missing_reply"}
    if reply is None:
        reply = await repo.get_message(reply_id, thread_id=thread_id)
    if not reply or not reply.get("pending"):
        return {"ok": True, "status": "skipped", "reason": "reply_not_pending"}
    channel_reply = reply.get("channelReply") if isinstance(reply.get("channelReply"), dict) else {}
    latest_reply: dict[str, Any] | None = None
    with contextlib.suppress(Exception):
        latest_reply = await repo.get_message(reply_id, thread_id=thread_id)
        if latest_reply and not latest_reply.get("pending"):
            return {"ok": True, "status": "skipped", "reason": "reply_not_pending"}
        latest_channel = latest_reply.get("channelReply") if isinstance(latest_reply, dict) and isinstance(latest_reply.get("channelReply"), dict) else {}
        channel_reply = _merge_telegram_channel_reply(channel_reply, latest_channel)
    if channel_reply.get("provider") != "telegram":
        return {"ok": True, "status": "skipped", "reason": "not_telegram"}
    status = _telegram_progress_status_for_event(event_kind, text=text, run=run)
    if not status:
        return {"ok": True, "status": "skipped", "reason": "unsupported_event"}
    settings = settings or get_settings()
    auth = load_telegram_auth(settings)
    if not str(auth.get("botToken") or "").strip() or not _telegram_progress_messages_enabled(auth):
        return {"ok": False, "status": "skipped", "reason": "not_ready"}
    now = now_ms() if now is None else int(now)
    status_key, status_text = status
    current_text_len = len(str(reply.get("text") or ""))
    last_key = str(channel_reply.get("progressStatusKey") or "")
    last_queued_at = int(channel_reply.get("progressStreamQueuedAt") or 0)
    last_text_len = int(channel_reply.get("progressPreviewTextLength") or 0)
    interval_ms = int(_telegram_progress_stream_interval_s(auth) * 1000)
    text_delta_chars = _telegram_progress_stream_text_chars(auth)
    is_text_event = str(event_kind) in {"text_delta", "msg_delta"}
    status_changed = status_key != last_key
    enough_time = interval_ms <= 0 or not last_queued_at or now - last_queued_at >= interval_ms
    enough_text = current_text_len - last_text_len >= text_delta_chars
    if is_text_event and not (enough_time or enough_text):
        return {"ok": True, "status": "throttled", "reason": "text_delta"}
    if not is_text_event and not status_changed and not enough_time:
        return {"ok": True, "status": "throttled", "reason": "status_unchanged"}
    updated_channel = {
        **channel_reply,
        "progressStatusKind": str(event_kind),
        "progressStatusKey": status_key,
        "progressStatusText": status_text,
        "progressStreamQueuedAt": now,
        "progressPreviewTextLength": current_text_len,
    }
    reply["channelReply"] = updated_channel
    base_reply = {**(latest_reply or {}), **reply}
    updated_reply = {**base_reply, "channelReply": updated_channel}
    await repo.update_message(updated_reply)
    await repo.enqueue(
        uid("job"),
        "telegram_progress",
        {
            "replyMessageId": reply_id,
            "threadId": thread_id or reply.get("threadId"),
            "channelReply": updated_channel,
            "streamEvent": str(event_kind),
        },
        now,
        priority=2,
    )
    with contextlib.suppress(Exception):
        await commit_and_release(repo.s)
    return {
        "ok": True,
        "status": "queued",
        "eventKind": str(event_kind),
        "progressStatusText": status_text,
    }


async def _telegram_edit_message_text(bot_token: str, channel_reply: dict[str, Any], text: str) -> dict[str, Any]:
    edit_target = _telegram_edit_target(channel_reply)
    if not edit_target.get("chat_id") or not edit_target.get("message_id"):
        raise TelegramGatewayError("Telegram progress edit target missing chatId/messageId")
    payload = {
        **edit_target,
        "text": _trim_telegram_text(text, TELEGRAM_MAX_TEXT),
        "disable_web_page_preview": False,
    }
    try:
        return await asyncio.to_thread(_telegram_api_request, bot_token, "editMessageText", payload)
    except TelegramGatewayError as exc:
        if "message is not modified" in str(exc).lower():
            return {"ok": True, "result": {"message_id": edit_target["message_id"], "not_modified": True}}
        raise


async def _telegram_delete_progress_message(bot_token: str, channel_reply: dict[str, Any]) -> dict[str, Any]:
    edit_target = _telegram_edit_target(channel_reply)
    if not edit_target.get("chat_id") or not edit_target.get("message_id"):
        raise TelegramGatewayError("Telegram progress delete target missing chatId/messageId")
    return await asyncio.to_thread(_telegram_api_request, bot_token, "deleteMessage", edit_target)


async def _send_or_edit_telegram_progress(
    bot_token: str,
    reply: dict[str, Any],
    channel_reply: dict[str, Any],
    settings: Settings,
    auth: dict[str, Any],
) -> dict[str, Any]:
    del settings
    text = _telegram_progress_text(reply, auth)
    progress_id = str(channel_reply.get("progressMessageId") or "").strip()
    if progress_id:
        edited = await _telegram_edit_message_text(bot_token, channel_reply, text)
        return {"ok": True, "method": "editMessageText", "progressMessageId": progress_id, "response": edited.get("result")}
    target = _telegram_target(channel_reply)
    if not target.get("chat_id"):
        raise TelegramGatewayError("Telegram progress target missing chatId")
    sent = await asyncio.to_thread(
        _telegram_api_request,
        bot_token,
        "sendMessage",
        {
            **target,
            "text": text,
            "disable_web_page_preview": True,
        },
    )
    progress_id = _telegram_result_message_id(sent)
    return {"ok": True, "method": "sendMessage", "progressMessageId": progress_id, "response": sent.get("result")}


async def _repair_completed_telegram_progress_message(
    repo: Repo,
    *,
    bot_token: str,
    reply: dict[str, Any],
    channel_reply: dict[str, Any],
    settings: Settings,
    auth: dict[str, Any],
    progress_method: str | None = None,
) -> dict[str, Any] | None:
    if not channel_reply.get("progressMessageId"):
        return None
    dedupe_key = str(channel_reply.get("dedupeKey") or f"telegram:reply:{reply.get('id')}")
    receipt_id = hmac.new(b"atrium-telegram", dedupe_key.encode("utf-8"), hashlib.sha256).hexdigest()[:40]
    existing_receipt = await repo.get_entity("telegram_outbox_receipt", receipt_id)
    if existing_receipt and existing_receipt.get("status") == "sent" and progress_method == "sendMessage":
        try:
            deleted = await _telegram_delete_progress_message(bot_token, channel_reply)
            return {
                "ok": True,
                "status": "deleted_stale_progress",
                "method": "deleteMessage",
                "response": deleted.get("result"),
            }
        except Exception as exc:
            edit_error = f"{type(exc).__name__}: {exc}"
        else:
            edit_error = ""
    else:
        edit_error = ""
    chunks = _split_text(str(reply.get("text") or ""), _telegram_outbound_chunk_chars(settings, auth))
    final_text = chunks[0] if chunks else "(empty reply)"
    edited = await _telegram_edit_message_text(bot_token, channel_reply, final_text)
    receipts: list[dict[str, Any]] = [{"method": "editMessageText", "response": edited.get("result"), "progressFinal": True}]
    target = _telegram_target(channel_reply)
    for chunk in chunks[1:]:
        message_payload = {
            **target,
            "text": chunk,
            "disable_web_page_preview": False,
        }
        message_payload.pop("reply_parameters", None)
        sent = await asyncio.to_thread(_telegram_api_request, bot_token, "sendMessage", message_payload)
        receipts.append({"method": "sendMessage", "response": sent.get("result")})
    return {
        "ok": True,
        "status": "edited_progress_to_final",
        "method": "editMessageText",
        "response": edited.get("result"),
        "receipts": receipts,
        "chunkCount": len(chunks) or 1,
        **({"deleteError": edit_error} if edit_error else {}),
    }


def _telegram_receipt_used_progress_final(receipt: dict[str, Any] | None) -> bool:
    if not isinstance(receipt, dict):
        return False
    result = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
    receipts = result.get("receipts") if isinstance(result.get("receipts"), list) else []
    return any(isinstance(item, dict) and item.get("progressFinal") for item in receipts)


async def _finalize_completed_telegram_progress_from_job(
    repo: Repo,
    *,
    reply: dict[str, Any],
    payload: dict[str, Any],
    settings: Settings,
    auth: dict[str, Any],
    bot_token: str,
) -> dict[str, Any]:
    reply_id = str(reply.get("id") or payload.get("replyMessageId") or "")
    thread_id = str(payload.get("threadId") or reply.get("threadId") or "") or None
    reply_channel = reply.get("channelReply") if isinstance(reply.get("channelReply"), dict) else {}
    payload_channel = payload.get("channelReply") if isinstance(payload.get("channelReply"), dict) else {}
    channel_reply = _merge_telegram_channel_reply(reply_channel, payload_channel)
    progress_record = await repo.get_entity("telegram_progress_message", reply_id)
    if isinstance(progress_record, dict) and progress_record.get("progressMessageId"):
        record_channel = {
            "provider": "telegram",
            "chatId": progress_record.get("chatId"),
            "progressMessageId": str(progress_record["progressMessageId"]),
            "progressUpdatedAt": progress_record.get("updatedAt"),
        }
        channel_reply = _merge_telegram_channel_reply(channel_reply, record_channel)
    if channel_reply.get("provider") != "telegram" or not channel_reply.get("progressMessageId"):
        return {"ok": True, "status": "done"}
    target = _telegram_target(channel_reply)
    if not target.get("chat_id"):
        return {"ok": True, "status": "done", "reason": "missing_target"}
    dedupe_key = str(channel_reply.get("dedupeKey") or f"telegram:reply:{reply.get('id')}")
    receipt_id = hmac.new(b"atrium-telegram", dedupe_key.encode("utf-8"), hashlib.sha256).hexdigest()[:40]
    existing_receipt = await repo.get_entity("telegram_outbox_receipt", receipt_id)
    if _telegram_receipt_used_progress_final(existing_receipt):
        return {"ok": True, "status": "done", "reason": "already_finalized"}
    completed_reply = {
        **reply,
        "channelReply": channel_reply,
    }
    with contextlib.suppress(Exception):
        await repo.update_message(completed_reply)
    with contextlib.suppress(Exception):
        await commit_and_release(repo.s)
    try:
        final_progress = await _repair_completed_telegram_progress_message(
            repo,
            bot_token=bot_token,
            reply=completed_reply,
            channel_reply=channel_reply,
            settings=settings,
            auth=auth,
            progress_method="sendMessage" if existing_receipt and existing_receipt.get("status") == "sent" else "",
        )
    except Exception as exc:
        return {
            "ok": False,
            "status": "completed_finalize_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": True,
        "status": "completed_finalized",
        "finalProgress": final_progress,
    }


def _multipart_body(fields: dict[str, Any], file_field: str, filename: str, data: bytes, mime: str) -> tuple[bytes, str]:
    boundary = "----atriumtelegram" + secrets.token_hex(12)
    parts: list[bytes] = []
    for key, value in fields.items():
        if value is None:
            continue
        encoded = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{encoded}\r\n".encode("utf-8")
        )
    safe_name = safe_filename(filename)
    parts.append(
        (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"{file_field}\"; filename=\"{safe_name}\"\r\n"
            f"Content-Type: {mime or 'application/octet-stream'}\r\n\r\n"
        ).encode("utf-8")
        + data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), boundary


def _telegram_api_multipart(bot_token: str, method: str, fields: dict[str, Any], *, file_field: str, filename: str, data: bytes, mime: str) -> dict[str, Any]:
    timeout = max(1.0, float(getattr(get_settings(), "telegram_gateway_timeout_s", 20.0) or 20.0))
    body, boundary = _multipart_body(fields, file_field, filename, data, mime)
    url = f"https://api.telegram.org/bot{bot_token}/{method.lstrip('/')}"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise _telegram_api_error(method, raw, http_code=exc.code) from exc
    except urllib.error.URLError as exc:
        raise TelegramGatewayError(f"Telegram API {method} failed: {exc}") from exc
    parsed = json.loads(raw)
    if not parsed.get("ok"):
        raise _telegram_api_error(method, raw)
    return parsed


def _attachment_paths(attachments: list[dict[str, Any]], settings: Settings) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    max_bytes = _telegram_max_file_bytes(settings)
    for attachment in attachments:
        uri = str(attachment.get("uri") or "").strip()
        if not uri or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", uri):
            continue
        path = Path(uri)
        if not path.exists() or not path.is_file():
            continue
        size = path.stat().st_size
        if size > max_bytes:
            continue
        out.append({
            "path": path,
            "filename": attachment.get("name") or path.name,
            "mime": attachment.get("mime") or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "sizeBytes": size,
        })
    return out


async def send_telegram_payload(bot_token: str, payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    channel_reply = payload.get("channelReply") if isinstance(payload.get("channelReply"), dict) else {}
    target = _telegram_target(channel_reply)
    if not target.get("chat_id"):
        raise TelegramGatewayError("Telegram outbound target missing chatId")
    receipts: list[dict[str, Any]] = []
    with contextlib.suppress(Exception):
        await asyncio.to_thread(_telegram_api_request, bot_token, "sendChatAction", {**target, "action": "typing"})
    auth = load_telegram_auth(settings)
    chunks = _split_text(str(payload.get("text") or ""), _telegram_outbound_chunk_chars(settings, auth))
    start_idx = 0
    if channel_reply.get("progressMessageId") and chunks:
        try:
            edited = await _telegram_edit_message_text(bot_token, channel_reply, chunks[0])
            receipts.append({"method": "editMessageText", "response": edited.get("result"), "progressFinal": True})
            start_idx = 1
        except Exception as exc:
            receipts.append({
                "method": "editMessageText",
                "error": f"{type(exc).__name__}: {exc}",
                "fallback": "sendMessage",
            })
    for idx, chunk in enumerate(chunks[start_idx:], start=start_idx):
        message_payload = {
            **target,
            "text": chunk,
            "disable_web_page_preview": False,
        }
        if idx > 0 or start_idx > 0:
            message_payload.pop("reply_parameters", None)
        sent = await asyncio.to_thread(_telegram_api_request, bot_token, "sendMessage", message_payload)
        receipts.append({"method": "sendMessage", "response": sent.get("result")})
    for file_item in _attachment_paths(list(payload.get("attachments") or []), settings):
        with contextlib.suppress(Exception):
            await asyncio.to_thread(_telegram_api_request, bot_token, "sendChatAction", {**target, "action": "upload_document"})
        fields = {
            **target,
            "caption": str(file_item["filename"])[:1024],
        }
        sent = await asyncio.to_thread(
            _telegram_api_multipart,
            bot_token,
            "sendDocument",
            fields,
            file_field="document",
            filename=str(file_item["filename"]),
            data=Path(file_item["path"]).read_bytes(),
            mime=str(file_item["mime"]),
        )
        receipts.append({"method": "sendDocument", "response": sent.get("result"), "filename": str(file_item["filename"])})
    if not receipts:
        sent = await asyncio.to_thread(_telegram_api_request, bot_token, "sendMessage", {**target, "text": "(empty reply)"})
        receipts.append({"method": "sendMessage", "response": sent.get("result"), "empty": True})
    return {"ok": True, "receipts": receipts}


async def maybe_deliver_telegram_reply_for_message(
    repo: Repo,
    reply: dict[str, Any],
    *,
    settings: Settings | None = None,
    sender: TelegramSender | None = None,
    allow_retry: bool = True,
) -> dict[str, Any]:
    channel_reply = reply.get("channelReply") if isinstance(reply.get("channelReply"), dict) else None
    if not channel_reply or channel_reply.get("provider") != "telegram":
        return {"ok": True, "status": "skipped", "reason": "not_telegram"}
    if reply.get("pending"):
        return {"ok": True, "status": "skipped", "reason": "reply_pending"}
    with contextlib.suppress(Exception):
        progress_record = await repo.get_entity("telegram_progress_message", str(reply.get("id") or ""))
        if (
            isinstance(progress_record, dict)
            and progress_record.get("progressMessageId")
            and not channel_reply.get("progressMessageId")
        ):
            channel_reply = {
                **channel_reply,
                "progressMessageId": str(progress_record["progressMessageId"]),
                "progressUpdatedAt": progress_record.get("updatedAt"),
            }
    settings = settings or get_settings()
    auth = load_telegram_auth(settings)
    bot_token = str(auth.get("botToken") or "").strip()
    if not bot_token:
        return {"ok": False, "status": "blocked", "reason": "missing_bot_token"}
    dedupe_key = str(channel_reply.get("dedupeKey") or f"telegram:reply:{reply.get('id')}")
    receipt_id = hmac.new(b"atrium-telegram", dedupe_key.encode("utf-8"), hashlib.sha256).hexdigest()[:40]
    existing_receipt = await repo.get_entity("telegram_outbox_receipt", receipt_id)
    if existing_receipt and existing_receipt.get("status") == "sent":
        return {"ok": True, "status": "duplicate", "receiptId": receipt_id}
    attempts = int((existing_receipt or {}).get("attempts") or 0) + 1
    sender = sender or send_telegram_payload
    outbound_payload = {
        "replyId": reply.get("id"),
        "text": str(reply.get("text") or ""),
        "attachments": list(reply.get("attachments") or []),
        "channelReply": channel_reply,
    }
    try:
        sent = await sender(bot_token, outbound_payload, settings)
    except Exception as exc:
        auth = load_telegram_auth(settings)
        max_attempts = max(1, _telegram_outbound_retry_attempts(settings, auth))
        status = "failed"
        next_retry_at = None
        retry_after_s = getattr(exc, "retry_after_s", None)
        if allow_retry and attempts < max_attempts:
            delay_s = retry_after_s if isinstance(retry_after_s, (int, float)) and retry_after_s > 0 else None
            delay_ms = int(max(1.0, delay_s or _telegram_outbound_retry_delay_s(settings, auth)) * 1000)
            next_retry_at = now_ms() + delay_ms
            await repo.enqueue(
                uid("job"),
                "telegram_outbound",
                {
                    "replyMessageId": reply.get("id"),
                    "threadId": reply.get("threadId"),
                    "dedupeKey": dedupe_key,
                },
                next_retry_at,
                priority=2,
            )
            status = "retry_queued"
        receipt = {
            "id": receipt_id,
            "provider": "telegram",
            "status": status,
            "replyMessageId": reply.get("id"),
            "threadId": reply.get("threadId"),
            "dedupeKey": dedupe_key,
            "attempts": attempts,
            "error": f"{type(exc).__name__}: {exc}",
            "nextRetryAt": next_retry_at,
            **({"retryAfterS": retry_after_s} if isinstance(retry_after_s, (int, float)) and retry_after_s > 0 else {}),
            "updatedAt": now_ms(),
        }
        await repo.put_entity("telegram_outbox_receipt", receipt, dept=dept_id_from_thread(str(reply.get("threadId") or "")), status=status, ts=receipt["updatedAt"])
        updated = {
            **reply,
            "channelReply": {
                **channel_reply,
                "status": status,
                "receiptId": receipt_id,
                "attempts": attempts,
                **({"nextRetryAt": next_retry_at} if next_retry_at else {}),
            },
        }
        await repo.update_message(updated)
        return {"ok": False, "status": status, "receiptId": receipt_id, "error": receipt["error"]}
    receipt = {
        "id": receipt_id,
        "provider": "telegram",
        "status": "sent",
        "replyMessageId": reply.get("id"),
        "threadId": reply.get("threadId"),
        "dedupeKey": dedupe_key,
        "attempts": attempts,
        "result": sent,
        "updatedAt": now_ms(),
    }
    await repo.put_entity("telegram_outbox_receipt", receipt, dept=dept_id_from_thread(str(reply.get("threadId") or "")), status="sent", ts=receipt["updatedAt"])
    updated = {
        **reply,
        "channelReply": {
            **channel_reply,
            "status": "sent",
            "receiptId": receipt_id,
            "attempts": attempts,
            "sentAt": receipt["updatedAt"],
        },
    }
    await repo.update_message(updated)
    return {"ok": True, "status": "sent", "receiptId": receipt_id, "receipt": receipt}


async def process_telegram_outbound_job(repo: Repo, payload: dict[str, Any], now: int) -> dict[str, Any]:
    del now
    reply_id = str(payload.get("replyMessageId") or "")
    if not reply_id:
        raise TelegramGatewayError("telegram_outbound missing replyMessageId")
    thread_id = str(payload.get("threadId") or "") or None
    reply = await repo.get_message(reply_id, thread_id=thread_id)
    if not reply:
        raise TelegramGatewayError(f"telegram reply message not found: {reply_id}")
    return await maybe_deliver_telegram_reply_for_message(repo, reply, allow_retry=True)


async def process_telegram_update_job(repo: Repo, payload: dict[str, Any], now: int) -> dict[str, Any]:
    update = payload.get("update") if isinstance(payload.get("update"), dict) else None
    if not update:
        return {"ok": False, "status": "skipped", "reason": "missing_update"}
    try:
        return await handle_telegram_update(
            repo,
            update,
            settings=get_settings(),
            store_file=_REGISTERED_FILE_STORE,
        )
    except Exception as exc:
        update_id = str(update.get("update_id") or uid("telegram_update_error"))
        error = f"{type(exc).__name__}: {exc}"
        await repo.put_entity(
            "telegram_update_error",
            {
                "id": update_id,
                "provider": "telegram",
                "status": "failed",
                "error": error,
                "update": update,
                "updatedAt": now_ms(),
            },
            dept=EXEC_ID,
            status="failed",
            ts=now,
        )
        await repo.add_activity(_activity(
            f"Telegram webhook update {update_id} failed after async ingest: {error}",
            department_id=EXEC_ID,
            severity="warn",
        ))
        return {"ok": False, "status": "failed", "updateId": update_id, "error": error}


async def process_telegram_progress_job(repo: Repo, payload: dict[str, Any], now: int) -> dict[str, Any]:
    reply_id = str(payload.get("replyMessageId") or "")
    if not reply_id:
        return {"ok": False, "status": "skipped", "reason": "missing_reply"}
    thread_id = str(payload.get("threadId") or "") or None
    reply = await repo.get_message(reply_id, thread_id=thread_id)
    if not reply:
        return {"ok": True, "status": "done"}
    settings = get_settings()
    auth = load_telegram_auth(settings)
    bot_token = str(auth.get("botToken") or "").strip()
    if not reply.get("pending"):
        if not bot_token or not _telegram_progress_messages_enabled(auth):
            return {"ok": True, "status": "done"}
        return await _finalize_completed_telegram_progress_from_job(
            repo,
            reply=reply,
            payload=payload,
            settings=settings,
            auth=auth,
            bot_token=bot_token,
        )
    reply_channel = reply.get("channelReply") if isinstance(reply.get("channelReply"), dict) else {}
    payload_channel = payload.get("channelReply") if isinstance(payload.get("channelReply"), dict) else {}
    channel_reply = _merge_telegram_channel_reply(reply_channel, payload_channel)
    if not bot_token or channel_reply.get("provider") != "telegram":
        return {"ok": False, "status": "skipped", "reason": "not_ready"}
    target = _telegram_target(channel_reply)
    if not target.get("chat_id"):
        return {"ok": False, "status": "skipped", "reason": "missing_target"}
    await asyncio.to_thread(_telegram_api_request, bot_token, "sendChatAction", {**target, "action": "typing"})
    progress_status: dict[str, Any] | None = None
    if _telegram_progress_messages_enabled(auth):
        try:
            progress_status = await _send_or_edit_telegram_progress(
                bot_token,
                reply,
                channel_reply,
                settings,
                auth,
            )
            progress_id = progress_status.get("progressMessageId")
            if progress_id:
                channel_reply = {
                    **channel_reply,
                    "progressMessageId": str(progress_id),
                    "progressUpdatedAt": now,
                }
                await repo.put_entity(
                    "telegram_progress_message",
                    {
                        "id": reply_id,
                        "provider": "telegram",
                        "replyMessageId": reply_id,
                        "threadId": thread_id,
                        "chatId": channel_reply.get("chatId"),
                        "progressMessageId": str(progress_id),
                        "updatedAt": now,
                    },
                    dept=dept_id_from_thread(str(thread_id or "")),
                    status="active",
                    ts=now,
                )
                latest_reply = await repo.get_message(reply_id, thread_id=thread_id)
                base_reply = latest_reply if isinstance(latest_reply, dict) else reply
                await repo.update_message({
                    **base_reply,
                    "channelReply": channel_reply,
                })
                with contextlib.suppress(Exception):
                    await commit_and_release(repo.s)
                completed_reply = await repo.get_message(reply_id, thread_id=thread_id)
                if completed_reply and not completed_reply.get("pending"):
                    completed_channel = completed_reply.get("channelReply") if isinstance(completed_reply.get("channelReply"), dict) else {}
                    final_channel = _merge_telegram_channel_reply(channel_reply, completed_channel)
                    final_channel["progressMessageId"] = str(progress_id)
                    completed_reply = {**completed_reply, "channelReply": final_channel}
                    await repo.update_message(completed_reply)
                    final_progress = await _repair_completed_telegram_progress_message(
                        repo,
                        bot_token=bot_token,
                        reply=completed_reply,
                        channel_reply=final_channel,
                        settings=settings,
                        auth=auth,
                        progress_method=str(progress_status.get("method") or ""),
                    )
                    return {
                        "ok": True,
                        "status": "completed_finalized",
                        "progress": progress_status,
                        "finalProgress": final_progress,
                    }
        except Exception as exc:
            progress_status = {
                "ok": False,
                "status": "progress_failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
    expires_at = int(payload.get("expiresAt") or 0)
    if expires_at and now < expires_at:
        interval_ms = int(max(2.0, _telegram_progress_typing_interval_s(auth)) * 1000)
        await repo.enqueue(
            uid("job"),
            "telegram_progress",
            {
                **payload,
                "channelReply": channel_reply,
                "attempt": int(payload.get("attempt") or 0) + 1,
            },
            now + interval_ms,
            priority=3,
        )
        return {
            "ok": True,
            "status": "typing_rescheduled",
            "progress": progress_status,
            "nextRunAt": now + interval_ms,
        }
    return {"ok": True, "status": "expired", "progress": progress_status}


async def run_telegram_polling_loop(
    settings: Settings | None = None,
    *,
    store_file: TelegramFileStore | None = None,
    file_loader: TelegramFileLoader | None = None,
) -> None:
    settings = settings or get_settings()
    offset = 0
    offset_loaded = False
    burst_until_ms = 0
    while True:
        auth = load_telegram_auth(settings)
        now = now_ms()
        interval_s = (
            float(auth.get("pollingBurstIntervalS") or DEFAULT_POLLING_BURST_INTERVAL_S)
            if burst_until_ms and now < burst_until_ms
            else float(auth.get("pollingIntervalS") or DEFAULT_POLLING_INTERVAL_S)
        )
        await asyncio.sleep(0.1)
        try:
            settings = get_settings()
            auth = load_telegram_auth(settings)
            if not bool(auth.get("pollingEnabled")):
                await asyncio.sleep(max(1.0, interval_s))
                continue
            bot_token = str(auth.get("botToken") or "").strip()
            if not bot_token:
                await asyncio.sleep(max(1.0, interval_s))
                continue
            if not offset_loaded:
                with contextlib.suppress(Exception):
                    async with session_scope() as s:
                        state = await Repo(s).get_entity("telegram_polling_state", "default")
                        if state:
                            offset = int(state.get("offset") or 0)
                offset_loaded = True
            payload = {
                "timeout": max(1, min(int(round(interval_s)), 20)),
                "allowed_updates": ["message", "edited_message", "channel_post", "edited_channel_post"],
                **({"offset": offset} if offset else {}),
            }
            result = await asyncio.to_thread(_telegram_api_request, bot_token, "getUpdates", payload)
            updates = result.get("result") if isinstance(result.get("result"), list) else []
            if not updates:
                continue
            burst_window_s = float(auth.get("pollingBurstWindowS") or DEFAULT_POLLING_BURST_WINDOW_S)
            burst_until_ms = now_ms() + int(max(0.0, burst_window_s) * 1000)
            async with session_scope() as s:
                repo = Repo(s)
                for update in updates:
                    if not isinstance(update, dict):
                        continue
                    next_offset = _telegram_next_update_offset(update)
                    try:
                        await handle_telegram_update(repo, update, settings=settings, store_file=store_file, file_loader=file_loader)
                    except Exception as exc:
                        update_id = update.get("update_id")
                        update_error_id = str(update_id) if update_id is not None else uid("telegram_update_error")
                        error_text = f"{type(exc).__name__}: {exc}"
                        error_ts = now_ms()
                        with contextlib.suppress(Exception):
                            await repo.put_entity(
                                "telegram_update_error",
                                {
                                    "id": update_error_id,
                                    "updateId": update_id,
                                    "status": "error",
                                    "error": error_text,
                                    "update": update,
                                    "updatedAt": error_ts,
                                },
                                dept=EXEC_ID,
                                status="error",
                                ts=error_ts,
                            )
                        with contextlib.suppress(Exception):
                            await repo.add_activity(_activity(f"Telegram polling skipped failed update {update_error_id}: {error_text}", department_id=EXEC_ID, severity="warning"))
                    finally:
                        if next_offset is not None:
                            offset = max(offset, next_offset)
                await repo.put_entity(
                    "telegram_polling_state",
                    {"id": "default", "offset": offset, "updatedAt": now_ms()},
                    dept=EXEC_ID,
                    status="running",
                    ts=now_ms(),
                )
                hub.mark_dirty()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                async with session_scope() as s:
                    await Repo(s).put_entity(
                        "telegram_polling_state",
                        {"id": "default", "offset": offset, "status": "error", "error": f"{type(exc).__name__}: {exc}", "updatedAt": now_ms()},
                        dept=EXEC_ID,
                        status="error",
                        ts=now_ms(),
                    )
