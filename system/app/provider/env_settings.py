"""Editable provider environment settings for the owner UI.

The API in this module deliberately edits only a fixed allowlist of keys. It is
not a generic process environment editor.
"""
from __future__ import annotations

import os
import re
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..config import Settings, _read_dotenv_values
from .reference import provider_credential_reference


_ENV_KEY_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _field(
    key: str,
    label: str,
    *,
    kind: str = "text",
    setting: str | None = None,
    aliases: tuple[str, ...] = (),
    placeholder: str = "",
    restart_recommended: bool = False,
    impact: str = "",
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "kind": kind,
        "setting": setting or "",
        "aliases": list(aliases),
        "placeholder": placeholder,
        "restartRecommended": restart_recommended,
        "impact": impact,
    }


def _groups() -> list[dict[str, Any]]:
    return [
        {
            "id": "anthropic",
            "label": "Direct Anthropic",
            "credentialId": "anthropic_direct_key",
            "providerIds": ["anthropic"],
            "fields": [
                _field(
                    "ATRIUM_ANTHROPIC_AUTH_TOKEN",
                    "Bearer token",
                    kind="secret",
                    setting="",
                    placeholder="Direct Anthropic ต้องใช้ ATRIUM_ANTHROPIC_AUTH_TOKEN",
                    impact="มีผลกับ providerId anthropic เท่านั้น",
                ),
                _field(
                    "ATRIUM_ANTHROPIC_BASE_URL",
                    "Base URL",
                    setting="anthropic_base_url",
                    placeholder="https://api.anthropic.com",
                    impact="มีผลกับ providerId anthropic calls ใหม่ทันที",
                ),
            ],
        },
        {
            "id": "openai",
            "label": "OpenAI Platform",
            "credentialId": "openai_platform_key",
            "providerIds": ["openai"],
            "fields": [
                _field(
                    "ATRIUM_OPENAI_API_KEY",
                    "API key",
                    kind="secret",
                    setting="openai_api_key",
                    aliases=("OPENAI_API_KEY",),
                    impact="มีผลกับ providerId openai และ subsystem ที่ใช้ OpenAI Platform key เช่น audio transcription",
                ),
                _field(
                    "ATRIUM_OPENAI_BASE_URL",
                    "Base URL",
                    setting="openai_base_url",
                    aliases=("OPENAI_BASE_URL",),
                    placeholder="https://api.openai.com/v1",
                    impact="มีผลกับ providerId openai และ OpenAI-compatible subsystem calls ใหม่ทันที",
                ),
            ],
        },
        {
            "id": "chatgpt_account",
            "label": "ChatGPT Account",
            "credentialId": "chatgpt_account_oauth",
            "providerIds": ["chatgpt_account"],
            "fields": [
                _field(
                    "ATRIUM_CHATGPT_ACCOUNT_BASE_URL",
                    "Base URL",
                    setting="chatgpt_account_base_url",
                    aliases=("CHATGPT_ACCOUNT_BASE_URL",),
                    placeholder="https://chatgpt.com/backend-api/codex",
                    impact="มีผลกับ providerId chatgpt_account calls ใหม่ทันที",
                ),
                _field(
                    "ATRIUM_CHATGPT_ACCOUNT_OAUTH_STORE",
                    "OAuth store path",
                    setting="chatgpt_account_oauth_store",
                    placeholder="./data/auth/chatgpt-account.json",
                    impact="มีผลกับแหล่ง token ของ ChatGPT OAuth status/provider calls ใหม่ทันที",
                ),
                _field(
                    "ATRIUM_CHATGPT_ACCOUNT_ACCESS_TOKEN",
                    "Access token override",
                    kind="secret",
                    setting="chatgpt_account_access_token",
                    aliases=("CHATGPT_ACCOUNT_ACCESS_TOKEN",),
                    impact="มีผลกับ ChatGPT OAuth env-token override และ providerId chatgpt_account calls ใหม่ทันที",
                ),
                _field(
                    "ATRIUM_CHATGPT_ACCOUNT_REFRESH_TOKEN",
                    "Refresh token override",
                    kind="secret",
                    setting="chatgpt_account_refresh_token",
                    aliases=("CHATGPT_ACCOUNT_REFRESH_TOKEN",),
                    impact="มีผลกับ ChatGPT OAuth refresh และ providerId chatgpt_account calls ใหม่ทันที",
                ),
                _field(
                    "ATRIUM_CHATGPT_ACCOUNT_EXPIRES_AT",
                    "Token expires at",
                    setting="chatgpt_account_expires_at",
                    aliases=("CHATGPT_ACCOUNT_EXPIRES_AT",),
                    impact="มีผลกับการประเมินอายุ token ของ ChatGPT OAuth ทันที",
                ),
            ],
        },
        {
            "id": "claude_code",
            "label": "Claude Code Account",
            "credentialId": "claude_code_account",
            "providerIds": ["claude_code"],
            "fields": [
                _field(
                    "ATRIUM_CLAUDE_CODE_COMMAND",
                    "Command",
                    setting="claude_code_command",
                    aliases=("CLAUDE_CODE_COMMAND",),
                    placeholder="claude",
                    impact="มีผลกับ providerId claude_code calls ใหม่ทันที",
                ),
                _field(
                    "ATRIUM_CLAUDE_CODE_TIMEOUT_S",
                    "Timeout seconds",
                    kind="number",
                    setting="claude_code_timeout_s",
                    aliases=("CLAUDE_CODE_TIMEOUT_S",),
                    placeholder="300",
                    impact="มีผลกับ timeout ของ providerId claude_code calls ใหม่ทันที",
                ),
            ],
        },
        {
            "id": "audio_transcription",
            "label": "Audio transcription",
            "credentialId": "openai_platform_key",
            "providerIds": ["openai"],
            "subsystemId": "audio_transcription",
            "fields": [
                _field(
                    "ATRIUM_AUDIO_TRANSCRIPTION_ENABLED",
                    "Enabled",
                    kind="boolean",
                    setting="audio_transcription_enabled",
                    placeholder="true",
                    impact="มีผลกับ audio upload/transcription ใหม่ทันที",
                ),
                _field(
                    "ATRIUM_AUDIO_TRANSCRIPTION_AUTO_ON_UPLOAD",
                    "Auto on upload",
                    kind="boolean",
                    setting="audio_transcription_auto_on_upload",
                    placeholder="true",
                    impact="มีผลกับ audio uploads ใหม่ทันที",
                ),
                _field(
                    "ATRIUM_AUDIO_TRANSCRIPTION_MODEL",
                    "Model",
                    setting="audio_transcription_model",
                    placeholder="gpt-4o-transcribe",
                    impact="มีผลกับ audio transcription jobs ใหม่ทันที",
                ),
                _field(
                    "ATRIUM_AUDIO_TRANSCRIPTION_BASE_URL",
                    "Base URL override",
                    setting="audio_transcription_base_url",
                    aliases=("OPENAI_AUDIO_BASE_URL",),
                    impact="มีผลกับ audio transcription endpoint ใหม่ทันที",
                ),
                _field(
                    "ATRIUM_AUDIO_TRANSCRIPTION_TIMEOUT_S",
                    "Timeout seconds",
                    kind="number",
                    setting="audio_transcription_timeout_s",
                    placeholder="120",
                    impact="มีผลกับ timeout ของ audio transcription ใหม่ทันที",
                ),
                _field(
                    "ATRIUM_AUDIO_TRANSCRIPTION_MAX_BYTES",
                    "Max bytes",
                    kind="number",
                    setting="audio_transcription_max_bytes",
                    placeholder="20971520",
                    impact="มีผลกับขนาด audio upload ที่ส่ง transcription ใหม่ทันที",
                ),
                _field(
                    "ATRIUM_AUDIO_TRANSCRIPTION_AUTH_ORDER",
                    "Auth order",
                    setting="audio_transcription_auth_order",
                    placeholder="api_key",
                    impact="มีผลกับลำดับ credential ของ audio transcription ใหม่ทันที",
                ),
                _field(
                    "ATRIUM_AUDIO_TRANSCRIPTION_LANGUAGE",
                    "Language",
                    setting="audio_transcription_language",
                    placeholder="th",
                    impact="มีผลกับ audio transcription jobs ใหม่ทันที",
                ),
                _field(
                    "ATRIUM_AUDIO_TRANSCRIPTION_PROMPT",
                    "Prompt",
                    setting="audio_transcription_prompt",
                    impact="มีผลกับ audio transcription jobs ใหม่ทันที",
                ),
            ],
        },
        {
            "id": "image_generation",
            "label": "Image generation",
            "credentialId": "image_generation_key",
            "subsystemId": "image_generation",
            "fields": [
                _field(
                    "ATRIUM_IMAGE_GENERATION_API_KEY",
                    "API key",
                    kind="secret",
                    setting="image_generation_api_key",
                    aliases=("IMAGE_GENERATION_API_KEY",),
                    impact="มีผลกับ image generation jobs ใหม่ทันที",
                ),
                _field(
                    "ATRIUM_IMAGE_GENERATION_BASE_URL",
                    "Base URL",
                    setting="image_generation_base_url",
                    aliases=("IMAGE_GENERATION_BASE_URL",),
                    placeholder="https://api.openai.com/v1",
                    impact="มีผลกับ image generation jobs ใหม่ทันที",
                ),
                _field(
                    "ATRIUM_IMAGE_GENERATION_TIMEOUT_S",
                    "Timeout seconds",
                    kind="number",
                    setting="image_generation_timeout_s",
                    placeholder="1800",
                    impact="มีผลกับ timeout ของ image generation jobs ใหม่ทันที",
                ),
                _field(
                    "ATRIUM_IMAGE_GENERATION_WORKER_CONCURRENCY",
                    "Worker concurrency",
                    kind="number",
                    setting="image_generation_worker_concurrency",
                    placeholder="3",
                    impact="มีผลกับจำนวน image generation jobs ที่ worker รับในรอบถัดไป",
                ),
            ],
        },
    ]


def _all_fields() -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    for group in _groups():
        for field in group["fields"]:
            fields[field["key"]] = field
    return fields


def provider_env_allowed_keys() -> list[str]:
    """Return the allowlisted canonical provider env keys."""
    return sorted(_all_fields())


def _line_key(raw: str) -> str | None:
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    match = _ENV_KEY_RE.match(line)
    return match.group(1) if match else None


def _needs_quotes(value: str) -> bool:
    if value == "":
        return False
    return bool(re.search(r"\s|#|=|\"|'", value))


def _format_env_value(value: str) -> str:
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError("env values cannot contain null bytes or newlines")
    if not _needs_quotes(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _format_env_line(key: str, value: str) -> str:
    return f"{key}={_format_env_value(value)}"


def _mask_secret(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


def _field_keys(field: dict[str, Any]) -> list[str]:
    return [str(field["key"]), *[str(alias) for alias in field.get("aliases") or []]]


def _first_value(values: dict[str, str], keys: list[str]) -> tuple[str, str] | tuple[None, str]:
    for key in keys:
        value = values.get(key)
        if value is not None and str(value).strip() != "":
            return key, str(value)
    return None, ""


def _active_value(settings: Settings, field: dict[str, Any]) -> str:
    attr = str(field.get("setting") or "")
    if not attr:
        return ""
    value = getattr(settings, attr, "")
    return "" if value is None else str(value)


def _field_state(settings: Settings, dotenv: dict[str, str], field: dict[str, Any]) -> dict[str, Any]:
    keys = _field_keys(field)
    source_key, dotenv_value = _first_value(dotenv, keys)
    process_key, process_value = _first_value(os.environ, keys)
    active_value = process_value or dotenv_value or _active_value(settings, field)
    kind = str(field.get("kind") or "text")
    has_dotenv_value = bool(source_key and dotenv_value)
    has_process_value = bool(process_key and process_value)
    source = "process" if has_process_value else "dotenv" if has_dotenv_value else "default" if active_value else "empty"

    out = {
        **field,
        "configured": bool(active_value),
        "source": source,
        "sourceKey": source_key or process_key or "",
        "processOverride": bool(process_key and process_key == field["key"]),
    }
    if kind == "secret":
        out["value"] = ""
        out["maskedValue"] = _mask_secret(active_value)
        out["valueRedacted"] = bool(active_value)
    else:
        out["value"] = active_value
        out["maskedValue"] = ""
    return out


def provider_env_settings(settings: Settings, *, env_path: Path | None = None) -> dict[str, Any]:
    env_path = env_path or Path(".env")
    dotenv = _read_dotenv_values(env_path)
    groups: list[dict[str, Any]] = []
    for group in _groups():
        fields = [_field_state(settings, dotenv, field) for field in group["fields"]]
        groups.append({**group, "fields": fields})

    process_overrides: list[str] = []
    for field in _all_fields().values():
        if field["key"] in os.environ:
            process_overrides.append(field["key"])

    return {
        "version": 1,
        "envPath": str(env_path.resolve()),
        "groups": groups,
        "reference": provider_credential_reference(settings),
        "processOverrides": sorted(process_overrides),
        "restartRecommended": any(
            bool(field.get("restartRecommended")) for group in groups for field in group["fields"]
        ),
    }


def update_provider_env_settings(
    updates: list[dict[str, Any]],
    *,
    env_path: Path | None = None,
    apply_to_process: bool = False,
) -> dict[str, Any]:
    env_path = env_path or Path(".env")
    allowed = _all_fields()
    parsed_updates: list[tuple[str, str, bool, list[str], bool]] = []
    secret_changed = False
    for raw in updates:
        key = str((raw or {}).get("key") or "").strip()
        if key not in allowed:
            raise ValueError(f"unsupported provider env key: {key}")
        field = allowed[key]
        unset = bool((raw or {}).get("unset"))
        value = "" if (raw or {}).get("value") is None else str((raw or {}).get("value"))
        if len(value) > 20_000:
            raise ValueError(f"env value is too long: {key}")
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError(f"env value cannot contain null bytes or newlines: {key}")
        if str(field.get("kind") or "") == "secret" and (unset or value):
            secret_changed = True
        parsed_updates.append((key, value, unset, _field_keys(field), bool(field.get("restartRecommended"))))

    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    changed_keys: list[str] = []
    unset_keys: list[str] = []
    restart_recommended = False
    for key, value, unset, keys_for_field, field_restart in parsed_updates:
        restart_recommended = restart_recommended or field_restart
        keys_to_remove = set(keys_for_field)
        replacement = None if unset else _format_env_line(key, value)
        found = False
        next_lines: list[str] = []
        for line in lines:
            line_key = _line_key(line)
            if line_key in keys_to_remove:
                found = True
                if replacement is not None and line_key == key:
                    next_lines.append(replacement)
                    replacement = None
                continue
            next_lines.append(line)
        lines = next_lines
        if replacement is not None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(replacement)
        if unset:
            unset_keys.append(key)
        elif found or value:
            changed_keys.append(key)

    env_path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    if text:
        text += "\n"
    env_path.write_text(text, encoding="utf-8")
    if secret_changed:
        with suppress(OSError):
            env_path.chmod(0o600)

    runtime_applied_keys: list[str] = []
    runtime_unset_keys: list[str] = []
    if apply_to_process:
        for key, value, unset, keys_for_field, _field_restart in parsed_updates:
            if unset:
                for item_key in keys_for_field:
                    if item_key in os.environ:
                        os.environ.pop(item_key, None)
                        runtime_unset_keys.append(item_key)
                continue
            for item_key in keys_for_field:
                if item_key != key and item_key in os.environ:
                    os.environ.pop(item_key, None)
                    runtime_unset_keys.append(item_key)
            os.environ[key] = value
            runtime_applied_keys.append(key)

    return {
        "updatedKeys": sorted(set(changed_keys)),
        "unsetKeys": sorted(set(unset_keys)),
        "runtimeAppliedKeys": sorted(set(runtime_applied_keys)),
        "runtimeUnsetKeys": sorted(set(runtime_unset_keys)),
        "restartRecommended": restart_recommended,
    }
