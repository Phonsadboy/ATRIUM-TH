from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import tempfile
import unittest

from app import chat_tools, main
from app.mcp_local import mcp_runtime_block_reason, mcp_unrestricted_policy


def _settings(**overrides):
    base = {
        "mcp_gateway_url": "",
        "mcp_enabled_servers": "github",
        "mcp_timeout_s": 20.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class MCPUnrestrictedVisibilityTest(unittest.TestCase):
    def test_mcp_policy_marks_configured_servers_as_visibility_not_gate(self) -> None:
        policy = mcp_unrestricted_policy("github,drive")

        self.assertEqual(policy["mode"], "unrestricted")
        self.assertFalse(policy["allowlistEnforced"])
        self.assertFalse(policy["denyUnknownServers"])
        self.assertEqual(policy["configuredServers"], ["drive", "github"])
        self.assertEqual(policy["configuredServersPurpose"], "status_visibility_only_not_a_deny_gate")
        self.assertTrue(policy["auditRequired"])

    def test_runtime_block_does_not_enforce_enabled_servers_as_allowlist(self) -> None:
        self.assertIsNone(
            mcp_runtime_block_reason(
                {"server": "drive", "tool": "list_tools"},
                gateway_url="",
                enabled_servers="github",
            )
        )

    def test_api_tool_mcp_call_result_carries_unrestricted_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(main, "get_settings", return_value=_settings()),
                mock.patch.object(main, "_workspace_for_dept", return_value=Path(tmp)),
            ):
                result = main._execute_mcp_call(
                    {"server": "drive", "tool": "list_tools", "arguments": {}},
                    dept_id="exec",
        )

        self.assertEqual(result["server"], "drive")
        self.assertTrue(result["response"]["localGateway"])
        self.assertFalse(result["policy"]["allowlistEnforced"])
        self.assertEqual(result["policy"]["configuredServers"], ["github"])
        self.assertEqual(result["audit"]["channel"], "local_fallback")

    def test_mcp_call_audit_event_says_unrestricted_not_blocked(self) -> None:
        run = {
            "id": "tool_1",
            "tool": "mcp.call",
            "departmentId": "exec",
            "threadId": "executive",
            "status": "succeeded",
            "args": {"server": "drive", "tool": "list_tools"},
            "result": {
                "policy": mcp_unrestricted_policy("github"),
                "audit": {"channel": "local_fallback"},
            },
        }
        with mock.patch.object(main, "get_settings", return_value=_settings()):
            event = main._mcp_call_audit_event(run)

        self.assertIn("mcp.call drive.list_tools", event["text"])
        self.assertIn("unrestricted", event["text"])
        self.assertFalse(event["allowlistEnforced"])
        self.assertEqual(event["configuredServers"], ["github"])
        self.assertEqual(event["mcpChannel"], "local_fallback")

    def test_chat_tool_mcp_call_result_carries_unrestricted_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(chat_tools, "get_settings", return_value=_settings()),
                mock.patch.object(chat_tools, "_owner_workspace", return_value=Path(tmp)),
            ):
                result = chat_tools._owner_execute_mcp_call(
                    {"server": "drive", "tool": "list_tools", "arguments": {}},
                    dept_id="exec",
        )

        self.assertEqual(result["server"], "drive")
        self.assertTrue(result["response"]["localGateway"])
        self.assertFalse(result["policy"]["allowlistEnforced"])
        self.assertEqual(result["policy"]["configuredServers"], ["github"])
        self.assertEqual(result["audit"]["channel"], "local_fallback")


if __name__ == "__main__":
    unittest.main()
