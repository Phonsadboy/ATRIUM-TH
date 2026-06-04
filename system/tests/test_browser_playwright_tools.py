import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import chat_tools
from app import main as main_module
from app import schema as schema_module
from app.db import repo as repo_module
from app.tools import host_bridge, visual_bridge


class _NoCustomToolRepo:
    async def get_entity(self, _kind: str, _name: str):
        return None


class BrowserPlaywrightToolsTest(unittest.TestCase):
    def test_browser_profile_descriptor_separates_open_and_control_profile_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(visual_bridge, "_browser_profiles_root", return_value=root / "browser-profiles"),
                mock.patch.object(visual_bridge, "_browser_control_profiles_root", return_value=root / "browser-control-profiles"),
            ):
                descriptor = visual_bridge.browser_profile_descriptor("atrium")

        self.assertEqual(descriptor["userDataDir"], str(root / "browser-profiles" / "atrium"))
        self.assertEqual(descriptor["controlDataDir"], str(root / "browser-control-profiles" / "atrium"))
        self.assertNotEqual(descriptor["userDataDir"], descriptor["controlDataDir"])

    def test_execute_browser_snapshot_runs_node_helper_from_ui_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ui_root = root / "ui"
            (ui_root / "node_modules").mkdir(parents=True)
            helper = root / "browser_playwright.js"
            data_dir = root / "profile"
            calls: list[dict[str, object]] = []

            def fake_run(command, *, cwd=None, timeout=None, env=None):
                payload = json.loads(command[2])
                calls.append({"command": command, "cwd": cwd, "timeout": timeout, "env": env, "payload": payload})
                return {
                    "command": command,
                    "cwd": str(cwd) if cwd else None,
                    "returnCode": 0,
                    "stdout": json.dumps(
                        {
                            "returnCode": 0,
                            "ok": True,
                            "backend": "playwright",
                            "profile": payload["profile"],
                            "profileKind": "isolated",
                            "url": "http://127.0.0.1:5173/",
                            "title": "ATRIUM",
                            "refCount": 1,
                            "snapshot": {
                                "elements": [
                                    {"ref": "b1", "role": "button", "name": "Save", "selector": "button"}
                                ]
                            },
                        }
                    ),
                    "stderr": "",
                }

            with (
                mock.patch.object(visual_bridge, "_node_executable", return_value="/usr/bin/node"),
                mock.patch.object(visual_bridge, "_ensure_browser_playwright_helper", return_value=helper),
                mock.patch.object(visual_bridge, "_ui_root", return_value=ui_root),
                mock.patch.object(visual_bridge, "browser_control_profile_data_dir", return_value=data_dir),
                mock.patch.object(visual_bridge, "_browser_app_candidate", return_value=None),
            ):
                result = visual_bridge.execute_browser_snapshot({"url": "http://127.0.0.1:5173"}, fake_run)

            self.assertEqual(result["returnCode"], 0)
            self.assertTrue(result["ok"])
            self.assertEqual(result["profile"], "atrium")
            self.assertEqual(result["profileKind"], "isolated")
            self.assertEqual(result["title"], "ATRIUM")
            self.assertEqual(result["snapshot"]["elements"][0]["ref"], "b1")
            self.assertEqual(len(calls), 1)
            payload = calls[0]["payload"]
            self.assertEqual(payload["mode"], "snapshot")
            self.assertEqual(payload["profile"], "atrium")
            self.assertEqual(payload["url"], "http://127.0.0.1:5173")
            self.assertEqual(payload["requireFrom"], str(ui_root))
            self.assertEqual(calls[0]["cwd"], ui_root)
            self.assertIn(str(ui_root / "node_modules"), str(calls[0]["env"].get("NODE_PATH")))

    def test_execute_browser_act_sends_ref_action_and_updated_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "browser_playwright.js"
            data_dir = root / "profile"
            payloads: list[dict[str, object]] = []

            def fake_run(command, *, cwd=None, timeout=None, env=None):
                payload = json.loads(command[2])
                payloads.append(payload)
                return {
                    "command": command,
                    "cwd": str(cwd) if cwd else None,
                    "returnCode": 0,
                    "stdout": json.dumps(
                        {
                            "returnCode": 0,
                            "ok": True,
                            "backend": "playwright",
                            "profile": "atrium",
                            "profileKind": "isolated",
                            "action": {"action": "click", "ref": "b1", "selector": "button"},
                            "snapshot": {"elements": []},
                        }
                    ),
                    "stderr": "",
                }

            with (
                mock.patch.object(visual_bridge, "_node_executable", return_value="/usr/bin/node"),
                mock.patch.object(visual_bridge, "_ensure_browser_playwright_helper", return_value=helper),
                mock.patch.object(visual_bridge, "browser_control_profile_data_dir", return_value=data_dir),
                mock.patch.object(visual_bridge, "_browser_app_candidate", return_value=None),
            ):
                result = visual_bridge.execute_browser_act({"ref": "b1", "action": "click"}, fake_run)

            self.assertEqual(result["returnCode"], 0)
            self.assertEqual(result["action"]["ref"], "b1")
            self.assertEqual(payloads[0]["mode"], "act")
            self.assertEqual(payloads[0]["ref"], "b1")
            self.assertEqual(payloads[0]["action"], "click")
            self.assertFalse(payloads[0]["allowStaleRef"])
            self.assertEqual(payloads[0]["maxRefAgeMs"], 300000)

    def test_execute_browser_act_forwards_stale_ref_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "browser_playwright.js"
            data_dir = root / "profile"
            payloads: list[dict[str, object]] = []

            def fake_run(command, *, cwd=None, timeout=None, env=None):
                payload = json.loads(command[2])
                payloads.append(payload)
                return {
                    "command": command,
                    "cwd": str(cwd) if cwd else None,
                    "returnCode": 0,
                    "stdout": json.dumps(
                        {
                            "returnCode": 0,
                            "ok": True,
                            "backend": "playwright",
                            "profile": "atrium",
                            "profileKind": "isolated",
                            "action": {"action": "click", "ref": "b1", "selector": "button"},
                            "snapshot": {"elements": []},
                        }
                    ),
                    "stderr": "",
                }

            with (
                mock.patch.object(visual_bridge, "_node_executable", return_value="/usr/bin/node"),
                mock.patch.object(visual_bridge, "_ensure_browser_playwright_helper", return_value=helper),
                mock.patch.object(visual_bridge, "browser_control_profile_data_dir", return_value=data_dir),
                mock.patch.object(visual_bridge, "_browser_app_candidate", return_value=None),
            ):
                result = visual_bridge.execute_browser_act(
                    {"ref": "b1", "action": "click", "allowStaleRef": True, "maxRefAgeMs": 1234},
                    fake_run,
                )

            self.assertEqual(result["returnCode"], 0)
            self.assertTrue(payloads[0]["allowStaleRef"])
            self.assertEqual(payloads[0]["maxRefAgeMs"], 1234)

    def test_browser_playwright_helper_enforces_fresh_ref_state(self) -> None:
        source = visual_bridge._BROWSER_PLAYWRIGHT_HELPER_SOURCE

        self.assertIn("function validateBrowserRefState", source)
        self.assertIn("browser.act ref is stale; call browser.snapshot again", source)
        self.assertIn("browser.act ref age is unknown; call browser.snapshot again", source)
        self.assertIn("browser.act ref was captured for profile", source)
        self.assertIn("browser.act ref was captured on", source)
        self.assertIn("browser.act ref URL is unknown; call browser.snapshot again", source)
        self.assertIn("browser.act ref was captured for ${stateUrl}; call browser.snapshot again for ${requestedUrl}", source)
        self.assertIn("async function validateBrowserRefElement", source)
        self.assertIn("browser.act ref selector matched ${count} elements; call browser.snapshot again", source)
        self.assertIn("browser.act ref ${field} changed from", source)
        self.assertIn("browser.act ref is disabled; call browser.snapshot again", source)
        self.assertIn("currentBrowserElementIdentity", source)
        self.assertIn("function elementEnabled", source)
        self.assertIn("aria-disabled", source)
        self.assertIn("function elementHref", source)
        self.assertIn("checked: element.checked", source)
        self.assertIn("href: element.href", source)
        self.assertIn("enabled: element.enabled", source)
        self.assertIn("if (expectedHref && expectedHref !== actualHref)", source)
        self.assertIn("function normalizeUrl", source)
        self.assertIn("updatedAtMs: Date.now()", source)
        self.assertLess(source.index("validateBrowserRefState(input, state, refInfo);"), source.index("const targetUrl = input.url || state.lastUrl;"))
        self.assertLess(source.index("await validateBrowserRefElement(page, selector, refInfo);"), source.index("await locator.waitFor({ state: 'visible'"))

    def test_execute_browser_playwright_reports_structured_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "browser_playwright.js"
            data_dir = root / "profile"

            def timeout_run(command, **kwargs):
                raise subprocess.TimeoutExpired(command, kwargs.get("timeout"), output=b"partial stdout", stderr=None)

            with (
                mock.patch.object(visual_bridge, "_node_executable", return_value="/usr/bin/node"),
                mock.patch.object(visual_bridge, "_ensure_browser_playwright_helper", return_value=helper),
                mock.patch.object(visual_bridge, "browser_control_profile_data_dir", return_value=data_dir),
                mock.patch.object(visual_bridge, "_browser_app_candidate", return_value=None),
            ):
                result = visual_bridge.execute_browser_snapshot({"url": "http://127.0.0.1:5173"}, timeout_run)

        self.assertIsNone(result["returnCode"])
        self.assertTrue(result["timeout"])
        self.assertEqual(result["stdout"], "partial stdout")
        self.assertIn("command timed out", result["stderr"])
        self.assertEqual(result["backend"], "playwright")
        self.assertEqual(result["profile"], "atrium")

    def test_execute_browser_act_rejects_user_profile_without_running_process(self) -> None:
        def fail_run(*_args, **_kwargs):
            raise AssertionError("browser.act with user profile should not execute a process")

        result = visual_bridge.execute_browser_act({"profile": "user", "action": "click"}, fail_run)

        self.assertEqual(result["returnCode"], 64)
        self.assertIn("isolated browser profile", result["stderr"])

    def test_catalog_and_wire_schema_include_browser_snapshot_tools(self) -> None:
        by_tool = {row["tool"]: row for row in repo_module.TOOL_CATALOG}

        self.assertIn("browser.snapshot", schema_module.BUILTIN_TOOL_NAMES)
        self.assertIn("browser.act", schema_module.BUILTIN_TOOL_NAMES)
        self.assertEqual(by_tool["browser.snapshot"]["riskClass"], "safe_read")
        self.assertFalse(by_tool["browser.snapshot"]["mutatesState"])
        self.assertEqual(by_tool["browser.act"]["riskClass"], "desktop")
        self.assertTrue(by_tool["browser.act"]["mutatesState"])
        self.assertIn("snapshot", by_tool["browser.snapshot"]["outputSchema"]["properties"])
        self.assertIn("allowStaleRef", by_tool["browser.act"]["inputSchema"])
        self.assertIn("maxRefAgeMs", by_tool["browser.act"]["inputSchema"])
        self.assertIn("redact_public_ui_captures", by_tool["browser.act"]["redactionRules"])

    def test_risk_classification_and_host_bridge_profile_gating(self) -> None:
        self.assertEqual(
            main_module._tool_risk_class({"tool": "browser.snapshot", "departmentId": "exec", "args": {}}),
            "safe_read",
        )
        self.assertEqual(
            main_module._tool_risk_class({"tool": "browser.act", "departmentId": "exec", "args": {}}),
            "desktop",
        )
        with (
            mock.patch.object(host_bridge.HostBridge, "status") as fake_status,
            mock.patch.object(host_bridge.shutil, "which", return_value="/usr/bin/node"),
        ):
            fake_status.return_value = host_bridge.HostBridgeStatus(
                platform="darwin",
                shell_executable="/bin/zsh",
                shell=True,
                shell_ready=True,
                filesystem_ready=True,
                git=True,
                http=True,
                browser_bridge_executable="/usr/bin/open",
                browser_bridge=True,
                browser_automation_ready=True,
                isolated_browser_profile_ready=True,
                isolated_browser_profile_app="Google Chrome",
                isolated_browser_profile_executable="/Applications/Google Chrome.app",
                browser_playwright_ready=True,
                browser_playwright_package="@playwright/test",
                browser_playwright_error=None,
                desktop_bridge_executable="/usr/bin/osascript",
                desktop_bridge=True,
                desktop_automation_ready=True,
                docker=False,
                interactive_session=None,
                interactive_session_name=None,
                interactive_session_id=None,
                windows_visual_preflight_checked=False,
                windows_visual_preflight_ok=None,
                windows_visual_preflight_error=None,
                windows_visual_preflight_checks={},
                notes=[],
            )
            bridge = host_bridge.HostBridge()
            self.assertTrue(bridge.can_run("browser.snapshot")[0])
            allowed, reason = bridge.can_run("browser.snapshot", {"profile": "user"})

        self.assertFalse(allowed)
        self.assertEqual(reason, "browser.snapshot/browser.act require an isolated browser profile")

    def test_host_bridge_blocks_browser_ref_tools_when_playwright_package_is_missing(self) -> None:
        fake_status = host_bridge.HostBridgeStatus(
            platform="darwin",
            shell_executable="/bin/zsh",
            shell=True,
            shell_ready=True,
            filesystem_ready=True,
            git=True,
            http=True,
            browser_bridge_executable="/usr/bin/open",
            browser_bridge=True,
            browser_automation_ready=True,
            isolated_browser_profile_ready=True,
            isolated_browser_profile_app="Google Chrome",
            isolated_browser_profile_executable="/Applications/Google Chrome.app",
            browser_playwright_ready=False,
            browser_playwright_package=None,
            browser_playwright_error="Playwright package is required for browser.snapshot/browser.act",
            desktop_bridge_executable="/usr/bin/osascript",
            desktop_bridge=True,
            desktop_automation_ready=True,
            docker=False,
            interactive_session=None,
            interactive_session_name=None,
            interactive_session_id=None,
            windows_visual_preflight_checked=False,
            windows_visual_preflight_ok=None,
            windows_visual_preflight_error=None,
            windows_visual_preflight_checks={},
            notes=[],
        )

        with (
            mock.patch.object(host_bridge.HostBridge, "status", return_value=fake_status),
            mock.patch.object(host_bridge, "_node_executable", return_value="/usr/bin/node"),
        ):
            allowed, reason = host_bridge.HostBridge().can_run("browser.snapshot", {"profile": "atrium"})

        self.assertFalse(allowed)
        self.assertEqual(reason, "Playwright package is required for browser.snapshot/browser.act")

    def test_owner_async_visual_fallback_runs_sync_tool_in_thread(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_owner_execute_tool(run: dict[str, object]) -> dict[str, object]:
            calls.append({"mode": "direct", "run": run})
            return {"returnCode": 0, "direct": True}

        async def fake_to_thread(func, *args, **kwargs):
            calls.append({"mode": "thread", "func": func, "args": args, "kwargs": kwargs})
            return {"returnCode": 0, "threaded": True}

        run = {"tool": "browser.open", "departmentId": "exec", "args": {"url": "https://example.com"}}
        with (
            mock.patch.object(chat_tools, "_owner_execute_tool", fake_owner_execute_tool),
            mock.patch.object(chat_tools.asyncio, "to_thread", fake_to_thread),
        ):
            result = asyncio.run(chat_tools._owner_execute_tool_async(_NoCustomToolRepo(), run))

        self.assertEqual(result["returnCode"], 0)
        self.assertTrue(result["threaded"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["mode"], "thread")
        self.assertIs(calls[0]["func"], fake_owner_execute_tool)
        self.assertEqual(calls[0]["args"], (run,))


if __name__ == "__main__":
    unittest.main()
