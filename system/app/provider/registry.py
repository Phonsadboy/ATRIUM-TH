"""Resolve a provider id to a concrete live backend.

ATRIUM no longer has an offline LLM provider. Chat and engine work
must use a configured live provider, and provider failures are
reported as real runtime failures.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..catalog import PROVIDERS, direct_anthropic_configured
from ..config import Settings, _read_dotenv_values, get_settings
from .anthropic_provider import AnthropicProvider
from .base import LLMProvider
from .chatgpt_oauth import ChatGPTAccountResponsesProvider, ChatGPTCodexOAuthTokenProvider, chatgpt_oauth_status
from .claude_code_provider import ClaudeCodeCliProvider, claude_code_auth_status
from .openai_provider import OpenAIResponsesProvider

_live_cache: dict[tuple[str, str, str, float | None], LLMProvider] = {}


def _env_or_dotenv(key: str) -> str:
    value = os.environ.get(key)
    if value and value.strip():
        return value.strip()
    return (_read_dotenv_values(Path(".env")).get(key) or "").strip()


def _direct_anthropic_auth_token(settings: Settings) -> str:
    token = _env_or_dotenv("ATRIUM_ANTHROPIC_AUTH_TOKEN")
    if token:
        return token
    return settings.anthropic_auth_token if direct_anthropic_configured() else ""


def get_provider(provider_id: str, settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    if provider_id == "openai":
        if not settings.openai_api_key:
            raise RuntimeError(
                "OpenAI Platform API key is required; set ATRIUM_OPENAI_API_KEY in system/.env."
            )
        base_url = settings.openai_base_url
        timeout = settings.provider_request_timeout_s
        key = (provider_id, base_url, settings.openai_api_key, timeout)
        if key in _live_cache:
            return _live_cache[key]
        provider = OpenAIResponsesProvider(provider_id, base_url, settings.openai_api_key, timeout)
        _live_cache[key] = provider
        return provider

    if provider_id == "chatgpt_account":
        status = chatgpt_oauth_status(settings)
        if not status.get("ready"):
            raise RuntimeError(
                "ChatGPT account OAuth is required; run "
                "`uv --project system run python ops/chatgpt_account_oauth_login.py` "
                "from the repo root."
            )
        base_url = settings.chatgpt_account_base_url
        timeout = settings.provider_request_timeout_s
        store_key = str(status.get("storePath") or "")
        token_source = str(status.get("source") or "")
        key = (provider_id, base_url, f"{token_source}:{store_key}", timeout)
        if key in _live_cache:
            return _live_cache[key]
        provider = ChatGPTAccountResponsesProvider(
            provider_id,
            base_url,
            ChatGPTCodexOAuthTokenProvider(settings),
            timeout,
        )
        _live_cache[key] = provider
        return provider

    if provider_id == "claude_code":
        timeout = settings.claude_code_timeout_s or settings.provider_request_timeout_s
        key = (provider_id, settings.claude_code_command, "", timeout)
        if key in _live_cache:
            return _live_cache[key]
        provider = ClaudeCodeCliProvider(provider_id, settings.claude_code_command, timeout)
        _live_cache[key] = provider
        return provider

    if provider_id == "anthropic":
        auth_token = _direct_anthropic_auth_token(settings)
        if not auth_token:
            raise RuntimeError(
                "Direct Anthropic API token is required; set "
                "ATRIUM_ANTHROPIC_AUTH_TOKEN in system/.env."
            )
        base_url = settings.anthropic_base_url
    else:
        allowed = ", ".join(sorted(PROVIDERS))
        raise RuntimeError(f"Unknown provider '{provider_id}'. Supported providerIds: {allowed}")
    timeout = settings.provider_request_timeout_s
    key = (provider_id, base_url, auth_token, timeout)
    if key in _live_cache:
        return _live_cache[key]
    provider = AnthropicProvider(provider_id, base_url, auth_token, timeout)
    _live_cache[key] = provider
    return provider


def _claude_code_light_status(command: str) -> dict:
    from .claude_code_provider import claude_code_cached_auth_status

    resolved = shutil.which(command) if "/" not in command else command if Path(command).exists() else None
    cached = claude_code_cached_auth_status(command)
    if cached:
        return cached
    return {
        "ready": None,
        "command": command,
        "installed": bool(resolved),
        "loggedIn": None,
        "authMethod": None,
        "apiProvider": None,
        "subscriptionType": None,
        "email": None,
        "status": "not_probed" if resolved else "missing",
        "state": "not_probed" if resolved else "missing",
        "fresh": False,
        "stale": False,
        "probeFailed": False,
    }


def provider_health(settings: Settings | None = None, *, probe_accounts: bool = True) -> dict:
    settings = settings or get_settings()
    has_direct_anthropic_token = bool(_direct_anthropic_auth_token(settings))
    has_openai_key = bool(settings.openai_api_key)
    chatgpt_status = chatgpt_oauth_status(settings)
    has_chatgpt_account_oauth = bool(chatgpt_status.get("ready"))
    claude_code_status = (
        claude_code_auth_status(settings.claude_code_command)
        if probe_accounts
        else _claude_code_light_status(settings.claude_code_command)
    )
    has_claude_code_cli = bool(claude_code_status.get("installed"))
    claude_code_ready = claude_code_status.get("ready")
    has_claude_code_account = claude_code_ready if isinstance(claude_code_ready, bool) else None
    ready = (
        has_direct_anthropic_token
        or has_openai_key
        or has_chatgpt_account_oauth
        or claude_code_ready is True
    )
    return {
        "mode": "live" if ready else "unconfigured",
        "ready": ready,
        "embeddings": (
            f"ollama:{settings.ollama_embedding_model}"
            if settings.prefer_ollama_embeddings
            else "voyage" if settings.live_embeddings else "hash"
        ),
        "openAIBaseUrl": settings.openai_base_url,
        "chatgptAccountBaseUrl": settings.chatgpt_account_base_url,
        "anthropicBaseUrl": settings.anthropic_base_url,
        "requestTimeoutS": settings.provider_request_timeout_s,
        "hasToken": has_direct_anthropic_token,
        "hasDirectAnthropicToken": has_direct_anthropic_token,
        "hasOpenAIKey": has_openai_key,
        "hasChatGPTAccountOAuth": has_chatgpt_account_oauth,
        "chatgptAccountOAuth": chatgpt_status,
        "hasClaudeCodeCli": has_claude_code_cli,
        "hasClaudeCodeAccount": has_claude_code_account,
        "claudeCodeAuth": claude_code_status,
        "claudeCodeCommand": settings.claude_code_command,
        "recoveryPolicy": provider_recovery_policy(settings),
    }


def provider_recovery_policy(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    return {
        "visibilityOnly": True,
        "hardCircuitBreaker": False,
        "engineCircuitBreaker": {
            "enabled": False,
            "reason": "Full Autonomy keeps provider calls available; failures surface as retryable errors or queued job recovery, not a global provider stop gate.",
        },
        "requestTimeoutS": settings.provider_request_timeout_s,
        "retryLayers": {
            "anthropicSdk": {
                "maxRetries": 2,
                "scope": "Anthropic SDK client",
            },
            "openaiResponses": {
                "attempts": 2,
                "retryableHttpStatuses": [408, 409, 425, 429, 500, 501, 502, 503, 504],
                "retryableTransportErrors": ["TimeoutException", "TransportError"],
                "backoff": "exponential+jitter",
                "honorsRetryAfter": True,
            },
            "chatgptAccountResponses": {
                "attempts": 2,
                "backoff": "exponential+jitter",
                "scope": "ChatGPT account Responses transport",
            },
        },
        "resume": {
            "manualMessageRetryRoute": "/api/messages/{thread_id}/retry",
            "nonChatJobTimeoutRecovery": "requeue",
            "runtimeDegradedRetry": True,
            "imageWorkerRetryRequeue": True,
        },
    }


def reset_providers() -> None:
    _live_cache.clear()
