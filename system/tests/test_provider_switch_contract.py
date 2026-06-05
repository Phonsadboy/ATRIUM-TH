import json
import os
import re
import unittest
from pathlib import Path
from typing import get_args

from app.catalog import (
    MODELS,
    PROVIDERS,
    coerce_provider,
    default_thinking_effort_for_model,
    efforts_for_model,
    is_model_available_for_provider,
    normalize_ai_config,
    provider_bypasses_agent_runtime,
    provider_has_native_chat_stream,
)
from app.config import Settings
from app.provider.registry import provider_health
from app.schema import AiProviderId


REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PROVIDERS = {
    "anthropic",
    "openai",
    "chatgpt_account",
    "claude_code",
}


def _openapi_provider_enum(schema_name: str) -> set[str]:
    data = json.loads((REPO_ROOT / "contract" / "openapi.json").read_text(encoding="utf-8"))
    provider = data["components"]["schemas"][schema_name]["properties"]["providerId"]
    if "enum" in provider:
        return set(provider["enum"])
    for branch in provider.get("anyOf") or []:
        if "enum" in branch:
            return set(branch["enum"])
    raise AssertionError(f"providerId enum missing from {schema_name}")


def _ui_provider_union() -> set[str]:
    source = (REPO_ROOT / "ui" / "src" / "contract" / "types.ts").read_text(encoding="utf-8")
    match = re.search(r"export type AiProviderId = (.+)", source)
    if not match:
        raise AssertionError("AiProviderId union missing from UI contract")
    return set(re.findall(r"'([^']+)'", match.group(1)))


class ProviderSwitchContractTest(unittest.TestCase):
    def test_provider_ids_match_across_contract_surfaces(self) -> None:
        self.assertEqual(set(PROVIDERS), EXPECTED_PROVIDERS)
        self.assertEqual(set(get_args(AiProviderId)), EXPECTED_PROVIDERS)
        self.assertEqual(_ui_provider_union(), EXPECTED_PROVIDERS)
        for schema_name in ("Department", "Executive", "CreateDepartmentInput", "EditDepartmentInput", "ProviderInput"):
            with self.subTest(schema=schema_name):
                self.assertEqual(_openapi_provider_enum(schema_name), EXPECTED_PROVIDERS)

    def test_provider_switch_normalization_keeps_each_configured_provider(self) -> None:
        original_direct_key = os.environ.get("ATRIUM_ANTHROPIC_AUTH_TOKEN")
        os.environ["ATRIUM_ANTHROPIC_AUTH_TOKEN"] = "test-direct-anthropic"
        try:
            for provider_id in sorted(EXPECTED_PROVIDERS):
                with self.subTest(provider=provider_id):
                    normalized_provider, model, effort = normalize_ai_config(provider_id, "__bad_model__", "__bad_effort__")
                    self.assertEqual(normalized_provider, provider_id)
                    self.assertTrue(is_model_available_for_provider(model, provider_id))
                    self.assertIn(effort, efforts_for_model(model))
        finally:
            if original_direct_key is None:
                os.environ.pop("ATRIUM_ANTHROPIC_AUTH_TOKEN", None)
            else:
                os.environ["ATRIUM_ANTHROPIC_AUTH_TOKEN"] = original_direct_key

    def test_unsupported_or_unintentional_provider_choices_are_coerced_safely(self) -> None:
        self.assertEqual(coerce_provider("__unknown__"), "claude_code")

    def test_direct_provider_choices_bypass_external_chat_runtime_gate(self) -> None:
        for provider_id in sorted(EXPECTED_PROVIDERS):
            with self.subTest(provider=provider_id):
                self.assertTrue(provider_bypasses_agent_runtime(provider_id))
                self.assertTrue(provider_has_native_chat_stream(provider_id))

    def test_invalid_explicit_supported_efforts_falls_back_without_crashing(self) -> None:
        model_id = "__test_bad_efforts__"
        MODELS[model_id] = {
            "id": model_id,
            "name": "Bad efforts",
            "providerIds": ["claude_code"],
            "supportedEfforts": ["turbo"],
            "defaultThinkingEffort": "turbo",
            "contextWindow": 1,
            "maxOutputTokens": 1,
            "pricing": {"inputPerMTok": 0, "outputPerMTok": 0},
        }
        try:
            efforts = efforts_for_model(model_id)
            self.assertGreater(len(efforts), 0)
            self.assertEqual(default_thinking_effort_for_model(model_id), efforts[0])
        finally:
            MODELS.pop(model_id, None)

    def test_provider_health_exposes_recovery_policy_without_hard_circuit_breaker(self) -> None:
        status = provider_health(Settings(openai_api_key="test-key"), probe_accounts=False)

        policy = status["recoveryPolicy"]
        self.assertTrue(policy["visibilityOnly"])
        self.assertFalse(policy["hardCircuitBreaker"])
        self.assertFalse(policy["engineCircuitBreaker"]["enabled"])
        self.assertEqual(policy["retryLayers"]["openaiResponses"]["attempts"], 2)
        self.assertTrue(policy["retryLayers"]["openaiResponses"]["honorsRetryAfter"])
        self.assertEqual(policy["resume"]["manualMessageRetryRoute"], "/api/messages/{thread_id}/retry")
        self.assertEqual(policy["resume"]["nonChatJobTimeoutRecovery"], "requeue")


if __name__ == "__main__":
    unittest.main()
