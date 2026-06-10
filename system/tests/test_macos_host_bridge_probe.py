import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.tools import visual_bridge


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_macos_probe_module():
    spec = importlib.util.spec_from_file_location(
        "macos_host_bridge_probe",
        REPO_ROOT / "ops" / "macos_host_bridge_probe.py",
    )
    if spec is None or spec.loader is None:
        raise AssertionError("macos_host_bridge_probe.py could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MacOSHostBridgeProbeTest(unittest.TestCase):
    def test_macos_probe_simulate_verifies_native_accessibility_action(self) -> None:
        probe = _load_macos_probe_module()

        result = probe._simulate()

        self.assertTrue(result["ok"])
        self.assertTrue(result["appleScriptClipboard"]["verified"])
        self.assertEqual(result["appleScriptClipboard"]["method"], "osascript")
        self.assertTrue(result["browserRef"]["containsExpected"])
        self.assertEqual(result["browserRef"]["browserSnapshot"]["returnCode"], 0)
        self.assertEqual(result["browserRef"]["browserActClick"]["returnCode"], 0)
        act = result["interactiveCalculator"]["desktopActClick"]
        self.assertEqual(act["returnCode"], 0)
        self.assertTrue(act["usedNativeAction"])
        self.assertEqual(act["nativeAttempt"]["nativeAction"], "AXPress")
        self.assertTrue(result["interactiveCalculator"]["nativeActionVerified"])
        self.assertTrue(result["interactiveCalculator"]["nativeActionMetadataVerified"])
        self.assertTrue(result["interactiveCalculator"]["displayValueVerified"])
        calculator_after = result["interactiveCalculator"]["desktopSnapshotAfter"]["snapshot"]["elements"]
        self.assertTrue(any(element.get("value") == "1" for element in calculator_after))
        text_act = result["interactiveTextEdit"]["desktopActSetText"]
        self.assertEqual(text_act["returnCode"], 0)
        self.assertTrue(text_act["usedNativeAction"])
        self.assertEqual(text_act["nativeAttempt"]["nativeAction"], "setValue")
        self.assertTrue(result["interactiveTextEdit"]["nativeActionVerified"])
        self.assertTrue(result["interactiveTextEdit"]["nativeActionMetadataVerified"])
        scroll_act = result["interactiveTextEdit"]["desktopActScroll"]
        self.assertEqual(scroll_act["returnCode"], 0)
        self.assertTrue(scroll_act["usedNativeAction"])
        self.assertEqual(scroll_act["nativeAttempt"]["nativeAction"], "AXScrollDownByPage")
        self.assertTrue(result["interactiveTextEdit"]["nativeScrollVerified"])
        self.assertTrue(result["interactiveTextEdit"]["nativeScrollMetadataVerified"])
        self.assertTrue(result["interactiveTextEdit"]["textValueVerified"])
        self.assertNotIn("error", result["interactiveTextEdit"])
        after_elements = result["interactiveTextEdit"]["desktopSnapshotAfter"]["snapshot"]["elements"]
        self.assertTrue(any(element.get("value") == "ATRIUM macOS TextEdit probe ไทย" for element in after_elements))

    def test_macos_live_probe_runs_native_interactive_when_only_foreground_fallbacks_blocked(self) -> None:
        probe = _load_macos_probe_module()
        calls: list[str] = []

        def fake_calculator(*_args: object, **_kwargs: object) -> dict[str, object]:
            calls.append("calculator")
            return {"desktopActClick": {"returnCode": 0, "usedNativeAction": True}}

        def fake_textedit(*_args: object, **_kwargs: object) -> dict[str, object]:
            calls.append("textedit")
            return {"desktopActSetText": {"returnCode": 0, "usedNativeAction": True}}

        with (
            mock.patch.object(
                probe.HostBridge,
                "status",
                lambda _self: type(
                    "Status",
                    (),
                    {
                        "to_dict": lambda _status: {
                            "platform": "darwin",
                            "shellExecutable": "/bin/bash",
                            "browserBridge": True,
                            "desktopBridge": True,
                            "desktopAutomationReady": False,
                        }
                    },
                )(),
            ),
            mock.patch.object(probe, "_routes", return_value={}),
            mock.patch.object(
                probe,
                "_runtime_blocks",
                return_value={
                    "desktop.act": {"api": "foregroundSession blocked", "chat": None},
                    "desktop.activate_app": {"api": "foregroundSession blocked", "chat": None},
                },
            ),
            mock.patch.object(probe, "_runtime_block_for", return_value={"api": None, "chat": None}),
            mock.patch.object(probe, "_shell_probe", return_value={"containsExpected": True}),
            mock.patch.object(visual_bridge, "list_browser_profiles", return_value={"profiles": []}),
            mock.patch.object(visual_bridge, "execute_list_apps", return_value={"returnCode": 0, "running": []}),
            mock.patch.object(visual_bridge, "execute_desktop_snapshot", return_value={"returnCode": 0, "refCount": 1}),
            mock.patch.object(probe, "_interactive_calculator_probe", fake_calculator),
            mock.patch.object(probe, "_interactive_textedit_probe", fake_textedit),
        ):
            result = probe._live(
                screenshot=False,
                notification=False,
                applescript_clipboard=False,
                browser_url=None,
                browser_profile="atrium",
                interactive=True,
            )

        self.assertFalse(result["ok"])
        self.assertNotIn("interactiveSkipped", result["checks"])
        self.assertEqual(calls, ["calculator", "textedit"])
        self.assertEqual(result["checks"]["interactiveNativeActRuntimeBlock"], {"api": None, "chat": None})

    def test_macos_live_probe_promotes_desktop_ready_only_after_native_proof(self) -> None:
        probe = _load_macos_probe_module()

        def fake_calculator(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {
                "desktopActClick": {"returnCode": 0, "usedNativeAction": True},
                "nativeActionVerified": True,
                "displayValueVerified": True,
            }

        def fake_textedit(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {
                "desktopActSetText": {"returnCode": 0, "usedNativeAction": True},
                "nativeActionVerified": True,
                "textValueVerified": True,
            }

        with (
            mock.patch.object(
                probe.HostBridge,
                "status",
                lambda _self: type(
                    "Status",
                    (),
                    {
                        "to_dict": lambda _status: {
                            "platform": "darwin",
                            "shellExecutable": "/bin/bash",
                            "browserBridge": True,
                            "desktopBridge": True,
                            "desktopAutomationReady": False,
                            "macosVisualPreflightChecks": {"foregroundSession": None, "accessibility": False},
                        }
                    },
                )(),
            ),
            mock.patch.object(probe, "_routes", return_value={}),
            mock.patch.object(probe, "_runtime_blocks", return_value={}),
            mock.patch.object(probe, "_runtime_block_for", return_value={"api": None, "chat": None}),
            mock.patch.object(probe, "_shell_probe", return_value={"containsExpected": True}),
            mock.patch.object(visual_bridge, "list_browser_profiles", return_value={"profiles": []}),
            mock.patch.object(visual_bridge, "execute_list_apps", return_value={"returnCode": 0, "running": []}),
            mock.patch.object(
                visual_bridge,
                "execute_desktop_snapshot",
                return_value={"returnCode": 0, "refCount": 1, "snapshotBackend": "native_ax"},
            ),
            mock.patch.object(probe, "_interactive_calculator_probe", fake_calculator),
            mock.patch.object(probe, "_interactive_textedit_probe", fake_textedit),
        ):
            result = probe._live(
                screenshot=False,
                notification=False,
                applescript_clipboard=False,
                browser_url=None,
                browser_profile="atrium",
                interactive=True,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["status"]["desktopAutomationReady"])
        self.assertFalse(result["status"]["desktopAutomationPreflightReady"])
        self.assertEqual(result["status"]["desktopAutomationProofSource"], "live_native_ax_probe")
        self.assertTrue(result["status"]["macosVisualPreflightChecks"]["foregroundSession"])
        self.assertEqual(result["status"]["macosVisualPreflightPreProofChecks"]["accessibility"], False)

    def test_macos_live_probe_skips_interactive_when_native_ref_action_is_blocked(self) -> None:
        probe = _load_macos_probe_module()

        def fail_interactive(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("interactive probes should be skipped when native ref action is blocked")

        with (
            mock.patch.object(
                probe.HostBridge,
                "status",
                lambda _self: type(
                    "Status",
                    (),
                    {
                        "to_dict": lambda _status: {
                            "platform": "darwin",
                            "shellExecutable": "/bin/bash",
                            "browserBridge": True,
                            "desktopBridge": True,
                            "desktopAutomationReady": False,
                        }
                    },
                )(),
            ),
            mock.patch.object(probe, "_routes", return_value={}),
            mock.patch.object(probe, "_runtime_blocks", return_value={}),
            mock.patch.object(probe, "_runtime_block_for", return_value={"api": "Accessibility blocked", "chat": None}),
            mock.patch.object(probe, "_shell_probe", return_value={"containsExpected": True}),
            mock.patch.object(visual_bridge, "list_browser_profiles", return_value={"profiles": []}),
            mock.patch.object(visual_bridge, "execute_list_apps", return_value={"returnCode": 0, "running": []}),
            mock.patch.object(visual_bridge, "execute_desktop_snapshot", return_value={"returnCode": 0, "refCount": 1}),
            mock.patch.object(probe, "_interactive_calculator_probe", fail_interactive),
            mock.patch.object(probe, "_interactive_textedit_probe", fail_interactive),
        ):
            result = probe._live(
                screenshot=False,
                notification=False,
                applescript_clipboard=False,
                browser_url=None,
                browser_profile="atrium",
                interactive=True,
            )

        self.assertFalse(result["ok"])
        self.assertTrue(result["checks"]["interactiveSkipped"]["skipped"])
        self.assertIn("desktop.act.requireNative", result["checks"]["interactiveSkipped"]["blockedTools"])

    def test_macos_interactive_probe_requires_native_desktop_act(self) -> None:
        probe = _load_macos_probe_module()
        captured: dict[str, object] = {}
        captured_quit: dict[str, object] = {}

        def ok_result(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {"returnCode": 0, "stdout": "", "stderr": "", "processId": 123}

        def fake_desktop_act(args: dict[str, object], _run_process: object) -> dict[str, object]:
            captured.update(args)
            return {
                "returnCode": 0,
                "usedNativeAction": True,
                "nativeAttempt": {
                    "returnCode": 0,
                    "method": "accessibility",
                    "inputMethod": "accessibility",
                    "nativeStatus": "OK",
                    "nativeAction": "AXPress",
                },
                "after": {
                    "returnCode": 0,
                    "snapshot": {"elements": [{"role": "AXStaticText", "value": "1"}]},
                },
            }

        def fake_quit_app(args: dict[str, object], _run_process: object) -> dict[str, object]:
            captured_quit.update(args)
            return {"returnCode": 0, "quitVerified": True}

        with (
            mock.patch.object(visual_bridge, "execute_list_apps", lambda _args, _run_process: {"returnCode": 0, "running": [], "installed": []}),
            mock.patch.object(visual_bridge, "execute_open_app", lambda _args, _run_process: {"returnCode": 0}),
            mock.patch.object(visual_bridge, "execute_activate_app", ok_result),
            mock.patch.object(
                visual_bridge,
                "execute_desktop_snapshot",
                lambda _args, _run_process: {
                    "returnCode": 0,
                    "refCount": 1,
                    "actionableRefCount": 1,
                    "processId": 123,
                    "snapshot": {"elements": [{"ref": "d1", "role": "AXButton", "name": "1"}]},
                },
            ),
            mock.patch.object(visual_bridge, "execute_desktop_act", fake_desktop_act),
            mock.patch.object(visual_bridge, "execute_quit_app", fake_quit_app),
            mock.patch.object(probe.time, "sleep", lambda _seconds: None),
        ):
            result = probe._interactive_calculator_probe()

        self.assertNotIn("error", result)
        self.assertTrue(captured["requireNative"])
        self.assertTrue(captured["macosUseAxHelper"])
        self.assertTrue(captured["snapshotAfter"])
        self.assertEqual(captured["maxDepth"], 8)
        self.assertEqual(captured["maxElements"], 200)
        self.assertTrue(result["displayValueVerified"])
        self.assertIn("quitApp", result)
        self.assertEqual(captured_quit["processId"], 123)
        self.assertEqual(captured_quit["appName"], "Calculator")

    def test_macos_calculator_probe_uses_exposed_digit_ref_when_one_is_missing(self) -> None:
        probe = _load_macos_probe_module()
        captured: dict[str, object] = {}

        def ok_result(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {"returnCode": 0, "stdout": "", "stderr": "", "processId": 123}

        def fake_desktop_act(args: dict[str, object], _run_process: object) -> dict[str, object]:
            captured.update(args)
            return {
                "returnCode": 0,
                "usedNativeAction": True,
                "nativeAttempt": {
                    "returnCode": 0,
                    "method": "accessibility",
                    "inputMethod": "accessibility",
                    "nativeStatus": "OK",
                    "nativeAction": "AXPress",
                },
                "after": {
                    "returnCode": 0,
                    "snapshot": {"elements": [{"role": "AXStaticText", "value": "2"}]},
                },
            }

        with (
            mock.patch.object(visual_bridge, "execute_list_apps", lambda _args, _run_process: {"returnCode": 0, "running": [], "installed": []}),
            mock.patch.object(visual_bridge, "execute_open_app", lambda _args, _run_process: {"returnCode": 0}),
            mock.patch.object(visual_bridge, "execute_activate_app", ok_result),
            mock.patch.object(
                visual_bridge,
                "execute_desktop_snapshot",
                lambda _args, _run_process: {
                    "returnCode": 0,
                    "refCount": 1,
                    "actionableRefCount": 1,
                    "processId": 123,
                    "snapshot": {"elements": [{"ref": "d2", "role": "AXButton", "name": "2", "nativeSupportedActions": ["click"], "axActions": ["AXPress"]}]},
                },
            ),
            mock.patch.object(visual_bridge, "execute_desktop_act", fake_desktop_act),
            mock.patch.object(visual_bridge, "execute_quit_app", lambda _args, _run_process: {"returnCode": 0, "quitVerified": True}),
            mock.patch.object(probe.time, "sleep", lambda _seconds: None),
        ):
            result = probe._interactive_calculator_probe()

        self.assertNotIn("error", result)
        self.assertEqual(captured["ref"], "d2")
        self.assertEqual(result["expectedDisplayValue"], "2")
        self.assertTrue(result["displayValueVerified"])

    def test_macos_calculator_probe_requires_digit_ref(self) -> None:
        probe = _load_macos_probe_module()

        def ok_result(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {"returnCode": 0, "stdout": "", "stderr": "", "processId": 123}

        def fail_desktop_act(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("Calculator probe must not click a fallback button when digit refs are missing")

        with (
            mock.patch.object(visual_bridge, "execute_list_apps", lambda _args, _run_process: {"returnCode": 0, "running": [], "installed": []}),
            mock.patch.object(visual_bridge, "execute_open_app", lambda _args, _run_process: {"returnCode": 0}),
            mock.patch.object(visual_bridge, "execute_activate_app", ok_result),
            mock.patch.object(
                visual_bridge,
                "execute_desktop_snapshot",
                lambda _args, _run_process: {
                    "returnCode": 0,
                    "refCount": 1,
                    "actionableRefCount": 1,
                    "processId": 123,
                    "snapshot": {"elements": [{"ref": "d2", "role": "AXButton", "name": "Clear"}]},
                },
            ),
            mock.patch.object(visual_bridge, "execute_desktop_act", fail_desktop_act),
            mock.patch.object(visual_bridge, "execute_quit_app", lambda _args, _run_process: {"returnCode": 0, "quitVerified": True}),
            mock.patch.object(probe.time, "sleep", lambda _seconds: None),
        ):
            result = probe._interactive_calculator_probe()

        self.assertEqual(result["error"], "desktop.snapshot did not expose a Calculator digit ref for desktop.act")

    def test_macos_textedit_probe_continues_native_action_when_activation_degrades(self) -> None:
        probe = _load_macos_probe_module()
        captured: dict[str, object] = {}
        captured_quit: dict[str, object] = {}
        list_calls = 0
        snapshot_calls = 0

        class Settings:
            data_dir = Path("/tmp")

        def fake_list_apps(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            nonlocal list_calls
            list_calls += 1
            running = [] if list_calls == 1 else [{"name": "TextEdit", "processId": 123}]
            return {"returnCode": 0, "running": running, "installed": []}

        def fake_run_process(command: list[str], **_kwargs: object) -> dict[str, object]:
            return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}

        def fake_snapshot(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            nonlocal snapshot_calls
            snapshot_calls += 1
            value = "ATRIUM macOS TextEdit probe ไทย" if snapshot_calls > 1 else ""
            return {
                "returnCode": 0,
                "refCount": 2,
                "snapshot": {
                    "elements": [
                        {
                            "ref": "d1",
                            "role": "AXScrollArea",
                            "name": "Document",
                            "nativeSupportedActions": ["scroll"],
                            "supportedActions": ["scroll"],
                            "axActions": ["AXScrollDownByPage"],
                        },
                        {
                            "ref": "d2",
                            "role": "AXTextArea",
                            "name": "Text Area",
                            "nativeSupportedActions": ["paste", "type"],
                            "supportedActions": ["paste", "type"],
                            "settableAttributes": ["AXValue"],
                            "value": value,
                        }
                    ]
                },
            }

        def fake_desktop_act(args: dict[str, object], _run_process: object) -> dict[str, object]:
            captured.update(args)
            native_action = "AXScrollDownByPage" if args.get("action") == "scroll" else "setValue"
            return {
                "returnCode": 0,
                "usedNativeAction": True,
                "nativeAttempt": {
                    "returnCode": 0,
                    "method": "accessibility",
                    "inputMethod": "accessibility",
                    "nativeStatus": "OK",
                    "nativeAction": native_action,
                },
            }

        def fake_quit_app(args: dict[str, object], _run_process: object) -> dict[str, object]:
            captured_quit.update(args)
            return {"returnCode": 0, "quitVerified": True}

        with (
            mock.patch.object(probe, "get_settings", return_value=Settings()),
            mock.patch.object(visual_bridge, "execute_list_apps", fake_list_apps),
            mock.patch.object(visual_bridge, "execute_open_app", lambda _args, _run_process: {"returnCode": 0}),
            mock.patch.object(visual_bridge, "execute_activate_app", lambda _args, _run_process: {"returnCode": 1, "ok": False}),
            mock.patch.object(visual_bridge, "execute_desktop_snapshot", fake_snapshot),
            mock.patch.object(visual_bridge, "execute_desktop_act", fake_desktop_act),
            mock.patch.object(visual_bridge, "execute_quit_app", fake_quit_app),
            mock.patch.object(probe.time, "sleep", lambda _seconds: None),
        ):
            result = probe._interactive_textedit_probe(fake_run_process)

        self.assertNotIn("error", result)
        self.assertTrue(result["activationDegraded"])
        self.assertTrue(result["activateApp"]["degraded"])
        self.assertEqual(captured["ref"], "d2")
        self.assertTrue(captured["requireNative"])
        self.assertTrue(captured["macosUseAxHelper"])
        self.assertTrue(result["nativeScrollVerified"])
        self.assertEqual(captured_quit["processId"], 123)

    def test_macos_textedit_probe_reports_foreground_limit_without_snapshot_loop(self) -> None:
        probe = _load_macos_probe_module()
        list_calls = 0
        snapshot_calls = 0

        class Settings:
            data_dir = Path("/tmp")

        def fake_list_apps(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            nonlocal list_calls
            list_calls += 1
            running = [] if list_calls == 1 else [{"name": "TextEdit", "processId": 123}]
            return {"returnCode": 0, "running": running, "installed": []}

        def fake_snapshot(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            nonlocal snapshot_calls
            snapshot_calls += 1
            return {
                "returnCode": 0,
                "refCount": 2,
                "snapshot": {
                    "window": None,
                    "elements": [
                        {"ref": "d1", "role": "AXApplication", "name": "TextEdit"},
                        {"ref": "d2", "role": "AXMenuBar", "name": ""},
                    ],
                },
            }

        with (
            mock.patch.object(probe, "get_settings", return_value=Settings()),
            mock.patch.object(visual_bridge, "execute_list_apps", fake_list_apps),
            mock.patch.object(visual_bridge, "execute_open_app", lambda _args, _run_process: {"returnCode": 0}),
            mock.patch.object(visual_bridge, "execute_activate_app", lambda _args, _run_process: {"returnCode": 1, "ok": False}),
            mock.patch.object(visual_bridge, "execute_desktop_snapshot", fake_snapshot),
            mock.patch.object(visual_bridge, "execute_quit_app", lambda _args, _run_process: {"returnCode": 0, "quitVerified": True}),
            mock.patch.object(probe.time, "sleep", lambda _seconds: None),
        ):
            result = probe._interactive_textedit_probe(lambda command, **_kwargs: {"command": command, "returnCode": 0})

        self.assertEqual(snapshot_calls, 1)
        self.assertTrue(result["foregroundControlLimited"])
        self.assertIn("foreground-control limitation", result["error"])

    def test_macos_probe_full_option_expands_live_parity_checks(self) -> None:
        probe = _load_macos_probe_module()
        captured: dict[str, object] = {}

        def fake_live(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {"ok": True, "mode": "live", "checks": {}}

        with (
            mock.patch.object(sys, "argv", ["macos_host_bridge_probe.py", "--full"]),
            mock.patch.object(probe, "_live", fake_live),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            exit_code = probe.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            captured,
            {
                "screenshot": True,
                "notification": True,
                "applescript_clipboard": True,
                "browser_url": "https://example.com",
                "browser_profile": "atrium",
                "interactive": True,
            },
        )

    def test_macos_probe_full_option_keeps_explicit_browser_url(self) -> None:
        probe = _load_macos_probe_module()
        captured: dict[str, object] = {}

        def fake_live(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {"ok": True, "mode": "live", "checks": {}}

        with (
            mock.patch.object(sys, "argv", ["macos_host_bridge_probe.py", "--full", "--browser-url", "https://local.test"]),
            mock.patch.object(probe, "_live", fake_live),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            exit_code = probe.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured["browser_url"], "https://local.test")

    def test_macos_probe_output_writes_stamped_json_artifact(self) -> None:
        probe = _load_macos_probe_module()
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "nested" / "macos.json"
            with (
                mock.patch.object(
                    sys,
                    "argv",
                    ["macos_host_bridge_probe.py", "--simulate", "--parity-run-id", "test-run-1", "--output", str(output_path)],
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                exit_code = probe.main()
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["mode"], "simulate")
        self.assertEqual(payload["parityRunId"], "test-run-1")
        self.assertRegex(payload["source"]["sourceFingerprint"], r"^[0-9a-f]{64}$")
        self.assertTrue(payload["source"]["files"]["system/app/host_bridge_proof.py"]["present"])

    def test_macos_probe_refuses_source_fingerprint_mismatch_before_probe(self) -> None:
        probe = _load_macos_probe_module()
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "macos.json"
            source = {
                "sourceFingerprint": "a" * 64,
                "gitHead": "b" * 40,
                "gitDirty": False,
                "files": {"system/app/host_bridge_proof.py": {"present": True}},
            }
            with (
                mock.patch.object(probe, "host_bridge_source_provenance", lambda _root: source),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "macos_host_bridge_probe.py",
                        "--simulate",
                        "--expect-source-fingerprint",
                        "f" * 64,
                        "--parity-run-id",
                        "test-run-1",
                        "--output",
                        str(output_path),
                    ],
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                exit_code = probe.main()
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["mode"], "source_preflight_failed")
        self.assertEqual(payload["parityRunId"], "test-run-1")
        self.assertIn("source fingerprint mismatch", " ".join(payload["sourcePreflight"]["findings"]))

    def test_macos_probe_commands_ok_accepts_successful_force_cleanup_after_timeout(self) -> None:
        probe = _load_macos_probe_module()

        self.assertTrue(
            probe._commands_ok(
                {
                    "quitApp": {
                        "returnCode": 0,
                        "timeout": True,
                        "forceResult": {"returnCode": 0},
                    }
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
