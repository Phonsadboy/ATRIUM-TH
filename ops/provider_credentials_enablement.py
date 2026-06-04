"""Enable ATRIUM direct provider credentials through the provider-auth API.

This helper intentionally routes writes through `/api/provider-auth/env` so the
same allowlist, redaction, and runtime reset behavior is used by the UI and chat
tool surface. It never prints raw credential values in the plan or result.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any


REDACTED_SECRET = "[redacted-secret]"

PROVIDER_SPECS = (
    {
        "providerId": "openai",
        "label": "OpenAI Platform",
        "targetKey": "ATRIUM_OPENAI_API_KEY",
        "defaultEnvNames": ("ATRIUM_OPENAI_API_KEY", "OPENAI_API_KEY"),
        "envArg": "openai_env",
        "keychainServiceArg": "openai_keychain_service",
        "keychainAccountArg": "openai_keychain_account",
    },
    {
        "providerId": "anthropic",
        "label": "Direct Anthropic",
        "targetKey": "ATRIUM_ANTHROPIC_AUTH_TOKEN",
        "defaultEnvNames": ("ATRIUM_ANTHROPIC_AUTH_TOKEN",),
        "envArg": "anthropic_env",
        "keychainServiceArg": "anthropic_keychain_service",
        "keychainAccountArg": "anthropic_keychain_account",
    },
)


def _clip(value: Any, limit: int = 600) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "..."


def _secret_meta(value: str) -> dict[str, Any]:
    return {
        "configured": bool(value),
        "length": len(value),
        "preview": "****" if value and len(value) <= 8 else f"{value[:4]}****{value[-4:]}" if value else "",
    }


def _redact_text(text: Any, secrets: list[str]) -> tuple[str, bool]:
    out = str(text or "")
    redacted = False
    for secret in secrets:
        if not secret:
            continue
        if secret in out:
            out = out.replace(secret, REDACTED_SECRET)
            redacted = True
    return out, redacted


def _redact_value(value: Any, secrets: list[str]) -> tuple[Any, bool]:
    if isinstance(value, str):
        return _redact_text(value, secrets)
    if isinstance(value, list):
        out = []
        redacted = False
        for item in value:
            redacted_item, item_redacted = _redact_value(item, secrets)
            out.append(redacted_item)
            redacted = redacted or item_redacted
        return out, redacted
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        redacted = False
        for key, item in value.items():
            redacted_item, item_redacted = _redact_value(item, secrets)
            out[str(key)] = redacted_item
            redacted = redacted or item_redacted
        return out, redacted
    return value, False


def _env_lookup(env_names: list[str]) -> tuple[str | None, str]:
    for name in env_names:
        value = os.environ.get(name)
        if value and value.strip():
            return name, value
    return None, ""


def _keychain_lookup(service: str, account: str, *, timeout: float) -> str:
    if not service or not account:
        return ""
    try:
        completed = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _source_for_spec(args: Any, spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str] | None]:
    explicit_env = str(getattr(args, spec["envArg"], "") or "").strip()
    env_names = [explicit_env] if explicit_env else list(spec["defaultEnvNames"])
    env_name, env_value = _env_lookup(env_names)
    if env_value:
        source = {
            "providerId": spec["providerId"],
            "label": spec["label"],
            "targetKey": spec["targetKey"],
            "source": "env",
            "envName": env_name,
            "secret": _secret_meta(env_value),
        }
        return source, {"key": spec["targetKey"], "value": env_value}

    if not bool(getattr(args, "no_keychain", False)):
        service = str(getattr(args, spec["keychainServiceArg"], "") or "").strip()
        account = str(getattr(args, spec["keychainAccountArg"], "") or "").strip()
        keychain_value = _keychain_lookup(service, account, timeout=min(float(getattr(args, "timeout", 1.0) or 1.0), 10.0))
        if keychain_value:
            source = {
                "providerId": spec["providerId"],
                "label": spec["label"],
                "targetKey": spec["targetKey"],
                "source": "keychain",
                "keychainService": service,
                "keychainAccount": account,
                "secret": _secret_meta(keychain_value),
            }
            return source, {"key": spec["targetKey"], "value": keychain_value}

    missing = {
        "providerId": spec["providerId"],
        "label": spec["label"],
        "targetKey": spec["targetKey"],
        "envNames": list(spec["defaultEnvNames"]) if not explicit_env else [explicit_env],
        "keychain": None
        if bool(getattr(args, "no_keychain", False))
        else {
            "service": str(getattr(args, spec["keychainServiceArg"], "") or ""),
            "account": str(getattr(args, spec["keychainAccountArg"], "") or ""),
        },
    }
    return missing, None


def build_plan(args: Any) -> tuple[dict[str, Any], list[dict[str, str]]]:
    sources: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    updates: list[dict[str, str]] = []
    for spec in PROVIDER_SPECS:
        source, update = _source_for_spec(args, spec)
        if update:
            sources.append(source)
            updates.append(update)
        else:
            missing.append(source)
    required_ids = {spec["providerId"] for spec in PROVIDER_SPECS} if bool(getattr(args, "require_all", False)) else set()
    missing_required = [item for item in missing if item.get("providerId") in required_ids]
    plan = {
        "ok": not missing_required and bool(updates),
        "apply": bool(getattr(args, "apply", False)),
        "requireAll": bool(getattr(args, "require_all", False)),
        "baseUrl": str(getattr(args, "base_url", "") or "").rstrip("/"),
        "sources": sources,
        "missing": missing,
        "plannedUpdates": [{"key": item["key"], "valueRedacted": bool(item.get("value"))} for item in updates],
    }
    return plan, updates


def _request_json(base_url: str, path: str, *, timeout: float) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _patch_provider_env(args: Any, updates: list[dict[str, str]]) -> dict[str, Any]:
    url = str(getattr(args, "base_url", "") or "").rstrip("/") + "/api/provider-auth/env"
    payload = json.dumps({"updates": updates}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="PATCH",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=float(getattr(args, "timeout", 30.0) or 30.0)) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_error_text(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    return _clip(body or exc.reason or exc)


def _apply_result_from_payload(payload: dict[str, Any], secrets: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    redacted_payload, found_secret_echo = _redact_value(payload, secrets)
    update = payload.get("update") if isinstance(payload.get("update"), dict) else {}
    result = {
        "updatedKeys": list(update.get("updatedKeys") or []),
        "unsetKeys": list(update.get("unsetKeys") or []),
        "runtimeAppliedKeys": list(update.get("runtimeAppliedKeys") or []),
        "restartRecommended": bool(update.get("restartRecommended")),
        "secretResponseRedacted": not found_secret_echo,
    }
    return result, redacted_payload if isinstance(redacted_payload, dict) else {}


def run(args: Any) -> dict[str, Any]:
    plan, updates = build_plan(args)
    secrets = [item["value"] for item in updates if item.get("value")]
    result: dict[str, Any] = {"ok": bool(plan["ok"]), "plan": plan}
    if bool(getattr(args, "apply", False)) and not updates:
        result.update({"ok": False, "error": "no provider credentials found to apply"})
        return result
    if not bool(getattr(args, "apply", False)):
        return result

    base_url = str(getattr(args, "base_url", "") or "").rstrip("/")
    timeout = float(getattr(args, "timeout", 30.0) or 30.0)
    try:
        before = _request_json(base_url, "/api/catalog", timeout=timeout)
        patched = _patch_provider_env(args, updates)
        after = _request_json(base_url, "/api/catalog", timeout=timeout)
    except urllib.error.HTTPError as exc:
        error, _ = _redact_text(_http_error_text(exc), secrets)
        result.update({"ok": False, "error": error})
        return result
    except Exception as exc:
        error, _ = _redact_text(_clip(exc), secrets)
        result.update({"ok": False, "error": error})
        return result

    apply_result, redacted_payload = _apply_result_from_payload(patched, secrets)
    result.update({
        "ok": True,
        "before": before,
        "after": after,
        "applyResult": apply_result,
        "providerEnv": redacted_payload,
    })
    redacted_result, _ = _redact_value(result, secrets)
    return redacted_result if isinstance(redacted_result, dict) else result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enable ATRIUM provider credentials through /api/provider-auth/env.")
    parser.add_argument("--apply", action="store_true", help="Apply discovered credentials through the backend API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--require-all", action="store_true", help="Require both OpenAI Platform and Direct Anthropic credentials.")
    parser.add_argument("--no-keychain", action="store_true", help="Do not read macOS Keychain.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--timeout-s", type=float, default=180.0)
    parser.add_argument("--openai-env")
    parser.add_argument("--anthropic-env")
    parser.add_argument("--openai-keychain-service", default="atrium.openai.platform")
    parser.add_argument("--openai-keychain-account", default="atrium")
    parser.add_argument("--anthropic-keychain-service", default="atrium.anthropic.direct")
    parser.add_argument("--anthropic-keychain-account", default="atrium")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
