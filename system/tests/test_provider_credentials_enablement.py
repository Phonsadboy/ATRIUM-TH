from types import SimpleNamespace
import io
import json
import os
import urllib.error
import unittest
from unittest import mock

from ops import provider_credentials_enablement as enablement


def _args(**patch):
    base = {
        "apply": False,
        "base_url": "http://127.0.0.1:8787",
        "require_all": False,
        "no_keychain": True,
        "timeout": 1.0,
        "timeout_s": 180.0,
        "openai_env": None,
        "anthropic_env": None,
        "openai_keychain_service": "atrium.openai.platform",
        "openai_keychain_account": "atrium",
        "anthropic_keychain_service": "atrium.anthropic.direct",
        "anthropic_keychain_account": "atrium",
    }
    base.update(patch)
    return SimpleNamespace(**base)


class ProviderCredentialsEnablementTest(unittest.TestCase):
    def test_build_plan_redacts_discovered_secret_values(self) -> None:
        openai_secret = "sk-test-enable-openai-123456"
        anthropic_secret = "sk-test-enable-anthropic-123456"
        with mock.patch.dict(
            os.environ,
            {
                "TEST_OPENAI_KEY": openai_secret,
                "TEST_ANTHROPIC_TOKEN": anthropic_secret,
            },
            clear=True,
        ):
            plan, updates = enablement.build_plan(
                _args(
                    require_all=True,
                    openai_env="TEST_OPENAI_KEY",
                    anthropic_env="TEST_ANTHROPIC_TOKEN",
                )
            )

        encoded = json.dumps(plan, sort_keys=True)
        self.assertIn({"key": "ATRIUM_OPENAI_API_KEY", "value": openai_secret}, updates)
        self.assertIn({"key": "ATRIUM_ANTHROPIC_AUTH_TOKEN", "value": anthropic_secret}, updates)
        self.assertNotIn(openai_secret, encoded)
        self.assertNotIn(anthropic_secret, encoded)
        source = next(item for item in plan["sources"] if item["providerId"] == "openai")
        self.assertEqual(source["source"], "env")
        self.assertEqual(source["secret"]["length"], len(openai_secret))

    def test_generic_anthropic_token_is_not_a_direct_anthropic_default(self) -> None:
        with mock.patch.dict(os.environ, {"ANTHROPIC_AUTH_TOKEN": "legacy-only"}, clear=True):
            plan, updates = enablement.build_plan(_args(require_all=True))

        self.assertEqual(updates, [])
        missing = {item["providerId"]: item for item in plan["missing"]}
        self.assertEqual(missing["anthropic"]["envNames"], ["ATRIUM_ANTHROPIC_AUTH_TOKEN"])
        self.assertFalse(plan["ok"])

    def test_apply_uses_provider_auth_api_and_reports_nested_update_result(self) -> None:
        openai_secret = "sk-test-enable-openai-abcdef"
        anthropic_secret = "sk-test-enable-anthropic-abcdef"
        args = _args(
            apply=True,
            require_all=True,
            openai_env="TEST_OPENAI_KEY",
            anthropic_env="TEST_ANTHROPIC_TOKEN",
        )
        before = {"providers": [{"id": "openai", "configured": False}, {"id": "anthropic", "configured": False}]}
        after = {"providers": [{"id": "openai", "configured": True}, {"id": "anthropic", "configured": True}]}
        patched = {
            "update": {
                "updatedKeys": ["ATRIUM_OPENAI_API_KEY", "ATRIUM_ANTHROPIC_AUTH_TOKEN"],
                "runtimeAppliedKeys": ["ATRIUM_OPENAI_API_KEY", "ATRIUM_ANTHROPIC_AUTH_TOKEN"],
                "restartRecommended": False,
            },
            "groups": [
                {
                    "fields": [
                        {"key": "ATRIUM_OPENAI_API_KEY", "kind": "secret", "value": "", "configured": True}
                    ]
                }
            ],
        }

        with mock.patch.dict(
            os.environ,
            {
                "TEST_OPENAI_KEY": openai_secret,
                "TEST_ANTHROPIC_TOKEN": anthropic_secret,
            },
            clear=True,
        ):
            with mock.patch.object(enablement, "_request_json", side_effect=[before, after]):
                with mock.patch.object(enablement, "_patch_provider_env", return_value=patched) as patch_api:
                    result = enablement.run(args)

        patch_api.assert_called_once()
        sent_updates = patch_api.call_args.args[1]
        self.assertEqual(
            sent_updates,
            [
                {"key": "ATRIUM_OPENAI_API_KEY", "value": openai_secret},
                {"key": "ATRIUM_ANTHROPIC_AUTH_TOKEN", "value": anthropic_secret},
            ],
        )
        self.assertEqual(result["applyResult"]["updatedKeys"], ["ATRIUM_OPENAI_API_KEY", "ATRIUM_ANTHROPIC_AUTH_TOKEN"])
        self.assertTrue(result["applyResult"]["secretResponseRedacted"])
        encoded = json.dumps(result, sort_keys=True)
        self.assertNotIn(openai_secret, encoded)
        self.assertNotIn(anthropic_secret, encoded)

    def test_apply_scrubs_secret_echoes_from_api_errors(self) -> None:
        secret = "sk-test-enable-openai-error-abcdef"
        args = _args(apply=True, openai_env="TEST_OPENAI_KEY")
        before = {"providers": [{"id": "openai", "configured": False}, {"id": "anthropic", "configured": False}]}
        error = urllib.error.HTTPError(
            args.base_url + "/api/provider-auth/env",
            400,
            "bad request",
            hdrs=None,
            fp=io.BytesIO(f"server echoed {secret}".encode("utf-8")),
        )

        with mock.patch.dict(os.environ, {"TEST_OPENAI_KEY": secret}, clear=True):
            with mock.patch.object(enablement, "_request_json", return_value=before):
                with mock.patch.object(enablement, "_patch_provider_env", side_effect=error):
                    result = enablement.run(args)

        self.assertFalse(result["ok"])
        self.assertIn("[redacted-secret]", result["error"])
        self.assertNotIn(secret, json.dumps(result, sort_keys=True))

    def test_apply_detects_secret_echoes_from_api_success_payload(self) -> None:
        secret = "sk-test-enable-openai-response-abcdef"
        args = _args(apply=True, openai_env="TEST_OPENAI_KEY")
        before = {"providers": [{"id": "openai", "configured": False}, {"id": "anthropic", "configured": False}]}
        after = {"providers": [{"id": "openai", "configured": True}, {"id": "anthropic", "configured": False}]}
        patched = {
            "update": {
                "updatedKeys": ["ATRIUM_OPENAI_API_KEY"],
                "runtimeAppliedKeys": ["ATRIUM_OPENAI_API_KEY"],
                "restartRecommended": False,
            },
            "echo": {"value": secret},
        }

        with mock.patch.dict(os.environ, {"TEST_OPENAI_KEY": secret}, clear=True):
            with mock.patch.object(enablement, "_request_json", side_effect=[before, after]):
                with mock.patch.object(enablement, "_patch_provider_env", return_value=patched):
                    result = enablement.run(args)

        self.assertTrue(result["ok"])
        self.assertFalse(result["applyResult"]["secretResponseRedacted"])
        self.assertNotIn(secret, json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
