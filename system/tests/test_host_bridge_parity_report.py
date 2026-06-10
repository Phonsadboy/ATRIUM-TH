import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path

from app.host_bridge_proof import host_bridge_source_provenance


REPO_ROOT = Path(__file__).resolve().parents[2]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _load_report_module():
    spec = importlib.util.spec_from_file_location(
        "host_bridge_parity_report",
        REPO_ROOT / "ops" / "host_bridge_parity_report.py",
    )
    if spec is None or spec.loader is None:
        raise AssertionError("host_bridge_parity_report.py could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_summary_module():
    spec = importlib.util.spec_from_file_location(
        "host_bridge_artifact_summary",
        REPO_ROOT / "ops" / "host_bridge_artifact_summary.py",
    )
    if spec is None or spec.loader is None:
        raise AssertionError("host_bridge_artifact_summary.py could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_source_summary_module():
    spec = importlib.util.spec_from_file_location(
        "host_bridge_source_summary",
        REPO_ROOT / "ops" / "host_bridge_source_summary.py",
    )
    if spec is None or spec.loader is None:
        raise AssertionError("host_bridge_source_summary.py could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source(fingerprint: str | None = None, git_head: str | None = None) -> dict:
    current = host_bridge_source_provenance(REPO_ROOT)
    return {
        "repoRoot": str(REPO_ROOT),
        "gitHead": git_head or current.get("gitHead") or "b" * 40,
        "gitDirty": False,
        "gitStatusShort": [],
        "sourceFingerprint": fingerprint or current["sourceFingerprint"],
        "sourceManifestSha256": fingerprint or current["sourceManifestSha256"],
        "sourceFileCount": len(current.get("files") or {}),
        "files": current.get("files") or {},
    }


def _host(platform: str = "darwin", hostname: str = "atrium-macos") -> dict:
    return {
        "schemaVersion": 1,
        "platform": platform,
        "system": "Darwin" if platform == "darwin" else "Windows",
        "release": "test",
        "machine": "arm64" if platform == "darwin" else "AMD64",
        "hostname": hostname,
        "hostFingerprint": ("a" if platform == "darwin" else "b") * 64,
    }


MACOS_APPLESCRIPT_CLIPBOARD_TEXT = "ATRIUM macOS AppleScript probe ไทย"
MACOS_TEXTEDIT_EXPECTED_TEXT = "ATRIUM macOS TextEdit probe ไทย"
MACOS_CALCULATOR_EXPECTED_VALUE = "1"
WINDOWS_INTERACTIVE_TYPE_TEXT = "ATRIUM Windows HostBridge probe ไทย"
WINDOWS_INTERACTIVE_PASTE_TEXT = "ATRIUM paste probe ไทย"
WINDOWS_INTERACTIVE_NATIVE_TEXT = "ATRIUM Windows ValuePattern probe ไทย"


def _utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def _macos_artifact(**overrides):
    artifact = {
        "schemaVersion": 1,
        "generatedAt": _now_ms(),
        "parityRunId": "parity-run-1",
        "source": _source(),
        "host": _host("darwin", "atrium-macos"),
        "ok": True,
        "mode": "live",
        "status": {
            "platform": "darwin",
            "browserBridge": True,
            "desktopBridge": True,
            "desktopAutomationReady": True,
            "macosVisualPreflightChecks": {"foregroundSession": True},
        },
        "routes": {"desktop.act": {"blockReason": None}},
        "runtimeBlocks": {"desktop.act": {"api": None, "chat": None}},
        "checks": {
            "apps": {"returnCode": 0, "runningMethod": "native_nsworkspace_apps"},
            "screenshotFile": {"ok": True},
            "notification": {"returnCode": 0},
            "browserOpen": {"returnCode": 0, "profileKind": "isolated", "isOwnProfile": True},
            "browserRef": {
                "browserSnapshot": {"returnCode": 0, "refCount": 1, "backend": "playwright", "profileKind": "isolated", "isOwnProfile": True},
                "browserActClick": {"returnCode": 0, "backend": "playwright", "profileKind": "isolated", "isOwnProfile": True},
                "containsExpected": True,
            },
            "appleScriptClipboard": {
                "returnCode": 0,
                "method": "osascript",
                "expected": MACOS_APPLESCRIPT_CLIPBOARD_TEXT,
                "verified": True,
                "textLength": len(MACOS_APPLESCRIPT_CLIPBOARD_TEXT),
                "expectedLength": len(MACOS_APPLESCRIPT_CLIPBOARD_TEXT),
                "expectedBytes": len(MACOS_APPLESCRIPT_CLIPBOARD_TEXT.encode("utf-8")),
            },
            "foregroundSnapshot": {"returnCode": 0, "snapshotBackend": "native_ax"},
            "interactiveCalculator": {
                "nativeActionVerified": True,
                "displayValueVerified": True,
                "nativeActionMetadataVerified": True,
                "desktopSnapshot": {
                    "returnCode": 0,
                    "snapshot": {
                        "elements": [
                            {
                                "role": "AXButton",
                                "name": "1",
                                "axActions": ["AXPress"],
                                "nativeSupportedActions": ["click"],
                            }
                        ]
                    },
                },
                "desktopActClick": {
                    "returnCode": 0,
                    "usedNativeAction": True,
                    "nativeAttempt": {
                        "returnCode": 0,
                        "method": "accessibility_ax_helper",
                        "inputMethod": "accessibility",
                        "nativeStatus": "OK",
                        "nativeAction": "AXPress",
                    },
                },
                "desktopSnapshotAfter": {
                    "returnCode": 0,
                    "snapshot": {
                        "elements": [
                            {
                                "role": "AXStaticText",
                                "value": MACOS_CALCULATOR_EXPECTED_VALUE,
                            }
                        ]
                    },
                },
            },
            "interactiveTextEdit": {
                "nativeActionVerified": True,
                "textValueVerified": True,
                "nativeActionMetadataVerified": True,
                "nativeScrollMetadataVerified": True,
                "nativeScrollVerified": True,
                "desktopSnapshot": {
                    "returnCode": 0,
                    "snapshot": {
                        "elements": [
                            {
                                "role": "AXScrollArea",
                                "name": "Document",
                                "axActions": ["AXScrollDownByPage", "AXScrollUpByPage"],
                                "nativeSupportedActions": ["scroll"],
                            },
                            {
                                "role": "AXTextArea",
                                "name": "Text Area",
                                "settableAttributes": ["AXValue"],
                                "nativeSupportedActions": ["paste", "type"],
                            }
                        ]
                    },
                },
                "desktopActScroll": {
                    "returnCode": 0,
                    "usedNativeAction": True,
                    "nativeAttempt": {
                        "returnCode": 0,
                        "method": "accessibility_ax_helper",
                        "inputMethod": "accessibility",
                        "nativeStatus": "OK",
                        "nativeAction": "AXScrollDownByPage",
                    },
                },
                "desktopActSetText": {
                    "returnCode": 0,
                    "usedNativeAction": True,
                    "nativeAttempt": {
                        "returnCode": 0,
                        "method": "accessibility_ax_helper",
                        "inputMethod": "accessibility",
                        "nativeStatus": "OK",
                        "nativeAction": "setValue",
                    },
                },
                "desktopSnapshotAfter": {
                    "returnCode": 0,
                    "snapshot": {
                        "elements": [
                            {
                                "role": "AXTextArea",
                                "value": MACOS_TEXTEDIT_EXPECTED_TEXT,
                            }
                        ]
                    },
                },
            },
        },
    }
    artifact.update(overrides)
    return artifact


def _windows_artifact(**overrides):
    artifact = {
        "schemaVersion": 1,
        "generatedAt": _now_ms(),
        "parityRunId": "parity-run-1",
        "source": _source(),
        "host": _host("win32", "atrium-windows"),
        "ok": True,
        "mode": "live",
        "status": {
            "platform": "win32",
            "browserBridge": True,
            "desktopBridge": True,
            "desktopAutomationReady": True,
            "interactiveSession": True,
            "interactiveSessionName": "Console",
            "interactiveSessionId": 1,
            "windowsVisualPreflightOk": True,
        },
        "routes": {"desktop.act": {"blockReason": None}},
        "runtimeBlocks": {"desktop.act": {"api": None, "chat": None}},
        "checks": {
            "apps": {"returnCode": 0},
            "screenshotFile": {"ok": True},
            "notification": {"returnCode": 0},
            "browserOpen": {"returnCode": 0, "profileKind": "isolated", "isOwnProfile": True},
            "browserRef": {
                "browserSnapshot": {"returnCode": 0, "refCount": 1, "backend": "playwright", "profileKind": "isolated", "isOwnProfile": True},
                "browserActClick": {"returnCode": 0, "backend": "playwright", "profileKind": "isolated", "isOwnProfile": True},
                "containsExpected": True,
            },
            "helperSelftest": {
                "ok": True,
                "dpiAwareness": "per_monitor_v2",
                "screenWidth": 1920,
                "screenHeight": 1080,
                "virtualLeft": 0,
                "virtualTop": 0,
                "virtualWidth": 1920,
                "virtualHeight": 1080,
            },
            "powershellPreflight": {
                "ok": True,
                "checks": {"dpiAwareness": True, "virtualScreen": True},
                "virtualScreen": {"left": 0, "top": 0, "width": 1920, "height": 1080},
            },
            "mcpExternalWriteReady": {
                "ok": True,
                "verified": True,
                "returnCode": 0,
                "stage": "mcp_external_write",
                "proofFacet": "mcpExternalWriteReady",
                "probe": True,
                "ready": True,
                "gatewayHealthOk": True,
                "id": "mcp",
                "status": "configured",
                "readReady": True,
                "writeReady": True,
                "localFallback": False,
                "externalWriteRequires": [],
                "probeCommand": ".\\atrium.ps1 tools mcp-probe --json",
                "setupCommand": ".\\atrium.ps1 tools mcp-gateway --json",
            },
            "windowsLiveProofRunner": {
                "ok": True,
                "verified": True,
                "runner": "ops/windows_host_bridge_live_proof.ps1",
                "command": ".\\atrium.ps1 automation windows-live-proof",
                "failureStages": [
                    "source_validate",
                    "mcp_external_write",
                    "windows_full_probe",
                    "artifact_validate",
                ],
                "readinessGates": {
                    "source": "source_validate",
                    "mcpExternalWrite": "mcp_external_write",
                    "browserDesktopSmoke": "windows_full_probe",
                    "artifactValidation": "artifact_validate",
                },
                "repoRoot": "C:\\atrium",
                "outputPath": "C:\\Temp\\atrium_host_bridge_windows_live.json",
                "parityRunId": "parity-run-1",
                "sourceFingerprint": _source()["sourceFingerprint"],
                "sourceManifestSha256": _source()["sourceManifestSha256"],
                "sourceFileCount": len(_source()["files"]),
                "maxArtifactAgeHours": 24.0,
            },
            "interactiveDesktop": {
                "foregroundActivationVerified": True,
                "activateApp": {
                    "returnCode": 0,
                    "processId": 42,
                    "activeProcessId": 42,
                    "foreground": True,
                },
                "unicodeTypeVerified": True,
                "type": {
                    "returnCode": 0,
                    "textBytes": len(WINDOWS_INTERACTIVE_TYPE_TEXT.encode("utf-8")),
                    "textCharacters": len(WINDOWS_INTERACTIVE_TYPE_TEXT),
                    "textUnits": _utf16_units(WINDOWS_INTERACTIVE_TYPE_TEXT),
                },
                "selectAllKeypressVerified": True,
                "keypress": {"returnCode": 0, "key": "a", "modifiers": ["control"]},
                "nativeValueVerified": True,
                "desktopActSetText": {
                    "returnCode": 0,
                    "usedNativeAction": True,
                    "nativeAttempt": {
                        "returnCode": 0,
                        "method": "uia",
                        "inputMethod": "uia",
                        "nativeAction": "ValuePattern",
                        "ok": True,
                    },
                },
                "desktopSnapshotAfter": {
                    "returnCode": 0,
                    "snapshot": {
                        "elements": [
                            {
                                "role": "Edit",
                                "value": WINDOWS_INTERACTIVE_NATIVE_TEXT,
                            }
                        ]
                    },
                },
                "clipboardRoundTrip": {
                    "returnCode": 0,
                    "expected": WINDOWS_INTERACTIVE_NATIVE_TEXT,
                    "containsExpected": True,
                    "verified": True,
                    "textLength": len(WINDOWS_INTERACTIVE_NATIVE_TEXT),
                    "textBytes": len(WINDOWS_INTERACTIVE_NATIVE_TEXT.encode("utf-8")),
                    "expectedLength": len(WINDOWS_INTERACTIVE_NATIVE_TEXT),
                    "expectedBytes": len(WINDOWS_INTERACTIVE_NATIVE_TEXT.encode("utf-8")),
                },
            },
        },
    }
    artifact.update(overrides)
    source = artifact.get("source") if isinstance(artifact.get("source"), dict) else {}
    checks = artifact.get("checks") if isinstance(artifact.get("checks"), dict) else {}
    runner = checks.get("windowsLiveProofRunner") if isinstance(checks.get("windowsLiveProofRunner"), dict) else None
    if runner is not None:
        runner["parityRunId"] = artifact.get("parityRunId")
        runner["sourceFingerprint"] = source.get("sourceFingerprint")
        runner["sourceManifestSha256"] = source.get("sourceManifestSha256")
        runner["sourceFileCount"] = source.get("sourceFileCount")
    return artifact


class HostBridgeParityReportTest(unittest.TestCase):
    def _write_artifact(self, directory: Path, name: str, payload: dict) -> Path:
        path = directory / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_parity_report_accepts_complete_full_live_artifacts(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact())

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        self.assertTrue(report["ok"])
        self.assertEqual(report["schemaVersion"], 1)
        self.assertEqual(report["proofSchemaVersion"], 1)
        self.assertIsInstance(report["generatedAt"], int)
        self.assertRegex(report["proofId"], r"^[0-9a-f]{64}$")
        self.assertEqual(report["summary"], "full live HostBridge parity proof is complete")
        self.assertEqual(report["findings"], [])
        self.assertTrue(report["results"]["macos"]["ok"])
        self.assertTrue(report["results"]["windows"]["ok"])
        self.assertEqual(report["results"]["macos"]["schemaVersion"], 1)
        self.assertEqual(report["results"]["macos"]["proofSchemaVersion"], 1)
        self.assertIsInstance(report["results"]["macos"]["generatedAt"], int)
        self.assertEqual(report["results"]["windows"]["parityRunId"], "parity-run-1")
        self.assertEqual(report["results"]["macos"]["hostPlatform"], "darwin")
        self.assertEqual(report["results"]["windows"]["hostPlatform"], "win32")
        self.assertRegex(report["results"]["windows"]["hostFingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(report["results"]["macos"]["sourceFingerprint"], _source()["sourceFingerprint"])
        self.assertEqual(report["results"]["macos"]["sourceManifestSha256"], _source()["sourceManifestSha256"])
        self.assertEqual(report["results"]["macos"]["sourceFileCount"], len(_source()["files"]))
        self.assertEqual(report["currentSource"]["sourceManifestSha256"], _source()["sourceManifestSha256"])
        self.assertEqual(report["currentSource"]["sourceFileCount"], len(_source()["files"]))
        self.assertEqual(report["results"]["windows"]["gitHead"], _source()["gitHead"])
        self.assertEqual(report["currentSource"]["sourceFingerprint"], _source()["sourceFingerprint"])
        self.assertGreater(report["results"]["macos"]["artifactBytes"], 0)
        self.assertRegex(report["results"]["macos"]["artifactSha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(report["results"]["macos"]["proofs"]["browserActVerified"])
        self.assertTrue(report["results"]["macos"]["proofs"]["browserSnapshotIsolatedPlaywright"])
        self.assertTrue(report["results"]["macos"]["proofs"]["appleScriptClipboard"])
        self.assertTrue(report["results"]["macos"]["proofs"]["macosNativeActionMetadata"])
        self.assertTrue(report["results"]["macos"]["proofs"]["textEditNativeScroll"])
        self.assertTrue(report["results"]["windows"]["proofs"]["windowsForegroundActivation"])
        self.assertTrue(report["results"]["windows"]["proofs"]["windowsInteractiveSessionIdentity"])
        self.assertTrue(report["results"]["windows"]["proofs"]["windowsUnicodeTyping"])
        self.assertTrue(report["results"]["windows"]["proofs"]["windowsKeyboardShortcut"])
        self.assertTrue(report["results"]["windows"]["proofs"]["mcpExternalWriteReady"])
        self.assertTrue(report["results"]["windows"]["proofs"]["windowsLiveProofRunner"])
        self.assertTrue(report["results"]["windows"]["proofs"]["notepadNativeAct"])

    def test_parity_report_rejects_windows_artifact_without_mcp_external_write_proof(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            windows = _windows_artifact()
            windows["checks"].pop("mcpExternalWriteReady")
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("windows: MCP external-write readiness proof is missing", findings)
        self.assertFalse(report["results"]["windows"]["proofs"]["mcpExternalWriteReady"])

    def test_parity_report_rejects_windows_artifact_without_mcp_stage_attestation(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            windows = _windows_artifact()
            windows["checks"]["mcpExternalWriteReady"].pop("stage")
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("windows: MCP external-write readiness proof is missing", findings)
        self.assertFalse(report["results"]["windows"]["proofs"]["mcpExternalWriteReady"])

    def test_parity_report_rejects_windows_artifact_without_mcp_probe_attestation(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            windows = _windows_artifact()
            windows["checks"]["mcpExternalWriteReady"].pop("probe")
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("windows: MCP external-write readiness proof is missing", findings)
        self.assertFalse(report["results"]["windows"]["proofs"]["mcpExternalWriteReady"])

    def test_parity_report_rejects_windows_artifact_without_live_runner_attestation(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            windows = _windows_artifact()
            windows["checks"].pop("windowsLiveProofRunner")
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("windows: Windows live proof runner attestation is missing", findings)
        self.assertFalse(report["results"]["windows"]["proofs"]["windowsLiveProofRunner"])

    def test_parity_report_rejects_windows_artifact_without_runner_age_contract(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            windows = _windows_artifact()
            windows["checks"]["windowsLiveProofRunner"]["maxArtifactAgeHours"] = 48.0
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("windows: Windows live proof runner attestation is missing", findings)
        self.assertFalse(report["results"]["windows"]["proofs"]["windowsLiveProofRunner"])

    def test_parity_report_rejects_windows_artifact_without_runner_stage_contract(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            windows = _windows_artifact()
            windows["checks"]["windowsLiveProofRunner"].pop("failureStages")
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("windows: Windows live proof runner attestation is missing", findings)
        self.assertFalse(report["results"]["windows"]["proofs"]["windowsLiveProofRunner"])

    def test_parity_report_writes_persisted_output_for_connector_proof(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact())
            output_path = directory / "host-bridge-parity-report.json"

            report = report_module.evaluate_artifacts(macos_path, windows_path, generated_at_ms=1234)
            same_inputs_report = report_module.evaluate_artifacts(macos_path, windows_path, generated_at_ms=5678)
            report_module.write_report(report, output_path)
            persisted = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertTrue(persisted["ok"])
        self.assertEqual(persisted["schemaVersion"], 1)
        self.assertEqual(persisted["generatedAt"], 1234)
        self.assertEqual(persisted["proofId"], same_inputs_report["proofId"])
        self.assertTrue(persisted["results"]["macos"]["ok"])
        self.assertTrue(persisted["results"]["windows"]["ok"])
        self.assertGreater(persisted["results"]["macos"]["artifactBytes"], 0)
        self.assertEqual(persisted["results"]["windows"]["sourceManifestSha256"], _source()["sourceManifestSha256"])
        self.assertEqual(persisted["results"]["windows"]["sourceFileCount"], len(_source()["files"]))
        self.assertRegex(persisted["results"]["windows"]["artifactSha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(persisted["results"]["macos"]["proofs"]["browserSnapshot"])
        self.assertTrue(persisted["results"]["windows"]["proofs"]["browserActIsolatedPlaywright"])
        self.assertTrue(persisted["results"]["windows"]["proofs"]["clipboardRoundTrip"])

    def test_parity_report_rejects_windows_live_failure_artifact_without_noisy_facet_cascade(self) -> None:
        report_module = _load_report_module()
        source = _source()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(
                directory,
                "windows-failed.json",
                {
                    "schemaVersion": 1,
                    "ok": False,
                    "mode": "windows_live_proof_failed",
                    "generatedAt": _now_ms(),
                    "parityRunId": "parity-run-1",
                    "source": {
                        "sourceFingerprint": source["sourceFingerprint"],
                        "sourceManifestSha256": source["sourceManifestSha256"],
                        "sourceFileCount": len(source["files"]),
                    },
                    "error": "Windows HostBridge live proof requires an interactive desktop session, not Services.",
                    "preflight": {
                        "os": {
                            "isWindows": True,
                            "sessionName": "Services",
                            "isElevated": False,
                        },
                    },
                },
            )

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        self.assertFalse(report["ok"])
        windows = report["results"]["windows"]
        self.assertTrue(windows["present"])
        self.assertFalse(windows["ok"])
        self.assertEqual(windows["mode"], "windows_live_proof_failed")
        self.assertEqual(windows["sourceFingerprint"], source["sourceFingerprint"])
        self.assertEqual(windows["sourceManifestSha256"], source["sourceManifestSha256"])
        self.assertEqual(windows["sourceFileCount"], len(source["files"]))
        self.assertIn("interactive desktop session", windows["failureError"])
        self.assertEqual(windows["failurePreflight"]["sessionName"], "Services")
        findings = "\n".join(windows["findings"])
        self.assertIn("Windows live proof runner failed before full proof artifact was produced", findings)
        self.assertNotIn("required proof facet", findings)

    def test_parity_report_surfaces_windows_live_failure_stage_and_partial_artifact(self) -> None:
        report_module = _load_report_module()
        source = _source()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(
                directory,
                "windows-failed-partial.json",
                {
                    "schemaVersion": 1,
                    "ok": False,
                    "mode": "windows_live_proof_failed",
                    "generatedAt": _now_ms(),
                    "parityRunId": "parity-run-1",
                    "source": {
                        "sourceFingerprint": source["sourceFingerprint"],
                        "sourceManifestSha256": source["sourceManifestSha256"],
                        "sourceFileCount": len(source["files"]),
                    },
                    "failedStage": "live_proof_pipeline",
                    "nextSteps": {
                        "failedStage": "live_proof_pipeline",
                        "commands": [
                            ".\\atrium.ps1 doctor --json",
                            ".\\atrium.ps1 report --bundle",
                        ],
                    },
                    "partialArtifact": {
                        "preserved": True,
                        "mode": "live",
                        "checkNames": ["browserRef", "interactiveNotepad"],
                        "checkCount": 2,
                    },
                    "preflight": {
                        "os": {
                            "isWindows": True,
                            "sessionName": "Console",
                            "isElevated": True,
                        },
                    },
                    "error": "Validate Windows HostBridge artifact failed",
                },
            )

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        windows = report["results"]["windows"]
        self.assertFalse(windows["ok"])
        self.assertEqual(windows["failureStage"], "live_proof_pipeline")
        self.assertEqual(windows["failurePartialArtifact"]["checkCount"], 2)
        self.assertEqual(windows["failureNextSteps"]["failedStage"], "live_proof_pipeline")
        self.assertIn(".\\atrium.ps1 report --bundle", windows["failureNextSteps"]["commands"])
        self.assertEqual(windows["failurePreflight"]["sessionName"], "Console")

    def test_parity_report_proof_id_changes_when_proof_facet_changes(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact())
            report_ok = report_module.evaluate_artifacts(macos_path, windows_path)

            macos = _macos_artifact()
            macos["checks"]["browserRef"]["containsExpected"] = False
            macos_bad_path = self._write_artifact(directory, "macos-bad.json", macos)
            report_bad = report_module.evaluate_artifacts(macos_bad_path, windows_path)

        self.assertNotEqual(report_ok["proofId"], report_bad["proofId"])
        self.assertFalse(report_bad["ok"])
        self.assertFalse(report_bad["results"]["macos"]["proofs"]["browserActVerified"])
        self.assertIn("macos: browser.act DOM-ref proof did not verify the post-click DOM state", "\n".join(report_bad["findings"]))

    def test_parity_report_rejects_incomplete_source_file_provenance(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos = _macos_artifact()
            windows = _windows_artifact()
            macos["source"]["files"] = {}
            windows["source"]["files"] = dict(windows["source"]["files"])
            windows["source"]["files"].pop("atrium.ps1", None)
            macos_path = self._write_artifact(directory, "macos.json", macos)
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        self.assertFalse(report["ok"])
        findings = "\n".join(report["findings"])
        self.assertIn("source file provenance is incomplete", findings)
        self.assertIn("atrium.ps1", findings)

    def test_parity_report_rejects_missing_or_mismatched_source_manifest_digest(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos = _macos_artifact()
            windows = _windows_artifact()
            macos["source"].pop("sourceManifestSha256", None)
            windows["source"]["sourceManifestSha256"] = "f" * 64
            macos_path = self._write_artifact(directory, "macos.json", macos)
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        self.assertFalse(report["ok"])
        findings = "\n".join(report["findings"])
        self.assertIn("sourceManifestSha256 is missing or invalid", findings)
        self.assertIn("sourceManifestSha256 must match sourceFingerprint", findings)

    def test_parity_report_rejects_non_lowercase_hex_provenance_hashes(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos = _macos_artifact()
            windows = _windows_artifact()
            macos["host"]["hostFingerprint"] = "A" * 64
            windows["source"]["gitHead"] = "G" * 40
            macos_path = self._write_artifact(directory, "macos.json", macos)
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        self.assertFalse(report["ok"])
        findings = "\n".join(report["findings"])
        self.assertIn("macos: artifact hostFingerprint is missing or invalid", findings)
        self.assertIn("windows: artifact gitHead is missing or invalid", findings)

    def test_parity_report_rejects_mismatched_declared_source_file_count(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos = _macos_artifact()
            windows = _windows_artifact()
            macos["source"]["sourceFileCount"] = len(macos["source"]["files"]) - 1
            windows["source"]["sourceFileCount"] = len(windows["source"]["files"]) + 1
            macos_path = self._write_artifact(directory, "macos.json", macos)
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        self.assertFalse(report["ok"])
        findings = "\n".join(report["findings"])
        self.assertIn("artifact sourceFileCount must match source file provenance", findings)
        self.assertEqual(report["results"]["windows"]["sourceFileCount"], len(windows["source"]["files"]) + 1)
        self.assertEqual(report["results"]["windows"]["sourceFileProvenanceCount"], len(windows["source"]["files"]))

    def test_parity_report_rejects_browser_ref_without_isolated_playwright_profile(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos = _macos_artifact()
            macos["checks"]["browserOpen"] = {"returnCode": 0, "profileKind": "user", "isOwnProfile": False}
            macos["checks"]["browserRef"]["browserSnapshot"]["profileKind"] = "user"
            macos["checks"]["browserRef"]["browserSnapshot"]["isOwnProfile"] = False
            macos["checks"]["browserRef"]["browserActClick"]["backend"] = "selenium"
            macos_path = self._write_artifact(directory, "macos.json", macos)
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact())

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertFalse(report["results"]["macos"]["proofs"]["browserOpenIsolatedProfile"])
        self.assertFalse(report["results"]["macos"]["proofs"]["browserSnapshotIsolatedPlaywright"])
        self.assertFalse(report["results"]["macos"]["proofs"]["browserActIsolatedPlaywright"])
        self.assertIn("macos: browser.open proof did not use ATRIUM's isolated browser profile", findings)
        self.assertIn("macos: browser.snapshot DOM-ref proof did not use Playwright with ATRIUM's isolated browser profile", findings)
        self.assertIn("macos: browser.act DOM-ref click proof did not use Playwright with ATRIUM's isolated browser profile", findings)

    def test_parity_report_rejects_macos_artifact_without_applescript_clipboard_proof(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos = _macos_artifact()
            macos["checks"]["appleScriptClipboard"]["verified"] = False
            macos["checks"]["appleScriptClipboard"]["method"] = "python"
            macos_path = self._write_artifact(directory, "macos.json", macos)
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact())

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("macos: AppleScript clipboard exact round-trip proof is missing", findings)
        self.assertFalse(report["results"]["macos"]["proofs"]["appleScriptClipboard"])

    def test_parity_report_rejects_macos_artifact_without_native_action_metadata(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos = _macos_artifact()
            macos["checks"]["interactiveCalculator"]["desktopSnapshot"]["snapshot"]["elements"][0]["axActions"] = []
            macos["checks"]["interactiveTextEdit"]["desktopSnapshot"]["snapshot"]["elements"][0]["settableAttributes"] = []
            macos_path = self._write_artifact(directory, "macos.json", macos)
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact())

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("macos: native AX action metadata proof is missing", findings)
        self.assertFalse(report["results"]["macos"]["proofs"]["macosNativeActionMetadata"])

    def test_parity_report_rejects_macos_calculator_artifact_without_axpress_metadata(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos = _macos_artifact()
            macos["checks"]["interactiveCalculator"]["desktopActClick"]["nativeAttempt"]["nativeAction"] = "click"
            macos_path = self._write_artifact(directory, "macos.json", macos)
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact())

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("macos: Calculator desktop.act native AXPress display proof is missing", findings)
        self.assertFalse(report["results"]["macos"]["proofs"]["calculatorNativeAct"])

    def test_parity_report_rejects_macos_calculator_artifact_without_display_snapshot_proof(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos = _macos_artifact()
            macos["checks"]["interactiveCalculator"]["displayValueVerified"] = False
            macos["checks"]["interactiveCalculator"]["desktopSnapshotAfter"]["snapshot"]["elements"][0]["value"] = "0"
            macos_path = self._write_artifact(directory, "macos.json", macos)
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact())

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("macos: Calculator desktop.act native AXPress display proof is missing", findings)
        self.assertFalse(report["results"]["macos"]["proofs"]["calculatorNativeAct"])

    def test_parity_report_rejects_macos_textedit_artifact_without_setvalue_snapshot_proof(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos = _macos_artifact()
            macos["checks"]["interactiveTextEdit"]["textValueVerified"] = False
            macos["checks"]["interactiveTextEdit"]["desktopSnapshotAfter"]["snapshot"]["elements"][0]["value"] = "wrong"
            macos_path = self._write_artifact(directory, "macos.json", macos)
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact())

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("macos: TextEdit desktop.act native setValue proof is missing", findings)
        self.assertFalse(report["results"]["macos"]["proofs"]["textEditNativeAct"])

    def test_parity_report_rejects_simulated_artifacts(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact(mode="simulate"))
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact())

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        self.assertFalse(report["ok"])
        self.assertIn("macos: artifact mode must be live", "\n".join(report["findings"]))
        self.assertFalse(report["results"]["macos"]["ok"])

    def test_parity_report_rejects_unstamped_artifacts(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos = _macos_artifact()
            macos.pop("schemaVersion")
            macos.pop("generatedAt")
            macos_path = self._write_artifact(directory, "macos.json", macos)
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact())

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("macos: artifact schemaVersion must be 1", findings)
        self.assertIn("macos: artifact generatedAt is missing or invalid", findings)

    def test_parity_report_rejects_missing_source_provenance(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos = _macos_artifact()
            macos.pop("source")
            macos_path = self._write_artifact(directory, "macos.json", macos)
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact())

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        self.assertFalse(report["ok"])
        self.assertIn("macos: artifact source provenance is missing", "\n".join(report["findings"]))

    def test_parity_report_rejects_missing_host_identity(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos = _macos_artifact()
            macos.pop("host")
            windows = _windows_artifact()
            windows["host"]["platform"] = "darwin"
            macos_path = self._write_artifact(directory, "macos.json", macos)
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("macos: artifact host identity is missing", findings)
        self.assertIn("windows: artifact host platform must be 'win32'", findings)

    def test_parity_report_rejects_source_fingerprint_mismatch(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact(source=_source("a" * 64)))
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact(source=_source("c" * 64)))

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("source provenance mismatch", findings)
        self.assertFalse(report["results"]["macos"]["ok"])
        self.assertFalse(report["results"]["windows"]["ok"])

    def test_parity_report_rejects_missing_or_mismatched_parity_run_id(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos = _macos_artifact()
            macos.pop("parityRunId")
            macos_path = self._write_artifact(directory, "macos.json", macos)
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact(parityRunId="other-run"))

            missing_report = report_module.evaluate_artifacts(macos_path, windows_path)

            macos_path = self._write_artifact(directory, "macos-2.json", _macos_artifact(parityRunId="mac-run"))
            windows_path = self._write_artifact(directory, "windows-2.json", _windows_artifact(parityRunId="win-run"))
            mismatch_report = report_module.evaluate_artifacts(macos_path, windows_path)

        self.assertFalse(missing_report["ok"])
        self.assertIn("macos: artifact parityRunId is missing or invalid", "\n".join(missing_report["findings"]))
        self.assertFalse(missing_report["results"]["macos"]["ok"])
        self.assertFalse(mismatch_report["ok"])
        self.assertIn("parity run mismatch", "\n".join(mismatch_report["findings"]))
        self.assertFalse(mismatch_report["results"]["windows"]["ok"])

    def test_parity_report_rejects_windows_artifact_without_dpi_or_virtual_screen_proof(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            windows = _windows_artifact()
            windows["checks"]["helperSelftest"] = {"ok": True}
            windows["checks"]["powershellPreflight"] = {"ok": True}
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("windows: DPI awareness proof is missing", findings)
        self.assertIn("windows: virtual screen bounds proof is missing", findings)
        self.assertFalse(report["results"]["windows"]["proofs"]["windowsDpiAwareness"])
        self.assertFalse(report["results"]["windows"]["proofs"]["windowsVirtualScreen"])

    def test_parity_report_rejects_windows_artifact_without_foreground_activation_proof(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            windows = _windows_artifact()
            windows["checks"]["interactiveDesktop"]["foregroundActivationVerified"] = False
            windows["checks"]["interactiveDesktop"]["activateApp"]["foreground"] = False
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("windows: Notepad foreground activation proof is missing", findings)
        self.assertFalse(report["results"]["windows"]["proofs"]["windowsForegroundActivation"])

    def test_parity_report_rejects_windows_artifact_with_forged_foreground_activation_flag(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            windows = _windows_artifact()
            windows["checks"]["interactiveDesktop"]["foregroundActivationVerified"] = True
            windows["checks"]["interactiveDesktop"]["activateApp"]["activeProcessId"] = 99
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("windows: Notepad foreground activation proof is missing", findings)
        self.assertFalse(report["results"]["windows"]["proofs"]["windowsForegroundActivation"])

    def test_parity_report_rejects_windows_artifact_without_interactive_session_identity(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            windows = _windows_artifact()
            windows["status"]["interactiveSessionName"] = "Services"
            windows["status"]["interactiveSessionId"] = 0
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("windows: interactive session identity proof is missing", findings)
        self.assertFalse(report["results"]["windows"]["proofs"]["windowsInteractiveSessionIdentity"])

    def test_parity_report_rejects_windows_artifact_with_flag_but_no_session_name(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            windows = _windows_artifact()
            windows["status"].pop("interactiveSessionName")
            windows["status"]["interactiveSessionId"] = 1
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("windows: interactive session identity proof is missing", findings)
        self.assertFalse(report["results"]["windows"]["proofs"]["windowsInteractiveSessionIdentity"])

    def test_parity_report_rejects_windows_artifact_without_keyboard_or_unicode_proof(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            windows = _windows_artifact()
            windows["checks"]["interactiveDesktop"]["unicodeTypeVerified"] = False
            windows["checks"]["interactiveDesktop"]["type"]["textUnits"] = 0
            windows["checks"]["interactiveDesktop"]["selectAllKeypressVerified"] = False
            windows["checks"]["interactiveDesktop"]["keypress"]["modifiers"] = []
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("windows: Unicode typing proof is missing", findings)
        self.assertIn("windows: keyboard shortcut mapping proof is missing", findings)
        self.assertFalse(report["results"]["windows"]["proofs"]["windowsUnicodeTyping"])
        self.assertFalse(report["results"]["windows"]["proofs"]["windowsKeyboardShortcut"])

    def test_parity_report_rejects_windows_artifact_with_contains_only_clipboard_proof(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            windows = _windows_artifact()
            windows["checks"]["interactiveDesktop"]["clipboardRoundTrip"]["verified"] = False
            windows["checks"]["interactiveDesktop"]["clipboardRoundTrip"]["textLength"] += 1
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("windows: Notepad clipboard exact round-trip proof is missing", findings)
        self.assertFalse(report["results"]["windows"]["proofs"]["clipboardRoundTrip"])

    def test_parity_report_rejects_windows_artifact_without_valuepattern_native_proof(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            windows = _windows_artifact()
            windows["checks"]["interactiveDesktop"]["desktopActSetText"]["nativeAttempt"]["nativeAction"] = "SendInput"
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("windows: Notepad desktop.act native ValuePattern text proof is missing", findings)
        self.assertFalse(report["results"]["windows"]["proofs"]["notepadNativeAct"])

    def test_parity_report_rejects_windows_artifact_without_distinct_valuepattern_text_proof(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            windows = _windows_artifact()
            windows["checks"]["interactiveDesktop"]["nativeValueVerified"] = False
            windows["checks"]["interactiveDesktop"]["desktopSnapshotAfter"]["snapshot"]["elements"][0]["value"] = WINDOWS_INTERACTIVE_PASTE_TEXT
            windows["checks"]["interactiveDesktop"]["clipboardRoundTrip"].update({
                "expected": WINDOWS_INTERACTIVE_PASTE_TEXT,
                "textLength": len(WINDOWS_INTERACTIVE_PASTE_TEXT),
                "textBytes": len(WINDOWS_INTERACTIVE_PASTE_TEXT.encode("utf-8")),
                "expectedLength": len(WINDOWS_INTERACTIVE_PASTE_TEXT),
                "expectedBytes": len(WINDOWS_INTERACTIVE_PASTE_TEXT.encode("utf-8")),
            })
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = self._write_artifact(directory, "windows.json", windows)

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("windows: Notepad desktop.act native ValuePattern text proof is missing", findings)
        self.assertIn("windows: Notepad clipboard exact round-trip proof is missing", findings)
        self.assertFalse(report["results"]["windows"]["proofs"]["notepadNativeAct"])

    def test_parity_report_rejects_artifacts_from_old_current_source(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact(source=_source("a" * 64, "b" * 40)))
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact(source=_source("a" * 64, "b" * 40)))

            report = report_module.evaluate_artifacts(
                macos_path,
                windows_path,
                current_source={
                    "sourceFingerprint": "c" * 64,
                    "sourceManifestSha256": "e" * 64,
                    "sourceFileCount": len(_source()["files"]) + 1,
                    "gitHead": "d" * 40,
                    "gitDirty": True,
                },
            )

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("macos: artifact sourceFingerprint does not match current HostBridge source", findings)
        self.assertIn("macos: artifact sourceManifestSha256 does not match current HostBridge source", findings)
        self.assertIn("windows: artifact sourceFileCount does not match current HostBridge source", findings)
        self.assertIn("windows: artifact gitHead does not match current checkout", findings)
        self.assertEqual(report["currentSource"]["sourceFingerprint"], "c" * 64)
        self.assertEqual(report["currentSource"]["sourceManifestSha256"], "e" * 64)
        self.assertEqual(report["currentSource"]["sourceFileCount"], len(_source()["files"]) + 1)
        self.assertFalse(report["results"]["macos"]["ok"])
        self.assertFalse(report["results"]["windows"]["ok"])

    def test_parity_report_rejects_failure_artifact_source_manifest_and_count_mismatch(self) -> None:
        report_module = _load_report_module()
        source = _source()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact(source=source))
            windows_path = self._write_artifact(
                directory,
                "windows-failed.json",
                {
                    "schemaVersion": 1,
                    "ok": False,
                    "mode": "windows_live_proof_failed",
                    "generatedAt": _now_ms(),
                    "parityRunId": "parity-run-1",
                    "sourceFingerprint": source["sourceFingerprint"],
                    "sourceManifestSha256": "f" * 64,
                    "sourceFileCount": len(source["files"]) - 1,
                    "error": "Windows HostBridge live proof requires an interactive desktop session, not Services.",
                    "preflight": {"os": {"isWindows": True, "sessionName": "Services", "isElevated": False}},
                },
            )

            report = report_module.evaluate_artifacts(
                macos_path,
                windows_path,
                current_source=source,
            )

        self.assertFalse(report["ok"])
        findings = "\n".join(report["findings"])
        self.assertIn("source manifest mismatch", findings)
        self.assertIn("source file count mismatch", findings)
        self.assertIn("windows: artifact sourceManifestSha256 does not match current HostBridge source", findings)
        self.assertIn("windows: artifact sourceFileCount does not match current HostBridge source", findings)

    def test_parity_report_flags_stale_windows_live_failure_artifact(self) -> None:
        report_module = _load_report_module()
        source = _source()
        now = 1_800_000_000_000
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact(generatedAt=now))
            windows_path = self._write_artifact(
                directory,
                "windows-failed.json",
                {
                    "schemaVersion": 1,
                    "ok": False,
                    "mode": "windows_live_proof_failed",
                    "generatedAt": now - (25 * 60 * 60 * 1000),
                    "parityRunId": "parity-run-1",
                    "sourceFingerprint": source["sourceFingerprint"],
                    "sourceManifestSha256": source["sourceManifestSha256"],
                    "sourceFileCount": len(source["files"]),
                    "error": "Windows HostBridge live proof requires an interactive desktop session, not Services.",
                    "preflight": {"os": {"isWindows": True, "sessionName": "Services", "isElevated": False}},
                },
            )

            report = report_module.evaluate_artifacts(
                macos_path,
                windows_path,
                current_source=source,
                now_ms=now,
            )

        self.assertFalse(report["ok"])
        self.assertIn("windows: artifact is stale", "\n".join(report["findings"]))
        self.assertIn("windows: artifact is stale", "\n".join(report["results"]["windows"]["findings"]))

    def test_parity_report_can_skip_current_source_check_for_offline_audits(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact(source=_source("a" * 64, "b" * 40)))
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact(source=_source("a" * 64, "b" * 40)))

            report = report_module.evaluate_artifacts(
                macos_path,
                windows_path,
                current_source={"sourceFingerprint": "c" * 64, "gitHead": "d" * 40, "gitDirty": True},
                enforce_current_source=False,
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["currentSource"]["sourceFingerprint"], "c" * 64)
        self.assertNotIn("current HostBridge source", "\n".join(report["findings"]))

    def test_parity_report_rejects_stale_artifacts(self) -> None:
        report_module = _load_report_module()
        now = 1_800_000_000_000
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            stale = now - (25 * 60 * 60 * 1000)
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact(generatedAt=stale))
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact(generatedAt=now))

            report = report_module.evaluate_artifacts(macos_path, windows_path, now_ms=now)

        self.assertFalse(report["ok"])
        self.assertIn("macos: artifact is stale", "\n".join(report["findings"]))

    def test_parity_report_rejects_missing_windows_live_artifact(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos_path = self._write_artifact(directory, "macos.json", _macos_artifact())
            windows_path = directory / "windows.json"

            report = report_module.evaluate_artifacts(
                macos_path,
                windows_path,
                artifact_source_paths={"windows": r"C:\Temp\atrium-windows-hostbridge-live.json"},
            )

        self.assertFalse(report["ok"])
        self.assertIn("windows: artifact file is missing", report["findings"])
        self.assertEqual(report["results"]["windows"]["artifactSourcePath"], r"C:\Temp\atrium-windows-hostbridge-live.json")
        self.assertIn("Copy the Windows full-probe artifact", report["results"]["windows"]["copyHint"])
        self.assertIn(str(windows_path), report["results"]["windows"]["copyHint"])

    def test_artifact_summary_accepts_matching_windows_live_artifact(self) -> None:
        summary_module = _load_summary_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            artifact = _windows_artifact()
            artifact_path = self._write_artifact(directory, "windows.json", artifact)

            summary = summary_module.summarize_artifact(
                artifact_path,
                label="windows",
                expect_parity_run_id="parity-run-1",
                expect_source_fingerprint=artifact["source"]["sourceFingerprint"],
                expect_source_manifest_sha256=artifact["source"]["sourceManifestSha256"],
                expect_source_file_count=len(artifact["source"]["files"]),
                max_artifact_age_hours=24.0,
            )

        self.assertTrue(summary["ok"])
        self.assertEqual(summary["label"], "windows")
        self.assertEqual(summary["hostPlatform"], "win32")
        self.assertEqual(summary["statusPlatform"], "win32")
        self.assertEqual(summary["sourceManifestSha256"], artifact["source"]["sourceManifestSha256"])
        self.assertEqual(summary["sourceFileCount"], len(_source()["files"]))
        self.assertTrue(summary["proofs"]["notepadNativeAct"])
        self.assertTrue(summary["proofs"]["browserActIsolatedPlaywright"])
        self.assertIn("notepadNativeAct", summary["requiredProofFacets"])
        self.assertEqual(summary["missingProofFacets"], [])
        self.assertGreater(summary["proofFacetCount"], 0)
        self.assertEqual(summary["missingProofFacetCount"], 0)
        self.assertEqual(summary["findings"], [])

    def test_artifact_summary_rejects_source_mismatch(self) -> None:
        summary_module = _load_summary_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            artifact = _windows_artifact()
            artifact_path = self._write_artifact(directory, "windows.json", artifact)

            summary = summary_module.summarize_artifact(
                artifact_path,
                label="windows",
                expect_parity_run_id="parity-run-1",
                expect_source_fingerprint="f" * 64,
                expect_source_manifest_sha256=artifact["source"]["sourceManifestSha256"],
                expect_source_file_count=len(artifact["source"]["files"]),
                max_artifact_age_hours=24.0,
            )

        self.assertFalse(summary["ok"])
        self.assertIn("sourceFingerprint mismatch", " ".join(summary["findings"]))

    def test_artifact_summary_rejects_uppercase_expected_source_fingerprint(self) -> None:
        summary_module = _load_summary_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            artifact = _windows_artifact()
            artifact_path = self._write_artifact(directory, "windows.json", artifact)

            summary = summary_module.summarize_artifact(
                artifact_path,
                label="windows",
                expect_parity_run_id="parity-run-1",
                expect_source_fingerprint=artifact["source"]["sourceFingerprint"].upper(),
                expect_source_manifest_sha256=artifact["source"]["sourceManifestSha256"].upper(),
                expect_source_file_count=len(artifact["source"]["files"]),
                max_artifact_age_hours=24.0,
            )

        self.assertFalse(summary["ok"])
        findings = " ".join(summary["findings"])
        self.assertIn("sourceFingerprint mismatch", findings)
        self.assertIn("sourceManifestSha256 mismatch", findings)

    def test_artifact_summary_rejects_incomplete_source_file_provenance(self) -> None:
        summary_module = _load_summary_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            artifact = _windows_artifact()
            artifact["source"]["files"] = dict(artifact["source"]["files"])
            artifact["source"]["files"].pop("atrium.ps1", None)
            artifact_path = self._write_artifact(directory, "windows.json", artifact)

            summary = summary_module.summarize_artifact(
                artifact_path,
                label="windows",
                expect_parity_run_id="parity-run-1",
                expect_source_fingerprint=artifact["source"]["sourceFingerprint"],
                expect_source_manifest_sha256=artifact["source"]["sourceManifestSha256"],
                expect_source_file_count=len(_source()["files"]),
                max_artifact_age_hours=24.0,
            )

        self.assertFalse(summary["ok"])
        self.assertIn("source file provenance is incomplete", " ".join(summary["findings"]))
        self.assertIn("atrium.ps1", " ".join(summary["findings"]))

    def test_artifact_summary_rejects_declared_source_file_count_mismatch(self) -> None:
        summary_module = _load_summary_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            artifact = _windows_artifact()
            artifact["source"]["sourceFileCount"] = len(artifact["source"]["files"]) + 1
            artifact_path = self._write_artifact(directory, "windows.json", artifact)

            summary = summary_module.summarize_artifact(
                artifact_path,
                label="windows",
                expect_parity_run_id="parity-run-1",
                expect_source_fingerprint=artifact["source"]["sourceFingerprint"],
                expect_source_manifest_sha256=artifact["source"]["sourceManifestSha256"],
                expect_source_file_count=len(artifact["source"]["files"]),
                max_artifact_age_hours=24.0,
            )

        self.assertFalse(summary["ok"])
        self.assertEqual(summary["sourceFileCount"], len(artifact["source"]["files"]) + 1)
        self.assertEqual(summary["sourceFileProvenanceCount"], len(artifact["source"]["files"]))
        self.assertIn("sourceFileCount must match source file provenance", " ".join(summary["findings"]))

    def test_artifact_summary_rejects_mismatched_source_manifest_digest(self) -> None:
        summary_module = _load_summary_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            artifact = _windows_artifact()
            artifact["source"]["sourceManifestSha256"] = "f" * 64
            artifact_path = self._write_artifact(directory, "windows.json", artifact)

            summary = summary_module.summarize_artifact(
                artifact_path,
                label="windows",
                expect_parity_run_id="parity-run-1",
                expect_source_fingerprint=artifact["source"]["sourceFingerprint"],
                expect_source_manifest_sha256=artifact["source"]["sourceFingerprint"],
                expect_source_file_count=len(artifact["source"]["files"]),
                max_artifact_age_hours=24.0,
            )

        self.assertFalse(summary["ok"])
        self.assertIn("sourceManifestSha256 must match sourceFingerprint", " ".join(summary["findings"]))

    def test_artifact_summary_rejects_non_lowercase_hex_host_fingerprint_and_git_head(self) -> None:
        summary_module = _load_summary_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            artifact = _windows_artifact()
            artifact["host"]["hostFingerprint"] = "B" * 64
            artifact["source"]["gitHead"] = "G" * 40
            artifact_path = self._write_artifact(directory, "windows.json", artifact)

            summary = summary_module.summarize_artifact(
                artifact_path,
                label="windows",
                expect_parity_run_id="parity-run-1",
                expect_source_fingerprint=artifact["source"]["sourceFingerprint"],
                expect_source_manifest_sha256=artifact["source"]["sourceManifestSha256"],
                expect_source_file_count=len(artifact["source"]["files"]),
                max_artifact_age_hours=24.0,
            )

        self.assertFalse(summary["ok"])
        findings = " ".join(summary["findings"])
        self.assertIn("hostFingerprint is missing or invalid", findings)
        self.assertIn("gitHead is missing or invalid", findings)

    def test_artifact_summary_rejects_missing_required_windows_proof_facet(self) -> None:
        summary_module = _load_summary_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            artifact = _windows_artifact()
            artifact["checks"]["interactiveDesktop"]["desktopActSetText"]["nativeAttempt"]["nativeAction"] = "SendInput"
            artifact_path = self._write_artifact(directory, "windows.json", artifact)

            summary = summary_module.summarize_artifact(
                artifact_path,
                label="windows",
                expect_parity_run_id="parity-run-1",
                expect_source_fingerprint=artifact["source"]["sourceFingerprint"],
                expect_source_manifest_sha256=artifact["source"]["sourceManifestSha256"],
                expect_source_file_count=len(artifact["source"]["files"]),
                max_artifact_age_hours=24.0,
            )

        self.assertFalse(summary["ok"])
        self.assertFalse(summary["proofs"]["notepadNativeAct"])
        self.assertIn("notepadNativeAct", summary["missingProofFacets"])
        self.assertGreater(summary["missingProofFacetCount"], 0)
        self.assertIn("required proof facet notepadNativeAct", " ".join(summary["findings"]))

    def test_artifact_summary_rejects_windows_live_failure_artifact_with_preflight_details(self) -> None:
        summary_module = _load_summary_module()
        source = _source()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            artifact = {
                "schemaVersion": 1,
                "ok": False,
                "mode": "windows_live_proof_failed",
                "generatedAt": _now_ms(),
                "parityRunId": "parity-run-1",
                "preflight": {
                    "sourceFingerprint": source["sourceFingerprint"],
                    "sourceManifestSha256": source["sourceManifestSha256"],
                    "sourceFileCount": len(source["files"]),
                    "os": {
                        "isWindows": True,
                        "sessionName": "Services",
                        "isElevated": False,
                    },
                },
                "error": "Windows HostBridge live proof requires an interactive desktop session, not Services.",
            }
            artifact_path = self._write_artifact(directory, "windows-failed.json", artifact)

            summary = summary_module.summarize_artifact(
                artifact_path,
                label="windows",
                expect_parity_run_id="parity-run-1",
                expect_source_fingerprint=source["sourceFingerprint"],
                expect_source_manifest_sha256=source["sourceManifestSha256"],
                expect_source_file_count=len(source["files"]),
                max_artifact_age_hours=24.0,
            )

        self.assertFalse(summary["ok"])
        self.assertEqual(summary["mode"], "windows_live_proof_failed")
        self.assertEqual(summary["sourceFingerprint"], source["sourceFingerprint"])
        self.assertEqual(summary["sourceManifestSha256"], source["sourceManifestSha256"])
        self.assertEqual(summary["sourceFileCount"], len(source["files"]))
        self.assertIn("Windows live proof runner failed before full proof artifact was produced", " ".join(summary["findings"]))
        self.assertIn("interactive desktop session", summary["failureError"])
        self.assertEqual(summary["failurePreflight"]["sessionName"], "Services")
        self.assertFalse(summary["proofs"]["browserOpen"])

    def test_artifact_summary_surfaces_windows_live_failure_stage_and_partial_artifact(self) -> None:
        summary_module = _load_summary_module()
        source = _source()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            artifact = {
                "schemaVersion": 1,
                "ok": False,
                "mode": "windows_live_proof_failed",
                "generatedAt": _now_ms(),
                "parityRunId": "parity-run-1",
                "source": {
                    "sourceFingerprint": source["sourceFingerprint"],
                    "sourceManifestSha256": source["sourceManifestSha256"],
                    "sourceFileCount": len(source["files"]),
                },
                "failedStage": "live_proof_pipeline",
                "nextSteps": {
                    "failedStage": "live_proof_pipeline",
                    "commands": [
                        ".\\atrium.ps1 doctor --json",
                        ".\\atrium.ps1 report --bundle",
                    ],
                },
                "partialArtifact": {
                    "preserved": True,
                    "mode": "live",
                    "checkNames": ["browserRef", "interactiveNotepad"],
                    "checkCount": 2,
                },
                "preflight": {
                    "os": {
                        "isWindows": True,
                        "sessionName": "Console",
                        "isElevated": True,
                    },
                },
                "error": "Validate Windows HostBridge artifact failed",
            }
            artifact_path = self._write_artifact(directory, "windows-failed-partial.json", artifact)

            summary = summary_module.summarize_artifact(
                artifact_path,
                label="windows",
                expect_parity_run_id="parity-run-1",
                expect_source_fingerprint=source["sourceFingerprint"],
                expect_source_manifest_sha256=source["sourceManifestSha256"],
                expect_source_file_count=len(source["files"]),
                max_artifact_age_hours=24.0,
            )

        self.assertFalse(summary["ok"])
        self.assertEqual(summary["failureStage"], "live_proof_pipeline")
        self.assertEqual(summary["failurePartialArtifact"]["mode"], "live")
        self.assertEqual(summary["failurePartialArtifact"]["checkCount"], 2)
        self.assertEqual(summary["failureNextSteps"]["failedStage"], "live_proof_pipeline")
        self.assertIn(".\\atrium.ps1 report --bundle", summary["failureNextSteps"]["commands"])
        self.assertEqual(summary["failurePreflight"]["sessionName"], "Console")

    def test_artifact_summary_flags_stale_windows_live_failure_source(self) -> None:
        summary_module = _load_summary_module()
        source = _source()
        stale_fingerprint = "f" * 64
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            artifact_path = self._write_artifact(
                directory,
                "windows-failed-stale.json",
                {
                    "schemaVersion": 1,
                    "ok": False,
                    "mode": "windows_live_proof_failed",
                    "generatedAt": _now_ms() - (25 * 60 * 60 * 1000),
                    "parityRunId": "parity-run-1",
                    "sourceFingerprint": stale_fingerprint,
                    "sourceManifestSha256": stale_fingerprint,
                    "sourceFileCount": len(source["files"]) - 1,
                    "error": "uv is required on PATH before running the Windows HostBridge live proof.",
                    "preflight": {"os": {"isWindows": True, "sessionName": "Console", "isElevated": False}},
                },
            )

            summary = summary_module.summarize_artifact(
                artifact_path,
                label="windows",
                expect_parity_run_id="parity-run-1",
                expect_source_fingerprint=source["sourceFingerprint"],
                expect_source_manifest_sha256=source["sourceManifestSha256"],
                expect_source_file_count=len(source["files"]),
                max_artifact_age_hours=24.0,
            )

        self.assertFalse(summary["ok"])
        findings = "\n".join(summary["findings"])
        self.assertIn("sourceFingerprint mismatch", findings)
        self.assertIn("sourceManifestSha256 mismatch", findings)
        self.assertIn("sourceFileCount mismatch", findings)
        self.assertIn("artifact is stale", findings)

    def test_source_summary_rejects_source_mismatch_before_probe_handoff(self) -> None:
        source_summary_module = _load_source_summary_module()

        summary = source_summary_module.summarize_source(
            expect_source_fingerprint="f" * 64,
            root=REPO_ROOT,
        )

        self.assertFalse(summary["ok"])
        self.assertRegex(summary["sourceFingerprint"], r"^[0-9a-f]{64}$")
        self.assertIn("sourceFingerprint mismatch", " ".join(summary["findings"]))

    def test_source_summary_rejects_uppercase_expected_source_fingerprint(self) -> None:
        source_summary_module = _load_source_summary_module()
        current = source_summary_module.summarize_source(root=REPO_ROOT)

        summary = source_summary_module.summarize_source(
            expect_source_fingerprint=str(current["sourceFingerprint"]).upper(),
            expect_source_manifest_sha256=str(current["sourceManifestSha256"]).upper(),
            expect_source_file_count=current["sourceFileCount"],
            root=REPO_ROOT,
        )

        self.assertFalse(summary["ok"])
        findings = " ".join(summary["findings"])
        self.assertIn("sourceFingerprint mismatch", findings)
        self.assertIn("sourceManifestSha256 mismatch", findings)

    def test_parity_report_rejects_missing_browser_ref_proof(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos = _macos_artifact()
            macos["checks"].pop("browserRef")
            macos_path = self._write_artifact(directory, "macos.json", macos)
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact())

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("macos: browser.snapshot DOM-ref proof is missing, skipped, or failed", findings)
        self.assertIn("macos: browser.act DOM-ref click proof is missing, skipped, or failed", findings)
        self.assertIn("macos: browser.act DOM-ref proof did not verify the post-click DOM state", findings)

    def test_parity_report_rejects_interactive_skipped_artifact(self) -> None:
        report_module = _load_report_module()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            macos = _macos_artifact(
                ok=False,
                status={
                    "platform": "darwin",
                    "browserBridge": True,
                    "desktopBridge": True,
                    "desktopAutomationReady": False,
                    "macosVisualPreflightChecks": {"foregroundSession": False},
                },
            )
            macos["checks"]["interactiveSkipped"] = {"skipped": True}
            macos_path = self._write_artifact(directory, "macos.json", macos)
            windows_path = self._write_artifact(directory, "windows.json", _windows_artifact())

            report = report_module.evaluate_artifacts(macos_path, windows_path)

        findings = "\n".join(report["findings"])
        self.assertFalse(report["ok"])
        self.assertIn("macos: interactive desktop proof was skipped", findings)
        self.assertIn("macos: foregroundSession is not true", findings)


if __name__ == "__main__":
    unittest.main()
