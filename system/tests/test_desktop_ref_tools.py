import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import main as main_module
from app import schema as schema_module
from app.db import repo as repo_module
from app.tools import host_bridge, visual_bridge


class DesktopRefToolsTest(unittest.TestCase):
    def test_macos_desktop_snapshot_parses_accessibility_rows_and_saves_refs(self) -> None:
        stdout = "\n".join(
            [
                "META\tappName\tTextEdit",
                "META\tprocessId\t123",
                "META\ttitle\tUntitled",
                "META\twindowX\t10",
                "META\twindowY\t20",
                "META\twindowWidth\t400",
                "META\twindowHeight\t300",
                "ROW\tw1\tAXWindow\t\tUntitled\t\t\ttrue\t10\t20\t400\t300\t1",
                "ROW\tw1.1\tAXButton\tAXCloseButton\tClose\t\t\ttrue\t15\t25\t20\t20\t0",
            ]
        )

        def fake_run(command, **_kwargs):
            self.assertEqual(command[0], "osascript")
            self.assertIn("appendElement", command[-1])
            self.assertIn("hasExplicitTarget", command[-1])
            self.assertIn('error "target application process not found"', command[-1])
            return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                with (
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=Path(tmp) / "desktop-state.json"),
                    mock.patch.object(visual_bridge, "_ensure_snapshot_helper", return_value=None),
                ):
                    result = visual_bridge.execute_desktop_snapshot({"maxElements": 20, "maxDepth": 2}, fake_run)
                    state = visual_bridge._read_desktop_state()
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["refCoverage"], "available")
        self.assertEqual(result["actionableRefCount"], 2)
        self.assertEqual(result["bboxActionableRefCount"], 2)
        self.assertEqual(result["nativeActionableRefCount"], 1)
        self.assertEqual(result["appName"], "TextEdit")
        self.assertEqual(result["processId"], 123)
        self.assertEqual(result["snapshot"]["window"], {"x": 10, "y": 20, "width": 400, "height": 300})
        self.assertEqual(result["snapshot"]["elements"][1]["ref"], "d2")
        self.assertTrue(result["snapshot"]["elements"][1]["actionable"])
        self.assertTrue(result["snapshot"]["elements"][1]["bboxActionable"])
        self.assertTrue(result["snapshot"]["elements"][1]["nativeActionable"])
        self.assertIn("click", result["snapshot"]["elements"][1]["supportedActions"])
        self.assertEqual(result["snapshot"]["elements"][1]["bbox"], {"x": 15, "y": 25, "width": 20, "height": 20})
        self.assertTrue(state["refs"]["d2"]["nativeActionable"])
        self.assertEqual(state["refs"]["d2"]["path"], "w1.1")

    def test_macos_desktop_snapshot_prefers_native_ax_helper(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            self.assertEqual(command[0], "/tmp/atrium-macos-snapshot")
            return {
                "command": command,
                "returnCode": 0,
                "stdout": json.dumps(
                    {
                        "appName": "TextEdit",
                        "processId": 123,
                        "title": "Untitled",
                        "window": {"x": 10, "y": 20, "width": 400, "height": 300},
                        "windowCount": 1,
                        "elements": [
                            {
                                "path": "w1",
                                "role": "AXWindow",
                                "name": "Untitled",
                                "enabled": True,
                                "axActions": [],
                                "settableAttributes": [],
                                "x": 10,
                                "y": 20,
                                "width": 400,
                                "height": 300,
                                "children": 1,
                            },
                            {
                                "path": "w1.1",
                                "role": "AXButton",
                                "subrole": "AXCloseButton",
                                "name": "Close",
                                "enabled": True,
                                "axActions": ["AXPress"],
                                "settableAttributes": [],
                                "x": 15,
                                "y": 25,
                                "width": 20,
                                "height": 20,
                                "children": 0,
                            },
                        ],
                    }
                ),
                "stderr": "",
            }

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                with (
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=Path(tmp) / "desktop-state.json"),
                    mock.patch.object(visual_bridge, "_ensure_snapshot_helper", return_value=Path("/tmp/atrium-macos-snapshot")),
                ):
                    result = visual_bridge.execute_desktop_snapshot({"appName": "TextEdit", "processId": 123}, fake_run)
                    state = visual_bridge._read_desktop_state()
            finally:
                sys.platform = original_platform

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:], ["123", "TextEdit", "120", "4"])
        self.assertEqual(result["returnCode"], 0)
        self.assertEqual(result["method"], "native_ax_snapshot")
        self.assertEqual(result["snapshotBackend"], "native_ax")
        self.assertEqual(result["windowCount"], 1)
        self.assertEqual(result["refCount"], 2)
        self.assertEqual(result["nativeActionableRefCount"], 1)
        self.assertEqual(result["snapshot"]["elements"][1]["axActions"], ["AXPress"])
        self.assertEqual(result["snapshot"]["elements"][1]["nativeSupportedActions"], ["click"])
        self.assertEqual(state["refs"]["d2"]["axActions"], ["AXPress"])
        self.assertEqual(state["refs"]["d2"]["path"], "w1.1")

    def test_macos_desktop_snapshot_uses_native_ax_metadata_over_role_heuristic(self) -> None:
        def fake_run(command, **_kwargs):
            self.assertEqual(command[0], "/tmp/atrium-macos-snapshot")
            return {
                "command": command,
                "returnCode": 0,
                "stdout": json.dumps(
                    {
                        "appName": "ProbeApp",
                        "processId": 123,
                        "title": "Probe",
                        "windowCount": 1,
                        "elements": [
                            {
                                "path": "w1.1",
                                "role": "AXButton",
                                "name": "Looks Actionable",
                                "enabled": True,
                                "axActions": [],
                                "settableAttributes": [],
                                "children": 0,
                            },
                            {
                                "path": "w1.2",
                                "role": "AXGroup",
                                "name": "Writable Value",
                                "enabled": True,
                                "axActions": [],
                                "settableAttributes": ["AXValue"],
                                "children": 0,
                            },
                        ],
                    }
                ),
                "stderr": "",
            }

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                with (
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=Path(tmp) / "desktop-state.json"),
                    mock.patch.object(visual_bridge, "_ensure_snapshot_helper", return_value=Path("/tmp/atrium-macos-snapshot")),
                ):
                    result = visual_bridge.execute_desktop_snapshot({"appName": "ProbeApp", "processId": 123}, fake_run)
                    state = visual_bridge._read_desktop_state()
            finally:
                sys.platform = original_platform

        button = result["snapshot"]["elements"][0]
        writable = result["snapshot"]["elements"][1]
        self.assertEqual(result["returnCode"], 0)
        self.assertFalse(button["nativeActionable"])
        self.assertEqual(button["nativeSupportedActions"], [])
        self.assertEqual(button["supportedActions"], [])
        self.assertTrue(writable["nativeActionable"])
        self.assertEqual(writable["nativeSupportedActions"], ["paste", "type"])
        self.assertEqual(writable["supportedActions"], ["paste", "type"])
        self.assertEqual(state["refs"]["d2"]["settableAttributes"], ["AXValue"])

    def test_macos_desktop_snapshot_marks_ax_scroll_actions_native(self) -> None:
        def fake_run(command, **_kwargs):
            self.assertEqual(command[0], "/tmp/atrium-macos-snapshot")
            return {
                "command": command,
                "returnCode": 0,
                "stdout": json.dumps(
                    {
                        "appName": "TextEdit",
                        "processId": 123,
                        "title": "Probe",
                        "windowCount": 1,
                        "elements": [
                            {
                                "path": "w1.1",
                                "role": "AXScrollArea",
                                "name": "Document",
                                "enabled": True,
                                "axActions": ["AXScrollDownByPage", "AXScrollUpByPage"],
                                "settableAttributes": ["AXFocused"],
                                "children": 0,
                            }
                        ],
                    }
                ),
                "stderr": "",
            }

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                with (
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=Path(tmp) / "desktop-state.json"),
                    mock.patch.object(visual_bridge, "_ensure_snapshot_helper", return_value=Path("/tmp/atrium-macos-snapshot")),
                ):
                    result = visual_bridge.execute_desktop_snapshot({"appName": "TextEdit", "processId": 123}, fake_run)
                    state = visual_bridge._read_desktop_state()
            finally:
                sys.platform = original_platform

        scroll_area = result["snapshot"]["elements"][0]
        self.assertEqual(result["returnCode"], 0)
        self.assertTrue(scroll_area["nativeActionable"])
        self.assertFalse(scroll_area["bboxActionable"])
        self.assertEqual(scroll_area["nativeSupportedActions"], ["scroll"])
        self.assertEqual(scroll_area["supportedActions"], ["scroll"])
        self.assertEqual(state["refs"]["d1"]["axActions"], ["AXScrollDownByPage", "AXScrollUpByPage"])
        self.assertEqual(state["refs"]["d1"]["nativeSupportedActions"], ["scroll"])

    def test_macos_desktop_snapshot_marks_native_only_menu_item_actionable(self) -> None:
        stdout = "\n".join(
            [
                "META\tappName\tCalculator",
                "META\tprocessId\t123",
                "META\ttitle\t",
                "ROW\tp1\tAXApplication\t\tCalculator\tapplication\t\tfalse\t\t\t\t\t1",
                "ROW\tp1.1\tAXMenuBarItem\t\tHelp\t\t\ttrue\t\t\t\t\t0",
            ]
        )

        def fake_run(command, **_kwargs):
            self.assertEqual(command[0], "osascript")
            return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                with (
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=Path(tmp) / "desktop-state.json"),
                    mock.patch.object(visual_bridge, "_ensure_snapshot_helper", return_value=None),
                ):
                    result = visual_bridge.execute_desktop_snapshot({"appName": "Calculator"}, fake_run)
                    state = visual_bridge._read_desktop_state()
            finally:
                sys.platform = original_platform

        menu_item = result["snapshot"]["elements"][1]
        self.assertEqual(result["actionableRefCount"], 1)
        self.assertEqual(result["bboxActionableRefCount"], 0)
        self.assertEqual(result["nativeActionableRefCount"], 1)
        self.assertTrue(menu_item["actionable"])
        self.assertFalse(menu_item["bboxActionable"])
        self.assertTrue(menu_item["nativeActionable"])
        self.assertNotIn("description", menu_item)
        self.assertNotIn("value", menu_item)
        self.assertEqual(menu_item["nativeSupportedActions"], ["click"])
        self.assertEqual(menu_item["supportedActions"], ["click"])
        self.assertTrue(state["refs"]["d2"]["nativeActionable"])
        self.assertEqual(state["refs"]["d2"]["supportedActions"], ["click"])

    def test_macos_desktop_snapshot_does_not_fall_back_when_explicit_target_is_missing(self) -> None:
        def fake_run(command, **_kwargs):
            self.assertEqual(command[0], "osascript")
            self.assertIn("hasExplicitTarget", command[-1])
            self.assertIn('error "target application process not found"', command[-1])
            return {
                "command": command,
                "returnCode": 1,
                "stdout": "",
                "stderr": "target application process not found",
            }

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            with mock.patch.object(visual_bridge, "_ensure_snapshot_helper", return_value=None):
                result = visual_bridge.execute_desktop_snapshot({"appName": "DefinitelyMissingATRIUMProbeApp"}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertFalse(result["ok"])
        self.assertEqual(result["returnCode"], 1)
        self.assertEqual(result["refCount"], 0)
        self.assertIn("target application process not found", result["stderr"])

    def test_macos_desktop_snapshot_reports_empty_ref_coverage(self) -> None:
        stdout = "\n".join(
            [
                "META\tappName\tCodex",
                "META\tprocessId\t321",
                "META\ttitle\t",
            ]
        )

        def fake_run(command, **_kwargs):
            self.assertIn('my appendElement(targetProcess, "p1", 0)', command[-1])
            return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                with (
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=Path(tmp) / "desktop-state.json"),
                    mock.patch.object(visual_bridge, "_ensure_snapshot_helper", return_value=None),
                ):
                    result = visual_bridge.execute_desktop_snapshot({"maxElements": 20, "maxDepth": 2}, fake_run)
            finally:
                sys.platform = original_platform

        self.assertTrue(result["ok"])
        self.assertEqual(result["refCount"], 0)
        self.assertEqual(result["actionableRefCount"], 0)
        self.assertEqual(result["nativeActionableRefCount"], 0)
        self.assertEqual(result["refCoverage"], "empty")
        self.assertIn("no actionable", result["warning"])

    def test_macos_desktop_snapshot_reports_structured_timeout(self) -> None:
        def timeout_run(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout"), output=b"partial", stderr=b"")

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            with mock.patch.object(visual_bridge, "_ensure_snapshot_helper", return_value=None):
                result = visual_bridge.execute_desktop_snapshot({"maxElements": 20, "maxDepth": 2}, timeout_run)
        finally:
            sys.platform = original_platform

        self.assertIsNone(result["returnCode"])
        self.assertTrue(result["timeout"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["refCount"], 0)
        self.assertIn("command timed out", result["stderr"])

    def test_macos_desktop_apps_returns_process_metadata(self) -> None:
        stdout = "\n".join(
            [
                "ROW\tCodex\t111\tATRIUM - Codex\tcom.openai.codex\tfalse",
                "ROW\tTextEdit\t222\tUntitled\tcom.apple.TextEdit\ttrue",
            ]
        )

        def fake_run(command, **_kwargs):
            self.assertEqual(command[0], "osascript")
            self.assertIn("unix id of appProcess", command[-1])
            self.assertIn("bundle identifier of appProcess", command[-1])
            return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            with mock.patch.object(visual_bridge, "_ensure_apps_helper", return_value=None):
                result = visual_bridge.execute_list_apps(
                    {"includeRunning": True, "includeInstalled": False, "query": "textedit"},
                    fake_run,
                )
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertEqual(result["platform"], "darwin")
        self.assertEqual(
            result["running"],
            [
                {
                    "name": "TextEdit",
                    "processId": 222,
                    "title": "Untitled",
                    "bundleId": "com.apple.TextEdit",
                    "frontmost": True,
                }
            ],
        )

    def test_macos_desktop_apps_prefers_native_nsworkspace_helper(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            self.assertEqual(command, ["/tmp/atrium-macos-apps"])
            return {
                "command": command,
                "returnCode": 0,
                "stdout": json.dumps(
                    [
                        {
                            "name": "Codex",
                            "processId": 111,
                            "bundleId": "com.openai.codex",
                            "path": "/Applications/Codex.app",
                            "frontmost": False,
                        },
                        {
                            "name": "TextEdit",
                            "processId": 222,
                            "bundleId": "com.apple.TextEdit",
                            "path": "/System/Applications/TextEdit.app",
                            "frontmost": True,
                        },
                    ]
                ),
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            with mock.patch.object(visual_bridge, "_ensure_apps_helper", return_value=Path("/tmp/atrium-macos-apps")):
                result = visual_bridge.execute_list_apps(
                    {"includeRunning": True, "includeInstalled": False, "query": "textedit"},
                    fake_run,
                )
        finally:
            sys.platform = original_platform

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["returnCode"], 0)
        self.assertEqual(result["runningMethod"], "native_nsworkspace_apps")
        self.assertEqual(result["running"][0]["name"], "TextEdit")
        self.assertEqual(result["running"][0]["processId"], 222)
        self.assertTrue(result["running"][0]["frontmost"])

    def test_macos_desktop_apps_keeps_legacy_name_list_parsing(self) -> None:
        def fake_run(command, **_kwargs):
            self.assertEqual(command[0], "osascript")
            return {"command": command, "returnCode": 0, "stdout": "Codex, Calculator", "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            with mock.patch.object(visual_bridge, "_ensure_apps_helper", return_value=None):
                result = visual_bridge.execute_list_apps(
                    {"includeRunning": True, "includeInstalled": False, "query": "calc"},
                    fake_run,
                )
        finally:
            sys.platform = original_platform

        self.assertEqual(result["running"], ["Calculator"])

    def test_macos_paste_text_verifies_clipboard_before_paste(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            self.assertEqual(command[0], "osascript")
            script = str(command[-1])
            if "set the clipboard to" in script:
                return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
            if "the clipboard as text" in script:
                return {"command": command, "returnCode": 0, "stdout": "OK\t9\tpaste ไทย", "stderr": ""}
            if 'keystroke "v" using {command down}' in script:
                return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
            raise AssertionError(f"unexpected command: {command}")

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            with mock.patch.object(visual_bridge, "_ensure_key_helper", return_value=None):
                result = visual_bridge.execute_paste_text({"text": "paste ไทย"}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertEqual(result["method"], "osascript")
        self.assertEqual(result["inputMethod"], "osascript")
        self.assertEqual(result["platform"], "darwin")
        self.assertEqual(result["textBytes"], len("paste ไทย".encode("utf-8")))
        self.assertTrue(result["clipboard"]["verified"])
        self.assertTrue(result["clipboard"]["containsExpected"])
        self.assertEqual(result["clipboard"]["textPreview"], "paste ไทย")
        self.assertEqual(result["clipboard"]["verifyStatus"], "OK")
        self.assertEqual(len(calls), 3)

    def test_macos_paste_text_fails_when_clipboard_round_trip_does_not_verify(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            self.assertEqual(command[0], "osascript")
            script = str(command[-1])
            if "set the clipboard to" in script:
                return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
            if "the clipboard as text" in script:
                return {"command": command, "returnCode": 0, "stdout": "MISMATCH\t5\twrong", "stderr": ""}
            raise AssertionError("keypress should not run when clipboard verification fails")

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            with mock.patch.object(visual_bridge, "_ensure_key_helper", return_value=None):
                result = visual_bridge.execute_paste_text({"text": "paste ไทย"}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertEqual(result["stderr"], "clipboard round-trip did not verify expected text")
        self.assertFalse(result["clipboard"]["verified"])
        self.assertFalse(result["clipboard"]["containsExpected"])
        self.assertEqual(result["clipboard"]["textPreview"], "wrong")
        self.assertEqual(result["clipboard"]["verifyStatus"], "MISMATCH")
        self.assertEqual(len(calls), 2)

    def test_windows_desktop_snapshot_parses_uia_json(self) -> None:
        stdout = json.dumps(
            {
                "appName": "notepad",
                "processId": 42,
                "title": "Untitled - Notepad",
                "window": {"x": 0, "y": 0, "width": 640, "height": 480},
                "elements": [
                    {
                        "path": "w1",
                        "role": "Window",
                        "name": "Untitled - Notepad",
                        "automationId": "",
                        "className": "Notepad",
                        "enabled": True,
                        "x": 0,
                        "y": 0,
                        "width": 640,
                        "height": 480,
                    },
                    {
                        "path": "w1.1",
                        "role": "Edit",
                        "name": "Text Editor",
                        "automationId": "15",
                        "className": "Edit",
                        "enabled": True,
                        "x": 20,
                        "y": 50,
                        "width": 600,
                        "height": 400,
                    },
                ],
            }
        )

        def fake_run(command, **_kwargs):
            joined = " ".join(command)
            self.assertIn("UIAutomationClient", joined)
            self.assertIn("GetForegroundWindow", joined)
            return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "win32"
                with (
                    mock.patch.object(visual_bridge, "_powershell_executable", return_value="powershell.exe"),
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=Path(tmp) / "desktop-state.json"),
                ):
                    result = visual_bridge.execute_desktop_snapshot({"processId": 42}, fake_run)
                    state = visual_bridge._read_desktop_state()
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["refCoverage"], "available")
        self.assertEqual(result["actionableRefCount"], 2)
        self.assertEqual(result["bboxActionableRefCount"], 2)
        self.assertEqual(result["nativeActionableRefCount"], 1)
        self.assertEqual(result["refCount"], 2)
        self.assertEqual(result["snapshot"]["elements"][1]["role"], "Edit")
        self.assertEqual(state["refs"]["d2"]["bbox"], {"x": 20, "y": 50, "width": 600, "height": 400})
        self.assertTrue(state["refs"]["d2"]["nativeActionable"])

    def test_windows_desktop_snapshot_uses_uia_patterns_for_native_actions(self) -> None:
        stdout = json.dumps(
            {
                "appName": "notepad",
                "processId": 42,
                "title": "Untitled - Notepad",
                "window": {"x": 0, "y": 0, "width": 640, "height": 480},
                "elements": [
                    {
                        "path": "w1",
                        "role": "Window",
                        "name": "Untitled - Notepad",
                        "enabled": True,
                        "patterns": [],
                        "x": 0,
                        "y": 0,
                        "width": 640,
                        "height": 480,
                    },
                    {
                        "path": "w1.1",
                        "role": "Custom",
                        "name": "Invoke Only",
                        "enabled": True,
                        "patterns": ["InvokePatternIdentifiers.Pattern"],
                        "x": 20,
                        "y": 50,
                        "width": 100,
                        "height": 30,
                    },
                    {
                        "path": "w1.2",
                        "role": "Button",
                        "name": "Role Without Pattern",
                        "enabled": True,
                        "patterns": [],
                        "x": 20,
                        "y": 90,
                        "width": 100,
                        "height": 30,
                    },
                    {
                        "path": "w1.3",
                        "role": "Edit",
                        "name": "Text Editor",
                        "enabled": True,
                        "patterns": ["ValuePatternIdentifiers.Pattern"],
                        "x": 20,
                        "y": 130,
                        "width": 400,
                        "height": 80,
                    },
                ],
            }
        )

        def fake_run(command, **_kwargs):
            self.assertIn("GetSupportedPatterns", command[-1])
            return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "win32"
                with (
                    mock.patch.object(visual_bridge, "_powershell_executable", return_value="powershell.exe"),
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=Path(tmp) / "desktop-state.json"),
                ):
                    result = visual_bridge.execute_desktop_snapshot({"processId": 42}, fake_run)
                    state = visual_bridge._read_desktop_state()
            finally:
                sys.platform = original_platform

        invoke_ref = result["snapshot"]["elements"][1]
        role_only_ref = result["snapshot"]["elements"][2]
        edit_ref = result["snapshot"]["elements"][3]
        self.assertEqual(result["nativeActionableRefCount"], 2)
        self.assertEqual(invoke_ref["nativeSupportedActions"], ["click"])
        self.assertEqual(role_only_ref["nativeSupportedActions"], [])
        self.assertEqual(edit_ref["nativeSupportedActions"], ["paste", "type"])
        self.assertEqual(state["refs"]["d2"]["patterns"], ["InvokePatternIdentifiers.Pattern"])
        self.assertEqual(state["refs"]["d3"]["nativeSupportedActions"], [])

    def test_windows_desktop_snapshot_does_not_fall_back_when_explicit_target_is_missing(self) -> None:
        def fake_run(command, **_kwargs):
            script = command[-1]
            self.assertIn("$hasExplicitTarget = [bool]($targetPidText -or $targetName)", script)
            self.assertIn("target application process not found", script)
            self.assertLess(
                script.index("target application process not found"),
                script.index("[ATRIUM.Win32Snapshot]::GetForegroundWindow()"),
            )
            return {
                "command": command,
                "returnCode": 1,
                "stdout": "",
                "stderr": "target application process not found",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge, "_powershell_executable", return_value="powershell.exe"):
                result = visual_bridge.execute_desktop_snapshot(
                    {"appName": "DefinitelyMissingATRIUMProbeApp"},
                    fake_run,
                )
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refCount"], 0)
        self.assertEqual(result["snapshot"]["elements"], [])
        self.assertIn("target application process not found", result["stderr"])

    def test_desktop_act_clicks_center_of_saved_ref(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_run(command, **_kwargs):
            self.assertEqual(command[0], "osascript")
            script = command[-1]
            if " to activate" in script:
                self.assertIn('tell application "TextEdit" to activate', script)
                self.assertNotIn('tell application "123"', script)
                return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
            self.assertIn("FOREGROUND", script)
            return {
                "command": command,
                "returnCode": 0,
                "stdout": "FOREGROUND\ttrue\tTextEdit\t123\tTextEdit\t123",
                "stderr": "",
            }

        def fake_click(args, _run_process):
            calls.append(dict(args))
            return {"returnCode": 0, "stdout": "", "stderr": "", "x": args["x"], "y": args["y"]}

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with (
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path),
                    mock.patch.object(visual_bridge, "_ensure_activate_helper", return_value=None),
                ):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "darwin",
                            "appName": "TextEdit",
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d7": {
                                    "path": "w1.3",
                                    "role": "AXButton",
                                    "name": "Save",
                                    "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fake_click):
                        result = visual_bridge.execute_desktop_act(
                            {"ref": "d7", "action": "click", "preferNative": False, "snapshotAfter": False},
                            fake_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertTrue(result["ok"])
        self.assertEqual(calls, [{"x": 25, "y": 40, "button": "left"}])
        self.assertEqual(result["action"]["target"]["name"], "Save")
        self.assertFalse(result["usedNativeAction"])
        self.assertEqual(result["targetActivation"]["foreground"], True)

    def test_desktop_act_refuses_coordinate_fallback_when_macos_target_is_not_foreground(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_run(command, **_kwargs):
            self.assertEqual(command[0], "osascript")
            script = command[-1]
            if " to activate" in script:
                self.assertIn('tell application "TextEdit" to activate', script)
                self.assertNotIn('tell application "123"', script)
                return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
            self.assertIn("FOREGROUND", script)
            return {
                "command": command,
                "returnCode": 0,
                "stdout": "FOREGROUND\tfalse\tTextEdit\t123\tOtherApp\t456",
                "stderr": "",
            }

        def fail_click(args, _run_process):
            calls.append(dict(args))
            raise AssertionError("coordinate click should not run without foreground verification")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "darwin",
                            "appName": "TextEdit",
                            "processId": 123,
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d7": {
                                    "path": "w1.3",
                                    "role": "AXButton",
                                    "name": "Save",
                                    "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                                }
                            },
                        }
                    )
                    with (
                        mock.patch.object(visual_bridge, "execute_click", fail_click),
                        mock.patch.object(visual_bridge, "_ensure_activate_helper", return_value=None),
                    ):
                        result = visual_bridge.execute_desktop_act(
                            {"ref": "d7", "action": "click", "preferNative": False, "snapshotAfter": False},
                            fake_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertFalse(result["ok"])
        self.assertFalse(result["usedNativeAction"])
        self.assertFalse(result["targetActivation"]["foreground"])
        self.assertIn("window did not become foreground", result["stderr"])
        self.assertEqual(calls, [])

    def test_desktop_act_rejects_stale_snapshot_ref(self) -> None:
        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "darwin",
                            "appName": "TextEdit",
                            "updatedAt": 1,
                            "refs": {
                                "d7": {
                                    "path": "w1.3",
                                    "role": "AXButton",
                                    "name": "Save",
                                    "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                                }
                            },
                        }
                    )
                    result = visual_bridge.execute_desktop_act(
                        {"ref": "d7", "action": "click", "snapshotAfter": False},
                        lambda *_args, **_kwargs: {},
                    )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 64)
        self.assertIn("ref is stale", result["stderr"])

    def test_desktop_act_rejects_unknown_age_snapshot_ref(self) -> None:
        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "darwin",
                            "appName": "TextEdit",
                            "refs": {
                                "d7": {
                                    "path": "w1.3",
                                    "role": "AXButton",
                                    "name": "Save",
                                    "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                                }
                            },
                        }
                    )
                    result = visual_bridge.execute_desktop_act(
                        {"ref": "d7", "action": "click", "snapshotAfter": False},
                        lambda *_args, **_kwargs: {},
                    )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 64)
        self.assertIn("ref age is unknown", result["stderr"])

    def test_desktop_act_rejects_cross_platform_snapshot_ref(self) -> None:
        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "win32",
                            "appName": "notepad",
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d7": {
                                    "path": "w1.3",
                                    "role": "Button",
                                    "name": "Save",
                                    "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                                }
                            },
                        }
                    )
                    result = visual_bridge.execute_desktop_act(
                        {"ref": "d7", "action": "click", "snapshotAfter": False},
                        lambda *_args, **_kwargs: {},
                    )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 64)
        self.assertIn("captured on win32", result["stderr"])

    def test_desktop_act_rejects_explicit_app_target_mismatch(self) -> None:
        def fail_run(*_args, **_kwargs):
            raise AssertionError("desktop.act should reject mismatched appName before native action")

        def fail_click(*_args, **_kwargs):
            raise AssertionError("desktop.act should reject mismatched appName before coordinate fallback")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "darwin",
                            "appName": "TextEdit",
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d7": {
                                    "path": "w1.3",
                                    "role": "AXButton",
                                    "name": "Save",
                                    "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {"ref": "d7", "appName": "Calculator", "action": "click", "snapshotAfter": False},
                            fail_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 64)
        self.assertIn("ref was captured for app TextEdit", result["stderr"])
        self.assertIn("snapshot again for app Calculator", result["stderr"])

    def test_desktop_act_rejects_explicit_process_target_mismatch(self) -> None:
        def fail_run(*_args, **_kwargs):
            raise AssertionError("desktop.act should reject mismatched processId before native action")

        def fail_click(*_args, **_kwargs):
            raise AssertionError("desktop.act should reject mismatched processId before coordinate fallback")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "win32"
                state_path = Path(tmp) / "desktop-state.json"
                with (
                    mock.patch.object(visual_bridge, "_powershell_executable", return_value="powershell.exe"),
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path),
                ):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "win32",
                            "appName": "notepad",
                            "processId": 42,
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d2": {
                                    "path": "w1.1",
                                    "role": "Button",
                                    "name": "Save",
                                    "bbox": {"x": 20, "y": 50, "width": 100, "height": 30},
                                    "platform": "win32",
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {"ref": "d2", "processId": 43, "action": "click", "snapshotAfter": False},
                            fail_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 64)
        self.assertIn("ref was captured for process 42", result["stderr"])
        self.assertIn("snapshot again for process 43", result["stderr"])

    def test_desktop_act_rejects_fractional_state_process_id_before_fallback(self) -> None:
        def fail_run(*_args, **_kwargs):
            raise AssertionError("desktop.act should reject invalid saved processId before native action")

        def fail_click(*_args, **_kwargs):
            raise AssertionError("desktop.act should reject invalid saved processId before coordinate fallback")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "win32"
                state_path = Path(tmp) / "desktop-state.json"
                with (
                    mock.patch.object(visual_bridge, "_powershell_executable", return_value="powershell.exe"),
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path),
                ):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "win32",
                            "appName": "notepad",
                            "processId": 42.5,
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d2": {
                                    "path": "w1.1",
                                    "role": "Button",
                                    "name": "Save",
                                    "bbox": {"x": 20, "y": 50, "width": 100, "height": 30},
                                    "platform": "win32",
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {"ref": "d2", "action": "click", "snapshotAfter": False},
                            fail_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 64)
        self.assertIn("ref processId is invalid", result["stderr"])
        self.assertIn("desktop.snapshot again", result["stderr"])

    def test_desktop_act_rejects_fractional_target_process_id_before_fallback(self) -> None:
        def fail_run(*_args, **_kwargs):
            raise AssertionError("desktop.act should reject invalid target processId before native action")

        def fail_click(*_args, **_kwargs):
            raise AssertionError("desktop.act should reject invalid target processId before coordinate fallback")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "win32"
                state_path = Path(tmp) / "desktop-state.json"
                with (
                    mock.patch.object(visual_bridge, "_powershell_executable", return_value="powershell.exe"),
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path),
                ):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "win32",
                            "appName": "notepad",
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d2": {
                                    "path": "w1.1",
                                    "role": "Button",
                                    "name": "Save",
                                    "processId": 42.5,
                                    "bbox": {"x": 20, "y": 50, "width": 100, "height": 30},
                                    "platform": "win32",
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {"ref": "d2", "action": "click", "snapshotAfter": False},
                            fail_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 64)
        self.assertIn("target processId is invalid", result["stderr"])
        self.assertIn("desktop.snapshot again", result["stderr"])

    def test_desktop_act_refuses_coordinate_fallback_when_windows_target_is_not_foreground(self) -> None:
        def fake_run(command, **_kwargs):
            script = command[-1]
            self.assertIn("SetForegroundWindow", script)
            return {
                "command": command,
                "returnCode": 0,
                "stdout": json.dumps(
                    {
                        "name": "notepad",
                        "title": "Untitled - Notepad",
                        "processId": 42,
                        "foreground": False,
                        "activeProcessId": 7,
                    }
                ),
                "stderr": "",
            }

        def fail_click(*_args, **_kwargs):
            raise AssertionError("coordinate click should not run when Windows activation fails")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "win32"
                state_path = Path(tmp) / "desktop-state.json"
                with (
                    mock.patch.object(visual_bridge, "_powershell_executable", return_value="powershell.exe"),
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path),
                ):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "win32",
                            "appName": "notepad",
                            "processId": 42,
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d2": {
                                    "path": "w1.1",
                                    "role": "Button",
                                    "name": "Save",
                                    "bbox": {"x": 20, "y": 50, "width": 100, "height": 30},
                                    "platform": "win32",
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {"ref": "d2", "action": "click", "preferNative": False, "snapshotAfter": False},
                            fake_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertFalse(result["ok"])
        self.assertFalse(result["usedNativeAction"])
        self.assertFalse(result["targetActivation"]["foreground"])
        self.assertIn("window did not become foreground", result["stderr"])

    def test_desktop_act_rejects_unsupported_ref_action_before_coordinate_fallback(self) -> None:
        def fail_click(*_args, **_kwargs):
            raise AssertionError("coordinate fallback should not run for unsupported ref action")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "darwin",
                            "appName": "Calculator",
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d7": {
                                    "path": "p1.1.6",
                                    "role": "AXMenuBarItem",
                                    "name": "Help",
                                    "bbox": {"x": 300, "y": 0, "width": 50, "height": 30},
                                    "enabled": True,
                                    "supportedActions": ["click"],
                                    "nativeSupportedActions": ["click"],
                                    "platform": "darwin",
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {"ref": "d7", "action": "paste", "text": "wrong", "snapshotAfter": False},
                            lambda *_args, **_kwargs: {},
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 64)
        self.assertIn("action paste is not supported", result["stderr"])

    def test_desktop_act_rejects_disabled_ref(self) -> None:
        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "darwin",
                            "appName": "TextEdit",
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d7": {
                                    "path": "w1.3",
                                    "role": "AXButton",
                                    "name": "Save",
                                    "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                                    "enabled": False,
                                    "supportedActions": [],
                                    "nativeSupportedActions": [],
                                    "platform": "darwin",
                                }
                            },
                        }
                    )
                    result = visual_bridge.execute_desktop_act(
                        {"ref": "d7", "action": "click", "snapshotAfter": False},
                        lambda *_args, **_kwargs: {},
                    )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 64)
        self.assertIn("ref is disabled", result["stderr"])

    def test_desktop_act_require_native_rejects_bbox_only_ref(self) -> None:
        def fail_click(*_args, **_kwargs):
            raise AssertionError("coordinate fallback should not run when requireNative is unsupported")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "darwin",
                            "appName": "TextEdit",
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d7": {
                                    "path": "w1.3",
                                    "role": "AXGroup",
                                    "name": "Content",
                                    "bbox": {"x": 10, "y": 20, "width": 300, "height": 200},
                                    "enabled": True,
                                    "supportedActions": ["click", "type"],
                                    "nativeSupportedActions": ["click"],
                                    "platform": "darwin",
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {"ref": "d7", "action": "type", "text": "wrong", "requireNative": True, "snapshotAfter": False},
                            lambda *_args, **_kwargs: {},
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 64)
        self.assertIn("native action type is not supported", result["stderr"])

    def test_macos_activate_app_falls_back_to_open_when_applescript_times_out(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            if command[0] == "osascript":
                if "FOREGROUND" in command[-1]:
                    return {
                        "command": command,
                        "returnCode": 0,
                        "stdout": "FOREGROUND\ttrue\tCalculator\t123\tCalculator\t123",
                        "stderr": "",
                    }
                return {
                    "command": command,
                    "returnCode": None,
                    "timeout": True,
                    "stdout": "",
                    "stderr": "command timed out after 10.0s",
                }
            return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            with mock.patch.object(visual_bridge, "_ensure_activate_helper", return_value=None):
                result = visual_bridge.execute_activate_app({"appName": "Calculator"}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertEqual(result["activationFallback"], "open")
        self.assertEqual(calls[0][0], "osascript")
        self.assertEqual(calls[1], ["open", "-a", "Calculator"])
        self.assertEqual(calls[2][0], "osascript")
        self.assertTrue(result["foreground"])
        self.assertTrue(result["fallbackResult"]["returnCode"] == 0)

    def test_macos_activate_app_prefers_native_helper_when_target_is_running(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            self.assertEqual(command[0], "/tmp/atrium-macos-activate")
            return {
                "command": command,
                "returnCode": 0,
                "stdout": json.dumps(
                    {
                        "foreground": True,
                        "targetFound": True,
                        "processId": 123,
                        "processName": "TextEdit",
                        "activeProcessId": 123,
                        "activeProcessName": "TextEdit",
                        "axTrusted": True,
                        "axWindowCount": 1,
                        "nsActivated": True,
                    }
                ),
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            with mock.patch.object(visual_bridge, "_ensure_activate_helper", return_value=Path("/tmp/atrium-macos-activate")):
                result = visual_bridge.execute_activate_app({"appName": "TextEdit", "processId": 123}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:], ["123", "TextEdit", "", ""])
        self.assertEqual(result["returnCode"], 0)
        self.assertEqual(result["method"], "native_appkit_activation")
        self.assertEqual(result["activationBackend"], "appkit_ax")
        self.assertTrue(result["foreground"])
        self.assertEqual(result["processId"], 123)
        self.assertEqual(result["foregroundVerification"]["method"], "native_appkit_activation")

    def test_macos_activate_app_native_helper_returns_foreground_failure_without_applescript(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            self.assertEqual(command[0], "/tmp/atrium-macos-activate")
            return {
                "command": command,
                "returnCode": 1,
                "stdout": json.dumps(
                    {
                        "foreground": False,
                        "targetFound": True,
                        "processId": 123,
                        "processName": "TextEdit",
                        "activeProcessId": 764,
                        "activeProcessName": "Codex",
                        "axTrusted": True,
                        "axWindowCount": 0,
                        "nsActivated": True,
                        "error": "window did not become foreground",
                    }
                ),
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            with mock.patch.object(visual_bridge, "_ensure_activate_helper", return_value=Path("/tmp/atrium-macos-activate")):
                result = visual_bridge.execute_activate_app({"appName": "TextEdit", "processId": 123}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["returnCode"], 1)
        self.assertEqual(result["method"], "native_appkit_activation")
        self.assertTrue(result["targetFound"])
        self.assertFalse(result["foreground"])
        self.assertEqual(result["activeProcessName"], "Codex")
        self.assertIn("foreground", result["stderr"])

    def test_macos_activate_app_supports_process_id_only(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            self.assertEqual(command[0], "osascript")
            script = command[-1]
            self.assertIn('set targetPid to "123"', script)
            self.assertIn('set frontmost of targetProcess to true', script)
            self.assertNotIn('tell application "123" to activate', script)
            return {
                "command": command,
                "returnCode": 0,
                "stdout": "FOREGROUND\ttrue\tTextEdit\t123\tTextEdit\t123",
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            with mock.patch.object(visual_bridge, "_ensure_activate_helper", return_value=None):
                result = visual_bridge.execute_activate_app({"processId": 123}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["returnCode"], 0)
        self.assertTrue(result["foreground"])
        self.assertEqual(result["processId"], 123)
        self.assertEqual(result["processName"], "TextEdit")
        self.assertEqual(result["foregroundVerification"]["foreground"], True)

    def test_macos_open_app_rejects_process_id_only(self) -> None:
        def fake_run(_command, **_kwargs):
            raise AssertionError("desktop.open_app should reject processId-only before invoking OS commands")

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            with self.assertRaisesRegex(ValueError, "requires appName, bundleId, or path"):
                visual_bridge.execute_open_app({"processId": 123}, fake_run)
        finally:
            sys.platform = original_platform

    def test_desktop_app_tools_reject_explicit_zero_process_id(self) -> None:
        def fake_run(_command, **_kwargs):
            raise AssertionError("desktop app tools should reject processId=0 before invoking OS commands")

        with self.assertRaisesRegex(ValueError, "processId must be a positive integer"):
            visual_bridge.execute_activate_app({"appName": "Calculator", "processId": 0}, fake_run)

    def test_desktop_snapshot_rejects_explicit_zero_process_id(self) -> None:
        def fake_run(_command, **_kwargs):
            raise AssertionError("desktop.snapshot should reject processId=0 before invoking OS commands")

        with self.assertRaisesRegex(ValueError, "processId must be a positive integer"):
            visual_bridge.execute_desktop_snapshot({"appName": "Calculator", "processId": 0}, fake_run)

    def test_desktop_snapshot_accepts_whole_float_process_id(self) -> None:
        stdout = "\n".join(
            [
                "META\tappName\tCalculator",
                "META\tprocessId\t123",
                "META\ttitle\tCalculator",
                "ROW\tp1\tAXApplication\t\tCalculator\tapplication\t\tfalse\t\t\t\t\t0",
            ]
        )

        def fake_run(command, **_kwargs):
            self.assertIn('set targetPid to "123"', command[-1])
            return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            with mock.patch.object(visual_bridge, "_ensure_snapshot_helper", return_value=None):
                result = visual_bridge.execute_desktop_snapshot({"appName": "Calculator", "processId": 123.0}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertEqual(result["processId"], 123)

    def test_desktop_snapshot_preserves_large_string_process_id_exactly(self) -> None:
        large_pid = "9007199254740993"
        stdout = "\n".join(
            [
                "META\tappName\tCalculator",
                f"META\tprocessId\t{large_pid}",
                "META\ttitle\tCalculator",
                "ROW\tp1\tAXApplication\t\tCalculator\tapplication\t\tfalse\t\t\t\t\t0",
            ]
        )

        def fake_run(command, **_kwargs):
            self.assertIn(f'set targetPid to "{large_pid}"', command[-1])
            return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            with mock.patch.object(visual_bridge, "_ensure_snapshot_helper", return_value=None):
                result = visual_bridge.execute_desktop_snapshot({"appName": "Calculator", "processId": large_pid}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertEqual(result["processId"], int(large_pid))

    def test_desktop_snapshot_rejects_scientific_process_id(self) -> None:
        def fake_run(_command, **_kwargs):
            raise AssertionError("desktop.snapshot should reject scientific processId before invoking OS commands")

        with self.assertRaisesRegex(ValueError, "processId must be a positive integer"):
            visual_bridge.execute_desktop_snapshot({"appName": "Calculator", "processId": "1e3"}, fake_run)

    def test_desktop_snapshot_rejects_fractional_process_id(self) -> None:
        def fake_run(_command, **_kwargs):
            raise AssertionError("desktop.snapshot should reject fractional processId before invoking OS commands")

        with self.assertRaisesRegex(ValueError, "processId must be a positive integer"):
            visual_bridge.execute_desktop_snapshot({"appName": "Calculator", "processId": 123.5}, fake_run)

    def test_macos_quit_app_force_falls_back_to_pkill_when_applescript_times_out(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            if command[0] == "osascript":
                return {
                    "command": command,
                    "returnCode": None,
                    "timeout": True,
                    "stdout": "",
                    "stderr": "command timed out after 15.0s",
                }
            return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            result = visual_bridge.execute_quit_app(
                {"appName": "Calculator", "force": True, "forceDelaySeconds": 0},
                fake_run,
            )
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertEqual(calls[0][0], "osascript")
        self.assertEqual(calls[1], ["pkill", "-x", "Calculator"])
        self.assertEqual(result["forceResult"]["returnCode"], 0)

    def test_macos_quit_app_supports_process_id_only(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            self.assertNotEqual(command[0], "osascript")
            if command == ["/bin/kill", "-TERM", "123"]:
                return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
            if command == ["/bin/kill", "-0", "123"]:
                return {"command": command, "returnCode": 1, "stdout": "", "stderr": "No such process"}
            raise AssertionError(f"unexpected command: {command}")

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            result = visual_bridge.execute_quit_app({"processId": 123, "forceDelaySeconds": 0}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertTrue(result["quitVerified"])
        self.assertEqual(result["processId"], 123)
        self.assertEqual(calls, [["/bin/kill", "-TERM", "123"], ["/bin/kill", "-0", "123"]])

    def test_macos_quit_app_force_kills_process_id_when_term_does_not_exit(self) -> None:
        calls: list[list[str]] = []
        verify_count = 0

        def fake_run(command, **_kwargs):
            nonlocal verify_count
            calls.append(command)
            self.assertNotEqual(command[0], "osascript")
            if command == ["/bin/kill", "-TERM", "123"]:
                return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
            if command == ["/bin/kill", "-KILL", "123"]:
                return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
            if command == ["/bin/kill", "-0", "123"]:
                verify_count += 1
                return {
                    "command": command,
                    "returnCode": 0 if verify_count == 1 else 1,
                    "stdout": "",
                    "stderr": "" if verify_count == 1 else "No such process",
                }
            raise AssertionError(f"unexpected command: {command}")

        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            result = visual_bridge.execute_quit_app(
                {"appName": "Calculator", "processId": 123, "force": True, "forceDelaySeconds": 0},
                fake_run,
            )
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertTrue(result["quitVerified"])
        self.assertEqual(result["forceSignal"], "KILL")
        self.assertEqual(
            calls,
            [
                ["/bin/kill", "-TERM", "123"],
                ["/bin/kill", "-0", "123"],
                ["/bin/kill", "-KILL", "123"],
                ["/bin/kill", "-0", "123"],
            ],
        )

    def test_desktop_act_uses_macos_ax_helper_by_default(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            self.assertEqual(command[0], "/tmp/atrium-macos-ax-action")
            self.assertEqual(command[1:6], ["123", "TextEdit", "w1.3", "click", ""])
            self.assertEqual(command[6:], ["AXButton", "Save"])
            return {
                "command": command,
                "returnCode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "nativeAction": "AXPress",
                        "inputMethod": "accessibility",
                        "path": "w1.3",
                        "action": "click",
                    }
                ),
                "stderr": "",
            }

        def fail_click(*_args, **_kwargs):
            raise AssertionError("coordinate click should not run after native AX helper success")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with (
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path),
                    mock.patch.object(visual_bridge, "_ensure_ax_action_helper", return_value=Path("/tmp/atrium-macos-ax-action")),
                ):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "darwin",
                            "appName": "TextEdit",
                            "processId": 123,
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d7": {
                                    "path": "w1.3",
                                    "role": "AXButton",
                                    "name": "Save",
                                    "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                                    "platform": "darwin",
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {"ref": "d7", "action": "click", "snapshotAfter": False, "waitAfterMs": 0},
                            fake_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertTrue(result["ok"])
        self.assertTrue(result["usedNativeAction"])
        self.assertEqual(result["nativeAttempt"]["method"], "accessibility_ax_helper")
        self.assertEqual(result["nativeAttempt"]["nativeAction"], "AXPress")
        self.assertEqual(len(commands), 1)

    def test_desktop_act_uses_macos_ax_helper_for_scroll(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            self.assertEqual(command[0], "/tmp/atrium-macos-ax-action")
            self.assertEqual(command[1:6], ["123", "TextEdit", "w1.2", "scroll", "down:page:2"])
            self.assertEqual(command[6:], ["AXScrollArea", "Document"])
            return {
                "command": command,
                "returnCode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "nativeAction": "AXScrollDownByPage",
                        "inputMethod": "accessibility",
                        "path": "w1.2",
                        "action": "scroll",
                        "direction": "down",
                        "unit": "page",
                        "amount": 2,
                        "performed": 2,
                    }
                ),
                "stderr": "",
            }

        def fail_click(*_args, **_kwargs):
            raise AssertionError("coordinate click should not run after native AX scroll success")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with (
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path),
                    mock.patch.object(visual_bridge, "_ensure_ax_action_helper", return_value=Path("/tmp/atrium-macos-ax-action")),
                ):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "darwin",
                            "appName": "TextEdit",
                            "processId": 123,
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d8": {
                                    "path": "w1.2",
                                    "role": "AXScrollArea",
                                    "name": "Document",
                                    "bbox": {"x": 10, "y": 20, "width": 300, "height": 400},
                                    "nativeSupportedActions": ["scroll"],
                                    "supportedActions": ["scroll"],
                                    "platform": "darwin",
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {
                                "ref": "d8",
                                "action": "scroll",
                                "direction": "down",
                                "unit": "page",
                                "amount": 2,
                                "requireNative": True,
                                "snapshotAfter": False,
                                "waitAfterMs": 0,
                            },
                            fake_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertTrue(result["ok"])
        self.assertTrue(result["usedNativeAction"])
        self.assertEqual(result["nativeAttempt"]["method"], "accessibility_ax_helper")
        self.assertEqual(result["nativeAttempt"]["nativeAction"], "AXScrollDownByPage")
        self.assertEqual(result["nativeAttempt"]["helper"]["performed"], 2)
        self.assertEqual(len(commands), 1)

    def test_desktop_act_can_use_macos_applescript_accessibility_press_when_helper_disabled(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            self.assertEqual(command[0], "osascript")
            self.assertIn('perform action "AXPress"', command[-1])
            self.assertIn("cleanIdentityText(value of targetElement)", command[-1])
            self.assertIn('value of attribute "AXDescription" of targetElement', command[-1])
            self.assertIn('value of attribute "AXValue" of targetElement', command[-1])
            self.assertIn('if textValue is "button" then return ""', command[-1])
            self.assertIn('currentName is "button" or currentName is "text entry area"', command[-1])
            self.assertIn("hasExplicitTarget", command[-1])
            self.assertIn('error "target application process not found"', command[-1])
            self.assertIn("UI element (childIndex as integer)", command[-1])
            return {"command": command, "returnCode": 0, "stdout": "OK\tAXPress", "stderr": ""}

        def fail_click(*_args, **_kwargs):
            raise AssertionError("coordinate click should not run after native accessibility success")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "darwin",
                            "appName": "TextEdit",
                            "processId": 123,
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d7": {
                                    "path": "w1.3",
                                    "role": "AXButton",
                                    "name": "Save",
                                    "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                                    "platform": "darwin",
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {
                                "ref": "d7",
                                "action": "click",
                                "macosUseAxHelper": False,
                                "snapshotAfter": False,
                                "waitAfterMs": 0,
                            },
                            fake_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertTrue(result["ok"])
        self.assertTrue(result["usedNativeAction"])
        self.assertEqual(result["nativeAttempt"]["method"], "accessibility")
        self.assertEqual(result["nativeAttempt"]["nativeAction"], "AXPress")
        self.assertEqual(len(commands), 1)

    def test_desktop_act_falls_back_to_bbox_click_when_native_action_fails(self) -> None:
        click_calls: list[dict[str, object]] = []

        def fake_run(command, **_kwargs):
            self.assertEqual(command[0], "osascript")
            script = command[-1]
            if " to activate" in script:
                self.assertIn('tell application "TextEdit" to activate', script)
                self.assertNotIn('tell application "123"', script)
                return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
            if "FOREGROUND" in script:
                return {
                    "command": command,
                    "returnCode": 0,
                    "stdout": "FOREGROUND\ttrue\tTextEdit\t123\tTextEdit\t123",
                    "stderr": "",
                }
            return {"command": command, "returnCode": 0, "stdout": "FAIL\tAXPress unavailable", "stderr": ""}

        def fake_click(args, _run_process):
            click_calls.append(dict(args))
            return {"returnCode": 0, "stdout": "", "stderr": "", "x": args["x"], "y": args["y"]}

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "darwin",
                            "appName": "TextEdit",
                            "processId": 123,
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d7": {
                                    "path": "w1.3",
                                    "role": "AXButton",
                                    "name": "Save",
                                    "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                                    "platform": "darwin",
                                }
                            },
                        }
                    )
                    with (
                        mock.patch.object(visual_bridge, "execute_click", fake_click),
                        mock.patch.object(visual_bridge, "_ensure_activate_helper", return_value=None),
                    ):
                        result = visual_bridge.execute_desktop_act(
                            {
                                "ref": "d7",
                                "action": "click",
                                "macosUseAxHelper": False,
                                "snapshotAfter": False,
                                "waitAfterMs": 0,
                            },
                            fake_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertTrue(result["ok"])
        self.assertFalse(result["usedNativeAction"])
        self.assertEqual(result["nativeAttempt"]["returnCode"], 1)
        self.assertEqual(click_calls, [{"x": 25, "y": 40, "button": "left"}])
        self.assertEqual(result["steps"][0]["method"], "accessibility")
        self.assertEqual(result["steps"][1]["purpose"], "desktop.act coordinate/input fallback target activation")
        self.assertTrue(result["targetActivation"]["foreground"])

    def test_desktop_act_refuses_coordinate_fallback_when_macos_ref_identity_changed(self) -> None:
        def fake_run(command, **_kwargs):
            self.assertEqual(command[0], "osascript")
            self.assertIn("expectedRole", command[-1])
            self.assertIn("expectedName", command[-1])
            self.assertIn("MISMATCH", command[-1])
            return {
                "command": command,
                "returnCode": 0,
                "stdout": "MISMATCH\tdesktop.act ref name changed from Save to Delete; call desktop.snapshot again",
                "stderr": "",
            }

        def fail_click(*_args, **_kwargs):
            raise AssertionError("coordinate click should not run after native identity mismatch")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "darwin",
                            "appName": "TextEdit",
                            "processId": 123,
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d7": {
                                    "path": "w1.3",
                                    "role": "AXButton",
                                    "name": "Save",
                                    "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                                    "platform": "darwin",
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {
                                "ref": "d7",
                                "action": "click",
                                "macosUseAxHelper": False,
                                "snapshotAfter": False,
                                "waitAfterMs": 0,
                            },
                            fake_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertFalse(result["ok"])
        self.assertFalse(result["usedNativeAction"])
        self.assertTrue(result["nativeAttempt"]["identityMismatch"])
        self.assertIn("ref name changed", result["stderr"])

    def test_desktop_act_refuses_coordinate_fallback_when_macos_target_is_missing(self) -> None:
        def fake_run(command, **_kwargs):
            self.assertEqual(command[0], "osascript")
            self.assertIn("target application process not found", command[-1])
            return {
                "command": command,
                "returnCode": 1,
                "stdout": "",
                "stderr": "target application process not found",
            }

        def fail_click(*_args, **_kwargs):
            raise AssertionError("coordinate click should not run when the explicit macOS target is missing")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "darwin",
                            "appName": "DefinitelyMissingATRIUMProbeApp",
                            "processId": 987654,
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d7": {
                                    "path": "w1.3",
                                    "role": "AXButton",
                                    "name": "Save",
                                    "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                                    "platform": "darwin",
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {
                                "ref": "d7",
                                "action": "click",
                                "macosUseAxHelper": False,
                                "snapshotAfter": False,
                                "waitAfterMs": 0,
                            },
                            fake_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertFalse(result["ok"])
        self.assertFalse(result["usedNativeAction"])
        self.assertTrue(result["refUnavailable"])
        self.assertIn("target application process not found", result["stderr"])
        self.assertIn("refusing coordinate/input fallback", result["stderr"])

    def test_desktop_act_require_native_refuses_coordinate_fallback(self) -> None:
        def fake_run(command, **_kwargs):
            self.assertEqual(command[0], "osascript")
            return {"command": command, "returnCode": 0, "stdout": "FAIL\tAXPress unavailable", "stderr": ""}

        def fail_click(*_args, **_kwargs):
            raise AssertionError("coordinate click should not run when requireNative is true")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "darwin",
                            "appName": "TextEdit",
                            "processId": 123,
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d7": {
                                    "path": "w1.3",
                                    "role": "AXButton",
                                    "name": "Save",
                                    "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                                    "platform": "darwin",
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {
                                "ref": "d7",
                                "action": "click",
                                "requireNative": True,
                                "macosUseAxHelper": False,
                                "snapshotAfter": False,
                                "waitAfterMs": 0,
                            },
                            fake_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertFalse(result["ok"])
        self.assertFalse(result["usedNativeAction"])
        self.assertEqual(result["nativeAttempt"]["returnCode"], 1)
        self.assertEqual(result["steps"][0]["method"], "accessibility")

    def test_desktop_act_refuses_coordinate_fallback_when_native_action_times_out(self) -> None:
        click_calls: list[dict[str, object]] = []

        def timeout_run(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout"), output=b"", stderr=b"")

        def fake_click(args, _run_process):
            click_calls.append(dict(args))
            return {"returnCode": 0, "stdout": "", "stderr": "", "x": args["x"], "y": args["y"]}

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "darwin"
                state_path = Path(tmp) / "desktop-state.json"
                with mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "darwin",
                            "appName": "TextEdit",
                            "processId": 123,
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d7": {
                                    "path": "w1.3",
                                    "role": "AXButton",
                                    "name": "Save",
                                    "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                                    "platform": "darwin",
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fake_click):
                        result = visual_bridge.execute_desktop_act(
                            {
                                "ref": "d7",
                                "action": "click",
                                "macosUseAxHelper": False,
                                "snapshotAfter": False,
                                "waitAfterMs": 0,
                            },
                            timeout_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertIsNone(result["returnCode"])
        self.assertFalse(result["ok"])
        self.assertFalse(result["usedNativeAction"])
        self.assertTrue(result["nativeAttempt"]["timeout"])
        self.assertIn("native action timed out", result["stderr"])
        self.assertIn("refusing coordinate/input fallback", result["stderr"])
        self.assertEqual(click_calls, [])

    def test_desktop_act_prefers_windows_uia_invoke_pattern(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            script = command[-1]
            self.assertIn("UIAutomationClient", script)
            self.assertIn("InvokePattern", script)
            self.assertIn("TryGetCurrentPattern", script)
            self.assertIn("TogglePattern", script)
            self.assertIn("SelectionItemPattern", script)
            self.assertIn("ValuePattern", script)
            self.assertIn("$expectedRole = 'Button'", script)
            self.assertIn("$expectedName = 'Save'", script)
            self.assertIn("Exit-AtriumIdentityMismatch", script)
            self.assertIn("$targetPath = 'w1.1'", script)
            return {
                "command": command,
                "returnCode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "nativeAction": "InvokePattern",
                        "inputMethod": "uia",
                        "path": "w1.1",
                        "action": "click",
                    }
                ),
                "stderr": "",
            }

        def fail_click(*_args, **_kwargs):
            raise AssertionError("coordinate click should not run after UIA InvokePattern success")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "win32"
                state_path = Path(tmp) / "desktop-state.json"
                with (
                    mock.patch.object(visual_bridge, "_powershell_executable", return_value="powershell.exe"),
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path),
                ):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "win32",
                            "appName": "notepad",
                            "processId": 42,
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d2": {
                                    "path": "w1.1",
                                    "role": "Button",
                                    "name": "Save",
                                    "bbox": {"x": 20, "y": 50, "width": 100, "height": 30},
                                    "platform": "win32",
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {"ref": "d2", "action": "click", "snapshotAfter": False, "waitAfterMs": 0},
                            fake_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertTrue(result["ok"])
        self.assertTrue(result["usedNativeAction"])
        self.assertEqual(result["nativeAttempt"]["method"], "uia")
        self.assertEqual(result["nativeAttempt"]["nativeAction"], "InvokePattern")
        self.assertEqual(len(commands), 1)

    def test_desktop_act_refuses_coordinate_fallback_when_windows_ref_identity_changed(self) -> None:
        def fake_run(command, **_kwargs):
            script = command[-1]
            self.assertIn("Exit-AtriumIdentityMismatch", script)
            return {
                "command": command,
                "returnCode": 1,
                "stdout": json.dumps(
                    {
                        "ok": False,
                        "identityMismatch": True,
                        "error": "desktop.act ref role changed from Button to Edit; call desktop.snapshot again",
                        "path": "w1.1",
                        "action": "click",
                        "expectedRole": "Button",
                        "currentRole": "Edit",
                    }
                ),
                "stderr": "",
            }

        def fail_click(*_args, **_kwargs):
            raise AssertionError("coordinate click should not run after UIA identity mismatch")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "win32"
                state_path = Path(tmp) / "desktop-state.json"
                with (
                    mock.patch.object(visual_bridge, "_powershell_executable", return_value="powershell.exe"),
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path),
                ):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "win32",
                            "appName": "notepad",
                            "processId": 42,
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d2": {
                                    "path": "w1.1",
                                    "role": "Button",
                                    "name": "Save",
                                    "bbox": {"x": 20, "y": 50, "width": 100, "height": 30},
                                    "platform": "win32",
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {"ref": "d2", "action": "click", "snapshotAfter": False, "waitAfterMs": 0},
                            fake_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertFalse(result["ok"])
        self.assertFalse(result["usedNativeAction"])
        self.assertTrue(result["nativeAttempt"]["identityMismatch"])
        self.assertIn("ref role changed", result["stderr"])

    def test_desktop_act_refuses_coordinate_fallback_when_windows_uia_path_is_missing(self) -> None:
        def fake_run(command, **_kwargs):
            script = command[-1]
            self.assertIn("UIAutomation path segment not found", script)
            return {
                "command": command,
                "returnCode": 1,
                "stdout": json.dumps(
                    {
                        "ok": False,
                        "error": "UIAutomation path segment not found: w1.4",
                        "path": "w1.4",
                        "action": "click",
                    }
                ),
                "stderr": "",
            }

        def fail_click(*_args, **_kwargs):
            raise AssertionError("coordinate click should not run when the UIA path is missing")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "win32"
                state_path = Path(tmp) / "desktop-state.json"
                with (
                    mock.patch.object(visual_bridge, "_powershell_executable", return_value="powershell.exe"),
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path),
                ):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "win32",
                            "appName": "notepad",
                            "processId": 42,
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d2": {
                                    "path": "w1.4",
                                    "role": "Button",
                                    "name": "Save",
                                    "bbox": {"x": 20, "y": 50, "width": 100, "height": 30},
                                    "platform": "win32",
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {"ref": "d2", "action": "click", "snapshotAfter": False, "waitAfterMs": 0},
                            fake_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertFalse(result["ok"])
        self.assertFalse(result["usedNativeAction"])
        self.assertTrue(result["refUnavailable"])
        self.assertIn("UIAutomation path segment not found", result["stderr"])
        self.assertIn("refusing coordinate/input fallback", result["stderr"])
        self.assertIn("UIAutomation path segment not found", result["nativeAttempt"]["helper"]["error"])

    def test_desktop_act_windows_uia_does_not_fall_back_when_explicit_target_is_missing(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            script = command[-1]
            self.assertIn("$hasExplicitTarget = [bool]($targetPidText -or $targetName)", script)
            self.assertIn("target application process not found", script)
            self.assertLess(
                script.index("target application process not found"),
                script.index("[ATRIUM.Win32NativeAction]::GetForegroundWindow()"),
            )
            return {
                "command": command,
                "returnCode": 1,
                "stdout": json.dumps({"ok": False, "error": "target application process not found"}),
                "stderr": "",
            }

        def fail_click(*_args, **_kwargs):
            raise AssertionError("coordinate click should not run when explicit UIA target is missing")

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "win32"
                state_path = Path(tmp) / "desktop-state.json"
                with (
                    mock.patch.object(visual_bridge, "_powershell_executable", return_value="powershell.exe"),
                    mock.patch.object(visual_bridge, "_desktop_state_path", return_value=state_path),
                ):
                    visual_bridge._write_desktop_state(
                        {
                            "platform": "win32",
                            "appName": "DefinitelyMissingATRIUMProbeApp",
                            "processId": 987654,
                            "updatedAt": 9_999_999_999_999,
                            "refs": {
                                "d2": {
                                    "path": "w1.1",
                                    "role": "Button",
                                    "name": "Save",
                                    "bbox": {"x": 20, "y": 50, "width": 100, "height": 30},
                                    "platform": "win32",
                                    "supportedActions": ["click"],
                                    "nativeSupportedActions": ["click"],
                                }
                            },
                        }
                    )
                    with mock.patch.object(visual_bridge, "execute_click", fail_click):
                        result = visual_bridge.execute_desktop_act(
                            {
                                "ref": "d2",
                                "action": "click",
                                "requireNative": True,
                                "snapshotAfter": False,
                                "waitAfterMs": 0,
                            },
                            fake_run,
                        )
            finally:
                sys.platform = original_platform

        self.assertEqual(len(commands), 1)
        self.assertEqual(result["returnCode"], 1)
        self.assertFalse(result["ok"])
        self.assertFalse(result["usedNativeAction"])
        self.assertIn("target application process not found", result["stderr"])
        self.assertIn("target application process not found", result["nativeAttempt"]["helper"]["error"])

    def test_catalog_risk_schema_and_host_bridge_include_desktop_ref_tools(self) -> None:
        by_tool = {row["tool"]: row for row in repo_module.TOOL_CATALOG}

        self.assertIn("desktop.snapshot", schema_module.BUILTIN_TOOL_NAMES)
        self.assertIn("desktop.act", schema_module.BUILTIN_TOOL_NAMES)
        self.assertEqual(by_tool["desktop.snapshot"]["riskClass"], "safe_read")
        self.assertFalse(by_tool["desktop.snapshot"]["mutatesState"])
        self.assertEqual(by_tool["desktop.act"]["riskClass"], "desktop")
        self.assertTrue(by_tool["desktop.act"]["mutatesState"])
        self.assertIn("snapshot", by_tool["desktop.snapshot"]["outputSchema"]["properties"])
        self.assertIn("actionableRefCount", by_tool["desktop.snapshot"]["outputSchema"]["properties"])
        self.assertIn("nativeActionableRefCount", by_tool["desktop.snapshot"]["outputSchema"]["properties"])
        self.assertIn("requireNative", by_tool["desktop.act"]["inputSchema"])
        self.assertIn("redact_public_ui_captures", by_tool["desktop.act"]["redactionRules"])
        self.assertIn("processId", by_tool["desktop.activate_app"]["description"])
        self.assertNotIn("On Windows prefer processId", by_tool["desktop.activate_app"]["description"])
        self.assertIn("processId", by_tool["desktop.quit_app"]["description"])
        self.assertNotIn("On Windows prefer processId", by_tool["desktop.quit_app"]["description"])
        self.assertEqual(
            main_module._tool_risk_class({"tool": "desktop.snapshot", "departmentId": "exec", "args": {}}),
            "safe_read",
        )
        self.assertEqual(
            main_module._tool_risk_class({"tool": "desktop.act", "departmentId": "exec", "args": {"ref": "d1"}}),
            "desktop",
        )

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
            isolated_browser_profile_ready=False,
            isolated_browser_profile_app=None,
            isolated_browser_profile_executable=None,
            browser_playwright_ready=False,
            browser_playwright_package=None,
            browser_playwright_error=None,
            desktop_bridge_executable="/usr/bin/osascript",
            desktop_bridge=False,
            desktop_automation_ready=False,
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
            mock.patch.object(host_bridge.shutil, "which", return_value="/usr/bin/osascript"),
            mock.patch.object(host_bridge, "_macos_accessibility_enabled", return_value=True),
        ):
            bridge = host_bridge.HostBridge()
            self.assertTrue(bridge.can_run("desktop.snapshot")[0])
            allowed, reason = bridge.can_run("desktop.act")

        self.assertFalse(allowed)
        self.assertEqual(reason, "darwin desktop bridge unavailable")

        with (
            mock.patch.object(host_bridge.HostBridge, "status", return_value=fake_status),
            mock.patch.object(host_bridge.shutil, "which", return_value="/usr/bin/osascript"),
            mock.patch.object(host_bridge, "_macos_accessibility_enabled", return_value=False),
        ):
            allowed, reason = host_bridge.HostBridge().can_run("desktop.snapshot")
            act_allowed, act_reason = host_bridge.HostBridge().can_run("desktop.act")

        self.assertFalse(allowed)
        self.assertEqual(reason, "macOS Accessibility permission is disabled for System Events")
        self.assertFalse(act_allowed)
        self.assertEqual(act_reason, "macOS Accessibility permission is disabled for System Events")


if __name__ == "__main__":
    unittest.main()
