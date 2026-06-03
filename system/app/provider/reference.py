"""Provider and credential meaning reference for agents and operators."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..catalog import MODELS, PROVIDERS, default_model_for_provider, direct_anthropic_configured
from ..config import Settings, _read_dotenv_values, get_settings


def _env_or_dotenv(key: str) -> str:
    value = os.environ.get(key)
    if value and value.strip():
        return value.strip()
    return (_read_dotenv_values(Path(".env")).get(key) or "").strip()


def _has_direct_anthropic_token(settings: Settings) -> bool:
    return bool(_env_or_dotenv("ATRIUM_ANTHROPIC_AUTH_TOKEN")) or bool(
        settings.anthropic_auth_token and direct_anthropic_configured()
    )


def _models_for(provider_id: str) -> list[str]:
    return [str(model["id"]) for model in MODELS.values() if provider_id in model.get("providerIds", [])]


def _credential_ref(
    credential_id: str,
    *,
    label: str,
    kind: str,
    env: list[str],
    used_by: list[str],
    enables: list[str],
    notes: list[str] | None = None,
    configured: bool | None = None,
) -> dict[str, Any]:
    out = {
        "id": credential_id,
        "label": label,
        "kind": kind,
        "env": env,
        "usedBy": used_by,
        "enables": enables,
        "notes": notes or [],
    }
    if configured is not None:
        out["configured"] = configured
    return out


def _provider_ref(
    provider_id: str,
    *,
    credential_id: str,
    route_type: str,
    chat: bool = True,
    subsystems: list[str] | None = None,
    preferred_when: str = "",
    cautions: list[str] | None = None,
    configured: bool | None = None,
) -> dict[str, Any]:
    provider = PROVIDERS[provider_id]
    out = {
        "id": provider_id,
        "label": provider.get("label"),
        "shortLabel": provider.get("shortLabel"),
        "routeType": route_type,
        "wireApi": provider.get("wireApi") or ("anthropic-compatible" if provider_id == "anthropic" else None),
        "baseUrl": provider.get("baseUrl"),
        "baseUrlEnv": provider.get("baseUrlEnv"),
        "credentialId": credential_id,
        "credentialHint": provider.get("authTokenEnv"),
        "supportsChat": chat,
        "subsystems": subsystems or [],
        "defaultModel": default_model_for_provider(provider_id),
        "models": _models_for(provider_id),
        "purpose": provider.get("purpose"),
        "blurb": provider.get("blurb"),
        "preferredWhen": preferred_when,
        "cautions": cautions or [],
    }
    if configured is not None:
        out["configured"] = configured
    return out


def provider_credential_reference(settings: Settings | None = None) -> dict[str, Any]:
    """Return a secret-free reference that agents can read before provider work."""
    settings = settings or get_settings()
    chatgpt_ready = False
    claude_ready: bool | None = False
    audio_status: dict[str, Any] = {}
    with_status_errors: list[str] = []
    try:
        from .chatgpt_oauth import chatgpt_oauth_status

        chatgpt_ready = bool(chatgpt_oauth_status(settings).get("ready"))
    except Exception as exc:
        with_status_errors.append(f"chatgpt_account status unavailable: {type(exc).__name__}")
    try:
        from .claude_code_provider import claude_code_auth_status

        claude_status = claude_code_auth_status(settings.claude_code_command)
        claude_ready_value = claude_status.get("ready")
        claude_ready = claude_ready_value if isinstance(claude_ready_value, bool) else None
        if claude_ready is None:
            with_status_errors.append(f"claude_code status unknown: {claude_status.get('status') or 'probe unavailable'}")
    except Exception as exc:
        with_status_errors.append(f"claude_code status unavailable: {type(exc).__name__}")
    try:
        from ..audio_transcription import audio_transcription_status

        audio_status = audio_transcription_status(settings)
    except Exception as exc:
        with_status_errors.append(f"audio transcription status unavailable: {type(exc).__name__}")

    has_direct_anthropic = _has_direct_anthropic_token(settings)
    has_openai = bool(settings.openai_api_key)

    credentials = [
        _credential_ref(
            "anthropic_direct_key",
            label="Direct Anthropic API token",
            kind="api_key",
            env=["ATRIUM_ANTHROPIC_AUTH_TOKEN", "ATRIUM_ANTHROPIC_BASE_URL"],
            used_by=["anthropic"],
            enables=["Claude chat/provider calls through direct Anthropic Messages API"],
            notes=["Direct Anthropic is intentionally app-scoped."],
            configured=has_direct_anthropic,
        ),
        _credential_ref(
            "openai_platform_key",
            label="OpenAI Platform API key",
            kind="api_key",
            env=["ATRIUM_OPENAI_API_KEY", "OPENAI_API_KEY (system/.env only)"],
            used_by=["openai", "audio_transcription", "future_openai_subsystems"],
            enables=[
                "GPT chat/provider calls through OpenAI Responses API",
                "OpenAI subsystem calls such as audio transcription",
                "Future OpenAI subsystem fallbacks such as TTS, realtime, embeddings, or file/media processing when wired",
            ],
            notes=[
                "This is not ChatGPT OAuth and does not represent a logged-in ChatGPT subscription.",
                "Use ATRIUM_OPENAI_BASE_URL for compatible OpenAI Platform endpoints.",
                "Subsystems may have their own enablement flags and models.",
            ],
            configured=has_openai,
        ),
        _credential_ref(
            "image_generation_key",
            label="OpenAI-compatible image generation key",
            kind="api_key",
            env=["ATRIUM_IMAGE_GENERATION_API_KEY", "IMAGE_GENERATION_API_KEY"],
            used_by=["image_generation"],
            enables=["Durable image artifact generation through the configured OpenAI-compatible Images API"],
            notes=[
                "This is a subsystem credential, not a chat providerId.",
                "ATRIUM_IMAGE_GENERATION_BASE_URL controls the image endpoint; the default is OpenAI-compatible.",
                "Do not assume it is the same credential as openai_platform_key.",
            ],
            configured=bool(settings.image_generation_api_key),
        ),
        _credential_ref(
            "chatgpt_account_oauth",
            label="ChatGPT account OAuth",
            kind="oauth_account",
            env=[
                "system/data/auth/chatgpt-account.json",
                "ATRIUM_CHATGPT_ACCOUNT_ACCESS_TOKEN",
                "ATRIUM_CHATGPT_ACCOUNT_REFRESH_TOKEN",
                "ATRIUM_CHATGPT_ACCOUNT_EXPIRES_AT",
            ],
            used_by=["chatgpt_account"],
            enables=["GPT chat/provider calls through the ChatGPT Codex account backend"],
            notes=[
                "Account/subscription route. Do not treat it as an OpenAI Platform API key.",
                "OAuth tokens must stay out of logs; the default file is app-scoped and should be 0600.",
            ],
            configured=chatgpt_ready,
        ),
        _credential_ref(
            "claude_code_account",
            label="Claude Code account",
            kind="local_cli_account",
            env=["ATRIUM_CLAUDE_CODE_COMMAND", "ATRIUM_CLAUDE_CODE_TIMEOUT_S"],
            used_by=["claude_code"],
            enables=["Claude chat/provider calls through local Claude Code CLI account/subscription"],
            notes=[
                "This is not an Anthropic Messages API token.",
                "ATRIUM runs claude -p and parses Claude Code JSON/tool output into native ATRIUM tool calls.",
            ],
            configured=claude_ready,
        ),
    ]

    providers = [
        _provider_ref(
            "claude_code",
            credential_id="claude_code_account",
            route_type="account_cli",
            preferred_when="Prefer for Claude account/subscription work when connected, especially agentic/tool-heavy Claude tasks.",
            cautions=["Requires the local claude CLI to be installed and logged in."],
            configured=claude_ready,
        ),
        _provider_ref(
            "chatgpt_account",
            credential_id="chatgpt_account_oauth",
            route_type="account_oauth",
            preferred_when="Prefer for GPT account/subscription work when ChatGPT OAuth is connected and OpenAI Platform billing should not be used.",
            cautions=["Not the same as ATRIUM_OPENAI_API_KEY; public OpenAI subsystem calls may not accept this token."],
            configured=chatgpt_ready,
        ),
        _provider_ref(
            "openai",
            credential_id="openai_platform_key",
            route_type="platform_api_key",
            subsystems=["audio_transcription"],
            preferred_when="Use when the owner wants OpenAI Platform API-key billing or when OpenAI subsystems are needed alongside chat.",
            cautions=["This route spends OpenAI Platform API quota; do not confuse it with ChatGPT subscription/OAuth."],
            configured=has_openai,
        ),
        _provider_ref(
            "anthropic",
            credential_id="anthropic_direct_key",
            route_type="provider_api_key",
            preferred_when="Use only when direct Anthropic is explicitly configured for comparison or production.",
            cautions=["Requires ATRIUM_ANTHROPIC_AUTH_TOKEN; generic ANTHROPIC_AUTH_TOKEN is not used by ATRIUM."],
            configured=has_direct_anthropic,
        ),
    ]

    recommendation_order = ["claude_code", "chatgpt_account", "openai", "anthropic"]
    connected_recommendations = [provider["id"] for provider in providers if provider.get("configured")]
    connected_recommendations.sort(key=lambda provider_id: recommendation_order.index(provider_id))

    return {
        "version": 1,
        "purpose": "Secret-free reference for choosing providerId and understanding credentials in ATRIUM.",
        "agentUsage": {
            "when": [
                "Before creating departments or org plans that require providerId",
                "Before changing provider/model settings",
                "When a provider or credential error appears",
                "When selecting OpenAI Platform API key versus ChatGPT OAuth versus direct Anthropic",
            ],
            "readViaApi": "GET /api/provider-auth/reference",
            "statusViaApi": "GET /api/provider-auth/status",
            "editEnvViaApi": "GET/PATCH /api/provider-auth/env",
            "editEnvViaChatTool": "update_provider_env_settings",
            "rules": [
                "Always specify providerId explicitly when creating a department.",
                "Prefer connected account providers first: claude_code, then chatgpt_account.",
                "Use openai when OpenAI Platform API-key chat or OpenAI subsystems are intended.",
                "Provider .env edits must use the update_provider_env_settings chat tool or allowlisted provider-auth env API, not arbitrary env writes.",
                "Before a chat-based provider .env edit, ask for explicit approval in normal chat unless the current user message already directly asks for that exact set/unset.",
                "Provider .env edits are applied to the current backend process and settings/provider caches immediately.",
                "GET /api/provider-auth/env reports configured/masked secret state only; it must not echo raw credential values.",
                "Never paste or log credential values.",
            ],
        },
        "providers": providers,
        "credentials": credentials,
        "subsystems": [
            {
                "id": "audio_transcription",
                "label": "Audio transcription",
                "primaryCredentialId": "openai_platform_key",
                "fallbackCredentialIds": ["chatgpt_account_oauth"],
                "status": audio_status,
                "routes": ["GET /api/audio/status", "POST /api/audio/transcribe", "POST /api/attachments/upload"],
                "notes": [
                    "Audio uploads can become transcript previews and knowledge context.",
                    "OAuth fallback is opt-in through ATRIUM_AUDIO_TRANSCRIPTION_AUTH_ORDER for routes known to accept it.",
                ],
            },
            {
                "id": "image_generation",
                "label": "Image generation",
                "primaryCredentialId": "image_generation_key",
                "routes": ["generate_image_asset chat tool", "image.generate owner tool"],
                "notes": [
                    "Image generation currently has its own settings/key path and is not automatically the same as chat providerId.",
                    "Prefer gpt-image-2 unless the user approves/failure policy allows another GPT image model.",
                ],
            },
        ],
        "recommendedConnectedProviderIds": connected_recommendations,
        "statusWarnings": with_status_errors,
    }
