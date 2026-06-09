import importlib.util
import asyncio
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import chat_tools, mcp_local
from app import main as main_module
from app.db import repo as repo_module
from app.host_bridge_proof import host_bridge_parity_proof_id
from app.provider import chatgpt_oauth
from app.tools import host_bridge, visual_bridge


REPO_ROOT = Path(__file__).resolve().parents[2]
WINDOWS_INTERACTIVE_NATIVE_TEXT = "ATRIUM Windows ValuePattern probe ไทย"


def _fake_png(width: int = 100, height: int = 80) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + int(width).to_bytes(4, "big")
        + int(height).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )


def _load_windows_probe_module():
    spec = importlib.util.spec_from_file_location(
        "windows_host_bridge_probe",
        REPO_ROOT / "ops" / "windows_host_bridge_probe.py",
    )
    if spec is None or spec.loader is None:
        raise AssertionError("windows_host_bridge_probe.py could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _windows_preflight_ok(_run_process: object) -> dict[str, object]:
    return {
        "returnCode": 0,
        "ok": True,
        "checks": {
            "winForms": True,
            "drawing": True,
            "virtualScreen": True,
            "systemIcon": True,
            "setClipboardCommand": True,
            "getClipboardCommand": True,
            "dpiAwareness": True,
        },
        "virtualScreen": {"left": 0, "top": 0, "width": 1920, "height": 1080},
    }


def _windows_helper_selftest_ok(_run_process: object) -> dict[str, object]:
    return {
        "returnCode": 0,
        "ok": True,
        "dpiAwareness": "per_monitor_v2",
        "screenWidth": 1920,
        "screenHeight": 1080,
        "virtualLeft": 0,
        "virtualTop": 0,
        "virtualWidth": 1920,
        "virtualHeight": 1080,
    }


def _windows_foreground_activation_ok(process_id: int = 42) -> dict[str, object]:
    return {
        "returnCode": 0,
        "processId": process_id,
        "activeProcessId": process_id,
        "foreground": True,
        "stdout": "",
        "stderr": "",
    }


def _utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def _windows_type_text_ok(text: str = "ATRIUM Windows HostBridge probe ไทย") -> dict[str, object]:
    return {
        "returnCode": 0,
        "textBytes": len(text.encode("utf-8")),
        "textCharacters": len(text),
        "textUnits": _utf16_units(text),
        "stdout": "",
        "stderr": "",
    }


def _windows_keypress_ok(args: dict[str, object]) -> dict[str, object]:
    keys = [str(item).lower() for item in args.get("keys") or []]
    modifiers = [item for item in keys if item in {"control", "shift", "alt", "win"}]
    key = next((item for item in keys if item not in modifiers), "")
    return {
        "returnCode": 0,
        "key": key,
        "modifiers": modifiers,
        "stdout": "",
        "stderr": "",
    }


def _windows_native_value_pattern_ok(text: str = WINDOWS_INTERACTIVE_NATIVE_TEXT) -> dict[str, object]:
    return {
        "returnCode": 0,
        "usedNativeAction": True,
        "nativeAttempt": {
            "returnCode": 0,
            "method": "uia",
            "inputMethod": "uia",
            "nativeAction": "ValuePattern",
            "ok": True,
        },
        "after": {
            "returnCode": 0,
            "snapshot": {"elements": [{"role": "Edit", "value": text}]},
        },
    }


def _verified_parity_proofs() -> dict[str, dict[str, bool]]:
    common = {
        "browserOpen": True,
        "browserOpenIsolatedProfile": True,
        "browserSnapshot": True,
        "browserSnapshotIsolatedPlaywright": True,
        "browserAct": True,
        "browserActIsolatedPlaywright": True,
        "browserActVerified": True,
        "appsDiscovery": True,
        "screenshotFile": True,
        "notification": True,
        "desktopAutomationReady": True,
    }
    return {
        "macos": {
            **common,
            "foregroundSession": True,
            "appleScriptClipboard": True,
            "foregroundSnapshotNative": True,
            "appsNativeNSWorkspace": True,
            "macosNativeActionMetadata": True,
            "calculatorNativeAct": True,
            "textEditNativeAct": True,
            "textEditNativeScroll": True,
        },
        "windows": {
            **common,
            "interactiveSession": True,
            "windowsInteractiveSessionIdentity": True,
            "windowsVisualPreflight": True,
            "helperSelftest": True,
            "powershellPreflight": True,
            "windowsDpiAwareness": True,
            "windowsVirtualScreen": True,
            "windowsForegroundActivation": True,
            "windowsUnicodeTyping": True,
            "windowsKeyboardShortcut": True,
            "notepadNativeAct": True,
            "clipboardRoundTrip": True,
        },
    }


def _verified_parity_report(
    *,
    generated_at: int | None = None,
    macos_fingerprint: str = "a" * 64,
    windows_fingerprint: str = "a" * 64,
    macos_git_head: str = "b" * 40,
    windows_git_head: str = "b" * 40,
    include_artifact_provenance: bool = True,
) -> dict[str, object]:
    now = main_module.now_ms()
    proofs = _verified_parity_proofs()
    macos: dict[str, object] = {"present": True, "ok": True, "proofSchemaVersion": 1, "proofs": proofs["macos"]}
    windows: dict[str, object] = {"present": True, "ok": True, "proofSchemaVersion": 1, "proofs": proofs["windows"]}
    if include_artifact_provenance:
        macos.update({
            "schemaVersion": 1,
            "generatedAt": now,
            "artifactBytes": 1024,
            "artifactSha256": "1" * 64,
            "sourceFingerprint": macos_fingerprint,
            "sourceManifestSha256": macos_fingerprint,
            "sourceFileCount": len(main_module.SOURCE_FINGERPRINT_FILES),
            "gitHead": macos_git_head,
            "gitDirty": False,
            "parityRunId": "parity-run-1",
            "hostFingerprint": "c" * 64,
            "hostPlatform": "darwin",
            "hostName": "atrium-macos",
            "hostMachine": "arm64",
        })
        windows.update({
            "schemaVersion": 1,
            "generatedAt": now,
            "artifactBytes": 2048,
            "artifactSha256": "2" * 64,
            "sourceFingerprint": windows_fingerprint,
            "sourceManifestSha256": windows_fingerprint,
            "sourceFileCount": len(main_module.SOURCE_FINGERPRINT_FILES),
            "gitHead": windows_git_head,
            "gitDirty": False,
            "parityRunId": "parity-run-1",
            "hostFingerprint": "d" * 64,
            "hostPlatform": "win32",
            "hostName": "atrium-windows",
            "hostMachine": "AMD64",
        })
    report = {
        "schemaVersion": 1,
        "proofSchemaVersion": 1,
        "generatedAt": now if generated_at is None else generated_at,
        "ok": True,
        "summary": "full live HostBridge parity proof is complete",
        "findings": [],
        "results": {
            "macos": macos,
            "windows": windows,
        },
    }
    report["proofId"] = host_bridge_parity_proof_id(
        report["results"],
        {"sourceFingerprint": "a" * 64, "sourceManifestSha256": "a" * 64, "sourceFileCount": len(main_module.SOURCE_FINGERPRINT_FILES), "gitHead": "b" * 40, "gitDirty": False},
        enforce_current_source=True,
    )
    return report


class WindowsHostBridgeTest(unittest.TestCase):
    def test_windows_live_proof_runner_refreshes_common_paths_and_reports_cli_next_step(self) -> None:
        script = (REPO_ROOT / "ops" / "windows_host_bridge_live_proof.ps1").read_text(encoding="utf-8")

        self.assertIn("function Add-PathIfExists", script)
        self.assertIn("AppData\\Roaming\\npm", script)
        self.assertIn("$UvCommand = Get-Command uv", script)
        self.assertIn("& $UvPath @Arguments", script)
        self.assertIn("SourceManifestSha256", script)
        self.assertIn("SourceFileCount", script)
        self.assertIn("--expect-source-manifest-sha256", script)
        self.assertIn("--expect-source-file-count", script)
        self.assertIn("$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptDirectory '..'))", script)
        self.assertIn("'system/pyproject.toml'", script)
        self.assertIn("Set-Location -LiteralPath $RepoRoot", script)
        self.assertIn("Set-Location -LiteralPath $PreviousLocation", script)
        self.assertIn("./atrium automation report", script)

    def _connector_catalog_for_test(
        self,
        fake_host_bridge: object,
        fake_profiles: dict[str, object],
        *,
        parity_report: dict[str, object] | None = None,
        current_source: dict[str, object] | None = None,
    ) -> dict[str, dict[str, object]]:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "host-bridge-parity-report.json"
            if parity_report is not None:
                report_path.write_text(json.dumps(parity_report), encoding="utf-8")
            settings = main_module.get_settings().model_copy(update={"host_bridge_parity_report_path": report_path})
            source = current_source or {
                "sourceFingerprint": "a" * 64,
                "sourceManifestSha256": "a" * 64,
                "sourceFileCount": len(main_module.SOURCE_FINGERPRINT_FILES),
                "gitHead": "b" * 40,
                "gitDirty": False,
            }
            with (
                mock.patch.object(main_module, "HostBridge", fake_host_bridge),
                mock.patch.object(main_module, "list_browser_profiles", lambda: fake_profiles),
                mock.patch.object(main_module, "get_settings", lambda: settings),
                mock.patch.object(main_module, "host_bridge_source_provenance", lambda: source),
            ):
                return {item["id"]: item for item in main_module._connector_catalog()}

    def _host_bridge_parity_status_for_test(
        self,
        fake_host_bridge: object,
        fake_profiles: dict[str, object],
        *,
        parity_report: dict[str, object] | None = None,
        current_source: dict[str, object] | None = None,
    ) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "host-bridge-parity-report.json"
            if parity_report is not None:
                report_path.write_text(json.dumps(parity_report), encoding="utf-8")
            settings = main_module.get_settings().model_copy(update={"host_bridge_parity_report_path": report_path})
            source = current_source or {
                "sourceFingerprint": "a" * 64,
                "sourceManifestSha256": "a" * 64,
                "sourceFileCount": len(main_module.SOURCE_FINGERPRINT_FILES),
                "gitHead": "b" * 40,
                "gitDirty": False,
            }
            with (
                mock.patch.object(main_module, "HostBridge", fake_host_bridge),
                mock.patch.object(main_module, "list_browser_profiles", lambda: fake_profiles),
                mock.patch.object(main_module, "get_settings", lambda: settings),
                mock.patch.object(main_module, "host_bridge_source_provenance", lambda: source),
            ):
                return main_module._host_bridge_parity_status_payload()

    def test_host_bridge_uses_known_windows_shell_paths_without_path_env(self) -> None:
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        cmd = "C:/Windows/System32/cmd.exe"

        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/") in {powershell, cmd}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(host_bridge.shutil, "which", return_value=None),
                mock.patch.object(Path, "exists", fake_exists),
                mock.patch.dict(os.environ, {"SESSIONNAME": "Console"}, clear=False),
            ):
                status = host_bridge.HostBridge().status().to_dict()
                self.assertEqual(status["shellExecutable"], powershell)
                self.assertEqual(status["browserBridgeExecutable"], powershell)
                self.assertEqual(status["desktopBridgeExecutable"], powershell)
                self.assertFalse(status["isolatedBrowserProfileReady"])
                self.assertIsNone(status["isolatedBrowserProfileExecutable"])
                self.assertTrue(host_bridge.HostBridge().can_run("browser.screenshot")[0])
                self.assertTrue(host_bridge.HostBridge().can_run("desktop.screenshot")[0])
                self.assertTrue(host_bridge.HostBridge().can_run("notify.send")[0])
        finally:
            sys.platform = original_platform

    def test_host_bridge_blocks_visual_tools_when_windows_session_cannot_be_verified(self) -> None:
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        cmd = "C:/Windows/System32/cmd.exe"

        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/") in {powershell, cmd}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(host_bridge.shutil, "which", return_value=None),
                mock.patch.object(Path, "exists", fake_exists),
                mock.patch.object(host_bridge, "_windows_current_session_id", return_value=None),
                mock.patch.dict(os.environ, {"SESSIONNAME": ""}, clear=False),
            ):
                bridge = host_bridge.HostBridge()
                status = bridge.status().to_dict()
                self.assertFalse(status["browserAutomationReady"])
                self.assertFalse(status["desktopAutomationReady"])
                self.assertIsNone(status["interactiveSession"])
                self.assertTrue(bridge.can_run("browser.profiles")[0])
                self.assertTrue(bridge.can_run("desktop.apps")[0])
                allowed, reason = bridge.can_run("browser.screenshot")
        finally:
            sys.platform = original_platform

        self.assertFalse(allowed)
        self.assertEqual(reason, "win32 interactive desktop session could not be verified")

    def test_host_bridge_blocks_visual_tools_for_unknown_windows_session_name(self) -> None:
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        cmd = "C:/Windows/System32/cmd.exe"

        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/") in {powershell, cmd}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(host_bridge.shutil, "which", return_value=None),
                mock.patch.object(Path, "exists", fake_exists),
                mock.patch.object(host_bridge, "_windows_current_session_id", return_value=None),
                mock.patch.dict(os.environ, {"SESSIONNAME": "UnknownSession"}, clear=False),
            ):
                bridge = host_bridge.HostBridge()
                status = bridge.status().to_dict()
                self.assertIsNone(status["interactiveSession"])
                self.assertEqual(status["interactiveSessionName"], "UnknownSession")
                self.assertFalse(status["browserAutomationReady"])
                self.assertFalse(status["desktopAutomationReady"])
                self.assertTrue(bridge.can_run("browser.profiles")[0])
                self.assertTrue(bridge.can_run("desktop.apps")[0])
                allowed, reason = bridge.can_run("desktop.screenshot")
        finally:
            sys.platform = original_platform

        self.assertFalse(allowed)
        self.assertEqual(reason, "win32 interactive desktop session could not be verified")

    def test_isolated_browser_profile_open_is_args_gated_when_browser_app_missing(self) -> None:
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        cmd = "C:/Windows/System32/cmd.exe"

        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/") in {powershell, cmd}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(host_bridge.shutil, "which", return_value=None),
                mock.patch.object(Path, "exists", fake_exists),
                mock.patch.dict(os.environ, {"SESSIONNAME": "Console"}, clear=False),
            ):
                bridge = host_bridge.HostBridge()
                self.assertTrue(bridge.can_run("browser.open", {"profile": "user"})[0])
                allowed, reason = bridge.can_run("browser.open", {"profile": "atrium"})
                user_route = main_module._build_tool_route("browser.open", args={"profile": "user"})
                isolated_route = main_module._build_tool_route("browser.open", args={"profile": "atrium"})
                api_reason = main_module._tool_runtime_block_reason(
                    {"tool": "browser.open", "args": {"profile": "atrium", "url": "https://example.com"}}
                )
                chat_reason = chat_tools._owner_runtime_block(
                    {"tool": "browser.open", "args": {"profile": "atrium", "url": "https://example.com"}}
                )
        finally:
            sys.platform = original_platform

        self.assertFalse(allowed)
        self.assertEqual(reason, "isolated browser profile requires Chrome, Edge, Brave, or Chromium")
        self.assertIsNone(user_route["blockReason"])
        self.assertEqual(isolated_route["blockReason"], reason)
        self.assertEqual(api_reason, reason)
        self.assertEqual(chat_reason, reason)

    def test_host_bridge_reports_isolated_browser_profile_app_separately_on_windows(self) -> None:
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        cmd = "C:/Windows/System32/cmd.exe"
        chrome = "C:/Program Files/Google/Chrome/Application/chrome.exe"

        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/") in {powershell, cmd, chrome}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(host_bridge.shutil, "which", return_value=None),
                mock.patch.object(Path, "exists", fake_exists),
                mock.patch.dict(os.environ, {"SESSIONNAME": "Console"}, clear=False),
            ):
                status = host_bridge.HostBridge().status().to_dict()
        finally:
            sys.platform = original_platform

        self.assertTrue(status["browserAutomationReady"])
        self.assertTrue(status["isolatedBrowserProfileReady"])
        self.assertEqual(status["isolatedBrowserProfileApp"], "Google Chrome")
        self.assertEqual(status["isolatedBrowserProfileExecutable"], chrome)

    def test_host_bridge_windows_visual_preflight_requires_dpi_awareness(self) -> None:
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        cmd = "C:/Windows/System32/cmd.exe"
        failed_preflight = {
            "checked": True,
            "ok": False,
            "error": "failed checks: dpiAwareness",
            "checks": {
                "winForms": True,
                "drawing": True,
                "virtualScreen": True,
                "systemIcon": True,
                "setClipboardCommand": True,
                "getClipboardCommand": True,
                "dpiAwareness": False,
            },
        }

        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/") in {powershell, cmd}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(host_bridge.shutil, "which", return_value=None),
                mock.patch.object(host_bridge, "_windows_visual_preflight", return_value=failed_preflight),
                mock.patch.object(Path, "exists", fake_exists),
                mock.patch.dict(os.environ, {"SESSIONNAME": "Console"}, clear=False),
            ):
                bridge = host_bridge.HostBridge()
                status = bridge.status().to_dict()
                screenshot_allowed, screenshot_reason = bridge.can_run("browser.screenshot")
                snapshot_allowed, snapshot_reason = bridge.can_run("desktop.snapshot")
                open_allowed, open_reason = bridge.can_run("browser.open", {"profile": "user"})
                apps_allowed, apps_reason = bridge.can_run("desktop.apps")
        finally:
            sys.platform = original_platform

        self.assertTrue(status["windowsVisualPreflightChecked"])
        self.assertFalse(status["windowsVisualPreflightOk"])
        self.assertFalse(status["windowsVisualPreflightChecks"]["dpiAwareness"])
        self.assertEqual(status["windowsVisualPreflightError"], "failed checks: dpiAwareness")
        self.assertFalse(status["browserAutomationReady"])
        self.assertFalse(status["desktopAutomationReady"])
        self.assertFalse(screenshot_allowed)
        self.assertEqual(screenshot_reason, "win32 visual automation preflight failed: failed checks: dpiAwareness")
        self.assertFalse(snapshot_allowed)
        self.assertEqual(snapshot_reason, "win32 visual automation preflight failed: failed checks: dpiAwareness")
        self.assertTrue(open_allowed)
        self.assertIsNone(open_reason)
        self.assertTrue(apps_allowed)
        self.assertIsNone(apps_reason)

    def test_host_bridge_windows_visual_preflight_script_checks_dpi_awareness(self) -> None:
        calls: list[list[str]] = []

        class FakePath:
            def __init__(self, value: object):
                self.value = str(value)

            @property
            def name(self) -> str:
                return self.value.replace("\\", "/").rsplit("/", 1)[-1]

        def fake_run(command: list[str], **_: object) -> object:
            calls.append(command)
            return type(
                "Completed",
                (),
                {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {
                            "ok": False,
                            "checks": {
                                "winForms": True,
                                "drawing": True,
                                "virtualScreen": True,
                                "systemIcon": True,
                                "setClipboardCommand": True,
                                "getClipboardCommand": True,
                                "dpiAwareness": False,
                            },
                            "errors": {},
                        },
                        separators=(",", ":"),
                    ),
                    "stderr": "",
                },
            )()

        original_platform = sys.platform
        original_cache = host_bridge._WINDOWS_PREFLIGHT_CACHE
        try:
            sys.platform = "win32"
            host_bridge._WINDOWS_PREFLIGHT_CACHE = None
            with (
                mock.patch.object(host_bridge.os, "name", "nt"),
                mock.patch.object(host_bridge, "Path", FakePath),
                mock.patch.object(host_bridge.subprocess, "run", fake_run),
            ):
                result = host_bridge._windows_visual_preflight("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
        finally:
            sys.platform = original_platform
            host_bridge._WINDOWS_PREFLIGHT_CACHE = original_cache

        self.assertTrue(result["checked"])
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["dpiAwareness"])
        self.assertEqual(result["error"], "failed checks: dpiAwareness")
        self.assertIn("-STA", calls[0])
        self.assertIn("SetProcessDpiAwarenessContext", calls[0][-1])

    def test_host_bridge_windows_visual_preflight_requires_sendinput_helper_selftest(self) -> None:
        class FakePath:
            def __init__(self, value: object):
                self.value = str(value)

            @property
            def name(self) -> str:
                return self.value.replace("\\", "/").rsplit("/", 1)[-1]

        def fake_run(_command: list[str], **_: object) -> object:
            return type(
                "Completed",
                (),
                {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {
                            "ok": True,
                            "checks": {
                                "winForms": True,
                                "drawing": True,
                                "virtualScreen": True,
                                "systemIcon": True,
                                "setClipboardCommand": True,
                                "getClipboardCommand": True,
                                "dpiAwareness": True,
                            },
                            "errors": {},
                        },
                        separators=(",", ":"),
                    ),
                    "stderr": "",
                },
            )()

        original_platform = sys.platform
        original_cache = host_bridge._WINDOWS_PREFLIGHT_CACHE
        try:
            sys.platform = "win32"
            host_bridge._WINDOWS_PREFLIGHT_CACHE = None
            with (
                mock.patch.object(host_bridge.os, "name", "nt"),
                mock.patch.object(host_bridge, "Path", FakePath),
                mock.patch.object(host_bridge.subprocess, "run", fake_run),
                mock.patch.object(
                    host_bridge,
                    "_windows_visual_helper_selftest",
                    return_value={
                        "returnCode": 1,
                        "ok": False,
                        "virtualWidth": 1920,
                        "virtualHeight": 1080,
                        "stderr": "helper failed",
                    },
                ),
            ):
                result = host_bridge._windows_visual_preflight("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
        finally:
            sys.platform = original_platform
            host_bridge._WINDOWS_PREFLIGHT_CACHE = original_cache

        self.assertTrue(result["checked"])
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["sendInputHelper"])
        self.assertIn("sendInputHelper: helper failed", result["error"])
        self.assertIn("failed checks: sendInputHelper", result["error"])

    def test_host_bridge_macos_visual_preflight_blocks_accessibility_refs_when_disabled(self) -> None:
        def fake_which(name: str) -> str | None:
            if name in {"bash", "open", "osascript", "screencapture"}:
                return f"/usr/bin/{name}"
            return None

        original_platform = sys.platform
        original_cache = host_bridge._MACOS_PREFLIGHT_CACHE
        original_accessibility_cache = host_bridge._MACOS_ACCESSIBILITY_CACHE
        try:
            sys.platform = "darwin"
            host_bridge._MACOS_PREFLIGHT_CACHE = None
            host_bridge._MACOS_ACCESSIBILITY_CACHE = None
            with (
                mock.patch.object(host_bridge.shutil, "which", fake_which),
                mock.patch.object(host_bridge, "_macos_accessibility_enabled", return_value=False),
                mock.patch.object(host_bridge, "_isolated_browser_profile_app", return_value=None),
            ):
                bridge = host_bridge.HostBridge()
                status = bridge.status().to_dict()
                snapshot_allowed, snapshot_reason = bridge.can_run("desktop.snapshot")
                click_allowed, click_reason = bridge.can_run("desktop.click")
        finally:
            sys.platform = original_platform
            host_bridge._MACOS_PREFLIGHT_CACHE = original_cache
            host_bridge._MACOS_ACCESSIBILITY_CACHE = original_accessibility_cache

        self.assertTrue(status["macosVisualPreflightChecked"])
        self.assertFalse(status["macosVisualPreflightOk"])
        self.assertFalse(status["macosVisualPreflightChecks"]["accessibility"])
        self.assertFalse(status["desktopAutomationReady"])
        self.assertFalse(snapshot_allowed)
        self.assertIn("darwin visual automation preflight failed", snapshot_reason or "")
        self.assertIn("accessibility", snapshot_reason or "")
        self.assertTrue(click_allowed)
        self.assertIsNone(click_reason)

    def test_host_bridge_macos_visual_preflight_blocks_foreground_writes_in_loginwindow_session(self) -> None:
        def fake_which(name: str) -> str | None:
            if name in {"bash", "open", "osascript", "screencapture"}:
                return f"/usr/bin/{name}"
            return None

        original_platform = sys.platform
        original_cache = host_bridge._MACOS_PREFLIGHT_CACHE
        original_accessibility_cache = host_bridge._MACOS_ACCESSIBILITY_CACHE
        try:
            sys.platform = "darwin"
            host_bridge._MACOS_PREFLIGHT_CACHE = None
            host_bridge._MACOS_ACCESSIBILITY_CACHE = None
            with (
                mock.patch.object(host_bridge.shutil, "which", fake_which),
                mock.patch.object(host_bridge, "_macos_accessibility_enabled", return_value=True),
                mock.patch.object(
                    host_bridge,
                    "_macos_foreground_session_status",
                    return_value={
                        "checked": True,
                        "ok": False,
                        "error": "macOS foreground session is loginwindow; user GUI session is not foreground-controllable",
                        "details": {"appName": "loginwindow", "processId": 400, "windowCount": 1},
                    },
                ),
                mock.patch.object(host_bridge, "_isolated_browser_profile_app", return_value=None),
            ):
                bridge = host_bridge.HostBridge()
                status = bridge.status().to_dict()
                snapshot_allowed, snapshot_reason = bridge.can_run("desktop.snapshot")
                apps_allowed, apps_reason = bridge.can_run("desktop.apps")
                activate_allowed, activate_reason = bridge.can_run("desktop.activate_app")
                click_allowed, click_reason = bridge.can_run("desktop.click")
                native_act_allowed, native_act_reason = bridge.can_run(
                    "desktop.act",
                    {"ref": "d1", "action": "click", "requireNative": True},
                )
                fallback_act_allowed, fallback_act_reason = bridge.can_run(
                    "desktop.act",
                    {"ref": "d1", "action": "click"},
                )
                browser_screenshot_allowed, browser_screenshot_reason = bridge.can_run("browser.screenshot")
                browser_click_allowed, browser_click_reason = bridge.can_run("browser.click")
        finally:
            sys.platform = original_platform
            host_bridge._MACOS_PREFLIGHT_CACHE = original_cache
            host_bridge._MACOS_ACCESSIBILITY_CACHE = original_accessibility_cache

        self.assertTrue(status["macosVisualPreflightChecked"])
        self.assertFalse(status["macosVisualPreflightOk"])
        self.assertFalse(status["desktopAutomationReady"])
        self.assertFalse(status["macosVisualPreflightChecks"]["foregroundSession"])
        self.assertEqual(status["macosVisualPreflightChecks"]["foregroundAppName"], "loginwindow")
        self.assertTrue(snapshot_allowed)
        self.assertIsNone(snapshot_reason)
        self.assertTrue(apps_allowed)
        self.assertIsNone(apps_reason)
        self.assertFalse(activate_allowed)
        self.assertIn("foregroundSession", activate_reason or "")
        self.assertIn("loginwindow", activate_reason or "")
        self.assertFalse(click_allowed)
        self.assertIn("foregroundSession", click_reason or "")
        self.assertTrue(native_act_allowed)
        self.assertIsNone(native_act_reason)
        self.assertFalse(fallback_act_allowed)
        self.assertIn("foregroundSession", fallback_act_reason or "")
        self.assertTrue(browser_screenshot_allowed)
        self.assertIsNone(browser_screenshot_reason)
        self.assertFalse(browser_click_allowed)
        self.assertIn("foregroundSession", browser_click_reason or "")

    def test_host_bridge_blocks_visual_tools_in_windows_service_session(self) -> None:
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        cmd = "C:/Windows/System32/cmd.exe"

        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/") in {powershell, cmd}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(host_bridge.shutil, "which", return_value=None),
                mock.patch.object(Path, "exists", fake_exists),
                mock.patch.dict(os.environ, {"SESSIONNAME": "Services"}, clear=False),
            ):
                bridge = host_bridge.HostBridge()
                status = bridge.status().to_dict()
                self.assertTrue(status["browserBridge"])
                self.assertFalse(status["browserAutomationReady"])
                self.assertFalse(status["desktopAutomationReady"])
                self.assertFalse(status["interactiveSession"])
                self.assertEqual(status["interactiveSessionName"], "Services")
                self.assertTrue(bridge.can_run("browser.profiles")[0])
                self.assertTrue(bridge.can_run("desktop.apps")[0])
                for tool in ("browser.screenshot", "desktop.screenshot", "desktop.snapshot", "desktop.click", "notify.send"):
                    with self.subTest(tool=tool):
                        allowed, reason = bridge.can_run(tool)
                        self.assertFalse(allowed)
                        self.assertEqual(reason, "win32 interactive desktop session unavailable")
        finally:
            sys.platform = original_platform

    def test_host_bridge_blocks_visual_tools_in_windows_session_zero_without_session_name(self) -> None:
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        cmd = "C:/Windows/System32/cmd.exe"

        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/") in {powershell, cmd}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(host_bridge.shutil, "which", return_value=None),
                mock.patch.object(Path, "exists", fake_exists),
                mock.patch.object(host_bridge, "_windows_current_session_id", return_value=0),
                mock.patch.dict(os.environ, {"SESSIONNAME": ""}, clear=False),
            ):
                bridge = host_bridge.HostBridge()
                status = bridge.status().to_dict()
                self.assertFalse(status["browserAutomationReady"])
                self.assertFalse(status["desktopAutomationReady"])
                self.assertFalse(status["interactiveSession"])
                self.assertIsNone(status["interactiveSessionName"])
                self.assertEqual(status["interactiveSessionId"], 0)
                self.assertTrue(bridge.can_run("browser.profiles")[0])
                self.assertTrue(bridge.can_run("desktop.apps")[0])
                allowed, reason = bridge.can_run("desktop.click")
                self.assertFalse(allowed)
                self.assertEqual(reason, "win32 interactive desktop session unavailable")
        finally:
            sys.platform = original_platform

    def test_host_bridge_blocks_windows_session_zero_even_when_session_name_looks_interactive(self) -> None:
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        cmd = "C:/Windows/System32/cmd.exe"

        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/") in {powershell, cmd}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(host_bridge.shutil, "which", return_value=None),
                mock.patch.object(Path, "exists", fake_exists),
                mock.patch.object(host_bridge, "_windows_current_session_id", return_value=0),
                mock.patch.dict(os.environ, {"SESSIONNAME": "Console"}, clear=False),
            ):
                bridge = host_bridge.HostBridge()
                status = bridge.status().to_dict()
                self.assertFalse(status["browserAutomationReady"])
                self.assertFalse(status["desktopAutomationReady"])
                self.assertFalse(status["interactiveSession"])
                self.assertEqual(status["interactiveSessionName"], "Console")
                self.assertEqual(status["interactiveSessionId"], 0)
                allowed, reason = bridge.can_run("notify.send")
                self.assertFalse(allowed)
                self.assertEqual(reason, "win32 interactive desktop session unavailable")
        finally:
            sys.platform = original_platform

    def test_visual_bridge_windows_browser_fallback_sta_and_process_id_targets(self) -> None:
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")

        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/") in {powershell, str(chrome)}

        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            stdout = ""
            if any("Start-Process" in str(part) for part in command):
                stdout = '{"processId":42,"processName":"notepad","launchPath":"notepad"}'
            return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(visual_bridge.shutil, "which", return_value=None),
                mock.patch.object(Path, "exists", fake_exists),
            ):
                self.assertEqual(visual_bridge._browser_app_candidate(), ("Google Chrome", chrome))
                sta_command = visual_bridge._powershell_command("Write-Output ok", sta=True)
                self.assertIsNotNone(sta_command)
                self.assertIn("-STA", sta_command or [])

                browser_open = visual_bridge.execute_browser_open(
                    {"url": "https://example.com", "profile": "atrium"},
                    fake_run,
                )
                self.assertEqual(browser_open["processId"], 42)
                self.assertEqual(browser_open["method"], "powershell")
                opened = visual_bridge.execute_open_app({"appName": "notepad"}, fake_run)
                self.assertEqual(opened["processId"], 42)
                visual_bridge.execute_activate_app({"processId": 42}, fake_run)
                visual_bridge.execute_quit_app({"processId": 42, "force": True}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertIn("Start-Process", " ".join(calls[0]))
        self.assertIn("--user-data-dir", " ".join(calls[0]))
        self.assertIn("Get-Process -Id 42", " ".join(calls[-2]))
        self.assertIn("Get-Process -Id 42", " ".join(calls[-1]))

    def test_windows_open_falls_back_to_process_lookup_for_missing_start_process_id(self) -> None:
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
        calls: list[list[str]] = []

        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/") in {powershell, str(chrome)}

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            joined = " ".join(command)
            if "Get-CimInstance" in joined:
                stdout = '{"processId":99,"startedProcessId":88,"processName":"chrome.exe","launchPath":"C:/Program Files/Google/Chrome/Application/chrome.exe","source":"profileProcessLookup","profileVerified":true}'
            else:
                stdout = ""
            return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(visual_bridge.shutil, "which", return_value=None),
                mock.patch.object(Path, "exists", fake_exists),
            ):
                browser_open = visual_bridge.execute_browser_open(
                    {"url": "https://example.com", "profile": "atrium"},
                    fake_run,
                )
        finally:
            sys.platform = original_platform

        self.assertEqual(browser_open["processId"], 99)
        self.assertEqual(browser_open["startedProcessId"], 88)
        self.assertTrue(browser_open["profileVerified"])
        self.assertEqual(browser_open["source"], "profileProcessLookup")
        self.assertIn("Get-CimInstance", " ".join(calls[0]))
        self.assertIn("CommandLine", " ".join(calls[0]))
        self.assertIn("profileDir", " ".join(calls[0]))

    def test_windows_isolated_browser_open_fails_when_process_cannot_be_verified(self) -> None:
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")

        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/") in {powershell, str(chrome)}

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(visual_bridge.shutil, "which", return_value=None),
                mock.patch.object(Path, "exists", fake_exists),
            ):
                browser_open = visual_bridge.execute_browser_open(
                    {"url": "https://example.com", "profile": "atrium"},
                    fake_run,
                )
        finally:
            sys.platform = original_platform

        self.assertEqual(browser_open["returnCode"], 1)
        self.assertIsNone(browser_open["processId"])
        self.assertEqual(browser_open["stderr"], "isolated browser profile process was not found after launch")
        self.assertEqual(browser_open["profileKind"], "isolated")

    def test_windows_isolated_browser_open_fails_when_profile_flag_is_missing(self) -> None:
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")

        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/") in {powershell, str(chrome)}

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"processId":99,"startedProcessId":88,"processName":"chrome.exe","launchPath":"C:/Program Files/Google/Chrome/Application/chrome.exe","source":"processLookup"}',
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(visual_bridge.shutil, "which", return_value=None),
                mock.patch.object(Path, "exists", fake_exists),
            ):
                browser_open = visual_bridge.execute_browser_open(
                    {"url": "https://example.com", "profile": "atrium"},
                    fake_run,
                )
        finally:
            sys.platform = original_platform

        self.assertEqual(browser_open["returnCode"], 1)
        self.assertEqual(browser_open["processId"], 99)
        self.assertIsNone(browser_open["profileVerified"])
        self.assertEqual(browser_open["stderr"], "isolated browser profile process did not verify requested profile")

    def test_windows_argument_list_quotes_values_with_spaces(self) -> None:
        command_line = visual_bridge._windows_argument_list([
            "--user-data-dir=C:/Users/Test User/ATRIUM Profiles/atrium",
            "--new-window",
            "https://example.com/a b",
        ])

        self.assertEqual(
            command_line,
            '"--user-data-dir=C:/Users/Test User/ATRIUM Profiles/atrium" --new-window "https://example.com/a b"',
        )

    def test_windows_user_browser_open_resolves_default_browser_process_metadata(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"processId":66,"startedProcessId":65,"processName":"msedge.exe","launchPath":"C:/Program Files/Microsoft/Edge/Application/msedge.exe","browserName":"msedge","source":"defaultBrowserRegistry","processVerified":true,"progId":"MSEdgeHTM"}',
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"):
                browser_open = visual_bridge.execute_browser_open(
                    {"url": "https://example.com/a b", "profile": "user"},
                    fake_run,
                )
        finally:
            sys.platform = original_platform

        self.assertEqual(browser_open["profile"], "user")
        self.assertEqual(browser_open["profileKind"], "user")
        self.assertEqual(browser_open["processId"], 66)
        self.assertEqual(browser_open["startedProcessId"], 65)
        self.assertEqual(browser_open["processName"], "msedge.exe")
        self.assertEqual(browser_open["browserApp"], "msedge")
        self.assertEqual(browser_open["browserAppPath"], "C:/Program Files/Microsoft/Edge/Application/msedge.exe")
        self.assertEqual(browser_open["source"], "defaultBrowserRegistry")
        self.assertEqual(browser_open["progId"], "MSEdgeHTM")
        self.assertTrue(browser_open["processVerified"])
        joined = " ".join(calls[0])
        self.assertIn("UrlAssociations", joined)
        self.assertIn("Get-CimInstance Win32_Process", joined)
        self.assertIn("$argumentList =", joined)
        self.assertIn('"https://example.com/a b"', joined)

    def test_windows_user_browser_open_allows_shell_association_without_process_metadata(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"):
                browser_open = visual_bridge.execute_browser_open(
                    {"url": "https://example.com", "profile": "default"},
                    fake_run,
                )
        finally:
            sys.platform = original_platform

        self.assertEqual(browser_open["returnCode"], 0)
        self.assertEqual(browser_open["profile"], "user")
        self.assertIsNone(browser_open["processId"])
        self.assertIsNone(browser_open["source"])
        self.assertIn("Start-Process -FilePath $url", " ".join(calls[0]))

    def test_windows_isolated_browser_open_quotes_profile_dir_with_spaces(self) -> None:
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
        data_dir = Path("C:/Users/Test User/ATRIUM Profiles/atrium")
        calls: list[list[str]] = []

        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/") in {powershell, str(chrome)}

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"processId":77,"startedProcessId":77,"processName":"chrome.exe","launchPath":"C:/Program Files/Google/Chrome/Application/chrome.exe","source":"profileProcessLookup","profileVerified":true}',
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(visual_bridge.shutil, "which", return_value=None),
                mock.patch.object(Path, "exists", fake_exists),
                mock.patch.object(Path, "mkdir", return_value=None),
                mock.patch.object(visual_bridge, "browser_profile_data_dir", return_value=data_dir),
            ):
                browser_open = visual_bridge.execute_browser_open(
                    {"url": "https://example.com/a b", "profile": "atrium"},
                    fake_run,
                )
        finally:
            sys.platform = original_platform

        self.assertEqual(browser_open["processId"], 77)
        joined = " ".join(calls[0])
        self.assertIn("$argumentList =", joined)
        self.assertIn('"--user-data-dir=C:/Users/Test User/ATRIUM Profiles/atrium"', joined)
        self.assertIn('"https://example.com/a b"', joined)
        self.assertIn("-ArgumentList $argumentList", joined)

    def test_windows_open_app_falls_back_to_process_lookup_for_shortcuts(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            stdout = '{"processId":55,"processName":"notepad","launchPath":"C:/Menu/Notepad.lnk","source":"processLookup"}'
            return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"),
                mock.patch.object(visual_bridge, "_windows_find_start_menu_shortcut", return_value=Path("C:/Menu/Notepad.lnk")),
            ):
                opened = visual_bridge.execute_open_app({"appName": "Notepad"}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(opened["processId"], 55)
        self.assertEqual(opened["source"], "processLookup")
        joined = " ".join(calls[0])
        self.assertIn("Start-Sleep -Milliseconds 500", joined)
        self.assertIn("C:/Menu/Notepad.lnk", joined)

    def test_windows_open_app_fails_when_process_cannot_be_verified(self) -> None:
        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"):
                opened = visual_bridge.execute_open_app({"appName": "Notepad"}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(opened["returnCode"], 1)
        self.assertIsNone(opened["processId"])
        self.assertFalse(opened["processVerified"])
        self.assertEqual(opened["stderr"], "desktop app process was not found after launch")
        self.assertEqual(opened["appName"], "Notepad")

    def test_windows_open_app_quotes_argument_list_with_spaces(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"processId":55,"processName":"testapp","launchPath":"C:/Program Files/Test App/app.exe","source":"startProcess"}',
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"):
                opened = visual_bridge.execute_open_app(
                    {
                        "path": "C:/Program Files/Test App/app.exe",
                        "target": "C:/Users/Test User/input file.txt",
                        "arguments": ["--name", "Two Words"],
                    },
                    fake_run,
                )
        finally:
            sys.platform = original_platform

        self.assertEqual(opened["processId"], 55)
        joined = " ".join(calls[0])
        self.assertIn("$argumentList =", joined)
        self.assertIn('"C:/Users/Test User/input file.txt"', joined)
        self.assertIn('"Two Words"', joined)
        self.assertIn("-ArgumentList $argumentList", joined)

    def test_windows_sta_powershell_prefers_windows_powershell_over_pwsh_path(self) -> None:
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        pwsh = "C:/Program Files/PowerShell/7/pwsh.exe"

        def fake_which(name: str) -> str | None:
            return pwsh if name in {"pwsh.exe", "pwsh"} else None

        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/") == powershell

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(visual_bridge.shutil, "which", fake_which),
                mock.patch.object(Path, "exists", fake_exists),
            ):
                sta_command = visual_bridge._powershell_command("Write-Output ok", sta=True)
                non_sta_command = visual_bridge._powershell_command("Write-Output ok", sta=False)
        finally:
            sys.platform = original_platform

        self.assertEqual(sta_command[0], powershell)
        self.assertIn("-STA", sta_command)
        self.assertIn("[Console]::OutputEncoding", sta_command[-1])
        self.assertEqual(non_sta_command[0], pwsh)
        self.assertNotIn("-STA", non_sta_command)
        self.assertIn("[Console]::OutputEncoding", non_sta_command[-1])

    def test_windows_sta_powershell_uses_sta_for_pwsh_when_windows_powershell_is_missing(self) -> None:
        pwsh = "C:/Program Files/PowerShell/7/pwsh.exe"

        def fake_which(name: str) -> str | None:
            return pwsh if name in {"pwsh.exe", "pwsh"} else None

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(visual_bridge.shutil, "which", fake_which),
                mock.patch.object(Path, "exists", return_value=False),
            ):
                sta_command = visual_bridge._powershell_command("Write-Output ok", sta=True)
        finally:
            sys.platform = original_platform

        self.assertEqual(sta_command[0], pwsh)
        self.assertIn("-STA", sta_command)
        self.assertNotIn("-ExecutionPolicy", sta_command)
        self.assertIn("[Console]::OutputEncoding", sta_command[-1])

    def test_sta_flag_is_not_added_to_pwsh_on_non_windows_hosts(self) -> None:
        original_platform = sys.platform
        try:
            sys.platform = "darwin"
            with mock.patch.object(visual_bridge.shutil, "which", return_value="/opt/homebrew/bin/pwsh"):
                command = visual_bridge._powershell_command("Write-Output ok", sta=True)
        finally:
            sys.platform = original_platform

        self.assertEqual(command[0], "/opt/homebrew/bin/pwsh")
        self.assertNotIn("-STA", command)
        self.assertNotIn("[Console]::OutputEncoding", command[-1])

    def test_windows_keypress_reports_actual_and_requested_modifiers(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"ok":true,"mode":"keypress","key":"l","modifiers":["control"],"inputMethod":"sendinput"}',
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge, "_ensure_windows_visual_helper", return_value=Path("C:/tmp/windows_visual.py")):
                result = visual_bridge.execute_keypress({"keys": ["cmd", "l"]}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["method"], "win32_sendinput")
        self.assertEqual(result["key"], "l")
        self.assertEqual(result["modifiers"], ["control"])
        self.assertEqual(result["requestedKey"], "l")
        self.assertEqual(result["requestedModifiers"], ["cmd"])
        self.assertEqual(result["inputMethod"], "sendinput")
        self.assertEqual(result["helper"]["mode"], "keypress")
        self.assertIn("windows_visual.py", " ".join(calls[0]))

    def test_windows_keypress_supports_forward_delete_and_insert_keys(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            payload = json.loads(str(command[-1]))
            raw_key = payload["keys"][0]
            key = "forwarddelete" if raw_key == "forward_delete" else raw_key
            return {
                "command": command,
                "returnCode": 0,
                "stdout": json.dumps(
                    {"ok": True, "mode": "keypress", "key": key, "modifiers": [], "inputMethod": "sendinput"},
                    separators=(",", ":"),
                ),
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge, "_ensure_windows_visual_helper", return_value=Path("C:/tmp/windows_visual.py")):
                forward_delete = visual_bridge.execute_keypress({"keys": ["forward_delete"]}, fake_run)
                insert = visual_bridge.execute_keypress({"keys": ["insert"]}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(forward_delete["key"], "forwarddelete")
        self.assertEqual(forward_delete["requestedKey"], "forwarddelete")
        self.assertEqual(insert["key"], "insert")
        self.assertEqual(insert["requestedKey"], "insert")
        self.assertIn('"keys": ["forward_delete"]', calls[0][-1])
        self.assertIn('"keys": ["insert"]', calls[1][-1])
        self.assertIn('"forwarddelete": 0x2E', visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)
        self.assertIn('"insert": 0x2D', visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)

    def test_windows_keypress_allows_windows_key_modifier(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            payload = json.loads(str(command[-1]))
            raw_keys = [str(item).lower() for item in payload["keys"]]
            modifiers = ["win"] if any(item in {"win", "windows", "super"} for item in raw_keys) else []
            key = next(item for item in raw_keys if item not in {"win", "windows", "super"})
            return {
                "command": command,
                "returnCode": 0,
                "stdout": json.dumps(
                    {"ok": True, "mode": "keypress", "key": key, "modifiers": modifiers, "inputMethod": "sendinput"},
                    separators=(",", ":"),
                ),
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge, "_ensure_windows_visual_helper", return_value=Path("C:/tmp/windows_visual.py")):
                windows_r = visual_bridge.execute_keypress({"keys": ["win", "r"]}, fake_run)
                super_r = visual_bridge.execute_keypress({"keys": ["super", "r"]}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(windows_r["key"], "r")
        self.assertEqual(windows_r["modifiers"], ["win"])
        self.assertEqual(windows_r["requestedModifiers"], ["win"])
        self.assertEqual(super_r["modifiers"], ["win"])
        self.assertEqual(super_r["requestedModifiers"], ["win"])
        self.assertIn('"keys": ["win", "r"]', calls[0][-1])
        self.assertIn('"keys": ["super", "r"]', calls[1][-1])
        self.assertIn('"super": "win"', visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)
        self.assertIn('case "win", "windows", "super"', visual_bridge._KEY_HELPER_SOURCE)

    def test_windows_scroll_uses_win32_mouse_wheel_helper(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"ok":true,"mode":"scroll","direction":"down","unit":"line","amount":2,"steps":2,"wheelDelta":-120,"horizontal":false,"x":400,"y":300,"inputMethod":"sendinput","dpiAwareness":"per_monitor_v2"}',
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge, "_ensure_windows_visual_helper", return_value=Path("C:/tmp/windows_visual.py")):
                result = visual_bridge.execute_scroll({"direction": "down", "unit": "line", "amount": 2, "x": 400, "y": 300}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["method"], "win32_sendinput")
        self.assertEqual(result["helperMode"], "scroll")
        self.assertEqual(result["direction"], "down")
        self.assertEqual(result["unit"], "line")
        self.assertEqual(result["amount"], 2)
        self.assertEqual(result["steps"], 2)
        self.assertEqual(result["wheelDelta"], -120)
        self.assertFalse(result["horizontal"])
        self.assertEqual(result["inputMethod"], "sendinput")
        self.assertEqual(result["x"], 400)
        self.assertEqual(result["y"], 300)
        self.assertIn("scroll", calls[0])
        self.assertIn('"x": 400', calls[0][-1])
        self.assertNotIn("keypress", calls[0])

    def test_windows_click_uses_win32_sendinput_helper(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"ok":true,"mode":"click","x":10,"y":20,"button":"left","inputMethod":"sendinput","dpiAwareness":"per_monitor_v2"}',
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge, "_ensure_windows_visual_helper", return_value=Path("C:/tmp/windows_visual.py")):
                result = visual_bridge.execute_click({"x": 10, "y": 20}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["method"], "win32_sendinput")
        self.assertEqual(result["helperMode"], "click")
        self.assertEqual(result["inputMethod"], "sendinput")
        self.assertEqual(result["x"], 10)
        self.assertEqual(result["y"], 20)
        self.assertEqual(result["button"], "left")
        self.assertIn("click", calls[0])
        self.assertIn('"x": 10', calls[0][-1])

    def test_windows_type_text_reports_utf16_unit_metadata(self) -> None:
        calls: list[list[str]] = []
        text = "ไทย🙂"

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            payload = json.loads(str(command[-1]))
            typed = str(payload["text"])
            return {
                "command": command,
                "returnCode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "mode": "type",
                        "textBytes": len(typed.encode("utf-8")),
                        "textCharacters": len(typed),
                        "textUnits": len(typed.encode("utf-16-le", errors="surrogatepass")) // 2,
                        "inputMethod": "sendinput",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge, "_ensure_windows_visual_helper", return_value=Path("C:/tmp/windows_visual.py")):
                result = visual_bridge.execute_type_text({"text": text}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertEqual(result["method"], "win32_sendinput")
        self.assertEqual(result["helperMode"], "type")
        self.assertEqual(result["inputMethod"], "sendinput")
        self.assertEqual(result["textBytes"], len(text.encode("utf-8")))
        self.assertEqual(result["textCharacters"], len(text))
        self.assertEqual(result["textUnits"], len(text.encode("utf-16-le", errors="surrogatepass")) // 2)
        self.assertEqual(result["helper"]["textUnits"], result["textUnits"])
        self.assertIn('"text": "ไทย🙂"', calls[0][-1])

    def test_windows_visual_helper_requires_verification_metadata(self) -> None:
        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge, "_ensure_windows_visual_helper", return_value=Path("C:/tmp/windows_visual.py")):
                result = visual_bridge.execute_click({"x": 10, "y": 20}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertEqual(result["stderr"], "Windows visual helper did not return verification metadata")
        self.assertEqual(
            visual_bridge.visual_process_error("desktop.click", result),
            "desktop.click bridge command failed: Windows visual helper did not return verification metadata",
        )

    def test_windows_visual_helper_requires_matching_mode_metadata(self) -> None:
        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"ok":true,"mode":"type","textBytes":2,"inputMethod":"sendinput"}',
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge, "_ensure_windows_visual_helper", return_value=Path("C:/tmp/windows_visual.py")):
                result = visual_bridge.execute_click({"x": 10, "y": 20}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertEqual(result["helperMode"], "type")
        self.assertEqual(result["stderr"], "Windows visual helper returned mode 'type'; expected 'click'")

    def test_windows_paste_text_preserves_keypress_helper_metadata(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            if any("Set-Clipboard" in str(part) for part in command):
                return {"command": command, "returnCode": 0, "stdout": "", "stderr": "", "method": "powershell"}
            if any("Get-Clipboard" in str(part) for part in command):
                return {
                    "command": command,
                    "returnCode": 0,
                    "stdout": '{"textLength":9,"textPreview":"paste ไทย","containsExpected":true,"verified":true}',
                    "stderr": "",
                    "method": "powershell",
                }
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"ok":true,"mode":"keypress","key":"v","modifiers":["control"],"inputMethod":"sendinput"}',
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"),
                mock.patch.object(visual_bridge, "_ensure_windows_visual_helper", return_value=Path("C:/tmp/windows_visual.py")),
            ):
                result = visual_bridge.execute_paste_text({"text": "paste ไทย"}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertEqual(result["method"], "win32_sendinput")
        self.assertEqual(result["inputMethod"], "sendinput")
        self.assertTrue(result["ok"])
        self.assertEqual(result["helperMode"], "keypress")
        self.assertEqual(result["helper"]["key"], "v")
        self.assertEqual(result["clipboard"]["setReturnCode"], 0)
        self.assertEqual(result["clipboard"]["verifyReturnCode"], 0)
        self.assertTrue(result["clipboard"]["verified"])
        self.assertTrue(result["clipboard"]["containsExpected"])
        self.assertEqual(result["clipboard"]["textPreview"], "paste ไทย")
        self.assertIn("-STA", calls[0])
        self.assertIn("Set-Clipboard", calls[0][-1])
        self.assertIn("Get-Clipboard", calls[1][-1])
        self.assertIn("keypress", calls[2])

    def test_windows_paste_text_fails_when_clipboard_round_trip_does_not_verify(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            if any("Set-Clipboard" in str(part) for part in command):
                return {"command": command, "returnCode": 0, "stdout": "", "stderr": "", "method": "powershell"}
            if any("Get-Clipboard" in str(part) for part in command):
                return {
                    "command": command,
                    "returnCode": 0,
                    "stdout": '{"textLength":5,"textPreview":"wrong","containsExpected":false,"verified":false}',
                    "stderr": "",
                    "method": "powershell",
                }
            raise AssertionError("keypress should not run when clipboard verification fails")

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"):
                result = visual_bridge.execute_paste_text({"text": "paste ไทย"}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertEqual(result["stderr"], "clipboard round-trip did not verify expected text")
        self.assertFalse(result["clipboard"]["verified"])
        self.assertEqual(result["clipboard"]["textPreview"], "wrong")
        self.assertEqual(len(calls), 2)

    def test_windows_notification_reports_show_balloon_verification_metadata(self) -> None:
        calls: list[tuple[list[str], object]] = []

        def fake_run(command: list[str], **kwargs: object) -> dict[str, object]:
            calls.append((command, kwargs.get("timeout")))
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"shown":true,"disposed":true,"timeoutMs":2000,"titleLength":6,"bodyLength":2}',
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"):
                result = visual_bridge.execute_notification(
                    {"title": "ATRIUM", "body": "ok", "timeoutMs": 2000},
                    fake_run,
                )
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertTrue(result["shown"])
        self.assertTrue(result["disposed"])
        self.assertEqual(result["timeoutMs"], 2000)
        self.assertEqual(result["titleLength"], 6)
        self.assertEqual(result["bodyLength"], 2)
        self.assertEqual(result["bodyBytes"], 2)
        self.assertIn("-STA", calls[0][0])
        self.assertIn("ShowBalloonTip(2000", calls[0][0][-1])
        self.assertIn("ConvertTo-Json", calls[0][0][-1])
        self.assertGreaterEqual(calls[0][1], 10.0)

    def test_windows_notification_fails_when_verification_metadata_missing(self) -> None:
        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"):
                result = visual_bridge.execute_notification({"title": "ATRIUM", "body": "ok"}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertIsNone(result["shown"])
        self.assertEqual(result["stderr"], "Windows notification did not return ShowBalloonTip verification metadata")

    def test_windows_visual_helper_selftest_contract(self) -> None:
        compile(visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE, "windows_visual.py", "exec")
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"ok":true,"mode":"selftest","screenWidth":1920,"screenHeight":1080,"virtualLeft":-1920,"virtualTop":0,"virtualWidth":3840,"virtualHeight":1080,"dpiAwareness":"per_monitor_v2"}',
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge, "_ensure_windows_visual_helper", return_value=Path("C:/tmp/windows_visual.py")):
                result = visual_bridge.execute_windows_visual_selftest(fake_run)
        finally:
            sys.platform = original_platform

        self.assertTrue(result["ok"])
        self.assertEqual(result["helperMode"], "selftest")
        self.assertEqual(result["screenWidth"], 1920)
        self.assertEqual(result["virtualLeft"], -1920)
        self.assertEqual(result["virtualTop"], 0)
        self.assertEqual(result["virtualWidth"], 3840)
        self.assertEqual(result["dpiAwareness"], "per_monitor_v2")
        self.assertIn("selftest", calls[0])
        self.assertIn("SetProcessDpiAwarenessContext", visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)
        self.assertIn("SendInput", visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)
        self.assertIn('"super": "win"', visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)
        self.assertIn("SendInput.argtypes", visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)
        self.assertIn("VkKeyScanW.restype = ctypes.c_short", visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)
        self.assertIn("KEYEVENTF_EXTENDEDKEY", visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)
        self.assertIn('"forwarddelete": 0x2E', visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)
        self.assertIn('"insert": 0x2D', visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)
        self.assertIn("SetCursorPos failed", visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)
        self.assertIn("SM_XVIRTUALSCREEN = 76", visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)
        self.assertIn("coordinates outside virtual screen bounds", visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)
        self.assertIn("_fail(exc, mode=mode)", visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)
        self.assertNotIn("user32.mouse_event", visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)
        self.assertNotIn("user32.keybd_event", visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)
        self.assertNotIn("VkKeyScanW(ord(key))", visual_bridge._WINDOWS_VISUAL_HELPER_SOURCE)

    def test_windows_powershell_visual_preflight_contract(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"ok":true,"checks":{"winForms":true,"drawing":true,"virtualScreen":true,"systemIcon":true,"setClipboardCommand":true,"getClipboardCommand":true,"dpiAwareness":true},"virtualScreen":{"left":0,"top":0,"width":1920,"height":1080},"errors":{},"powerShell":"5.1.19041.1"}',
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"):
                result = visual_bridge.execute_windows_powershell_visual_preflight(fake_run)
        finally:
            sys.platform = original_platform

        self.assertTrue(result["ok"])
        self.assertTrue(result["checks"]["winForms"])
        self.assertTrue(result["checks"]["setClipboardCommand"])
        self.assertTrue(result["checks"]["dpiAwareness"])
        self.assertEqual(result["virtualScreen"]["width"], 1920)
        self.assertEqual(result["powerShell"], "5.1.19041.1")
        self.assertIn("-STA", calls[0])
        self.assertIn("setClipboardCommand", calls[0][-1])
        self.assertIn("SetProcessDpiAwarenessContext", calls[0][-1])

    def test_windows_screenshot_capture_reports_screen_bounds(self) -> None:
        calls: list[list[str]] = []
        shot_path: Path | None = None

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            if shot_path is not None:
                shot_path.write_bytes(_fake_png(1920, 1080))
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"path":"C:/tmp/shot.png","left":0,"top":0,"width":1920,"height":1080,"dpiAwareness":true}',
                "stderr": "",
            }

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            shot_path = Path(tmp) / "shot.png"
            try:
                sys.platform = "win32"
                with mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"):
                    result = visual_bridge.execute_screenshot_capture(shot_path, fake_run)
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertEqual(result["width"], 1920)
        self.assertEqual(result["height"], 1080)
        self.assertEqual(result["left"], 0)
        self.assertEqual(result["top"], 0)
        self.assertTrue(result["dpiAwareness"])
        self.assertTrue(result["fileVerified"])
        self.assertGreater(result["fileBytes"], 0)
        self.assertEqual(result["platform"], "win32")
        self.assertIn("CopyFromScreen", calls[0][-1])
        self.assertIn("SetProcessDpiAwarenessContext", calls[0][-1])

    def test_windows_screenshot_capture_fails_when_file_missing(self) -> None:
        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"path":"C:/tmp/missing.png","left":0,"top":0,"width":1920,"height":1080,"dpiAwareness":true}',
                "stderr": "",
            }

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "win32"
                with mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"):
                    result = visual_bridge.execute_screenshot_capture(Path(tmp) / "missing.png", fake_run)
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertFalse(result["fileVerified"])
        self.assertEqual(result["stderr"], "screenshot file was not created")

    def test_windows_visual_json_parser_accepts_host_text_before_json(self) -> None:
        result = {
            "returnCode": 0,
            "stdout": "WARNING: transient host message\r\n{\"processId\":42,\"processName\":\"notepad\"}\r\n",
        }

        rows = visual_bridge._json_rows_from_stdout(result)

        self.assertEqual(rows[0]["processId"], 42)
        self.assertEqual(rows[0]["processName"], "notepad")

    def test_visual_process_error_reports_bridge_timeouts(self) -> None:
        error = visual_bridge.visual_process_error(
            "browser.screenshot",
            {"returnCode": None, "timeout": True, "stderr": "command timed out after 15s"},
        )

        self.assertEqual(error, "browser.screenshot bridge command failed: command timed out after 15s")

    def test_visual_process_error_reports_ok_false_payloads(self) -> None:
        error = visual_bridge.visual_process_error(
            "desktop.click",
            {
                "returnCode": 1,
                "ok": False,
                "stdout": '{"ok":false,"error":"raw json should not win"}',
                "helper": {"error": "SendInput failed"},
            },
        )

        self.assertEqual(error, "desktop.click bridge command failed: SendInput failed")

    def test_visual_process_error_reports_missing_return_code_with_detail(self) -> None:
        result = {"returnCode": None, "stderr": "bridge process disappeared before reporting a code"}

        self.assertEqual(
            visual_bridge.visual_process_error("desktop.screenshot", result),
            "desktop.screenshot bridge command failed: bridge process disappeared before reporting a code",
        )
        self.assertEqual(
            main_module._tool_process_error("desktop.screenshot", result),
            "desktop.screenshot bridge command failed: bridge process disappeared before reporting a code",
        )
        self.assertEqual(
            chat_tools._tool_process_error("desktop.screenshot", result),
            "desktop.screenshot bridge command failed: bridge process disappeared before reporting a code",
        )

    def test_windows_visual_helper_ok_false_error_is_parsed_for_process_errors(self) -> None:
        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            return {
                "command": command,
                "returnCode": 1,
                "stdout": '{"ok":false,"mode":"click","error":"coordinates outside virtual screen bounds: 9999,9999 not in 0,0,1920x1080"}',
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge, "_ensure_windows_visual_helper", return_value=Path("C:/tmp/windows_visual.py")):
                result = visual_bridge._run_windows_visual_helper("click", {"x": 9999, "y": 9999}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertFalse(result["ok"])
        self.assertEqual(result["helper"]["mode"], "click")
        self.assertEqual(
            visual_bridge.visual_process_error("desktop.click", result),
            "desktop.click bridge command failed: coordinates outside virtual screen bounds: 9999,9999 not in 0,0,1920x1080",
        )

    def test_windows_list_apps_treats_installed_registry_failure_as_partial_when_running_succeeds(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            joined = " ".join(command)
            if "Get-Process" in joined:
                return {
                    "command": command,
                    "returnCode": 0,
                    "stdout": '[{"name":"notepad","title":"Untitled - Notepad","processId":42,"path":"C:/Windows/notepad.exe"}]',
                    "stderr": "",
                }
            return {"command": command, "returnCode": 1, "stdout": "", "stderr": "registry denied"}

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "win32"
                with (
                    mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"),
                    mock.patch.dict(os.environ, {"ProgramData": tmp, "APPDATA": tmp}, clear=False),
                ):
                    result = visual_bridge.execute_list_apps({"includeRunning": True, "includeInstalled": True}, fake_run)
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 0)
        self.assertEqual(result["runningReturnCode"], 0)
        self.assertEqual(result["installedReturnCode"], 1)
        self.assertEqual(result["installedError"], "registry denied")
        self.assertEqual(result["running"][0]["processId"], 42)
        self.assertEqual(len(calls), 2)

    def test_windows_list_apps_fails_when_only_requested_discovery_path_fails(self) -> None:
        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            return {"command": command, "returnCode": 1, "stdout": "", "stderr": "registry denied"}

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "win32"
                with (
                    mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"),
                    mock.patch.dict(os.environ, {"ProgramData": tmp, "APPDATA": tmp}, clear=False),
                ):
                    result = visual_bridge.execute_list_apps({"includeRunning": False, "includeInstalled": True}, fake_run)
            finally:
                sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertEqual(result["runningReturnCode"], None)
        self.assertEqual(result["installedReturnCode"], 1)
        self.assertEqual(result["installedError"], "registry denied")

    def test_local_mcp_windows_app_and_drive_discovery(self) -> None:
        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            start_menu = root / "Microsoft/Windows/Start Menu/Programs"
            start_menu.mkdir(parents=True)
            (start_menu / "Notion.lnk").write_bytes(b"placeholder")
            drive = root / "OneDrive"
            drive.mkdir()
            try:
                sys.platform = "win32"
                with mock.patch.dict(
                    os.environ,
                    {"ProgramData": tmp, "USERPROFILE": tmp, "OneDrive": str(drive)},
                    clear=False,
                ):
                    self.assertTrue(mcp_local._app_status("Notion")["installed"])
                    self.assertIn(drive, mcp_local._drive_roots({}))
            finally:
                sys.platform = original_platform

    def test_tool_catalog_output_schema_includes_windows_visual_metadata(self) -> None:
        by_tool = {row["tool"]: row for row in repo_module.TOOL_CATALOG}
        browser_open = by_tool["browser.open"]["outputSchema"]["properties"]
        desktop_apps = by_tool["desktop.apps"]["outputSchema"]["properties"]
        desktop_open = by_tool["desktop.open_app"]["outputSchema"]["properties"]
        browser_profiles = by_tool["browser.profiles"]["outputSchema"]["properties"]
        browser_keypress = by_tool["browser.keypress"]["outputSchema"]["properties"]
        browser_type = by_tool["browser.type"]["outputSchema"]["properties"]
        browser_paste = by_tool["browser.paste_text"]["outputSchema"]["properties"]
        browser_screenshot = by_tool["browser.screenshot"]["outputSchema"]["properties"]
        browser_scroll = by_tool["browser.scroll"]["outputSchema"]["properties"]
        browser_click = by_tool["browser.click"]["outputSchema"]["properties"]
        notification = by_tool["notify.send"]["outputSchema"]["properties"]

        self.assertIn("processId", browser_open)
        self.assertIn("startedProcessId", browser_open)
        self.assertIn("profileVerified", browser_open)
        self.assertIn("processVerified", browser_open)
        self.assertIn("profileKind", browser_open)
        self.assertIn("browserAppPath", browser_open)
        self.assertIn("progId", browser_open)
        self.assertIn("source", browser_open)
        self.assertEqual(desktop_apps["running"], "string[]|object[]")
        self.assertIn("platform", desktop_apps)
        self.assertIn("installedError", desktop_apps)
        self.assertIn("runningReturnCode", desktop_apps)
        self.assertIn("processId", desktop_open)
        self.assertIn("requestedProcessId", desktop_open)
        self.assertIn("gracefulCloseSent", desktop_open)
        self.assertIn("remaining", desktop_open)
        self.assertIn("quitVerified", desktop_open)
        self.assertIn("launchPath", desktop_open)
        self.assertIn("processVerified", desktop_open)
        self.assertIn("foreground", desktop_open)
        self.assertIn("activeProcessId", desktop_open)
        self.assertIn("attachedCurrent", desktop_open)
        self.assertIn("source", desktop_open)
        self.assertIn("browserApp", browser_profiles)
        self.assertIn("requestedModifiers", browser_keypress)
        self.assertIn("inputMethod", browser_keypress)
        self.assertIn("helper", browser_keypress)
        self.assertIn("inputMethod", browser_type)
        self.assertIn("textCharacters", browser_type)
        self.assertIn("textUnits", browser_type)
        self.assertIn("inputMethod", browser_paste)
        self.assertIn("clipboard", browser_paste)
        self.assertIn("browserProfile", browser_screenshot)
        self.assertIn("platform", browser_screenshot)
        self.assertIn("width", browser_screenshot)
        self.assertIn("height", browser_screenshot)
        self.assertIn("dpiAwareness", browser_screenshot)
        self.assertIn("fileBytes", browser_screenshot)
        self.assertIn("fileVerified", browser_screenshot)
        self.assertIn("wheelDelta", browser_scroll)
        self.assertIn("horizontal", browser_scroll)
        self.assertIn("inputMethod", browser_scroll)
        self.assertIn("x", browser_scroll)
        self.assertIn("y", browser_scroll)
        self.assertIn("helper", browser_scroll)
        self.assertIn("inputMethod", browser_click)
        self.assertIn("helper", browser_click)
        self.assertIn("shown", notification)
        self.assertIn("disposed", notification)
        self.assertIn("timeoutMs", notification)
        self.assertIn("bodyBytes", notification)

    def test_browser_screenshot_runtime_result_includes_requested_profile(self) -> None:
        def fake_capture(path: Path, _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "path": str(path), "width": 100, "height": 80}

        run = {
            "tool": "browser.screenshot",
            "departmentId": "exec",
            "args": {"path": "screenshots/profile.png", "profile": "own"},
        }
        with mock.patch.object(main_module, "execute_screenshot_capture", fake_capture):
            result = main_module._execute_tool_sync(run)

        self.assertEqual(result["browserProfile"], "atrium")
        self.assertEqual(result["width"], 100)

        with mock.patch.object(chat_tools, "execute_screenshot_capture", fake_capture):
            owner_result = chat_tools._owner_execute_tool(run)

        self.assertEqual(owner_result["browserProfile"], "atrium")
        self.assertEqual(owner_result["height"], 80)

    def test_windows_activate_and_quit_parse_process_metadata(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            calls.append(command)
            joined = " ".join(command)
            if "SetForegroundWindow" in joined:
                stdout = '{"name":"notepad","title":"Untitled - Notepad","processId":42,"foreground":true,"activeProcessId":42,"activeThreadId":9,"setForeground":true,"bringToTop":true,"showWindow":true,"attachedCurrent":true,"attachedForeground":false}'
            else:
                stdout = '{"matched":1,"gracefulCloseSent":1,"force":true,"remaining":0,"quitVerified":true}'
            return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"):
                activated = visual_bridge.execute_activate_app({"appName": "notepad"}, fake_run)
                quit_result = visual_bridge.execute_quit_app({"processId": 42, "force": True}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(activated["processId"], 42)
        self.assertIsNone(activated["requestedProcessId"])
        self.assertEqual(activated["processName"], "notepad")
        self.assertEqual(activated["title"], "Untitled - Notepad")
        self.assertTrue(activated["foreground"])
        self.assertEqual(activated["activeProcessId"], 42)
        self.assertTrue(activated["attachedCurrent"])
        self.assertEqual(quit_result["matched"], 1)
        self.assertEqual(quit_result["gracefulCloseSent"], 1)
        self.assertEqual(quit_result["remaining"], 0)
        self.assertTrue(quit_result["quitVerified"])
        self.assertIn("AttachThreadInput", " ".join(calls[0]))
        self.assertIn("GetForegroundWindow", " ".join(calls[0]))
        self.assertIn("quitVerified", " ".join(calls[-1]))
        self.assertIn("Get-Process -Id 42", " ".join(calls[-1]))

    def test_windows_quit_app_fails_when_process_remains_after_close(self) -> None:
        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"matched":1,"gracefulCloseSent":1,"force":false,"remaining":1,"quitVerified":false}',
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"):
                result = visual_bridge.execute_quit_app({"processId": 42}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertEqual(result["remaining"], 1)
        self.assertFalse(result["quitVerified"])
        self.assertEqual(result["stderr"], "desktop app process did not exit")

    def test_windows_quit_app_fails_when_verification_metadata_missing(self) -> None:
        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"):
                result = visual_bridge.execute_quit_app({"processId": 42}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(result["returnCode"], 1)
        self.assertIsNone(result["quitVerified"])
        self.assertEqual(result["stderr"], "desktop app quit did not return process verification metadata")

    def test_windows_activate_fails_when_window_does_not_become_foreground(self) -> None:
        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"name":"notepad","title":"Untitled - Notepad","processId":42,"foreground":false,"activeProcessId":7}',
                "stderr": "",
            }

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"):
                activated = visual_bridge.execute_activate_app({"processId": 42}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(activated["returnCode"], 1)
        self.assertFalse(activated["foreground"])
        self.assertEqual(activated["stderr"], "window did not become foreground")

    def test_windows_activate_fails_when_verification_metadata_missing(self) -> None:
        def fake_run(command: list[str], **_: object) -> dict[str, object]:
            return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with mock.patch.object(visual_bridge.shutil, "which", return_value="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"):
                activated = visual_bridge.execute_activate_app({"processId": 42}, fake_run)
        finally:
            sys.platform = original_platform

        self.assertEqual(activated["returnCode"], 1)
        self.assertIsNone(activated["foreground"])
        self.assertEqual(activated["stderr"], "window activation did not return foreground verification metadata")

    def test_connector_catalog_blocks_windows_service_session_visual_connectors(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": False,
                    "desktopBridge": True,
                    "desktopAutomationReady": False,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(FakeHostBridge, fake_profiles)

        self.assertEqual(connectors["browser"]["status"], "blocked_by_runtime")
        self.assertTrue(connectors["browser"]["readReady"])
        self.assertFalse(connectors["browser"]["writeReady"])
        self.assertIn("profile discovery ready", connectors["browser"]["runtimeStatus"])
        self.assertIn("Win32 input APIs", connectors["browser"]["requires"])
        self.assertEqual(connectors["browser"]["proofStatus"], "local_blocked")
        self.assertIn("profile discovery ready", connectors["browser"]["proofGaps"][0])
        self.assertIn("./atrium automation report", connectors["browser"]["proofGaps"][-1])
        self.assertEqual(connectors["desktop"]["status"], "blocked_by_runtime")
        self.assertTrue(connectors["desktop"]["readReady"])
        self.assertFalse(connectors["desktop"]["writeReady"])
        self.assertEqual(connectors["desktop"]["proofStatus"], "local_blocked")
        self.assertIn("./atrium automation report", connectors["desktop"]["proofGaps"][-1])

    def test_connector_catalog_reports_windows_visual_preflight_failure(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": False,
                    "desktopBridge": True,
                    "desktopAutomationReady": False,
                    "windowsVisualPreflightChecked": True,
                    "windowsVisualPreflightOk": False,
                    "windowsVisualPreflightError": "failed checks: dpiAwareness",
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(FakeHostBridge, fake_profiles)

        self.assertEqual(connectors["browser"]["status"], "blocked_by_runtime")
        self.assertTrue(connectors["browser"]["readReady"])
        self.assertFalse(connectors["browser"]["writeReady"])
        self.assertIn("profile discovery ready", connectors["browser"]["runtimeStatus"])
        self.assertIn("visual automation preflight failed: failed checks: dpiAwareness", connectors["browser"]["runtimeStatus"])
        self.assertIn("DPI-aware visual preflight", connectors["browser"]["requires"])
        self.assertEqual(connectors["browser"]["proofStatus"], "local_blocked")
        self.assertIn("dpiAwareness", " ".join(connectors["browser"]["proofGaps"]))
        self.assertEqual(connectors["desktop"]["status"], "blocked_by_runtime")
        self.assertTrue(connectors["desktop"]["readReady"])
        self.assertFalse(connectors["desktop"]["writeReady"])
        self.assertEqual(connectors["desktop"]["runtimeStatus"], "Windows visual automation preflight failed: failed checks: dpiAwareness")
        self.assertIn("DPI-aware visual preflight", connectors["desktop"]["requires"])
        self.assertEqual(connectors["desktop"]["proofStatus"], "local_blocked")
        self.assertIn("./atrium automation report", connectors["desktop"]["proofGaps"][-1])

    def test_connector_catalog_reports_macos_visual_preflight_failure(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "darwin",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": False,
                    "macosVisualPreflightChecked": True,
                    "macosVisualPreflightOk": False,
                    "macosVisualPreflightError": "accessibility: macOS Accessibility permission is disabled for System Events; failed checks: accessibility",
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        fake_profiles = {"browserApp": {"name": "Google Chrome", "path": "/Applications/Google Chrome.app"}}
        connectors = self._connector_catalog_for_test(FakeHostBridge, fake_profiles)

        self.assertEqual(connectors["browser"]["proofStatus"], "local_blocked")
        self.assertIn("Accessibility permission", " ".join(connectors["browser"]["proofGaps"]))
        self.assertEqual(connectors["desktop"]["status"], "blocked_by_runtime")
        self.assertTrue(connectors["desktop"]["readReady"])
        self.assertFalse(connectors["desktop"]["writeReady"])
        self.assertIn("macOS visual automation preflight failed", connectors["desktop"]["runtimeStatus"])
        self.assertIn("Accessibility permission", connectors["desktop"]["runtimeStatus"])
        self.assertIn("macOS Accessibility permission", connectors["desktop"]["requires"])
        self.assertNotIn("pbcopy", connectors["desktop"]["requires"])
        self.assertEqual(connectors["desktop"]["proofStatus"], "local_blocked")
        self.assertIn("Accessibility permission", " ".join(connectors["desktop"]["proofGaps"]))

    def test_connector_catalog_does_not_claim_own_profile_without_browser_app(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        fake_profiles = {"browserApp": None, "profiles": []}
        browser = self._connector_catalog_for_test(FakeHostBridge, fake_profiles)["browser"]

        self.assertEqual(browser["status"], "available")
        self.assertTrue(browser["readReady"])
        self.assertTrue(browser["writeReady"])
        self.assertNotIn("own_profile", browser["capabilities"])
        self.assertIn("isolated browser profile app missing", browser["runtimeStatus"])
        self.assertIn("Win32 input APIs", browser["requires"])
        self.assertIn("Chrome/Edge/Brave/Chromium for isolated profile", browser["requires"])
        self.assertEqual(browser["proofStatus"], "cross_os_unverified")
        self.assertIn("isolated browser profile app missing", browser["proofGaps"][0])
        self.assertIn("./atrium automation report", browser["proofGaps"][-1])

    def test_connector_catalog_marks_ready_host_bridge_as_cross_os_unverified(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(FakeHostBridge, fake_profiles)

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        self.assertIn("verified macOS+Windows full parity report", connectors["browser"]["proofSummary"])
        self.assertIn("verified macOS+Windows full parity report", connectors["desktop"]["proofSummary"])
        self.assertIn("./atrium automation report", connectors["browser"]["proofGaps"][-1])
        self.assertIn("./atrium automation report", connectors["desktop"]["proofGaps"][-1])
        self.assertEqual(connectors["local_file"]["proofStatus"], "not_required")

    def test_connector_catalog_marks_ready_host_bridge_as_cross_os_verified_from_persisted_report(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        report = _verified_parity_report()
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=report,
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_verified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_verified")
        self.assertEqual(connectors["browser"]["proofGaps"], [])
        self.assertEqual(connectors["desktop"]["proofGaps"], [])
        self.assertIn("host-bridge-parity-report.json", connectors["browser"]["proofSummary"])
        self.assertEqual(connectors["browser"]["proofDetails"]["sourceFingerprint"], "a" * 64)
        self.assertEqual(connectors["browser"]["proofDetails"]["proofId"], report["proofId"])
        self.assertEqual(connectors["desktop"]["proofDetails"]["gitHead"], "b" * 40)
        self.assertEqual(connectors["browser"]["proofDetails"]["artifactSha256"]["macos"], "1" * 64)
        self.assertEqual(connectors["browser"]["proofDetails"]["artifactBytes"]["windows"], 2048)
        self.assertEqual(connectors["browser"]["proofDetails"]["hostFingerprint"]["macos"], "c" * 64)
        self.assertEqual(connectors["browser"]["proofDetails"]["hostPlatform"]["windows"], "win32")
        self.assertEqual(connectors["browser"]["proofDetails"]["hostName"]["windows"], "atrium-windows")

    def test_connector_catalog_rejects_verified_report_without_artifact_provenance(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=_verified_parity_report(include_artifact_provenance=False),
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        self.assertIn("sourceFingerprint", " ".join(connectors["browser"]["proofGaps"]))
        self.assertIn("generatedAt", " ".join(connectors["desktop"]["proofGaps"]))
        self.assertIn("reportGeneratedAt", connectors["browser"]["proofDetails"])
        self.assertEqual(connectors["browser"]["proofDetails"]["resultOk"]["macos"], True)

    def test_connector_catalog_rejects_verified_report_without_artifact_hashes(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        report = _verified_parity_report()
        for item in report["results"].values():
            item.pop("artifactSha256", None)
            item.pop("artifactBytes", None)
        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=report,
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        self.assertIn("artifactSha256", " ".join(connectors["browser"]["proofGaps"]))
        self.assertIn("artifactBytes", " ".join(connectors["desktop"]["proofGaps"]))

    def test_connector_catalog_rejects_verified_report_with_non_hex_artifact_hash(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        report = _verified_parity_report()
        report["results"]["windows"]["artifactSha256"] = "z" * 64
        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=report,
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        self.assertIn("artifactSha256", " ".join(connectors["browser"]["proofGaps"]))

    def test_connector_catalog_rejects_verified_report_without_host_identity(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        report = _verified_parity_report()
        report["results"]["macos"].pop("hostFingerprint", None)
        report["results"]["windows"]["hostPlatform"] = "darwin"
        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=report,
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        gaps = " ".join(connectors["browser"]["proofGaps"])
        self.assertIn("hostFingerprint", gaps)
        self.assertIn("hostPlatform", gaps)

    def test_connector_catalog_rejects_verified_report_without_source_file_provenance(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        report = _verified_parity_report()
        report["results"]["macos"].pop("sourceFileCount", None)
        report["results"]["windows"]["sourceFileCount"] = len(main_module.SOURCE_FINGERPRINT_FILES) - 1
        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=report,
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        gaps = " ".join(connectors["browser"]["proofGaps"])
        self.assertIn("sourceFileCount", gaps)

    def test_connector_catalog_rejects_verified_report_with_extra_source_file_count(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        report = _verified_parity_report()
        report["results"]["macos"]["sourceFileCount"] = len(main_module.SOURCE_FINGERPRINT_FILES) + 1
        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=report,
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        self.assertIn("sourceFileCount", " ".join(connectors["browser"]["proofGaps"]))

    def test_connector_catalog_rejects_verified_report_without_proof_id(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        report = _verified_parity_report()
        report.pop("proofId", None)
        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=report,
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        self.assertIn("proofId", " ".join(connectors["browser"]["proofGaps"]))

    def test_connector_catalog_rejects_verified_report_with_tampered_proof_id(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        report = _verified_parity_report()
        report["proofId"] = "0" * 64
        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=report,
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        self.assertIn("proofId does not match", " ".join(connectors["browser"]["proofGaps"]))
        self.assertRegex(connectors["browser"]["proofDetails"]["expectedProofId"], r"^[0-9a-f]{64}$")

    def test_connector_catalog_rejects_verified_report_without_browser_ref_proof(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        current_source = {"sourceFingerprint": "a" * 64, "sourceManifestSha256": "a" * 64, "sourceFileCount": len(main_module.SOURCE_FINGERPRINT_FILES), "gitHead": "b" * 40, "gitDirty": False}
        report = _verified_parity_report()
        report["results"]["macos"]["proofs"]["browserActVerified"] = False
        report["proofId"] = host_bridge_parity_proof_id(
            report["results"],
            current_source,
            enforce_current_source=True,
        )
        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=report,
            current_source=current_source,
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        self.assertIn("browser.act post-click DOM verification", " ".join(connectors["browser"]["proofGaps"]))
        self.assertFalse(connectors["browser"]["proofDetails"]["proofs"]["macos"]["browserActVerified"])
        self.assertNotIn("proofId does not match", " ".join(connectors["browser"]["proofGaps"]))

    def test_connector_catalog_rejects_verified_report_without_macos_applescript_clipboard_proof(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        current_source = {"sourceFingerprint": "a" * 64, "sourceManifestSha256": "a" * 64, "sourceFileCount": len(main_module.SOURCE_FINGERPRINT_FILES), "gitHead": "b" * 40, "gitDirty": False}
        report = _verified_parity_report()
        report["results"]["macos"]["proofs"].pop("appleScriptClipboard")
        report["proofId"] = host_bridge_parity_proof_id(
            report["results"],
            current_source,
            enforce_current_source=True,
        )
        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=report,
            current_source=current_source,
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        self.assertIn("macOS AppleScript clipboard proof", " ".join(connectors["desktop"]["proofGaps"]))
        self.assertNotIn("proofId does not match", " ".join(connectors["browser"]["proofGaps"]))

    def test_connector_catalog_rejects_verified_report_without_isolated_playwright_browser_proof(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        current_source = {"sourceFingerprint": "a" * 64, "sourceManifestSha256": "a" * 64, "sourceFileCount": len(main_module.SOURCE_FINGERPRINT_FILES), "gitHead": "b" * 40, "gitDirty": False}
        report = _verified_parity_report()
        report["results"]["windows"]["proofs"]["browserActIsolatedPlaywright"] = False
        report["proofId"] = host_bridge_parity_proof_id(
            report["results"],
            current_source,
            enforce_current_source=True,
        )
        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=report,
            current_source=current_source,
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        self.assertIn("browser.act Playwright isolated profile proof", " ".join(connectors["browser"]["proofGaps"]))
        self.assertFalse(connectors["desktop"]["proofDetails"]["proofs"]["windows"]["browserActIsolatedPlaywright"])
        self.assertNotIn("proofId does not match", " ".join(connectors["browser"]["proofGaps"]))

    def test_connector_catalog_rejects_verified_report_without_windows_session_identity_proof(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        current_source = {"sourceFingerprint": "a" * 64, "sourceManifestSha256": "a" * 64, "sourceFileCount": len(main_module.SOURCE_FINGERPRINT_FILES), "gitHead": "b" * 40, "gitDirty": False}
        report = _verified_parity_report()
        report["results"]["windows"]["proofs"].pop("windowsInteractiveSessionIdentity")
        report["proofId"] = host_bridge_parity_proof_id(
            report["results"],
            current_source,
            enforce_current_source=True,
        )
        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=report,
            current_source=current_source,
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        self.assertIn("Windows interactive session identity proof", " ".join(connectors["desktop"]["proofGaps"]))
        self.assertNotIn("proofId does not match", " ".join(connectors["browser"]["proofGaps"]))

    def test_connector_catalog_rejects_verified_report_without_matching_parity_run_id(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        current_source = {"sourceFingerprint": "a" * 64, "sourceManifestSha256": "a" * 64, "sourceFileCount": len(main_module.SOURCE_FINGERPRINT_FILES), "gitHead": "b" * 40, "gitDirty": False}
        report = _verified_parity_report()
        report["results"]["macos"].pop("parityRunId")
        report["results"]["windows"]["parityRunId"] = "other-run"
        report["proofId"] = host_bridge_parity_proof_id(
            report["results"],
            current_source,
            enforce_current_source=True,
        )
        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=report,
            current_source=current_source,
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        gaps = " ".join(connectors["desktop"]["proofGaps"])
        self.assertIn("parityRunId", gaps)
        self.assertNotIn("proofId does not match", gaps)

    def test_connector_catalog_rejects_verified_report_with_unexpected_result_label(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        current_source = {"sourceFingerprint": "a" * 64, "sourceManifestSha256": "a" * 64, "sourceFileCount": len(main_module.SOURCE_FINGERPRINT_FILES), "gitHead": "b" * 40, "gitDirty": False}
        report = _verified_parity_report()
        report["results"]["linux"] = {
            "present": True,
            "ok": True,
            "proofSchemaVersion": 1,
            "artifactBytes": 512,
            "artifactSha256": "3" * 64,
            "generatedAt": main_module.now_ms(),
            "sourceFingerprint": "a" * 64,
            "gitHead": "b" * 40,
            "gitDirty": False,
            "mode": "live",
            "platform": "linux",
            "probeOk": True,
            "desktopAutomationReady": True,
            "proofs": {},
        }
        report["proofId"] = host_bridge_parity_proof_id(
            report["results"],
            current_source,
            enforce_current_source=True,
        )
        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=report,
            current_source=current_source,
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        self.assertIn("unexpected OS result labels", " ".join(connectors["browser"]["proofGaps"]))
        self.assertIn("linux", " ".join(connectors["desktop"]["proofGaps"]))
        self.assertNotIn("proofId does not match", " ".join(connectors["browser"]["proofGaps"]))

    def test_connector_catalog_rejects_verified_report_with_source_mismatch(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=_verified_parity_report(windows_fingerprint="c" * 64),
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        self.assertIn("sourceFingerprint mismatch", " ".join(connectors["browser"]["proofGaps"]))

    def test_connector_catalog_rejects_verified_report_when_current_source_changed(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=_verified_parity_report(),
            current_source={"sourceFingerprint": "c" * 64, "sourceManifestSha256": "c" * 64, "sourceFileCount": len(main_module.SOURCE_FINGERPRINT_FILES), "gitHead": "b" * 40, "gitDirty": True},
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        self.assertIn("current HostBridge source", " ".join(connectors["browser"]["proofGaps"]))
        self.assertEqual(connectors["browser"]["proofDetails"]["currentSourceFingerprint"], "c" * 64)
        self.assertEqual(connectors["desktop"]["proofDetails"]["currentGitDirty"], True)

    def test_connector_catalog_refuses_verified_report_when_current_local_runtime_is_blocked(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "darwin",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": False,
                    "macosVisualPreflightChecked": True,
                    "macosVisualPreflightOk": False,
                    "macosVisualPreflightError": "foregroundSession: macOS foreground session is loginwindow",
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        fake_profiles = {"browserApp": {"name": "Google Chrome", "path": "/Applications/Google Chrome.app"}}
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=_verified_parity_report(),
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "local_blocked")
        self.assertEqual(connectors["desktop"]["proofStatus"], "local_blocked")
        self.assertIn("current local HostBridge runtime is blocked", " ".join(connectors["browser"]["proofGaps"]))
        self.assertIn("loginwindow", " ".join(connectors["desktop"]["proofGaps"]))
        self.assertIn("localRuntimeStatus", connectors["desktop"]["proofDetails"])

    def test_connector_catalog_rejects_stale_verified_parity_report(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        stale_generated_at = main_module.now_ms() - (25 * 60 * 60 * 1000)
        connectors = self._connector_catalog_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=_verified_parity_report(generated_at=stale_generated_at),
        )

        self.assertEqual(connectors["browser"]["proofStatus"], "cross_os_unverified")
        self.assertEqual(connectors["desktop"]["proofStatus"], "cross_os_unverified")
        self.assertIn("stale", " ".join(connectors["browser"]["proofGaps"]))

    def test_host_bridge_parity_endpoint_is_registered(self) -> None:
        paths = {getattr(route, "path", None) for route in main_module.app.routes}
        self.assertIn("/api/host-bridge/parity", paths)

    def test_probe_artifact_stamp_uses_start_source_snapshot(self) -> None:
        windows_probe = _load_windows_probe_module()
        macos_probe = _load_macos_probe_module()
        source_snapshot = {
            "sourceFingerprint": "a" * 64,
            "sourceManifestSha256": "a" * 64,
            "sourceFileCount": 18,
            "gitHead": "b" * 40,
        }
        changed_source = {
            "sourceFingerprint": "c" * 64,
            "sourceManifestSha256": "c" * 64,
            "sourceFileCount": 18,
            "gitHead": "d" * 40,
        }

        with mock.patch.object(windows_probe, "host_bridge_source_provenance", return_value=changed_source):
            stamped = windows_probe._stamp_result({"ok": True, "mode": "live"}, source=source_snapshot)
        self.assertEqual(stamped["source"]["sourceFingerprint"], "a" * 64)

        with mock.patch.object(macos_probe, "host_bridge_source_provenance", return_value=changed_source):
            stamped = macos_probe._stamp_result({"ok": True, "mode": "live"}, source=source_snapshot)
        self.assertEqual(stamped["source"]["sourceFingerprint"], "a" * 64)

    def test_macos_textedit_probe_refreshes_text_ref_after_native_scroll(self) -> None:
        probe = _load_macos_probe_module()
        act_refs: list[str] = []
        snapshot_calls = 0

        def fake_list_apps(args: dict[str, object], _run_process: object) -> dict[str, object]:
            query = str(args.get("query") or "")
            include_running = args.get("includeRunning") is True
            if query == "TextEdit" and include_running and not fake_list_apps.opened:
                return {"returnCode": 0, "running": []}
            if query == "TextEdit" and include_running:
                return {"returnCode": 0, "running": [{"name": "TextEdit", "processId": 42}]}
            return {"returnCode": 0, "running": []}

        fake_list_apps.opened = False  # type: ignore[attr-defined]

        def fake_open_app(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            fake_list_apps.opened = True  # type: ignore[attr-defined]
            return {"returnCode": 0}

        def fake_snapshot(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            nonlocal snapshot_calls
            snapshot_calls += 1
            text_ref = "d-old" if snapshot_calls == 1 else "d-new"
            return {
                "returnCode": 0,
                "refCount": 2,
                "snapshot": {
                    "elements": [
                        {
                            "ref": "d-scroll",
                            "role": "AXScrollArea",
                            "nativeSupportedActions": ["scroll"],
                            "axActions": ["AXScrollDownByPage"],
                            "supportedActions": ["scroll"],
                            "settableAttributes": [],
                        },
                        {
                            "ref": text_ref,
                            "role": "AXTextArea",
                            "nativeSupportedActions": ["paste", "type"],
                            "supportedActions": ["paste", "type"],
                            "settableAttributes": ["AXValue"],
                            "value": "" if text_ref == "d-old" else probe.MACOS_TEXTEDIT_EXPECTED_TEXT,
                        },
                    ]
                },
            }

        def fake_act(args: dict[str, object], _run_process: object) -> dict[str, object]:
            act_refs.append(str(args.get("ref") or ""))
            if args.get("action") == "scroll":
                return {
                    "returnCode": 0,
                    "usedNativeAction": True,
                    "nativeAttempt": {
                        "returnCode": 0,
                        "nativeStatus": "OK",
                        "nativeAction": "AXScrollDownByPage",
                    },
                }
            return {
                "returnCode": 0,
                "usedNativeAction": True,
                "nativeAttempt": {
                    "returnCode": 0,
                    "nativeStatus": "OK",
                    "nativeAction": "setValue",
                },
            }

        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(probe, "get_settings", return_value=type("Settings", (), {"data_dir": Path(tmp)})()),
                mock.patch.object(visual_bridge, "execute_list_apps", fake_list_apps),
                mock.patch.object(visual_bridge, "execute_open_app", fake_open_app),
                mock.patch.object(visual_bridge, "execute_activate_app", lambda _args, _run_process: {"returnCode": 0, "ok": True}),
                mock.patch.object(visual_bridge, "execute_desktop_snapshot", fake_snapshot),
                mock.patch.object(visual_bridge, "execute_desktop_act", fake_act),
                mock.patch.object(visual_bridge, "execute_quit_app", lambda _args, _run_process: {"returnCode": 0, "quitVerified": True}),
                mock.patch.object(probe.time, "sleep", lambda _seconds: None),
            ):
                result = probe._interactive_textedit_probe()

        self.assertNotIn("error", result)
        self.assertEqual(act_refs, ["d-scroll", "d-new"])
        self.assertEqual(result["refreshedTextRefAfterScroll"], "d-new")
        self.assertTrue(result["nativeActionVerified"])
        self.assertTrue(result["textValueVerified"])

    def test_host_bridge_parity_status_reports_local_blocked_runtime(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "darwin",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": False,
                    "macosVisualPreflightChecked": True,
                    "macosVisualPreflightOk": False,
                    "macosVisualPreflightError": "foregroundSession: macOS foreground session is loginwindow",
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        fake_profiles = {"browserApp": {"name": "Google Chrome", "path": "/Applications/Google Chrome.app"}}
        payload = self._host_bridge_parity_status_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=_verified_parity_report(),
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "local_blocked")
        self.assertIn("loginwindow", " ".join(payload["gaps"]))
        self.assertEqual(payload["local"]["platform"], "darwin")
        self.assertEqual(payload["local"]["desktop"]["proofStatus"], "local_blocked")
        self.assertEqual(payload["local"]["macosVisualPreflight"]["ok"], False)
        self.assertEqual(payload["contract"]["target"], "openclaw_level_windows_host_parity")
        self.assertTrue(payload["contract"]["noSilentDegradation"])
        self.assertTrue(payload["contract"]["windowsNativePrimary"])
        self.assertEqual(payload["contract"]["connectorRequirements"][1]["currentStatus"], "local_blocked")
        self.assertEqual(payload["commands"]["automationReport"], payload["commands"]["report"])
        self.assertIn("./atrium automation report", payload["commands"]["automationReport"])
        self.assertEqual(payload["commands"]["verify"], "./atrium automation audit")
        self.assertRegex(payload["commands"]["parityRunId"], r"^atrium-\d+-[0-9a-f-]{36}$")
        self.assertRegex(payload["commands"]["sourceFingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(payload["commands"]["sourceManifestSha256"], payload["commands"]["sourceFingerprint"])
        self.assertEqual(payload["commands"]["sourceFileCount"], str(len(main_module.SOURCE_FINGERPRINT_FILES)))
        self.assertIn(payload["commands"]["parityRunId"], payload["commands"]["macosRunIdExport"])
        self.assertIn("ops/host_bridge_source_summary.py", payload["commands"]["macosSourceValidate"])
        self.assertIn(payload["commands"]["sourceFingerprint"], payload["commands"]["macosSourceValidate"])
        self.assertIn("--expect-source-manifest-sha256", payload["commands"]["macosSourceValidate"])
        self.assertIn(payload["commands"]["sourceManifestSha256"], payload["commands"]["macosSourceValidate"])
        self.assertIn("--expect-source-file-count", payload["commands"]["macosSourceValidate"])
        self.assertIn(payload["commands"]["sourceFileCount"], payload["commands"]["macosSourceValidate"])
        self.assertIn(payload["commands"]["parityRunId"], payload["commands"]["windowsRunIdSet"])
        self.assertIn(".\\atrium.ps1 automation source", payload["commands"]["windowsSourceValidate"])
        self.assertIn(payload["commands"]["sourceFingerprint"], payload["commands"]["windowsSourceValidate"])
        self.assertIn("--expect-source-manifest-sha256", payload["commands"]["windowsSourceValidate"])
        self.assertIn(payload["commands"]["sourceManifestSha256"], payload["commands"]["windowsSourceValidate"])
        self.assertIn("--expect-source-file-count", payload["commands"]["windowsSourceValidate"])
        self.assertIn(payload["commands"]["sourceFileCount"], payload["commands"]["windowsSourceValidate"])
        self.assertIn("--json", payload["commands"]["windowsSourceValidate"])
        self.assertIn("ops/macos_host_bridge_probe.py --full", payload["commands"]["macosProbe"])
        self.assertIn("--parity-run-id", payload["commands"]["macosProbe"])
        self.assertIn("--expect-source-fingerprint", payload["commands"]["macosProbe"])
        self.assertIn(payload["commands"]["sourceFingerprint"], payload["commands"]["macosProbe"])
        self.assertIn("--expect-source-manifest-sha256", payload["commands"]["macosProbe"])
        self.assertIn(payload["commands"]["sourceManifestSha256"], payload["commands"]["macosProbe"])
        self.assertIn("--expect-source-file-count", payload["commands"]["macosProbe"])
        self.assertIn(payload["commands"]["sourceFileCount"], payload["commands"]["macosProbe"])
        self.assertIn(payload["commands"]["parityRunId"], payload["commands"]["macosProbe"])
        self.assertIn("ops/host_bridge_artifact_summary.py", payload["commands"]["macosArtifactValidate"])
        self.assertIn("--label macos", payload["commands"]["macosArtifactValidate"])
        self.assertIn(payload["commands"]["parityRunId"], payload["commands"]["macosArtifactValidate"])
        self.assertIn(payload["commands"]["sourceFingerprint"], payload["commands"]["macosArtifactValidate"])
        self.assertIn("--expect-source-manifest-sha256", payload["commands"]["macosArtifactValidate"])
        self.assertIn(payload["commands"]["sourceManifestSha256"], payload["commands"]["macosArtifactValidate"])
        self.assertIn("--expect-source-file-count", payload["commands"]["macosArtifactValidate"])
        self.assertIn(payload["commands"]["sourceFileCount"], payload["commands"]["macosArtifactValidate"])
        self.assertIn("--max-artifact-age-hours 24.0", payload["commands"]["macosArtifactValidate"])
        self.assertIn(".\\atrium.ps1 automation windows-probe --full", payload["commands"]["windowsProbe"])
        self.assertIn("--parity-run-id", payload["commands"]["windowsProbe"])
        self.assertIn("--expect-source-fingerprint", payload["commands"]["windowsProbe"])
        self.assertIn(payload["commands"]["sourceFingerprint"], payload["commands"]["windowsProbe"])
        self.assertIn("--expect-source-manifest-sha256", payload["commands"]["windowsProbe"])
        self.assertIn(payload["commands"]["sourceManifestSha256"], payload["commands"]["windowsProbe"])
        self.assertIn("--expect-source-file-count", payload["commands"]["windowsProbe"])
        self.assertIn(payload["commands"]["sourceFileCount"], payload["commands"]["windowsProbe"])
        self.assertIn(payload["commands"]["parityRunId"], payload["commands"]["windowsProbe"])
        self.assertIn(".\\atrium.ps1 automation windows-live-proof", payload["commands"]["windowsLiveProofRunner"])
        self.assertIn("--parity-run-id", payload["commands"]["windowsLiveProofRunner"])
        self.assertIn("--source-fingerprint", payload["commands"]["windowsLiveProofRunner"])
        self.assertIn("--source-manifest-sha256", payload["commands"]["windowsLiveProofRunner"])
        self.assertIn("--source-file-count", payload["commands"]["windowsLiveProofRunner"])
        self.assertIn(payload["commands"]["parityRunId"], payload["commands"]["windowsLiveProofRunner"])
        self.assertIn(payload["commands"]["sourceFingerprint"], payload["commands"]["windowsLiveProofRunner"])
        self.assertIn(payload["commands"]["sourceManifestSha256"], payload["commands"]["windowsLiveProofRunner"])
        self.assertIn(payload["commands"]["sourceFileCount"], payload["commands"]["windowsLiveProofRunner"])
        self.assertIn("C:\\Temp\\atrium_host_bridge_windows_live.json", payload["commands"]["windowsLiveProofRunner"])
        self.assertIn(".\\atrium.ps1 automation artifact", payload["commands"]["windowsArtifactValidateOnWindows"])
        self.assertIn("--label windows", payload["commands"]["windowsArtifactValidateOnWindows"])
        self.assertIn(payload["commands"]["parityRunId"], payload["commands"]["windowsArtifactValidateOnWindows"])
        self.assertIn(payload["commands"]["sourceFingerprint"], payload["commands"]["windowsArtifactValidateOnWindows"])
        self.assertIn("--expect-source-manifest-sha256", payload["commands"]["windowsArtifactValidateOnWindows"])
        self.assertIn(payload["commands"]["sourceManifestSha256"], payload["commands"]["windowsArtifactValidateOnWindows"])
        self.assertIn("--expect-source-file-count", payload["commands"]["windowsArtifactValidateOnWindows"])
        self.assertIn(payload["commands"]["sourceFileCount"], payload["commands"]["windowsArtifactValidateOnWindows"])
        self.assertIn("--max-artifact-age-hours 24.0", payload["commands"]["windowsArtifactValidateOnWindows"])
        self.assertIn("--json", payload["commands"]["windowsArtifactValidateOnWindows"])
        self.assertEqual(payload["commands"]["windowsArtifactSource"], "C:\\Temp\\atrium_host_bridge_windows_live.json")
        self.assertEqual(payload["commands"]["windowsArtifactLocal"], "/tmp/atrium_host_bridge_windows_live.json")
        self.assertIn("C:\\Temp\\atrium_host_bridge_windows_live.json", payload["commands"]["windowsArtifactCopyHint"])
        self.assertIn("/tmp/atrium_host_bridge_windows_live.json", payload["commands"]["windowsArtifactCopyHint"])
        self.assertIn("./atrium automation artifact", payload["commands"]["windowsArtifactValidateLocal"])
        self.assertIn("--label windows", payload["commands"]["windowsArtifactValidateLocal"])
        self.assertIn(payload["commands"]["parityRunId"], payload["commands"]["windowsArtifactValidateLocal"])
        self.assertIn(payload["commands"]["sourceFingerprint"], payload["commands"]["windowsArtifactValidateLocal"])
        self.assertIn("--expect-source-manifest-sha256", payload["commands"]["windowsArtifactValidateLocal"])
        self.assertIn(payload["commands"]["sourceManifestSha256"], payload["commands"]["windowsArtifactValidateLocal"])
        self.assertIn("--expect-source-file-count", payload["commands"]["windowsArtifactValidateLocal"])
        self.assertIn(payload["commands"]["sourceFileCount"], payload["commands"]["windowsArtifactValidateLocal"])
        self.assertIn("--max-artifact-age-hours 24.0", payload["commands"]["windowsArtifactValidateLocal"])
        self.assertIn("--json", payload["commands"]["windowsArtifactValidateLocal"])
        self.assertIn("--windows /tmp/atrium_host_bridge_windows_live.json", payload["commands"]["automationReport"])
        self.assertIn("--max-artifact-age-hours 24.0", payload["commands"]["automationReport"])
        self.assertIn("--windows-source-path 'C:\\Temp\\atrium_host_bridge_windows_live.json'", payload["commands"]["automationReport"])
        self.assertIn("ops/host_bridge_parity_report.py", payload["commands"]["legacyParityReport"])
        self.assertIn("--max-artifact-age-hours 24.0", payload["commands"]["legacyParityReport"])
        self.assertIn("--output system/data/host-bridge-parity-report.json", payload["commands"]["legacyParityReport"])

    def test_host_bridge_parity_status_accepts_verified_report_and_endpoint_handler(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                    "shellReady": True,
                    "interactiveSession": True,
                    "interactiveSessionName": "Console",
                    "interactiveSessionId": 1,
                    "windowsVisualPreflightChecked": True,
                    "windowsVisualPreflightOk": True,
                    "windowsVisualPreflightChecks": {
                        "dpiAwareness": True,
                        "virtualScreen": True,
                    },
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        report = _verified_parity_report()
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "host-bridge-parity-report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            settings = main_module.get_settings().model_copy(update={
                "host_bridge_parity_report_path": report_path,
                "mcp_gateway_url": "http://127.0.0.1:9999/mcp",
                "mcp_gateway_token": "test-token",
                "mcp_enabled_servers": "github,calendar,drive,notion",
            })
            fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
            with (
                mock.patch.object(main_module, "HostBridge", FakeHostBridge),
                mock.patch.object(main_module, "list_browser_profiles", lambda: fake_profiles),
                mock.patch.object(main_module, "_mcp_gateway_health", lambda _settings, probe=False: {"configured": True, "checked": True, "ok": True, "status": 200}),
                mock.patch.object(main_module, "get_settings", lambda: settings),
                mock.patch.object(
                    main_module,
                    "host_bridge_source_provenance",
                    lambda: {"sourceFingerprint": "a" * 64, "sourceManifestSha256": "a" * 64, "sourceFileCount": len(main_module.SOURCE_FINGERPRINT_FILES), "gitHead": "b" * 40, "gitDirty": False},
                ),
            ):
                payload = asyncio.run(main_module.get_host_bridge_parity())

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "cross_os_verified")
        self.assertEqual(payload["gaps"], [])
        self.assertEqual(payload["report"]["sourceFingerprint"], "a" * 64)
        self.assertEqual(payload["report"]["proofId"], report["proofId"])
        self.assertEqual(payload["report"]["parityRunId"], "parity-run-1")
        self.assertEqual(payload["report"]["artifactSha256"]["windows"], "2" * 64)
        self.assertEqual(payload["contract"]["status"], "cross_os_verified")
        self.assertTrue(payload["contract"]["noSilentDegradation"])
        self.assertTrue(payload["contract"]["windowsNativePrimary"])
        self.assertTrue(payload["contract"]["windowsNativeOnly"])
        lifecycle_requirement = next(
            item for item in payload["contract"]["localRequirements"]
            if item["id"] == "windows_native_lifecycle"
        )
        self.assertTrue(lifecycle_requirement["currentDetails"]["atriumPs1"])
        self.assertTrue(lifecycle_requirement["currentDetails"]["atriumPs1Runner"])
        self.assertTrue(lifecycle_requirement["currentDetails"]["atriumCmd"])
        self.assertTrue(lifecycle_requirement["currentDetails"]["atriumCmdForwarder"])
        self.assertTrue(lifecycle_requirement["currentDetails"]["installWindowsNativePs1"])
        self.assertTrue(lifecycle_requirement["currentDetails"]["installWindowsNativeSafety"])
        self.assertTrue(lifecycle_requirement["currentDetails"]["atriumCli"])
        self.assertTrue(lifecycle_requirement["currentDetails"]["pidFiles"])
        self.assertTrue(lifecycle_requirement["currentDetails"]["statusJson"])
        self.assertTrue(lifecycle_requirement["currentDetails"]["python3Validated"])
        self.assertTrue(lifecycle_requirement["currentDetails"]["logsJson"])
        self.assertTrue(lifecycle_requirement["currentDetails"]["supportBundle"])
        self.assertTrue(lifecycle_requirement["currentDetails"]["selfUpdateRestart"])
        self.assertIn(".\\atrium.ps1 status --json", lifecycle_requirement["requiredEvidence"])
        self.assertIn(".\\atrium.ps1 logs --json", lifecycle_requirement["requiredEvidence"])
        self.assertIn(".\\atrium.ps1 report --bundle", lifecycle_requirement["requiredEvidence"])
        self.assertIn("runnable Python 3 validation in .\\atrium.ps1 setup", lifecycle_requirement["requiredEvidence"])
        self.assertIn("UI self-update restart through .\\atrium.ps1 restart --force", lifecycle_requirement["requiredEvidence"])
        entrypoint_requirement = next(
            item for item in payload["contract"]["localRequirements"]
            if item["id"] == "windows_native_entrypoints"
        )
        entrypoint_evidence = " ".join(str(item) for item in entrypoint_requirement["requiredEvidence"])
        self.assertIn("atrium.cmd", entrypoint_evidence)
        self.assertIn("pwsh fallback", entrypoint_evidence)
        self.assertIn("ops/atrium_cli.py", entrypoint_requirement["requiredEvidence"])
        self.assertTrue(entrypoint_requirement["currentDetails"]["installWindowsNativeBaseSafety"])
        self.assertTrue(entrypoint_requirement["currentDetails"]["installWindowsNativeSetupRunner"])
        self.assertTrue(entrypoint_requirement["currentDetails"]["installWindowsNativeSafety"])
        provider_tools_requirement = next(
            item for item in payload["contract"]["localRequirements"]
            if item["id"] == "windows_native_provider_ai_tools"
        )
        self.assertTrue(provider_tools_requirement["currentReady"])
        self.assertTrue(provider_tools_requirement["currentDetails"]["commandProvider"])
        self.assertTrue(provider_tools_requirement["currentDetails"]["commandTools"])
        self.assertTrue(provider_tools_requirement["currentDetails"]["providerStatusJson"])
        self.assertTrue(provider_tools_requirement["currentDetails"]["providerStatusJsonRedaction"])
        self.assertTrue(provider_tools_requirement["currentDetails"]["windowsUrlOpenNative"])
        self.assertIn(".\\atrium.ps1 provider status --probe --json", provider_tools_requirement["requiredEvidence"])
        self.assertIn("native Windows URL opener for provider OAuth", provider_tools_requirement["requiredEvidence"])
        self.assertTrue(provider_tools_requirement["currentDetails"]["toolsStatusJson"])
        self.assertTrue(provider_tools_requirement["currentDetails"]["toolsCatalogJson"])
        self.assertIn(".\\atrium.ps1 tools status --json", provider_tools_requirement["requiredEvidence"])
        self.assertIn(".\\atrium.ps1 tools catalog --json", provider_tools_requirement["requiredEvidence"])
        self.assertTrue(provider_tools_requirement["currentDetails"]["toolCatalogEndpoint"])
        command_gate_requirement = next(
            item for item in payload["contract"]["localRequirements"]
            if item["id"] == "openclaw_audit_report_commands"
        )
        self.assertTrue(command_gate_requirement["currentReady"])
        self.assertTrue(command_gate_requirement["currentDetails"]["automationAudit"])
        self.assertTrue(command_gate_requirement["currentDetails"]["automationStatusJson"])
        self.assertTrue(command_gate_requirement["currentDetails"]["automationHandoff"])
        self.assertTrue(command_gate_requirement["currentDetails"]["automationReport"])
        self.assertTrue(command_gate_requirement["currentDetails"]["defaultReportPath"])
        self.assertIn(".\\atrium.ps1 automation status --json", command_gate_requirement["requiredEvidence"])
        self.assertIn(".\\atrium.ps1 automation handoff --macos <macos-json>", command_gate_requirement["requiredEvidence"])
        self.assertTrue(all(item["currentReady"] for item in payload["contract"]["reportRequirements"]))
        report_requirement_ids = {item["id"] for item in payload["contract"]["reportRequirements"]}
        self.assertIn("proof_id_bound_to_artifacts", report_requirement_ids)
        self.assertIn("artifact_hash_and_size_recorded", report_requirement_ids)
        self.assertIn("source_file_provenance_recorded", report_requirement_ids)
        self.assertIn("host_identity_recorded", report_requirement_ids)
        self.assertTrue(all(item["proved"] for item in payload["contract"]["windowsProofRequirements"]))
        self.assertTrue(all(item["proved"] for item in payload["contract"]["connectorRequirements"]))
        self.assertTrue(all(item["registered"] for item in payload["contract"]["apiSurfaceRequirements"]))
        required_features = [item for item in payload["contract"]["featureRequirements"] if item["required"]]
        self.assertTrue(all(item["ready"] for item in required_features))
        self.assertEqual(
            {item["id"] for item in required_features},
            {"local_file", "git", "sandbox", "http", "web", "browser", "desktop", "mcp"},
        )
        mcp_requirement = next(item for item in required_features if item["id"] == "mcp")
        self.assertTrue(mcp_requirement["requiresWriteReady"])
        self.assertFalse(mcp_requirement["degradedByLocalFallback"])
        self.assertTrue(mcp_requirement["writeReady"])
        self.assertEqual(payload["connectors"][0]["proofStatus"], "cross_os_verified")
        self.assertEqual(payload["connectors"][1]["proofStatus"], "cross_os_verified")
        self.assertEqual(payload["commands"]["automationReport"], payload["commands"]["report"])
        self.assertIn(".\\atrium.ps1 automation report", payload["commands"]["automationReport"])
        self.assertEqual(payload["commands"]["verify"], ".\\atrium.ps1 automation audit")
        self.assertIn("system/data/host-bridge-parity-report.json", payload["commands"]["legacyParityReport"])

    def test_host_bridge_parity_status_reports_openclaw_contract_gaps_when_report_proof_is_verified(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                    "shellReady": True,
                    "interactiveSession": True,
                    "interactiveSessionName": "Console",
                    "interactiveSessionId": 1,
                    "windowsVisualPreflightChecked": True,
                    "windowsVisualPreflightOk": True,
                    "windowsVisualPreflightChecks": {
                        "dpiAwareness": True,
                        "virtualScreen": True,
                    },
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        report = _verified_parity_report()
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "host-bridge-parity-report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            settings = main_module.get_settings().model_copy(update={
                "host_bridge_parity_report_path": report_path,
                "mcp_gateway_url": "",
                "mcp_gateway_token": "",
                "mcp_enabled_servers": "",
            })
            fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
            with (
                mock.patch.object(main_module, "HostBridge", FakeHostBridge),
                mock.patch.object(main_module, "list_browser_profiles", lambda: fake_profiles),
                mock.patch.object(main_module, "get_settings", lambda: settings),
                mock.patch.object(
                    main_module,
                    "host_bridge_source_provenance",
                    lambda: {"sourceFingerprint": "a" * 64, "sourceManifestSha256": "a" * 64, "sourceFileCount": len(main_module.SOURCE_FINGERPRINT_FILES), "gitHead": "b" * 40, "gitDirty": False},
                ),
            ):
                payload = asyncio.run(main_module.get_host_bridge_parity())

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "cross_os_unverified")
        self.assertEqual(payload["connectors"][0]["proofStatus"], "cross_os_verified")
        self.assertEqual(payload["connectors"][1]["proofStatus"], "cross_os_verified")
        self.assertEqual(payload["contract"]["status"], "cross_os_unverified")
        self.assertIn("OpenClaw contract gap: MCP external tools", payload["gaps"])
        mcp_requirement = next(item for item in payload["contract"]["featureRequirements"] if item["id"] == "mcp")
        self.assertFalse(mcp_requirement["ready"])
        self.assertTrue(mcp_requirement["degradedByLocalFallback"])

    def test_openclaw_level_contract_refuses_missing_required_feature_connector(self) -> None:
        host_bridge_status = {
            "platform": "win32",
            "shellReady": True,
            "interactiveSession": True,
            "interactiveSessionName": "Console",
            "interactiveSessionId": 1,
            "windowsVisualPreflightChecked": True,
            "windowsVisualPreflightOk": True,
            "isolatedBrowserProfileReady": True,
            "browserPlaywrightReady": True,
        }
        proof_connectors = [
            {"id": "browser", "proof_status": "cross_os_verified"},
            {"id": "desktop", "proof_status": "cross_os_verified"},
        ]
        connectors_by_id = {
            "local_file": {"status": "available", "readReady": True, "writeReady": True},
            "sandbox": {"status": "available", "readReady": True, "writeReady": True},
            "http": {"status": "available", "readReady": True, "writeReady": True},
            "web": {"status": "available", "readReady": True, "writeReady": False, "localFallback": True},
            "mcp": {"status": "configured", "readReady": True, "writeReady": True, "localFallback": False, "externalWriteRequires": []},
            "browser": {"status": "available", "readReady": True, "writeReady": True, "proofStatus": "cross_os_verified"},
            "desktop": {"status": "available", "readReady": True, "writeReady": True, "proofStatus": "cross_os_verified"},
        }

        contract = main_module._host_bridge_openclaw_level_contract(
            host_bridge=host_bridge_status,
            parity_report=_verified_parity_report(),
            proof_connectors=proof_connectors,
            connectors_by_id=connectors_by_id,
        )

        self.assertEqual(contract["status"], "cross_os_unverified")
        git_requirement = next(item for item in contract["featureRequirements"] if item["id"] == "git")
        self.assertEqual(git_requirement["status"], "missing")
        self.assertFalse(git_requirement["ready"])

    def test_openclaw_level_contract_refuses_mcp_local_fallback_for_external_tools(self) -> None:
        host_bridge_status = {
            "platform": "win32",
            "shellReady": True,
            "interactiveSession": True,
            "interactiveSessionName": "Console",
            "interactiveSessionId": 1,
            "windowsVisualPreflightChecked": True,
            "windowsVisualPreflightOk": True,
            "isolatedBrowserProfileReady": True,
            "browserPlaywrightReady": True,
        }
        proof_connectors = [
            {"id": "browser", "proof_status": "cross_os_verified"},
            {"id": "desktop", "proof_status": "cross_os_verified"},
        ]
        connectors_by_id = {
            "local_file": {"status": "available", "readReady": True, "writeReady": True},
            "git": {"status": "available", "readReady": True, "writeReady": True},
            "sandbox": {"status": "available", "readReady": True, "writeReady": True},
            "http": {"status": "available", "readReady": True, "writeReady": True},
            "web": {"status": "available", "readReady": True, "writeReady": False, "localFallback": True},
            "mcp": {
                "status": "available",
                "readReady": True,
                "writeReady": False,
                "localFallback": True,
                "externalWriteRequires": ["ATRIUM_MCP_GATEWAY_URL configured for write-capable external MCP servers"],
            },
            "browser": {"status": "available", "readReady": True, "writeReady": True, "proofStatus": "cross_os_verified"},
            "desktop": {"status": "available", "readReady": True, "writeReady": True, "proofStatus": "cross_os_verified"},
        }

        contract = main_module._host_bridge_openclaw_level_contract(
            host_bridge=host_bridge_status,
            parity_report=_verified_parity_report(),
            proof_connectors=proof_connectors,
            connectors_by_id=connectors_by_id,
        )

        self.assertEqual(contract["status"], "cross_os_unverified")
        mcp_requirement = next(item for item in contract["featureRequirements"] if item["id"] == "mcp")
        self.assertTrue(mcp_requirement["required"])
        self.assertTrue(mcp_requirement["requiresWriteReady"])
        self.assertTrue(mcp_requirement["degradedByLocalFallback"])
        self.assertFalse(mcp_requirement["ready"])
        self.assertIn("ATRIUM_MCP_GATEWAY_URL", " ".join(mcp_requirement["externalWriteRequires"]))

    def test_openclaw_level_contract_refuses_broken_cmd_entrypoint(self) -> None:
        host_bridge_status = {
            "platform": "win32",
            "shellReady": True,
            "interactiveSession": True,
            "interactiveSessionName": "Console",
            "interactiveSessionId": 1,
            "windowsVisualPreflightChecked": True,
            "windowsVisualPreflightOk": True,
            "isolatedBrowserProfileReady": True,
            "browserPlaywrightReady": True,
        }
        proof_connectors = [
            {"id": "browser", "proof_status": "cross_os_verified"},
            {"id": "desktop", "proof_status": "cross_os_verified"},
        ]
        connectors_by_id = {
            "local_file": {"status": "available", "readReady": True, "writeReady": True},
            "git": {"status": "available", "readReady": True, "writeReady": True},
            "sandbox": {"status": "available", "readReady": True, "writeReady": True},
            "http": {"status": "available", "readReady": True, "writeReady": True},
            "web": {"status": "available", "readReady": True, "writeReady": False, "localFallback": True},
            "mcp": {"status": "configured", "readReady": True, "writeReady": True, "localFallback": False, "externalWriteRequires": []},
            "browser": {"status": "available", "readReady": True, "writeReady": True, "proofStatus": "cross_os_verified"},
            "desktop": {"status": "available", "readReady": True, "writeReady": True, "proofStatus": "cross_os_verified"},
        }

        def fake_read_text(path: Path, *_args: object, **_kwargs: object) -> str:
            path_text = str(path).replace("\\", "/")
            if path_text.endswith("atrium.cmd"):
                return "@echo off\nrem broken shim\n"
            if path_text.endswith("atrium.ps1"):
                return "Add-PathIfExists\nAdd-PythonInstallPaths\nops\\atrium_cli.py\nsystem\\.venv\\Scripts\\python.exe\nPython3*\nPython312\nPython311\nuv\nPython 3 is required\n"
            if path_text.endswith("install_windows_native.ps1"):
                return (
                    "Assert-SafeInstallPath\nTest-Python3Available\nInstall-PythonIfMissing\n"
                    "Invoke-Native\n$LASTEXITCODE\nPython3*\nPython312\nPython311\nOneDrive\nDesktop\nDocuments\nDownloads\n"
                    "Test-BrowserInstalled\nInstall-WingetPackageIfMissing\nDocker.DockerDesktop\nGoogle.Chrome\n"
                    "BraveSoftware\nChromium\nAnthropic.ClaudeCode\ncorepack enable failed\n"
                    "corepack pnpm activation failed\nClaude Code npm install failed\n\".\\atrium.ps1\"\n\"setup\"\n\"--yes\"\n"
                )
            if path_text.endswith("atrium_cli.py"):
                return (
                    "BACKEND_PID\nUI_PID\npid_detail\nwindows_process_details\nwindows_process_status\nreport_command_path\ndef command_provider\n"
                    "def collect_status_payload\nstatus.add_argument(\"--json\"\nredact_json_value(collect_status_payload())\n"
                    "def command_report\n--bundle\nzipfile.ZipFile\nsupport-report.txt\n"
                    "diagnostics/status.json\ndiagnostics/process.json\ndiagnostics/logs.json\n"
                    "diagnostics/permission-mode.json\ndiagnostics/provider-status.json\n"
                    "diagnostics/tools-status.json\ndiagnostics/automation-status.json\nlogs/{label}.log\n"
                    "def command_logs\nlogs.add_argument(\"--json\"\nRun .\\\\atrium.ps1 start to create native\nredact_text\n"
                    "def collect_automation_status_payload\nnormalized[\"permissionMode\"]\n\"localArtifacts\"\n"
                    "automation_permission.local_artifacts\nOwner Permissions\nLocal Proof Artifacts\nsummarize_full_autonomy\n"
                    "def command_tools\nprovider_status.add_argument(\"--probe\"\n"
                    "provider_status.add_argument(\"--json\"\nredact_json_value\n"
                    "getattr(os, \"startfile\"\nStart-Process -FilePath\nps_single_quote(url)\n"
                    "/api/provider-auth/status\n/api/tools/catalog\n/api/connectors\n"
                    "/api/runtime\n/api/permissions/mode\n/api/host-bridge/parity\n"
                    "summarize_tool_catalog_payload\naction == \"audit\"\naction == \"artifact\"\naction == \"report\"\n"
                    "HOST_BRIDGE_PARITY_REPORT\nops/host_bridge_parity_report.py\n"
                    "--skip-current-source-check\nonly allowed for offline historical audits written to a custom --output path\n"
                    "OpenClaw Windows contract\n"
                )
            return ""

        with (
            mock.patch.object(Path, "exists", lambda _path: True),
            mock.patch.object(Path, "read_text", fake_read_text),
        ):
            contract = main_module._host_bridge_openclaw_level_contract(
                host_bridge=host_bridge_status,
                parity_report=_verified_parity_report(),
                proof_connectors=proof_connectors,
                connectors_by_id=connectors_by_id,
            )

        self.assertEqual(contract["status"], "local_blocked")
        lifecycle_requirement = next(item for item in contract["localRequirements"] if item["id"] == "windows_native_lifecycle")
        entrypoint_requirement = next(item for item in contract["localRequirements"] if item["id"] == "windows_native_entrypoints")
        self.assertFalse(lifecycle_requirement["currentDetails"]["atriumCmdForwarder"])
        self.assertFalse(lifecycle_requirement["currentReady"])
        self.assertFalse(entrypoint_requirement["currentReady"])

    def test_openclaw_level_contract_refuses_incomplete_report_integrity_details(self) -> None:
        host_bridge_status = {
            "platform": "win32",
            "shellReady": True,
            "interactiveSession": True,
            "interactiveSessionName": "Console",
            "interactiveSessionId": 1,
            "windowsVisualPreflightChecked": True,
            "windowsVisualPreflightOk": True,
            "isolatedBrowserProfileReady": True,
            "browserPlaywrightReady": True,
        }
        proof_connectors = [
            {"id": "browser", "proof_status": "cross_os_verified"},
            {"id": "desktop", "proof_status": "cross_os_verified"},
        ]
        connectors_by_id = {
            "local_file": {"status": "available", "readReady": True, "writeReady": True},
            "git": {"status": "available", "readReady": True, "writeReady": True},
            "sandbox": {"status": "available", "readReady": True, "writeReady": True},
            "http": {"status": "available", "readReady": True, "writeReady": True},
            "web": {"status": "available", "readReady": True, "writeReady": False, "localFallback": True},
            "mcp": {"status": "configured", "readReady": True, "writeReady": True, "localFallback": False, "externalWriteRequires": []},
            "browser": {"status": "available", "readReady": True, "writeReady": True, "proofStatus": "cross_os_verified"},
            "desktop": {"status": "available", "readReady": True, "writeReady": True, "proofStatus": "cross_os_verified"},
        }
        verified_proofs = _verified_parity_proofs()
        parity_report = {
            "ok": True,
            "details": {
                "proofs": verified_proofs,
                "proofId": "a" * 64,
                "expectedProofId": "a" * 64,
                "currentSourceFingerprint": "b" * 64,
                "currentGitHead": "c" * 40,
                "parityRunId": "parity-run-1",
            },
        }

        contract = main_module._host_bridge_openclaw_level_contract(
            host_bridge=host_bridge_status,
            parity_report=parity_report,
            proof_connectors=proof_connectors,
            connectors_by_id=connectors_by_id,
        )

        self.assertEqual(contract["status"], "cross_os_unverified")
        artifact_requirement = next(item for item in contract["reportRequirements"] if item["id"] == "artifact_hash_and_size_recorded")
        source_requirement = next(item for item in contract["reportRequirements"] if item["id"] == "source_file_provenance_recorded")
        host_requirement = next(item for item in contract["reportRequirements"] if item["id"] == "host_identity_recorded")
        self.assertFalse(artifact_requirement["currentReady"])
        self.assertFalse(source_requirement["currentReady"])
        self.assertFalse(host_requirement["currentReady"])

    def test_openclaw_command_surface_refuses_missing_report_gate(self) -> None:
        self.assertFalse(main_module._openclaw_windows_command_surface_ready('action == "audit"'))
        self.assertFalse(main_module._openclaw_windows_command_surface_ready(
            'action == "audit"\n'
            'action == "artifact"\n'
            'action == "report"\n'
            'HOST_BRIDGE_PARITY_REPORT\n'
            'ops/host_bridge_parity_report.py\n'
            'OpenClaw Windows contract\n'
        ))
        self.assertFalse(main_module._openclaw_windows_command_surface_ready(
            'action == "audit"\n'
            'action == "artifact"\n'
            'action == "report"\n'
            'automation_status.add_argument("--json"\n'
            'HOST_BRIDGE_PARITY_REPORT\n'
            'ops/host_bridge_parity_report.py\n'
            '--skip-current-source-check\n'
            'OpenClaw Windows contract\n'
        ))
        self.assertFalse(main_module._openclaw_windows_command_surface_ready(
            'action == "audit"\n'
            'action == "artifact"\n'
            'action == "report"\n'
            'automation_status.add_argument("--json"\n'
            'HOST_BRIDGE_PARITY_REPORT\n'
            'ops/host_bridge_parity_report.py\n'
            '--skip-current-source-check\n'
            'only allowed for offline historical audits written to a custom --output path\n'
            'OpenClaw Windows contract\n'
        ))
        self.assertTrue(main_module._openclaw_windows_command_surface_ready(
            'action == "audit"\n'
            'action == "artifact"\n'
            'action == "handoff"\n'
            'action == "report"\n'
            'automation_status.add_argument("--json"\n'
            'HOST_BRIDGE_PARITY_REPORT\n'
            'ops/host_bridge_parity_report.py\n'
            '--skip-current-source-check\n'
            'only allowed for offline historical audits written to a custom --output path\n'
            'OpenClaw Windows contract\n'
        ))

    def test_host_bridge_parity_refuses_openclaw_level_windows_contract_without_live_session(self) -> None:
        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {
                    "platform": "win32",
                    "browserBridge": True,
                    "browserAutomationReady": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": True,
                    "isolatedBrowserProfileReady": True,
                    "browserPlaywrightReady": True,
                    "shellReady": True,
                    "interactiveSession": False,
                    "interactiveSessionName": "Services",
                    "interactiveSessionId": 0,
                    "windowsVisualPreflightChecked": True,
                    "windowsVisualPreflightOk": True,
                }

        class FakeHostBridge:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def status(self) -> FakeStatus:
                return FakeStatus()

        fake_profiles = {"browserApp": {"name": "Microsoft Edge", "path": "C:/Program Files/Microsoft/Edge/Application/msedge.exe"}}
        payload = self._host_bridge_parity_status_for_test(
            FakeHostBridge,
            fake_profiles,
            parity_report=_verified_parity_report(),
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "local_blocked")
        self.assertEqual(payload["contract"]["status"], "local_blocked")
        interactive = next(
            item for item in payload["contract"]["localRequirements"]
            if item["id"] == "interactive_desktop_session"
        )
        self.assertFalse(interactive["currentReady"])

    def test_windows_background_shell_disables_screen_and_pty_without_posix_session_kwargs(self) -> None:
        class FakeProc:
            pid = 4321
            stdin = None

            def poll(self) -> int | None:
                return None

        popen_calls: list[dict[str, object]] = []

        def fake_popen(_command: list[str], **kwargs: object) -> FakeProc:
            popen_calls.append(kwargs)
            return FakeProc()

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                stdout_path = root / "out.log"
                stderr_path = root / "err.log"
                with (
                    mock.patch.object(chat_tools.shutil, "which", return_value="/usr/bin/screen"),
                    mock.patch.object(chat_tools.subprocess, "Popen", fake_popen),
                    mock.patch.object(chat_tools.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True),
                    mock.patch.object(chat_tools.os, "openpty", side_effect=AssertionError("openpty should not run on Windows")),
                ):
                    self.assertIsNone(chat_tools._owner_screen_executable())
                    proc, pty_fd, pty_error = chat_tools._owner_start_background_process(
                        ["powershell.exe", "-NoProfile", "-Command", "Write-Output ok"],
                        cwd=root,
                        stdout_path=stdout_path,
                        stderr_path=stderr_path,
                        env={},
                        use_pty=True,
                    )
        finally:
            sys.platform = original_platform

        self.assertEqual(proc.pid, 4321)
        self.assertIsNone(pty_fd)
        self.assertIn("PTY is not available on Windows", pty_error or "")
        self.assertEqual(popen_calls[0]["creationflags"], 512)
        self.assertNotIn("start_new_session", popen_calls[0])

    def test_windows_background_shell_marks_native_persistent_pipe_backend(self) -> None:
        class FakeStdin:
            closed = False

        class FakeProc:
            pid = 4321
            stdin = FakeStdin()

        result = chat_tools._owner_background_start_result(
            command=["powershell.exe", "-NoProfile"],
            cwd=Path("C:/repo"),
            proc=FakeProc(),
            stdout_path=Path("C:/repo/out.log"),
            stderr_path=Path("C:/repo/err.log"),
            started_at=123,
            timeout_seconds=300,
            persistent_requested=True,
            persistent_enabled=True,
            persistent_backend="windows_pipe",
            stdin_writable=True,
        )

        self.assertTrue(result["persistent"])
        self.assertTrue(result["persistentRequested"])
        self.assertEqual(result["persistentBackend"], "windows_pipe")
        self.assertTrue(result["stdinWritable"])
        self.assertNotIn("persistentFallbackError", result)

    def test_windows_background_timeout_terminates_and_kills_process_object(self) -> None:
        class FakeProc:
            pid = 2468

            def __init__(self) -> None:
                self.calls: list[str] = []
                self.wait_count = 0

            def wait(self, timeout: float | None = None) -> int:
                self.wait_count += 1
                self.calls.append(f"wait:{timeout}")
                if self.wait_count <= 2:
                    raise chat_tools.subprocess.TimeoutExpired(["slow"], timeout=timeout)
                return -9

            def terminate(self) -> None:
                self.calls.append("terminate")

            def kill(self) -> None:
                self.calls.append("kill")

        original_platform = sys.platform
        proc = FakeProc()
        try:
            sys.platform = "win32"
            return_code, timed_out, signal_name = asyncio.run(
                chat_tools._owner_wait_background_process(proc, timeout_seconds=0.01)
            )
        finally:
            sys.platform = original_platform

        self.assertEqual(return_code, -9)
        self.assertTrue(timed_out)
        self.assertEqual(signal_name, "KILL")
        self.assertIn("terminate", proc.calls)
        self.assertIn("kill", proc.calls)

    def test_windows_process_send_keys_ctrl_z_closes_pipe_stdin(self) -> None:
        class FakeStdin:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        class FakeProc:
            pid = 1357

            def __init__(self) -> None:
                self.stdin = FakeStdin()

            def poll(self) -> int | None:
                return None

        run_id = "run_windows_ctrl_z"
        run = {
            "id": run_id,
            "tool": "shell.exec",
            "status": "running",
            "result": {"backend": "popen", "pid": 1357},
        }
        proc = FakeProc()
        original_platform = sys.platform
        try:
            sys.platform = "win32"
            chat_tools._OWNER_BACKGROUND_PROCESSES[run_id] = proc
            result = asyncio.run(
                chat_tools._owner_process_write_stdin(run, {"keys": ["ctrl-z"]}, "send-keys")
            )
        finally:
            chat_tools._OWNER_BACKGROUND_PROCESSES.pop(run_id, None)
            sys.platform = original_platform

        self.assertTrue(result["ok"])
        self.assertTrue(result["eof"])
        self.assertTrue(proc.stdin.closed)

    def test_windows_probe_passes_browser_profile_to_live_browser_open(self) -> None:
        probe = _load_windows_probe_module()
        captured: dict[str, object] = {}

        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {"browserBridge": True, "desktopBridge": True}

        def fake_open(args: dict[str, object], _run_process: object) -> dict[str, object]:
            captured.update(args)
            return {"returnCode": 0, "stdout": "", "stderr": "", "profile": args.get("profile"), "processId": 42}

        def fake_browser_snapshot(args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {
                "returnCode": 0,
                "refCount": 1,
                "snapshot": {"elements": [{"ref": "b1", "role": "button", "name": "ATRIUM browser probe"}]},
            }

        def fake_browser_act(args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {
                "returnCode": 0,
                "snapshot": {"elements": [{"ref": "b1", "role": "button", "name": "ATRIUM clicked"}]},
            }

        def fake_list_apps(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "running": [], "installed": []}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(probe, "_routes", lambda: {tool: {"blockReason": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(probe, "_route_for", lambda _tool, _args: {"blockReason": None}),
                mock.patch.object(probe, "_runtime_blocks", lambda: {tool: {"api": None, "chat": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(probe, "_runtime_block_for", lambda _tool, _args: {"api": None, "chat": None}),
                mock.patch.object(probe, "_shell_probe", lambda _status: {"returnCode": 0, "containsExpected": True}),
                mock.patch.object(probe.HostBridge, "status", lambda _self: FakeStatus()),
                mock.patch.object(visual_bridge, "execute_windows_visual_selftest", _windows_helper_selftest_ok),
                mock.patch.object(visual_bridge, "execute_windows_powershell_visual_preflight", _windows_preflight_ok),
                mock.patch.object(visual_bridge, "list_browser_profiles", lambda: {"profiles": []}),
                mock.patch.object(visual_bridge, "execute_list_apps", fake_list_apps),
                mock.patch.object(visual_bridge, "execute_browser_open", fake_open),
                mock.patch.object(visual_bridge, "execute_browser_snapshot", fake_browser_snapshot),
                mock.patch.object(visual_bridge, "execute_browser_act", fake_browser_act),
            ):
                result = probe._live(
                    screenshot=False,
                    notification=False,
                    browser_url="https://example.com",
                    browser_profile="atrium",
                    interactive=False,
                )
        finally:
            sys.platform = original_platform

        self.assertTrue(result["ok"])
        self.assertEqual(captured["profile"], "atrium")
        self.assertIsNone(result["checks"]["browserOpenRoute"]["blockReason"])
        self.assertEqual(result["checks"]["browserOpenProcessId"], 42)
        self.assertTrue(result["checks"]["browserRef"]["containsExpected"])

    def test_windows_probe_reports_args_specific_isolated_profile_runtime_block(self) -> None:
        probe = _load_windows_probe_module()
        powershell = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        cmd = "C:/Windows/System32/cmd.exe"

        def fake_exists(path: Path) -> bool:
            return str(path).replace("\\", "/") in {powershell, cmd}

        def fake_list_apps(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "running": [], "installed": []}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(host_bridge.shutil, "which", return_value=None),
                mock.patch.object(Path, "exists", fake_exists),
                mock.patch.dict(os.environ, {"SESSIONNAME": "Console"}, clear=False),
                mock.patch.object(probe, "_routes", lambda: {tool: {"blockReason": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(probe, "_runtime_blocks", lambda: {tool: {"api": None, "chat": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(probe, "_shell_probe", lambda _status: {"returnCode": 0, "containsExpected": True}),
                mock.patch.object(visual_bridge, "execute_windows_visual_selftest", _windows_helper_selftest_ok),
                mock.patch.object(visual_bridge, "execute_windows_powershell_visual_preflight", _windows_preflight_ok),
                mock.patch.object(visual_bridge, "execute_list_apps", fake_list_apps),
                mock.patch.object(visual_bridge, "execute_browser_open", side_effect=AssertionError("blocked browser.open should not execute")),
            ):
                result = probe._live(
                    screenshot=False,
                    notification=False,
                    browser_url="https://example.com",
                    browser_profile="atrium",
                    interactive=False,
                )
        finally:
            sys.platform = original_platform

        self.assertFalse(result["ok"])
        self.assertEqual(result["checks"]["browserOpenRoute"]["blockReason"], "isolated browser profile requires Chrome, Edge, Brave, or Chromium")
        blocks = result["checks"]["browserOpenRuntimeBlocks"]
        self.assertEqual(blocks["api"], "isolated browser profile requires Chrome, Edge, Brave, or Chromium")
        self.assertEqual(blocks["chat"], blocks["api"])
        self.assertTrue(result["checks"]["browserOpen"]["skipped"])

    def test_windows_probe_shell_probe_verifies_marker_output(self) -> None:
        probe = _load_windows_probe_module()
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            calls.append(command)
            return {"returnCode": 0, "stdout": "ATRIUM_WINDOWS_SHELL_OK\n", "stderr": "", "command": command}

        result = probe._shell_probe(
            {"shellExecutable": "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"},
            fake_run,
        )

        self.assertTrue(result["containsExpected"])
        self.assertIn("-NoProfile", calls[0])
        self.assertIn("-Command", calls[0])

    def test_chatgpt_oauth_file_lock_uses_windows_msvcrt_when_fcntl_unavailable(self) -> None:
        class FakeMsvcrt:
            LK_LOCK = 1
            LK_UNLCK = 2

            def __init__(self) -> None:
                self.calls: list[tuple[int, int]] = []

            def locking(self, _fileno: int, mode: int, nbytes: int) -> None:
                self.calls.append((mode, nbytes))

        fake_msvcrt = FakeMsvcrt()
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "chatgpt-oauth.json"
            with (
                mock.patch.object(chatgpt_oauth, "fcntl", None),
                mock.patch.object(chatgpt_oauth, "msvcrt", fake_msvcrt),
            ):
                with chatgpt_oauth._credential_file_lock(store_path):
                    self.assertTrue(store_path.with_name("chatgpt-oauth.json.lock").exists())

        self.assertEqual(fake_msvcrt.calls, [(fake_msvcrt.LK_LOCK, 1), (fake_msvcrt.LK_UNLCK, 1)])

    def test_windows_probe_rejects_invalid_screenshot_file(self) -> None:
        probe = _load_windows_probe_module()

        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {"browserBridge": True, "desktopBridge": True}

        class FakeSettings:
            def __init__(self, data_dir: Path):
                self.data_dir = data_dir

        def fake_list_apps(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "running": [], "installed": []}

        def fake_screenshot(path: Path, _run_process: object) -> dict[str, object]:
            path.write_text("not a png", encoding="utf-8")
            return {"returnCode": 0, "path": str(path), "stdout": "", "stderr": ""}

        original_platform = sys.platform
        with tempfile.TemporaryDirectory() as tmp:
            try:
                sys.platform = "win32"
                with (
                    mock.patch.object(probe, "_routes", lambda: {tool: {"blockReason": None} for tool in probe.VISUAL_TOOLS}),
                    mock.patch.object(probe, "_runtime_blocks", lambda: {tool: {"api": None, "chat": None} for tool in probe.VISUAL_TOOLS}),
                    mock.patch.object(probe, "_shell_probe", lambda _status: {"returnCode": 0, "containsExpected": True}),
                    mock.patch.object(probe.HostBridge, "status", lambda _self: FakeStatus()),
                    mock.patch.object(probe, "get_settings", lambda: FakeSettings(Path(tmp))),
                    mock.patch.object(visual_bridge, "execute_windows_visual_selftest", _windows_helper_selftest_ok),
                    mock.patch.object(visual_bridge, "execute_windows_powershell_visual_preflight", _windows_preflight_ok),
                    mock.patch.object(visual_bridge, "list_browser_profiles", lambda: {"profiles": []}),
                    mock.patch.object(visual_bridge, "execute_list_apps", fake_list_apps),
                    mock.patch.object(visual_bridge, "execute_screenshot_capture", fake_screenshot),
                ):
                    result = probe._live(
                        screenshot=True,
                        notification=False,
                        browser_url=None,
                        browser_profile="atrium",
                        interactive=False,
                    )
            finally:
                sys.platform = original_platform

        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["screenshotFile"]["ok"])

    def test_windows_probe_fails_interactive_when_clipboard_roundtrip_does_not_match(self) -> None:
        probe = _load_windows_probe_module()

        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {"browserBridge": True, "desktopBridge": True}

        def fake_list_apps(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "running": [], "installed": []}

        def ok_result(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {"returnCode": 0, "stdout": "", "stderr": ""}

        def fake_open_app(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "processId": 42, "stdout": "", "stderr": ""}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(probe, "_routes", lambda: {tool: {"blockReason": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(probe, "_runtime_blocks", lambda: {tool: {"api": None, "chat": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(probe, "_shell_probe", lambda _status: {"returnCode": 0, "containsExpected": True}),
                mock.patch.object(probe.HostBridge, "status", lambda _self: FakeStatus()),
                mock.patch.object(visual_bridge, "execute_windows_visual_selftest", _windows_helper_selftest_ok),
                mock.patch.object(visual_bridge, "execute_windows_powershell_visual_preflight", _windows_preflight_ok),
                mock.patch.object(visual_bridge, "list_browser_profiles", lambda: {"profiles": []}),
                mock.patch.object(visual_bridge, "execute_list_apps", fake_list_apps),
                mock.patch.object(visual_bridge, "execute_open_app", fake_open_app),
                mock.patch.object(visual_bridge, "execute_activate_app", lambda _args, _run_process: _windows_foreground_activation_ok()),
                mock.patch.object(visual_bridge, "execute_type_text", lambda args, _run_process: _windows_type_text_ok(str(args.get("text") or ""))),
                mock.patch.object(visual_bridge, "execute_keypress", lambda args, _run_process: _windows_keypress_ok(args)),
                mock.patch.object(visual_bridge, "execute_paste_text", ok_result),
                mock.patch.object(
                    visual_bridge,
                    "execute_desktop_snapshot",
                    lambda _args, _run_process: {
                        "returnCode": 0,
                        "refCount": 1,
                        "snapshot": {"elements": [{"ref": "d1", "role": "Edit", "className": "Edit"}]},
                    },
                ),
                mock.patch.object(visual_bridge, "execute_desktop_act", lambda _args, _run_process: _windows_native_value_pattern_ok()),
                mock.patch.object(visual_bridge, "execute_scroll", ok_result),
                mock.patch.object(visual_bridge, "execute_quit_app", ok_result),
                mock.patch.object(probe, "_set_clipboard_text", lambda _value, _run_process=None: {"returnCode": 0, "verified": True}),
                mock.patch.object(probe, "_clipboard_round_trip", lambda _expected, _run_process=None: {"returnCode": 0, "containsExpected": False, "verified": False}),
            ):
                result = probe._live(
                    screenshot=False,
                    notification=False,
                    browser_url=None,
                    browser_profile="atrium",
                    interactive=True,
                )
        finally:
            sys.platform = original_platform

        self.assertFalse(result["ok"])
        self.assertIn("clipboard round-trip", result["checks"]["interactiveDesktop"]["error"])

    def test_windows_interactive_probe_requires_verified_activation_before_typing(self) -> None:
        probe = _load_windows_probe_module()

        def fake_open_app(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "processId": 42, "stdout": "", "stderr": ""}

        def timeout_activate(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": None, "timeout": True, "stdout": "", "stderr": "timed out"}

        with (
            mock.patch.object(visual_bridge, "execute_open_app", fake_open_app),
            mock.patch.object(visual_bridge, "execute_activate_app", timeout_activate),
            mock.patch.object(visual_bridge, "execute_type_text", side_effect=AssertionError("should not type without foreground verification")),
            mock.patch.object(visual_bridge, "execute_quit_app", lambda _args, _run_process: {"returnCode": 0, "quitVerified": True}),
            mock.patch.object(probe.time, "sleep", lambda _seconds: None),
        ):
            result = probe._interactive_desktop_probe()

        self.assertEqual(result["activateAttempts"], 10)
        self.assertEqual(result["error"], "desktop.activate_app failed; refusing to type into an unverified foreground app")
        self.assertIn("quitApp", result)

    def test_windows_interactive_probe_rejects_activation_without_foreground_metadata(self) -> None:
        probe = _load_windows_probe_module()

        def fake_open_app(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "processId": 42, "stdout": "", "stderr": ""}

        def wrong_foreground(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {
                "returnCode": 0,
                "processId": 42,
                "activeProcessId": 7,
                "foreground": False,
                "stdout": "",
                "stderr": "",
            }

        with (
            mock.patch.object(visual_bridge, "execute_open_app", fake_open_app),
            mock.patch.object(visual_bridge, "execute_activate_app", wrong_foreground),
            mock.patch.object(visual_bridge, "execute_type_text", side_effect=AssertionError("should not type into the wrong foreground app")),
            mock.patch.object(visual_bridge, "execute_quit_app", lambda _args, _run_process: {"returnCode": 0, "quitVerified": True}),
            mock.patch.object(probe.time, "sleep", lambda _seconds: None),
        ):
            result = probe._interactive_desktop_probe()

        self.assertEqual(result["activateAttempts"], 10)
        self.assertFalse(result["foregroundActivationVerified"])
        self.assertEqual(result["error"], "desktop.activate_app failed; refusing to type into an unverified foreground app")
        self.assertIn("quitApp", result)

    def test_windows_interactive_probe_requires_unicode_type_metrics(self) -> None:
        probe = _load_windows_probe_module()

        def fake_open_app(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "processId": 42, "stdout": "", "stderr": ""}

        with (
            mock.patch.object(visual_bridge, "execute_open_app", fake_open_app),
            mock.patch.object(visual_bridge, "execute_activate_app", lambda _args, _run_process: _windows_foreground_activation_ok()),
            mock.patch.object(visual_bridge, "execute_type_text", lambda _args, _run_process: {"returnCode": 0, "textBytes": 1, "textCharacters": 1, "textUnits": 1}),
            mock.patch.object(visual_bridge, "execute_keypress", side_effect=AssertionError("should not continue after Unicode typing proof fails")),
            mock.patch.object(visual_bridge, "execute_quit_app", lambda _args, _run_process: {"returnCode": 0, "quitVerified": True}),
            mock.patch.object(probe.time, "sleep", lambda _seconds: None),
        ):
            result = probe._interactive_desktop_probe()

        self.assertFalse(result["unicodeTypeVerified"])
        self.assertEqual(result["error"], "desktop.type did not prove Windows Unicode SendInput text metrics")
        self.assertIn("quitApp", result)

    def test_windows_interactive_probe_requires_control_a_key_mapping(self) -> None:
        probe = _load_windows_probe_module()

        def fake_open_app(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "processId": 42, "stdout": "", "stderr": ""}

        with (
            mock.patch.object(visual_bridge, "execute_open_app", fake_open_app),
            mock.patch.object(visual_bridge, "execute_activate_app", lambda _args, _run_process: _windows_foreground_activation_ok()),
            mock.patch.object(visual_bridge, "execute_type_text", lambda args, _run_process: _windows_type_text_ok(str(args.get("text") or ""))),
            mock.patch.object(visual_bridge, "execute_keypress", lambda _args, _run_process: {"returnCode": 0, "key": "a", "modifiers": []}),
            mock.patch.object(visual_bridge, "execute_paste_text", side_effect=AssertionError("should not paste after key mapping proof fails")),
            mock.patch.object(visual_bridge, "execute_quit_app", lambda _args, _run_process: {"returnCode": 0, "quitVerified": True}),
            mock.patch.object(probe.time, "sleep", lambda _seconds: None),
        ):
            result = probe._interactive_desktop_probe()

        self.assertTrue(result["unicodeTypeVerified"])
        self.assertFalse(result["selectAllKeypressVerified"])
        self.assertEqual(result["error"], "desktop.keypress did not prove Windows control+a mapping")
        self.assertIn("quitApp", result)

    def test_windows_interactive_probe_clears_clipboard_before_copyback(self) -> None:
        probe = _load_windows_probe_module()
        events: list[tuple[str, object]] = []

        def fake_open_app(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "processId": 42, "stdout": "", "stderr": ""}

        def ok_result(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {"returnCode": 0, "stdout": "", "stderr": ""}

        def fake_keypress(args: dict[str, object], _run_process: object) -> dict[str, object]:
            events.append(("key", tuple(args.get("keys") or [])))
            return _windows_keypress_ok(args)

        def fake_clear(value: str, _run_process: object = None) -> dict[str, object]:
            events.append(("clear", value))
            return {"returnCode": 0, "verified": True}

        def fake_round_trip(expected: str, _run_process: object = None) -> dict[str, object]:
            events.append(("roundTrip", expected))
            return {
                "returnCode": 0,
                "expected": expected,
                "containsExpected": True,
                "verified": True,
                "textLength": len(expected),
                "textBytes": len(expected.encode("utf-8")),
                "expectedLength": len(expected),
                "expectedBytes": len(expected.encode("utf-8")),
            }

        def fake_desktop_act(args: dict[str, object], _run_process: object) -> dict[str, object]:
            events.append(("desktopActRequireNative", args.get("requireNative")))
            events.append(("desktopActText", args.get("text")))
            return _windows_native_value_pattern_ok(str(args.get("text") or ""))

        with (
            mock.patch.object(visual_bridge, "execute_open_app", fake_open_app),
            mock.patch.object(visual_bridge, "execute_activate_app", lambda _args, _run_process: _windows_foreground_activation_ok()),
            mock.patch.object(visual_bridge, "execute_type_text", lambda args, _run_process: _windows_type_text_ok(str(args.get("text") or ""))),
            mock.patch.object(visual_bridge, "execute_keypress", fake_keypress),
            mock.patch.object(visual_bridge, "execute_paste_text", ok_result),
            mock.patch.object(
                visual_bridge,
                "execute_desktop_snapshot",
                lambda _args, _run_process: {
                    "returnCode": 0,
                    "refCount": 1,
                    "snapshot": {"elements": [{"ref": "d1", "role": "Edit", "className": "Edit"}]},
                },
            ),
            mock.patch.object(visual_bridge, "execute_desktop_act", fake_desktop_act),
            mock.patch.object(visual_bridge, "execute_scroll", ok_result),
            mock.patch.object(visual_bridge, "execute_quit_app", lambda _args, _run_process: {"returnCode": 0, "quitVerified": True}),
            mock.patch.object(probe, "_set_clipboard_text", fake_clear),
            mock.patch.object(probe, "_clipboard_round_trip", fake_round_trip),
            mock.patch.object(probe.time, "sleep", lambda _seconds: None),
        ):
            result = probe._interactive_desktop_probe()

        self.assertNotIn("error", result)
        self.assertTrue(result["clipboardClearBeforeCopy"]["verified"])
        self.assertEqual(
            events,
            [
                ("key", ("control", "a")),
                ("desktopActRequireNative", True),
                ("desktopActText", WINDOWS_INTERACTIVE_NATIVE_TEXT),
                ("key", ("control", "a")),
                ("clear", "ATRIUM clipboard cleared before copy-back verification"),
                ("key", ("control", "c")),
                ("roundTrip", WINDOWS_INTERACTIVE_NATIVE_TEXT),
            ],
        )

    def test_windows_interactive_probe_requires_desktop_act_ref_path(self) -> None:
        probe = _load_windows_probe_module()

        def fake_open_app(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "processId": 42, "stdout": "", "stderr": ""}

        def ok_result(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {"returnCode": 0, "stdout": "", "stderr": ""}

        with (
            mock.patch.object(visual_bridge, "execute_open_app", fake_open_app),
            mock.patch.object(visual_bridge, "execute_activate_app", lambda _args, _run_process: _windows_foreground_activation_ok()),
            mock.patch.object(visual_bridge, "execute_type_text", lambda args, _run_process: _windows_type_text_ok(str(args.get("text") or ""))),
            mock.patch.object(visual_bridge, "execute_keypress", lambda args, _run_process: _windows_keypress_ok(args)),
            mock.patch.object(visual_bridge, "execute_paste_text", ok_result),
            mock.patch.object(
                visual_bridge,
                "execute_desktop_snapshot",
                lambda _args, _run_process: {
                    "returnCode": 0,
                    "refCount": 1,
                    "snapshot": {"elements": [{"ref": "d1", "role": "Edit", "className": "Edit"}]},
                },
            ),
            mock.patch.object(visual_bridge, "execute_desktop_act", lambda _args, _run_process: {"returnCode": 1, "stderr": "uia failed"}),
            mock.patch.object(visual_bridge, "execute_scroll", side_effect=AssertionError("should not continue after desktop.act failure")),
            mock.patch.object(visual_bridge, "execute_quit_app", lambda _args, _run_process: {"returnCode": 0, "quitVerified": True}),
            mock.patch.object(probe.time, "sleep", lambda _seconds: None),
        ):
            result = probe._interactive_desktop_probe()

        self.assertEqual(result["error"], "desktop.act failed to set Notepad text through a snapshot ref")
        self.assertEqual(result["desktopActSetText"]["returnCode"], 1)
        self.assertIn("quitApp", result)

    def test_windows_interactive_probe_requires_valuepattern_native_desktop_act(self) -> None:
        probe = _load_windows_probe_module()

        def fake_open_app(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "processId": 42, "stdout": "", "stderr": ""}

        def ok_result(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {"returnCode": 0, "stdout": "", "stderr": ""}

        with (
            mock.patch.object(visual_bridge, "execute_open_app", fake_open_app),
            mock.patch.object(visual_bridge, "execute_activate_app", lambda _args, _run_process: _windows_foreground_activation_ok()),
            mock.patch.object(visual_bridge, "execute_type_text", lambda args, _run_process: _windows_type_text_ok(str(args.get("text") or ""))),
            mock.patch.object(visual_bridge, "execute_keypress", lambda args, _run_process: _windows_keypress_ok(args)),
            mock.patch.object(visual_bridge, "execute_paste_text", ok_result),
            mock.patch.object(
                visual_bridge,
                "execute_desktop_snapshot",
                lambda _args, _run_process: {
                    "returnCode": 0,
                    "refCount": 1,
                    "snapshot": {"elements": [{"ref": "d1", "role": "Edit", "className": "Edit"}]},
                },
            ),
            mock.patch.object(
                visual_bridge,
                "execute_desktop_act",
                lambda _args, _run_process: {
                    "returnCode": 0,
                    "usedNativeAction": True,
                    "nativeAttempt": {
                        "returnCode": 0,
                        "method": "uia",
                        "inputMethod": "uia",
                        "nativeAction": "SendInput",
                        "ok": True,
                    },
                },
            ),
            mock.patch.object(visual_bridge, "execute_scroll", side_effect=AssertionError("should not continue after non-ValuePattern native proof")),
            mock.patch.object(visual_bridge, "execute_quit_app", lambda _args, _run_process: {"returnCode": 0, "quitVerified": True}),
            mock.patch.object(probe.time, "sleep", lambda _seconds: None),
        ):
            result = probe._interactive_desktop_probe()

        self.assertEqual(result["error"], "desktop.act did not prove Notepad ValuePattern native UIAutomation action")
        self.assertFalse(result["nativeActionVerified"])
        self.assertIn("quitApp", result)

    def test_windows_interactive_probe_requires_valuepattern_text_snapshot(self) -> None:
        probe = _load_windows_probe_module()

        def fake_open_app(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "processId": 42, "stdout": "", "stderr": ""}

        def ok_result(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {"returnCode": 0, "stdout": "", "stderr": ""}

        def fake_desktop_act(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            result = _windows_native_value_pattern_ok("ATRIUM paste probe ไทย")
            result["after"]["snapshot"]["elements"][0]["value"] = "ATRIUM paste probe ไทย"
            return result

        with (
            mock.patch.object(visual_bridge, "execute_open_app", fake_open_app),
            mock.patch.object(visual_bridge, "execute_activate_app", lambda _args, _run_process: _windows_foreground_activation_ok()),
            mock.patch.object(visual_bridge, "execute_type_text", lambda args, _run_process: _windows_type_text_ok(str(args.get("text") or ""))),
            mock.patch.object(visual_bridge, "execute_keypress", lambda args, _run_process: _windows_keypress_ok(args)),
            mock.patch.object(visual_bridge, "execute_paste_text", ok_result),
            mock.patch.object(
                visual_bridge,
                "execute_desktop_snapshot",
                lambda _args, _run_process: {
                    "returnCode": 0,
                    "refCount": 1,
                    "snapshot": {"elements": [{"ref": "d1", "role": "Edit", "className": "Edit"}]},
                },
            ),
            mock.patch.object(visual_bridge, "execute_desktop_act", fake_desktop_act),
            mock.patch.object(visual_bridge, "execute_scroll", side_effect=AssertionError("should not continue after missing native text proof")),
            mock.patch.object(visual_bridge, "execute_quit_app", lambda _args, _run_process: {"returnCode": 0, "quitVerified": True}),
            mock.patch.object(probe.time, "sleep", lambda _seconds: None),
        ):
            result = probe._interactive_desktop_probe()

        self.assertEqual(result["error"], "desktop.snapshot did not confirm Notepad text after ValuePattern native UIAutomation action")
        self.assertTrue(result["nativeActionVerified"])
        self.assertFalse(result["nativeValueVerified"])
        self.assertIn("quitApp", result)

    def test_windows_probe_full_option_expands_live_parity_checks(self) -> None:
        probe = _load_windows_probe_module()
        captured: dict[str, object] = {}

        def fake_live(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {"ok": True, "mode": "live", "checks": {}}

        with (
            mock.patch.object(sys, "argv", ["windows_host_bridge_probe.py", "--full"]),
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
                "browser_url": "https://example.com",
                "browser_profile": "atrium",
                "interactive": True,
            },
        )

    def test_windows_probe_full_option_keeps_explicit_browser_url(self) -> None:
        probe = _load_windows_probe_module()
        captured: dict[str, object] = {}

        def fake_live(**kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {"ok": True, "mode": "live", "checks": {}}

        with (
            mock.patch.object(sys, "argv", ["windows_host_bridge_probe.py", "--full", "--browser-url", "https://local.test"]),
            mock.patch.object(probe, "_live", fake_live),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            exit_code = probe.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured["browser_url"], "https://local.test")

    def test_windows_probe_output_writes_stamped_json_artifact(self) -> None:
        probe = _load_windows_probe_module()
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "nested" / "windows.json"
            with (
                mock.patch.object(
                    sys,
                    "argv",
                    ["windows_host_bridge_probe.py", "--simulate", "--parity-run-id", "test-run-1", "--output", str(output_path)],
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

    def test_windows_probe_refuses_source_fingerprint_mismatch_before_probe(self) -> None:
        probe = _load_windows_probe_module()
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "windows.json"
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
                        "windows_host_bridge_probe.py",
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

    def test_windows_probe_fails_when_visual_helper_selftest_fails(self) -> None:
        probe = _load_windows_probe_module()

        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {"browserBridge": True, "desktopBridge": True}

        def fake_list_apps(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "running": [], "installed": []}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(probe, "_routes", lambda: {tool: {"blockReason": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(probe, "_runtime_blocks", lambda: {tool: {"api": None, "chat": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(probe, "_shell_probe", lambda _status: {"returnCode": 0, "containsExpected": True}),
                mock.patch.object(probe.HostBridge, "status", lambda _self: FakeStatus()),
                mock.patch.object(visual_bridge, "execute_windows_visual_selftest", lambda _run_process: {"returnCode": 1, "ok": False, "stderr": "helper failed"}),
                mock.patch.object(visual_bridge, "execute_windows_powershell_visual_preflight", _windows_preflight_ok),
                mock.patch.object(visual_bridge, "list_browser_profiles", lambda: {"profiles": []}),
                mock.patch.object(visual_bridge, "execute_list_apps", fake_list_apps),
            ):
                result = probe._live(
                    screenshot=False,
                    notification=False,
                    browser_url=None,
                    browser_profile="atrium",
                    interactive=False,
                )
        finally:
            sys.platform = original_platform

        self.assertFalse(result["ok"])
        self.assertEqual(result["checks"]["helperSelftest"]["stderr"], "helper failed")

    def test_windows_probe_requires_dpi_and_virtual_screen_metadata(self) -> None:
        probe = _load_windows_probe_module()

        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {"browserBridge": True, "desktopBridge": True}

        def fake_list_apps(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "running": [], "installed": []}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(probe, "_routes", lambda: {tool: {"blockReason": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(probe, "_runtime_blocks", lambda: {tool: {"api": None, "chat": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(probe, "_shell_probe", lambda _status: {"returnCode": 0, "containsExpected": True}),
                mock.patch.object(probe.HostBridge, "status", lambda _self: FakeStatus()),
                mock.patch.object(visual_bridge, "execute_windows_visual_selftest", lambda _run_process: {"returnCode": 0, "ok": True}),
                mock.patch.object(visual_bridge, "execute_windows_powershell_visual_preflight", lambda _run_process: {"returnCode": 0, "ok": True, "checks": {}}),
                mock.patch.object(visual_bridge, "list_browser_profiles", lambda: {"profiles": []}),
                mock.patch.object(visual_bridge, "execute_list_apps", fake_list_apps),
            ):
                result = probe._live(
                    screenshot=False,
                    notification=False,
                    browser_url=None,
                    browser_profile="atrium",
                    interactive=False,
                )
        finally:
            sys.platform = original_platform

        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["windowsVisualProof"]["dpiAwareness"])
        self.assertFalse(result["checks"]["windowsVisualProof"]["virtualScreen"])

    def test_windows_live_probe_skips_interactive_when_runtime_blocks_desktop_writes(self) -> None:
        probe = _load_windows_probe_module()

        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {"browserBridge": True, "desktopBridge": True}

        def fake_list_apps(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "running": [], "installed": []}

        def fail_interactive(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("interactive probe should be skipped when runtime readiness blocks desktop writes")

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(probe, "_routes", lambda: {tool: {"blockReason": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(
                    probe,
                    "_runtime_blocks",
                    lambda: {
                        **{tool: {"api": None, "chat": None} for tool in probe.VISUAL_TOOLS},
                        "desktop.act": {"api": "interactive desktop session unavailable", "chat": None},
                        "desktop.activate_app": {"api": "interactive desktop session unavailable", "chat": None},
                    },
                ),
                mock.patch.object(probe, "_shell_probe", lambda _status: {"returnCode": 0, "containsExpected": True}),
                mock.patch.object(probe.HostBridge, "status", lambda _self: FakeStatus()),
                mock.patch.object(visual_bridge, "execute_windows_visual_selftest", _windows_helper_selftest_ok),
                mock.patch.object(visual_bridge, "execute_windows_powershell_visual_preflight", _windows_preflight_ok),
                mock.patch.object(visual_bridge, "list_browser_profiles", lambda: {"profiles": []}),
                mock.patch.object(visual_bridge, "execute_list_apps", fake_list_apps),
                mock.patch.object(visual_bridge, "execute_desktop_snapshot", lambda _args, _run_process: {"returnCode": 0, "refCount": 1}),
                mock.patch.object(probe, "_interactive_desktop_probe", fail_interactive),
            ):
                result = probe._live(
                    screenshot=False,
                    notification=False,
                    browser_url=None,
                    browser_profile="atrium",
                    interactive=True,
                )
        finally:
            sys.platform = original_platform

        self.assertFalse(result["ok"])
        self.assertTrue(result["checks"]["interactiveSkipped"]["skipped"])
        self.assertIn("desktop.act", result["checks"]["interactiveSkipped"]["blockedTools"])
        self.assertTrue(result["checks"]["interactiveDesktop"]["skipped"])

    def test_windows_probe_fails_notification_without_show_metadata(self) -> None:
        probe = _load_windows_probe_module()

        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {"browserBridge": True, "desktopBridge": True}

        def fake_list_apps(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "running": [], "installed": []}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(probe, "_routes", lambda: {tool: {"blockReason": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(probe, "_runtime_blocks", lambda: {tool: {"api": None, "chat": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(probe, "_shell_probe", lambda _status: {"returnCode": 0, "containsExpected": True}),
                mock.patch.object(probe.HostBridge, "status", lambda _self: FakeStatus()),
                mock.patch.object(visual_bridge, "execute_windows_visual_selftest", _windows_helper_selftest_ok),
                mock.patch.object(visual_bridge, "execute_windows_powershell_visual_preflight", _windows_preflight_ok),
                mock.patch.object(visual_bridge, "list_browser_profiles", lambda: {"profiles": []}),
                mock.patch.object(visual_bridge, "execute_list_apps", fake_list_apps),
                mock.patch.object(visual_bridge, "execute_notification", lambda _args, _run_process: {"returnCode": 0, "shown": None, "disposed": None}),
            ):
                result = probe._live(
                    screenshot=False,
                    notification=True,
                    browser_url=None,
                    browser_profile="atrium",
                    interactive=False,
                )
        finally:
            sys.platform = original_platform

        self.assertFalse(result["ok"])
        self.assertIsNone(result["checks"]["notification"]["shown"])

    def test_windows_probe_fails_when_powershell_visual_preflight_fails(self) -> None:
        probe = _load_windows_probe_module()

        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {"browserBridge": True, "desktopBridge": True}

        def fake_list_apps(_args: dict[str, object], _run_process: object) -> dict[str, object]:
            return {"returnCode": 0, "running": [], "installed": []}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(probe, "_routes", lambda: {tool: {"blockReason": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(probe, "_runtime_blocks", lambda: {tool: {"api": None, "chat": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(probe, "_shell_probe", lambda _status: {"returnCode": 0, "containsExpected": True}),
                mock.patch.object(probe.HostBridge, "status", lambda _self: FakeStatus()),
                mock.patch.object(visual_bridge, "execute_windows_visual_selftest", _windows_helper_selftest_ok),
                mock.patch.object(visual_bridge, "execute_windows_powershell_visual_preflight", lambda _run_process: {"returnCode": 0, "ok": False, "checks": {"drawing": False}}),
                mock.patch.object(visual_bridge, "list_browser_profiles", lambda: {"profiles": []}),
                mock.patch.object(visual_bridge, "execute_list_apps", fake_list_apps),
            ):
                result = probe._live(
                    screenshot=False,
                    notification=False,
                    browser_url=None,
                    browser_profile="atrium",
                    interactive=False,
                )
        finally:
            sys.platform = original_platform

        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["powershellPreflight"]["ok"])
        self.assertFalse(result["checks"]["powershellPreflight"]["checks"]["drawing"])

    def test_windows_probe_fails_when_app_discovery_times_out(self) -> None:
        probe = _load_windows_probe_module()

        class FakeStatus:
            def to_dict(self) -> dict[str, object]:
                return {"browserBridge": True, "desktopBridge": True}

        original_platform = sys.platform
        try:
            sys.platform = "win32"
            with (
                mock.patch.object(probe, "_routes", lambda: {tool: {"blockReason": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(probe, "_runtime_blocks", lambda: {tool: {"api": None, "chat": None} for tool in probe.VISUAL_TOOLS}),
                mock.patch.object(probe, "_shell_probe", lambda _status: {"returnCode": 0, "containsExpected": True}),
                mock.patch.object(probe.HostBridge, "status", lambda _self: FakeStatus()),
                mock.patch.object(visual_bridge, "execute_windows_visual_selftest", _windows_helper_selftest_ok),
                mock.patch.object(visual_bridge, "execute_windows_powershell_visual_preflight", _windows_preflight_ok),
                mock.patch.object(visual_bridge, "list_browser_profiles", lambda: {"profiles": []}),
                mock.patch.object(visual_bridge, "execute_list_apps", lambda _args, _run_process: {"returnCode": None, "timeout": True}),
            ):
                result = probe._live(
                    screenshot=False,
                    notification=False,
                    browser_url=None,
                    browser_profile="atrium",
                    interactive=False,
                )
        finally:
            sys.platform = original_platform

        self.assertFalse(result["ok"])
        self.assertTrue(result["checks"]["apps"]["timeout"])

    def test_windows_probe_fails_interactive_on_command_timeout(self) -> None:
        probe = _load_windows_probe_module()

        checks = {
            "activateApp": {"returnCode": 0},
            "type": {"returnCode": None, "timeout": True},
        }

        self.assertFalse(probe._commands_ok(checks))

    def test_windows_probe_fails_interactive_on_ok_false_payload(self) -> None:
        probe = _load_windows_probe_module()

        checks = {
            "activateApp": {"returnCode": 0},
            "click": {"returnCode": 0, "ok": False, "stderr": "", "helper": {"error": "SendInput failed"}},
        }

        self.assertFalse(probe._commands_ok(checks))
        self.assertFalse(probe._commands_ok({"quitApp": {"returnCode": 0, "quitVerified": False}}))

    def test_windows_probe_json_parser_accepts_host_text_before_json(self) -> None:
        probe = _load_windows_probe_module()

        rows = probe._json_rows_from_stdout({
            "returnCode": 0,
            "stdout": "VERBOSE: launch output\n[{\"processId\":77,\"name\":\"notepad\"}]\n",
        })

        self.assertEqual(rows, [{"processId": 77, "name": "notepad"}])

    def test_windows_probe_run_process_returns_structured_timeout(self) -> None:
        probe = _load_windows_probe_module()

        def timeout_run(*_args: object, **_kwargs: object) -> object:
            import subprocess

            raise subprocess.TimeoutExpired(["slow"], timeout=1, output="out ไทย".encode(), stderr="err ไทย".encode())

        with mock.patch("subprocess.run", side_effect=timeout_run):
            result = probe._run_process(["slow"], timeout=1)

        self.assertIsNone(result["returnCode"])
        self.assertTrue(result["timeout"])
        self.assertEqual(result["stdout"], "out ไทย")
        self.assertEqual(result["stderr"], "err ไทย")

    def test_windows_probe_run_process_decodes_utf8_stdout(self) -> None:
        probe = _load_windows_probe_module()

        class FakeCompleted:
            returncode = 0
            stdout = "ATRIUM ไทย".encode()
            stderr = ""

        with mock.patch("subprocess.run", return_value=FakeCompleted()):
            result = probe._run_process(["ok"], timeout=1)

        self.assertEqual(result["returnCode"], 0)
        self.assertEqual(result["stdout"], "ATRIUM ไทย")

    def test_windows_process_decoders_handle_legacy_thai_codepage_output(self) -> None:
        probe = _load_windows_probe_module()
        encoded = "ATRIUM ภาษาไทย".encode("cp874")
        original_platform = sys.platform
        try:
            sys.platform = "win32"
            self.assertEqual(probe._decode_process_output(encoded), "ATRIUM ภาษาไทย")
            self.assertEqual(main_module._decode_process_output(encoded), "ATRIUM ภาษาไทย")
            self.assertEqual(chat_tools._decode_process_output(encoded), "ATRIUM ภาษาไทย")
        finally:
            sys.platform = original_platform

    def test_windows_shell_exec_risk_classifies_windows_commands(self) -> None:
        def api_risk(command: list[str]) -> str:
            return main_module._tool_risk_class({
                "id": "run_windows_risk",
                "tool": "shell.exec",
                "departmentId": "exec",
                "args": {"command": command},
            })

        def owner_risk(command: list[str]) -> str:
            return chat_tools._owner_tool_risk({
                "id": "run_windows_risk",
                "tool": "shell.exec",
                "departmentId": "exec",
                "args": {"command": command},
            })

        cases = [
            (
                ["C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe", "-NoProfile", "-Command", "Remove-Item -Recurse C:\\tmp\\x"],
                "destructive",
            ),
            (["cmd.exe", "/d", "/s", "/c", "del /q C:\\tmp\\x.txt"], "destructive"),
            (["git.exe", "reset", "--hard"], "destructive"),
            (["C:\\Program Files\\Git\\cmd\\git.exe", "push", "origin", "main"], "external_send"),
            (["curl.exe", "https://example.com"], "network"),
            (["pwsh.exe", "-NoProfile", "-Command", "Invoke-WebRequest https://example.com"], "network"),
            (["runas.exe", "/user:Administrator", "cmd.exe"], "privileged"),
            (["powershell.exe", "-Command", "Start-Process cmd.exe -Verb RunAs"], "privileged"),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(api_risk(command), expected)
                self.assertEqual(owner_risk(command), expected)

    def test_windows_probe_includes_shell_exec_command_risk_checks(self) -> None:
        probe = _load_windows_probe_module()

        result = probe._windows_command_risk_checks()

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["cases"]), 8)
        self.assertTrue(all(row["api"] == row["expected"] for row in result["cases"]))
        self.assertTrue(all(row["chat"] == row["expected"] for row in result["cases"]))

    def test_api_and_owner_run_process_return_structured_timeouts(self) -> None:
        def timeout_run(*_args: object, **_kwargs: object) -> object:
            raise main_module.subprocess.TimeoutExpired(
                ["slow"],
                timeout=1,
                output="out ไทย".encode(),
                stderr="err ไทย".encode(),
            )

        with mock.patch.object(main_module.subprocess, "run", side_effect=timeout_run):
            api_result = main_module._run_process(["slow"], timeout=1)
        with mock.patch.object(chat_tools.subprocess, "run", side_effect=timeout_run):
            owner_result = chat_tools._owner_run_process(["slow"], timeout=1)

        for result in (api_result, owner_result):
            self.assertIsNone(result["returnCode"])
            self.assertTrue(result["timeout"])
            self.assertEqual(result["stdout"], "out ไทย")
            self.assertEqual(result["stderr"], "err ไทย")


if __name__ == "__main__":
    unittest.main()
