"""Provider / model / thinking-effort catalog.

This is the backend mirror of the UI's `contract/models.ts`. It is the single
source of truth for *capability validation*: which models a provider offers,
which thinking efforts a model supports, and how to coerce an out-of-range
choice back into something valid. The UI does the same coercion client-side;
the backend re-validates so a hand-rolled API call can't create an invalid
department.

Pricing (USD / Mtok) is used for local cost accounting; real usage is billed by
the configured provider. Mirrors models.ts.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

AiProviderId = Literal["anthropic", "openai", "chatgpt_account", "claude_code"]
ModelId = Literal[
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "gpt-5.5",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
]
ThinkingEffort = Literal["off", "low", "medium", "high", "xhigh", "max"]
ModelSpeed = Literal["standard", "fast"]

PROVIDERS: dict[str, dict] = {
    "claude_code": {
        "id": "claude_code",
        "label": "Claude Code Account",
        "shortLabel": "Claude Code",
        "purpose": "ใช้บัญชี Claude ผ่าน Claude Code CLI",
        "baseUrl": "local claude CLI",
        "baseUrlEnv": "ATRIUM_CLAUDE_CODE_COMMAND",
        "authTokenEnv": "Claude Code OAuth / setup-token",
        "wireApi": "claude-code-cli",
        "blurb": "ช่องทาง subscription account; ไม่ใช่ Anthropic Messages API token",
    },
    "chatgpt_account": {
        "id": "chatgpt_account",
        "label": "ChatGPT Account",
        "shortLabel": "ChatGPT",
        "purpose": "ใช้บัญชี ChatGPT ผ่าน OAuth แบบ Codex",
        "baseUrl": "https://chatgpt.com/backend-api/codex",
        "baseUrlEnv": "ATRIUM_CHATGPT_ACCOUNT_BASE_URL",
        "authTokenEnv": "data/auth/chatgpt-account.json",
        "wireApi": "chatgpt-codex-responses",
        "blurb": "ช่องทาง account/subscription แยกจาก OpenAI API key",
    },
    "openai": {
        "id": "openai",
        "label": "OpenAI Platform",
        "shortLabel": "OpenAI",
        "purpose": "ใช้ OpenAI API key สำหรับแชทผ่าน Responses API และ subsystem ของ ATRIUM",
        "baseUrl": "https://api.openai.com/v1",
        "baseUrlEnv": "ATRIUM_OPENAI_BASE_URL",
        "authTokenEnv": "ATRIUM_OPENAI_API_KEY",
        "wireApi": "responses",
        "blurb": "Platform API key แยกจาก ChatGPT OAuth; ใช้ได้ทั้ง provider route และ subsystem เช่น audio/transcription",
    },
    "anthropic": {
        "id": "anthropic",
        "label": "Claude AI",
        "shortLabel": "Claude",
        "purpose": "ผู้ให้บริการตรงจาก Anthropic",
        "baseUrl": "https://api.anthropic.com",
        "baseUrlEnv": "ATRIUM_ANTHROPIC_BASE_URL",
        "authTokenEnv": "ATRIUM_ANTHROPIC_AUTH_TOKEN",
        "blurb": "ใช้สำหรับเทียบพฤติกรรมกับ Claude API ตรง",
    },
}

DIRECT_PROVIDER_IDS: set[str] = {"anthropic", "openai", "chatgpt_account", "claude_code"}

# Direct provider routes own native chat/tool/streaming paths, so no external
# agent runtime should gate provider availability.
ACCOUNT_RUNTIME_BYPASS_PROVIDER_IDS: set[str] = set(DIRECT_PROVIDER_IDS)
NATIVE_CHAT_STREAM_BYPASS_PROVIDER_IDS: set[str] = set(DIRECT_PROVIDER_IDS)


def provider_bypasses_agent_runtime(provider_id: str | None) -> bool:
    return str(provider_id or "").strip() in ACCOUNT_RUNTIME_BYPASS_PROVIDER_IDS


def provider_has_native_chat_stream(provider_id: str | None) -> bool:
    return str(provider_id or "").strip() in NATIVE_CHAT_STREAM_BYPASS_PROVIDER_IDS

MODELS: dict[str, dict] = {
    "claude-sonnet-4-6": {
        "id": "claude-sonnet-4-6",
        "label": "Sonnet 4.6",
        "tier": "sonnet",
        "providerIds": ["anthropic", "claude_code"],
        "inputPerMTok": 3.0,
        "outputPerMTok": 15.0,
        "contextWindowTokens": 1_000_000,
        "blurb": "เร็ว ฉลาด สมดุล เหมาะกับงานทั่วไปและ agent loop",
    },
    "claude-opus-4-7": {
        "id": "claude-opus-4-7",
        "label": "Opus 4.7",
        "tier": "opus",
        "providerIds": ["claude_code"],
        "inputPerMTok": 15.0,
        "outputPerMTok": 75.0,
        "fastInputPerMTok": 30.0,
        "fastOutputPerMTok": 150.0,
        "supportsFastMode": True,
        "supportedSpeeds": ["standard", "fast"],
        "contextWindowTokens": 1_000_000,
        "blurb": "รุ่น Opus สำหรับงาน reasoning ที่ต้องใช้ xhigh",
    },
    "claude-opus-4-6": {
        "id": "claude-opus-4-6",
        "label": "Opus 4.6",
        "tier": "opus",
        "providerIds": ["claude_code"],
        "inputPerMTok": 15.0,
        "outputPerMTok": 75.0,
        "fastInputPerMTok": 30.0,
        "fastOutputPerMTok": 150.0,
        "supportsFastMode": True,
        "supportedSpeeds": ["standard", "fast"],
        "contextWindowTokens": 1_000_000,
        "blurb": "รุ่น Opus legacy ที่รองรับ Claude Fast Mode",
    },
    "claude-opus-4-8": {
        "id": "claude-opus-4-8",
        "label": "Opus 4.8",
        "tier": "opus",
        "providerIds": ["anthropic", "claude_code"],
        "inputPerMTok": 5.0,
        "outputPerMTok": 25.0,
        "fastInputPerMTok": 10.0,
        "fastOutputPerMTok": 50.0,
        "supportsFastMode": True,
        "supportedSpeeds": ["standard", "fast"],
        "contextWindowTokens": 1_000_000,
        "blurb": "รุ่นหนักสุดสำหรับ reasoning และงาน agentic coding",
    },
    "gpt-5.5": {
        "id": "gpt-5.5",
        "label": "GPT-5.5",
        "tier": "gpt",
        "providerIds": ["openai", "chatgpt_account"],
        # Provisional local accounting only; replace with exact provider tariff when available.
        "inputPerMTok": 15.0,
        "outputPerMTok": 75.0,
        "contextWindowTokens": 1_000_000,
        "supportedEfforts": ["low", "medium", "high", "xhigh"],
        "defaultThinkingEffort": "medium",
        "blurb": "GPT route หลักผ่าน Responses API สำหรับงาน reasoning หนัก",
    },
    "gpt-5.4-mini": {
        "id": "gpt-5.4-mini",
        "label": "GPT-5.4 Mini",
        "tier": "gpt",
        "providerIds": ["openai", "chatgpt_account"],
        "inputPerMTok": 1.0,
        "outputPerMTok": 5.0,
        "contextWindowTokens": 1_000_000,
        "supportedEfforts": ["low", "medium", "high", "xhigh"],
        "defaultThinkingEffort": "medium",
        "blurb": "รุ่น GPT เบา เร็วกว่า เหมาะกับงานทั่วไปและงาน UI agent",
    },
    "gpt-5.3-codex": {
        "id": "gpt-5.3-codex",
        "label": "GPT-5.3 Codex",
        "tier": "gpt",
        "providerIds": ["openai", "chatgpt_account"],
        "inputPerMTok": 5.0,
        "outputPerMTok": 25.0,
        "contextWindowTokens": 1_000_000,
        "supportedEfforts": ["low", "medium", "high", "xhigh"],
        "defaultThinkingEffort": "medium",
        "blurb": "รุ่น GPT/Codex สำหรับงาน coding ผ่าน Responses API",
    },
}

for _model in MODELS.values():
    _model.setdefault("supportsFastMode", False)
    _model.setdefault("supportedSpeeds", ["standard"])

THINKING_EFFORTS: dict[str, dict] = {
    "off": {"id": "off", "label": "Off", "apiShape": 'omit thinking หรือ {type:"disabled"}',
            "blurb": "ไม่เปิด extended/adaptive thinking เพื่อลด latency"},
    "low": {"id": "low", "label": "Low", "apiShape": 'thinking:{type:"adaptive"} + effort:"low"',
            "blurb": "เน้นเร็วและประหยัด เหมาะกับงานสั้นหรือซ้ำเยอะ"},
    "medium": {"id": "medium", "label": "Medium", "apiShape": 'thinking:{type:"adaptive"} + effort:"medium"',
               "blurb": "สมดุลระหว่างคุณภาพกับต้นทุน"},
    "high": {"id": "high", "label": "High", "apiShape": 'thinking:{type:"adaptive"} + effort:"high"',
             "blurb": "ค่า default ของ Claude สำหรับงาน reasoning ที่จริงจัง"},
    "xhigh": {"id": "xhigh", "label": "XHigh", "apiShape": 'thinking:{type:"adaptive"} + effort:"xhigh"',
              "blurb": "คิดลึกเป็นพิเศษ ใช้กับ Opus 4.7/4.8"},
    "max": {"id": "max", "label": "Max", "apiShape": 'thinking:{type:"adaptive"} + effort:"max"',
            "blurb": "ความสามารถสูงสุด ใช้เมื่อคุ้มกับต้นทุนและ latency"},
}

SPEED_MODES: dict[str, dict] = {
    "standard": {
        "id": "standard",
        "label": "Standard",
        "apiShape": "omit speed",
        "blurb": "Claude response path ปกติ",
    },
    "fast": {
        "id": "fast",
        "label": "Fast",
        "apiShape": 'beta fast-mode-2026-02-01 + speed:"fast"',
        "blurb": "Claude Fast Mode ใช้ได้เฉพาะ Opus 4.8, Opus 4.7 และ Opus 4.6",
    },
}

# Effort levels in display order.
EFFORT_ORDER: list[str] = ["off", "low", "medium", "high", "xhigh", "max"]
SPEED_ORDER: list[str] = ["standard", "fast"]

# xhigh is only offered on these models (mirrors OPUS_HIGH_EFFORT_MODELS in models.ts).
_XHIGH_MODELS = {"claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8"}
FAST_MODE_MODELS = {"claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8"}

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_SPEED = "standard"


def _dotenv_has_key(key: str) -> bool:
    path = Path(".env")
    if not path.exists():
        return False
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, value = line.split("=", 1)
        if name.strip() == key and value.strip().strip("'\""):
            return True
    return False


def direct_anthropic_configured() -> bool:
    """Direct Anthropic requires an explicit app-scoped token."""
    return bool(os.environ.get("ATRIUM_ANTHROPIC_AUTH_TOKEN")) or _dotenv_has_key("ATRIUM_ANTHROPIC_AUTH_TOKEN")


def coerce_provider(provider_id: str) -> str:
    if provider_id == "anthropic" and not direct_anthropic_configured():
        return "claude_code"
    return provider_id if provider_id in PROVIDERS else "claude_code"


def models_for_provider(provider_id: str) -> list[dict]:
    return [m for m in MODELS.values() if provider_id in m["providerIds"]]


def default_model_for_provider(provider_id: str) -> str:
    if provider_id in {"openai", "chatgpt_account"}:
        return "gpt-5.5"
    if provider_id == "claude_code":
        return DEFAULT_MODEL
    return "claude-opus-4-8"


def is_model_available_for_provider(model_id: str, provider_id: str) -> bool:
    m = MODELS.get(model_id)
    return bool(m and provider_id in m["providerIds"])


def efforts_for_model(model_id: str) -> list[str]:
    explicit = MODELS.get(model_id, {}).get("supportedEfforts")
    if isinstance(explicit, list) and explicit:
        valid = [str(e) for e in explicit if e in EFFORT_ORDER]
        if valid:
            return valid
    return [e for e in EFFORT_ORDER if e != "xhigh" or model_id in _XHIGH_MODELS]


def is_effort_available_for_model(model_id: str, effort: str) -> bool:
    return effort in efforts_for_model(model_id)


def default_thinking_effort_for_model(model_id: str) -> str:
    fallback = str(MODELS.get(model_id, {}).get("defaultThinkingEffort") or "high")
    efforts = efforts_for_model(model_id)
    return fallback if fallback in efforts else efforts[0] if efforts else "high"


def speeds_for_model(model_id: str) -> list[str]:
    return [s for s in SPEED_ORDER if s == "standard" or model_id in FAST_MODE_MODELS]


def is_speed_available_for_model(model_id: str, speed: str) -> bool:
    return speed in speeds_for_model(model_id)


def coerce_thinking_effort(model_id: str, effort: str) -> str:
    """Drop back to the model default if the chosen effort is not valid."""
    if is_effort_available_for_model(model_id, effort):
        return effort
    return default_thinking_effort_for_model(model_id)


def coerce_model_speed(model_id: str, speed: str | None) -> str:
    """Fast Mode is a separate switch and only legal on selected Opus models."""
    requested = speed if speed in SPEED_ORDER else DEFAULT_SPEED
    return requested if is_speed_available_for_model(model_id, requested) else DEFAULT_SPEED


def coerce_model(provider_id: str, model_id: str) -> str:
    """Keep provider/model consistent — fall back to the provider default."""
    return model_id if is_model_available_for_provider(model_id, provider_id) else default_model_for_provider(provider_id)


def normalize_ai_config(provider_id: str, model_id: str, effort: str) -> tuple[str, str, str]:
    """Resolve a (provider, model, effort) triple into a mutually consistent one."""
    provider_id = coerce_provider(provider_id)
    model_id = coerce_model(provider_id, model_id)
    effort = coerce_thinking_effort(model_id, effort)
    return provider_id, model_id, effort


def model_pricing(model_id: str, speed: str | None = None) -> tuple[float, float]:
    m = MODELS.get(model_id) or MODELS[DEFAULT_MODEL]
    if coerce_model_speed(str(m["id"]), speed) == "fast":
        return m.get("fastInputPerMTok", m["inputPerMTok"]), m.get("fastOutputPerMTok", m["outputPerMTok"])
    return m["inputPerMTok"], m["outputPerMTok"]


def catalog_payload() -> dict:
    """Shape the UI's models.ts expects, exposed at GET /api/catalog."""
    return {
        "providers": list(PROVIDERS.values()),
        "models": list(MODELS.values()),
        "thinkingEfforts": list(THINKING_EFFORTS.values()),
        "speedModes": list(SPEED_MODES.values()),
    }
