#!/usr/bin/env python3
"""Validate HostBridge parity proof artifacts across macOS and Windows."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = ROOT / "system"
sys.path.insert(0, str(SYSTEM))

from app.host_bridge_proof import host_bridge_parity_proof_id, host_bridge_source_provenance  # noqa: E402


REPORT_SCHEMA_VERSION = 1
PROBE_SCHEMA_VERSION = 1
PROOF_SCHEMA_VERSION = 1
DEFAULT_MAX_ARTIFACT_AGE_HOURS = 24.0
DEFAULT_WINDOWS_ARTIFACT_SOURCE_PATH = r"C:\Temp\atrium_host_bridge_windows_live.json"
PARITY_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
MACOS_APPLESCRIPT_CLIPBOARD_TEXT = "ATRIUM macOS AppleScript probe ไทย"
MACOS_TEXTEDIT_EXPECTED_TEXT = "ATRIUM macOS TextEdit probe ไทย"
MACOS_CALCULATOR_EXPECTED_VALUE = "1"
MACOS_TEXTEDIT_NATIVE_SCROLL_ACTIONS = {"AXScrollDownByPage", "setScrollBarValue"}
WINDOWS_INTERACTIVE_TYPE_TEXT = "ATRIUM Windows HostBridge probe ไทย"
WINDOWS_INTERACTIVE_NATIVE_TEXT = "ATRIUM Windows ValuePattern probe ไทย"
WINDOWS_INTERACTIVE_SESSION_NAMES = {"console"}
WINDOWS_INTERACTIVE_SESSION_PREFIXES = ("rdp-", "ica-")

OS_LABELS = {
    "macos": "darwin",
    "windows": "win32",
}


def _transfer_hint(label: str, input_path: Path | None, source_path: str | None) -> str | None:
    if label != "windows" or not input_path or not source_path:
        return None
    return f"Copy the Windows full-probe artifact from {source_path} to {input_path} on this host, then rerun the verifier."


def _source_path_for(label: str, artifact_source_paths: dict[str, str] | None) -> str | None:
    if not isinstance(artifact_source_paths, dict):
        return None
    source_path = str(artifact_source_paths.get(label) or "").strip()
    return source_path or None


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "artifact file is missing"
    except json.JSONDecodeError as exc:
        return None, f"artifact JSON is invalid: {exc}"
    except OSError as exc:
        return None, f"artifact could not be read: {exc}"
    if not isinstance(loaded, dict):
        return None, "artifact root must be a JSON object"
    return loaded, None


def _artifact_file_metadata(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "artifactBytes": len(data),
        "artifactSha256": hashlib.sha256(data).hexdigest(),
    }


def _valid_parity_run_id(value: Any) -> bool:
    return isinstance(value, str) and PARITY_RUN_ID_RE.fullmatch(value) is not None


def _nested(data: dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _return_code_ok(value: Any) -> bool:
    item = _dict(value)
    return item.get("returnCode") == 0 and item.get("skipped") is not True


def _isolated_own_profile(value: Any) -> bool:
    item = _dict(value)
    return item.get("profileKind") == "isolated" and item.get("isOwnProfile") is True


def _playwright_isolated_own_profile(value: Any) -> bool:
    item = _dict(value)
    return item.get("backend") == "playwright" and _isolated_own_profile(item)


def _windows_helper_dpi_awareness(value: Any) -> bool:
    item = _dict(value)
    return bool(item.get("dpiAwareness"))


def _windows_helper_virtual_screen(value: Any) -> bool:
    item = _dict(value)
    return (
        _positive_int(item.get("screenWidth"))
        and _positive_int(item.get("screenHeight"))
        and _positive_int(item.get("virtualWidth"))
        and _positive_int(item.get("virtualHeight"))
    )


def _windows_powershell_dpi_awareness(value: Any) -> bool:
    item = _dict(value)
    checks = _dict(item.get("checks"))
    return checks.get("dpiAwareness") is True


def _windows_powershell_virtual_screen(value: Any) -> bool:
    item = _dict(value)
    checks = _dict(item.get("checks"))
    virtual_screen = _dict(item.get("virtualScreen"))
    return (
        checks.get("virtualScreen") is True
        and _positive_int(virtual_screen.get("width"))
        and _positive_int(virtual_screen.get("height"))
    )


def _utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def _windows_unicode_type_proof(value: Any) -> bool:
    item = _dict(value)
    return (
        item.get("returnCode") == 0
        and item.get("textBytes") == len(WINDOWS_INTERACTIVE_TYPE_TEXT.encode("utf-8"))
        and item.get("textCharacters") == len(WINDOWS_INTERACTIVE_TYPE_TEXT)
        and item.get("textUnits") == _utf16_units(WINDOWS_INTERACTIVE_TYPE_TEXT)
    )


def _windows_keypress_proof(value: Any, expected_key: str, expected_modifiers: list[str]) -> bool:
    item = _dict(value)
    return (
        item.get("returnCode") == 0
        and str(item.get("key") or "").lower() == expected_key
        and [str(modifier).lower() for modifier in item.get("modifiers") or []] == expected_modifiers
    )


def _windows_interactive_session_identity(status: dict[str, Any]) -> bool:
    session_name = str(status.get("interactiveSessionName") or "").strip()
    lowered = session_name.lower()
    return (
        status.get("interactiveSession") is True
        and _positive_int(status.get("interactiveSessionId"))
        and (
            lowered in WINDOWS_INTERACTIVE_SESSION_NAMES
            or lowered.startswith(WINDOWS_INTERACTIVE_SESSION_PREFIXES)
        )
    )


def _windows_foreground_activation_proof(value: Any) -> bool:
    item = _dict(value)
    process_id = item.get("processId")
    return (
        item.get("returnCode") == 0
        and item.get("foreground") is True
        and _positive_int(process_id)
        and item.get("activeProcessId") == process_id
    )


def _windows_clipboard_round_trip_proof(value: Any) -> bool:
    item = _dict(value)
    return (
        item.get("returnCode") == 0
        and item.get("expected") == WINDOWS_INTERACTIVE_NATIVE_TEXT
        and item.get("containsExpected") is True
        and item.get("verified") is True
        and item.get("textLength") == len(WINDOWS_INTERACTIVE_NATIVE_TEXT)
        and item.get("textBytes") == len(WINDOWS_INTERACTIVE_NATIVE_TEXT.encode("utf-8"))
        and item.get("expectedLength") == len(WINDOWS_INTERACTIVE_NATIVE_TEXT)
        and item.get("expectedBytes") == len(WINDOWS_INTERACTIVE_NATIVE_TEXT.encode("utf-8"))
    )


def _macos_applescript_clipboard_proof(value: Any) -> bool:
    item = _dict(value)
    return (
        item.get("returnCode") == 0
        and item.get("method") == "osascript"
        and item.get("expected") == MACOS_APPLESCRIPT_CLIPBOARD_TEXT
        and item.get("verified") is True
        and item.get("textLength") == len(MACOS_APPLESCRIPT_CLIPBOARD_TEXT)
        and item.get("expectedLength") == len(MACOS_APPLESCRIPT_CLIPBOARD_TEXT)
        and item.get("expectedBytes") == len(MACOS_APPLESCRIPT_CLIPBOARD_TEXT.encode("utf-8"))
    )


def _snapshot_contains_text_value(value: Any, expected_text: str) -> bool:
    snapshot = _dict(_dict(value).get("snapshot"))
    elements = snapshot.get("elements")
    if not isinstance(elements, list):
        return False
    for element in elements:
        if not isinstance(element, dict):
            continue
        if expected_text in str(element.get("value") or ""):
            return True
    return False


def _macos_snapshot_has_native_metadata(
    value: Any,
    *,
    native_action: str | None = None,
    supported_action: str | None = None,
    settable_attribute: str | None = None,
) -> bool:
    snapshot = _dict(_dict(value).get("snapshot"))
    elements = snapshot.get("elements")
    if not isinstance(elements, list):
        return False
    expected_native_action = native_action.strip().lower() if native_action else None
    expected_supported_action = supported_action.strip().lower() if supported_action else None
    expected_settable = settable_attribute.strip().lower() if settable_attribute else None
    for element in elements:
        if not isinstance(element, dict):
            continue
        native_supported = (
            {str(item).strip().lower() for item in element.get("nativeSupportedActions", []) if str(item).strip()}
            if isinstance(element.get("nativeSupportedActions"), list)
            else set()
        )
        ax_actions = (
            {str(item).strip().lower() for item in element.get("axActions", []) if str(item).strip()}
            if isinstance(element.get("axActions"), list)
            else set()
        )
        settable = (
            {str(item).strip().lower() for item in element.get("settableAttributes", []) if str(item).strip()}
            if isinstance(element.get("settableAttributes"), list)
            else set()
        )
        if expected_supported_action and expected_supported_action not in native_supported:
            continue
        if expected_native_action and expected_native_action not in ax_actions:
            continue
        if expected_settable and expected_settable not in settable:
            continue
        return True
    return False


def _macos_native_action_metadata_proof(checks: dict[str, Any]) -> bool:
    return (
        _nested(checks, "interactiveCalculator", "nativeActionMetadataVerified") is True
        and _macos_snapshot_has_native_metadata(
            _nested(checks, "interactiveCalculator", "desktopSnapshot"),
            native_action="AXPress",
            supported_action="click",
        )
        and _nested(checks, "interactiveTextEdit", "nativeActionMetadataVerified") is True
        and _macos_snapshot_has_native_metadata(
            _nested(checks, "interactiveTextEdit", "desktopSnapshot"),
            supported_action="paste",
            settable_attribute="AXValue",
        )
        and _nested(checks, "interactiveTextEdit", "nativeScrollMetadataVerified") is True
        and _macos_snapshot_has_native_metadata(
            _nested(checks, "interactiveTextEdit", "desktopSnapshot"),
            native_action="AXScrollDownByPage",
            supported_action="scroll",
        )
    )


def _macos_native_action_proof(value: Any, expected_action: str | set[str]) -> bool:
    item = _dict(value)
    native_attempt = _dict(item.get("nativeAttempt"))
    expected_actions = {expected_action} if isinstance(expected_action, str) else expected_action
    return (
        item.get("returnCode") == 0
        and item.get("usedNativeAction") is True
        and native_attempt.get("returnCode") == 0
        and native_attempt.get("method") in {"accessibility", "accessibility_ax_helper"}
        and native_attempt.get("inputMethod") == "accessibility"
        and native_attempt.get("nativeStatus") == "OK"
        and native_attempt.get("nativeAction") in expected_actions
    )


def _windows_native_action_proof(value: Any, expected_action: str) -> bool:
    item = _dict(value)
    native_attempt = _dict(item.get("nativeAttempt"))
    return (
        item.get("returnCode") == 0
        and item.get("usedNativeAction") is True
        and native_attempt.get("returnCode") == 0
        and native_attempt.get("method") == "uia"
        and native_attempt.get("inputMethod") == "uia"
        and native_attempt.get("ok") is True
        and native_attempt.get("nativeAction") == expected_action
    )


def _proof_summary(label: str, data: dict[str, Any]) -> dict[str, bool]:
    status = _dict(data.get("status"))
    checks = _dict(data.get("checks"))
    browser_ref = _dict(checks.get("browserRef"))
    browser_snapshot = browser_ref.get("browserSnapshot")
    browser_act = browser_ref.get("browserActClick")
    proofs = {
        "browserOpen": _return_code_ok(checks.get("browserOpen")),
        "browserOpenIsolatedProfile": _isolated_own_profile(checks.get("browserOpen")),
        "browserSnapshot": _return_code_ok(browser_snapshot),
        "browserSnapshotIsolatedPlaywright": _playwright_isolated_own_profile(browser_snapshot),
        "browserAct": _return_code_ok(browser_act),
        "browserActIsolatedPlaywright": _playwright_isolated_own_profile(browser_act),
        "browserActVerified": browser_ref.get("containsExpected") is True,
        "appsDiscovery": _return_code_ok(checks.get("apps")),
        "screenshotFile": _dict(checks.get("screenshotFile")).get("ok") is True,
        "notification": _return_code_ok(checks.get("notification")),
        "desktopAutomationReady": status.get("desktopAutomationReady") is True,
    }
    if label == "macos":
        status_checks = _dict(status.get("macosVisualPreflightChecks"))
        proofs.update({
            "foregroundSession": status_checks.get("foregroundSession") is True,
            "appleScriptClipboard": _macos_applescript_clipboard_proof(checks.get("appleScriptClipboard")),
            "foregroundSnapshotNative": _nested(checks, "foregroundSnapshot", "snapshotBackend") == "native_ax",
            "appsNativeNSWorkspace": _nested(checks, "apps", "runningMethod") == "native_nsworkspace_apps",
            "macosNativeActionMetadata": _macos_native_action_metadata_proof(checks),
            "calculatorNativeAct": _macos_native_action_proof(
                _nested(checks, "interactiveCalculator", "desktopActClick"),
                "AXPress",
            )
            and _nested(checks, "interactiveCalculator", "displayValueVerified") is True
            and _snapshot_contains_text_value(
                _nested(checks, "interactiveCalculator", "desktopSnapshotAfter"),
                MACOS_CALCULATOR_EXPECTED_VALUE,
            ),
            "textEditNativeAct": _macos_native_action_proof(
                _nested(checks, "interactiveTextEdit", "desktopActSetText"),
                "setValue",
            )
            and _nested(checks, "interactiveTextEdit", "textValueVerified") is True
            and _snapshot_contains_text_value(
                _nested(checks, "interactiveTextEdit", "desktopSnapshotAfter"),
                MACOS_TEXTEDIT_EXPECTED_TEXT,
            ),
            "textEditNativeScroll": _macos_native_action_proof(
                _nested(checks, "interactiveTextEdit", "desktopActScroll"),
                MACOS_TEXTEDIT_NATIVE_SCROLL_ACTIONS,
            )
            and _nested(checks, "interactiveTextEdit", "nativeScrollVerified") is True
            and _nested(checks, "interactiveTextEdit", "nativeScrollMetadataVerified") is True,
        })
    elif label == "windows":
        proofs.update({
            "interactiveSession": status.get("interactiveSession") is True,
            "windowsInteractiveSessionIdentity": _windows_interactive_session_identity(status),
            "windowsVisualPreflight": status.get("windowsVisualPreflightOk") is True,
            "helperSelftest": _nested(checks, "helperSelftest", "ok") is True,
            "powershellPreflight": _nested(checks, "powershellPreflight", "ok") is True,
            "windowsDpiAwareness": _windows_helper_dpi_awareness(checks.get("helperSelftest"))
            and _windows_powershell_dpi_awareness(checks.get("powershellPreflight")),
            "windowsVirtualScreen": _windows_helper_virtual_screen(checks.get("helperSelftest"))
            and _windows_powershell_virtual_screen(checks.get("powershellPreflight")),
            "windowsForegroundActivation": _nested(checks, "interactiveDesktop", "foregroundActivationVerified") is True
            and _windows_foreground_activation_proof(_nested(checks, "interactiveDesktop", "activateApp")),
            "windowsUnicodeTyping": _nested(checks, "interactiveDesktop", "unicodeTypeVerified") is True
            and _windows_unicode_type_proof(_nested(checks, "interactiveDesktop", "type")),
            "windowsKeyboardShortcut": _nested(checks, "interactiveDesktop", "selectAllKeypressVerified") is True
            and _windows_keypress_proof(_nested(checks, "interactiveDesktop", "keypress"), "a", ["control"]),
            "notepadNativeAct": _windows_native_action_proof(
                _nested(checks, "interactiveDesktop", "desktopActSetText"),
                "ValuePattern",
            )
            and _nested(checks, "interactiveDesktop", "nativeValueVerified") is True
            and _snapshot_contains_text_value(
                _nested(checks, "interactiveDesktop", "desktopSnapshotAfter"),
                WINDOWS_INTERACTIVE_NATIVE_TEXT,
            ),
            "clipboardRoundTrip": _windows_clipboard_round_trip_proof(
                _nested(checks, "interactiveDesktop", "clipboardRoundTrip")
            ),
        })
    return proofs


def _runtime_blocks(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    blocks = data.get("runtimeBlocks")
    if not isinstance(blocks, dict):
        return {}
    return {str(key): value for key, value in blocks.items() if isinstance(value, dict)}


def _blocked_runtime_tools(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        tool: block
        for tool, block in _runtime_blocks(data).items()
        if block.get("api") or block.get("chat")
    }


def _routes(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    routes = data.get("routes")
    if not isinstance(routes, dict):
        return {}
    return {str(key): value for key, value in routes.items() if isinstance(value, dict)}


def _blocked_routes(data: dict[str, Any]) -> dict[str, Any]:
    return {
        tool: route.get("blockReason")
        for tool, route in _routes(data).items()
        if route.get("blockReason")
    }


def _check_artifact_metadata(
    label: str,
    data: dict[str, Any],
    findings: list[str],
    *,
    now_ms: int,
    max_artifact_age_hours: float,
) -> None:
    if data.get("schemaVersion") != PROBE_SCHEMA_VERSION:
        findings.append(f"{label}: artifact schemaVersion must be {PROBE_SCHEMA_VERSION}, got {data.get('schemaVersion')!r}")
    generated_at = data.get("generatedAt")
    if not isinstance(generated_at, int):
        findings.append(f"{label}: artifact generatedAt is missing or invalid")
        return
    if generated_at > now_ms + 5 * 60 * 1000:
        findings.append(f"{label}: artifact generatedAt is in the future")
    if max_artifact_age_hours > 0:
        max_age_ms = int(max_artifact_age_hours * 60 * 60 * 1000)
        age_ms = now_ms - generated_at
        if age_ms > max_age_ms:
            findings.append(
                f"{label}: artifact is stale; regenerate the full probe artifact. "
                f"ageHours={age_ms / 3600000:.1f}; maxAgeHours={max_artifact_age_hours:.1f}"
            )
    source = data.get("source")
    if not isinstance(source, dict):
        findings.append(f"{label}: artifact source provenance is missing")
        return
    fingerprint = source.get("sourceFingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        findings.append(f"{label}: artifact sourceFingerprint is missing or invalid")
    git_head = source.get("gitHead")
    if not isinstance(git_head, str) or len(git_head) != 40:
        findings.append(f"{label}: artifact gitHead is missing or invalid")
    if not _valid_parity_run_id(data.get("parityRunId")):
        findings.append(
            f"{label}: artifact parityRunId is missing or invalid; rerun both full probes with the same --parity-run-id"
        )


def _check_common(
    label: str,
    data: dict[str, Any],
    findings: list[str],
    *,
    now_ms: int,
    max_artifact_age_hours: float,
) -> None:
    expected_platform = OS_LABELS[label]
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    checks = data.get("checks") if isinstance(data.get("checks"), dict) else {}
    host = data.get("host") if isinstance(data.get("host"), dict) else {}

    _check_artifact_metadata(label, data, findings, now_ms=now_ms, max_artifact_age_hours=max_artifact_age_hours)
    if not host:
        findings.append(f"{label}: artifact host identity is missing")
    else:
        if host.get("schemaVersion") != 1:
            findings.append(f"{label}: artifact host identity schemaVersion must be 1, got {host.get('schemaVersion')!r}")
        host_fingerprint = host.get("hostFingerprint")
        if not isinstance(host_fingerprint, str) or len(host_fingerprint) != 64:
            findings.append(f"{label}: artifact hostFingerprint is missing or invalid")
        if host.get("platform") != expected_platform:
            findings.append(f"{label}: artifact host platform must be {expected_platform!r}, got {host.get('platform')!r}")
        if not str(host.get("hostname") or "").strip():
            findings.append(f"{label}: artifact hostname is missing")
    if data.get("mode") != "live":
        findings.append(f"{label}: artifact mode must be live, got {data.get('mode')!r}")
    if data.get("ok") is not True:
        findings.append(f"{label}: probe ok must be true")
    if status.get("platform") != expected_platform:
        findings.append(f"{label}: status.platform must be {expected_platform!r}, got {status.get('platform')!r}")
    if status.get("browserBridge") is not True:
        findings.append(f"{label}: browserBridge is not true")
    if status.get("desktopBridge") is not True:
        findings.append(f"{label}: desktopBridge is not true")
    if status.get("desktopAutomationReady") is not True:
        findings.append(f"{label}: desktopAutomationReady is not true")

    blocked_routes = _blocked_routes(data)
    if blocked_routes:
        findings.append(f"{label}: routes are blocked: {', '.join(sorted(blocked_routes))}")

    runtime_blocks = _blocked_runtime_tools(data)
    if runtime_blocks:
        findings.append(f"{label}: runtime blocks are present: {', '.join(sorted(runtime_blocks))}")

    if not isinstance(checks.get("apps"), dict) or checks["apps"].get("returnCode") != 0:
        findings.append(f"{label}: apps discovery proof is missing or failed")
    if not isinstance(checks.get("screenshotFile"), dict) or checks["screenshotFile"].get("ok") is not True:
        findings.append(f"{label}: screenshot file proof is missing or failed")
    if not isinstance(checks.get("notification"), dict) or checks["notification"].get("returnCode") != 0:
        findings.append(f"{label}: notification proof is missing or failed")
    if not isinstance(checks.get("browserOpen"), dict) or checks["browserOpen"].get("returnCode") != 0 or checks["browserOpen"].get("skipped"):
        findings.append(f"{label}: browser.open proof is missing, skipped, or failed")
    elif not _isolated_own_profile(checks["browserOpen"]):
        findings.append(f"{label}: browser.open proof did not use ATRIUM's isolated browser profile")
    browser_ref = checks.get("browserRef") if isinstance(checks.get("browserRef"), dict) else {}
    if not isinstance(browser_ref.get("browserSnapshot"), dict) or browser_ref["browserSnapshot"].get("returnCode") != 0 or browser_ref["browserSnapshot"].get("skipped"):
        findings.append(f"{label}: browser.snapshot DOM-ref proof is missing, skipped, or failed")
    elif not _playwright_isolated_own_profile(browser_ref["browserSnapshot"]):
        findings.append(f"{label}: browser.snapshot DOM-ref proof did not use Playwright with ATRIUM's isolated browser profile")
    if not isinstance(browser_ref.get("browserActClick"), dict) or browser_ref["browserActClick"].get("returnCode") != 0 or browser_ref["browserActClick"].get("skipped"):
        findings.append(f"{label}: browser.act DOM-ref click proof is missing, skipped, or failed")
    elif not _playwright_isolated_own_profile(browser_ref["browserActClick"]):
        findings.append(f"{label}: browser.act DOM-ref click proof did not use Playwright with ATRIUM's isolated browser profile")
    if browser_ref.get("containsExpected") is not True:
        findings.append(f"{label}: browser.act DOM-ref proof did not verify the post-click DOM state")

    interactive_skipped = checks.get("interactiveSkipped")
    if isinstance(interactive_skipped, dict) and interactive_skipped.get("skipped") is True:
        findings.append(f"{label}: interactive desktop proof was skipped")


def _check_macos(data: dict[str, Any], findings: list[str]) -> None:
    checks = data.get("checks") if isinstance(data.get("checks"), dict) else {}
    status_checks = _nested(data, "status", "macosVisualPreflightChecks")
    if isinstance(status_checks, dict) and status_checks.get("foregroundSession") is not True:
        findings.append("macos: foregroundSession is not true")
    if not _macos_applescript_clipboard_proof(checks.get("appleScriptClipboard")):
        findings.append("macos: AppleScript clipboard exact round-trip proof is missing")
    if _nested(checks, "foregroundSnapshot", "snapshotBackend") != "native_ax":
        findings.append("macos: foregroundSnapshot did not use native_ax backend")
    if _nested(checks, "apps", "runningMethod") != "native_nsworkspace_apps":
        findings.append("macos: apps discovery did not use native_nsworkspace_apps")
    if not _macos_native_action_metadata_proof(checks):
        findings.append("macos: native AX action metadata proof is missing")
    if not (
        _macos_native_action_proof(_nested(checks, "interactiveCalculator", "desktopActClick"), "AXPress")
        and _nested(checks, "interactiveCalculator", "displayValueVerified") is True
        and _snapshot_contains_text_value(
            _nested(checks, "interactiveCalculator", "desktopSnapshotAfter"),
            MACOS_CALCULATOR_EXPECTED_VALUE,
        )
    ):
        findings.append("macos: Calculator desktop.act native AXPress display proof is missing")
    if not (
        _macos_native_action_proof(_nested(checks, "interactiveTextEdit", "desktopActSetText"), "setValue")
        and _nested(checks, "interactiveTextEdit", "textValueVerified") is True
        and _snapshot_contains_text_value(
            _nested(checks, "interactiveTextEdit", "desktopSnapshotAfter"),
            MACOS_TEXTEDIT_EXPECTED_TEXT,
        )
    ):
        findings.append("macos: TextEdit desktop.act native setValue proof is missing")
    if not (
        _macos_native_action_proof(
            _nested(checks, "interactiveTextEdit", "desktopActScroll"),
            MACOS_TEXTEDIT_NATIVE_SCROLL_ACTIONS,
        )
        and _nested(checks, "interactiveTextEdit", "nativeScrollVerified") is True
        and _nested(checks, "interactiveTextEdit", "nativeScrollMetadataVerified") is True
    ):
        findings.append("macos: TextEdit desktop.act native AXScrollDownByPage proof is missing")


def _check_windows(data: dict[str, Any], findings: list[str]) -> None:
    checks = data.get("checks") if isinstance(data.get("checks"), dict) else {}
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    if status.get("interactiveSession") is not True:
        findings.append("windows: interactiveSession is not true")
    if not _windows_interactive_session_identity(status):
        findings.append("windows: interactive session identity proof is missing")
    if status.get("windowsVisualPreflightOk") is not True:
        findings.append("windows: windowsVisualPreflightOk is not true")
    if _nested(checks, "helperSelftest", "ok") is not True:
        findings.append("windows: SendInput/UI helper selftest proof is missing")
    if _nested(checks, "powershellPreflight", "ok") is not True:
        findings.append("windows: PowerShell visual preflight proof is missing")
    if not (
        _windows_helper_dpi_awareness(checks.get("helperSelftest"))
        and _windows_powershell_dpi_awareness(checks.get("powershellPreflight"))
    ):
        findings.append("windows: DPI awareness proof is missing")
    if not (
        _windows_helper_virtual_screen(checks.get("helperSelftest"))
        and _windows_powershell_virtual_screen(checks.get("powershellPreflight"))
    ):
        findings.append("windows: virtual screen bounds proof is missing")
    if not (
        _nested(checks, "interactiveDesktop", "foregroundActivationVerified") is True
        and _windows_foreground_activation_proof(_nested(checks, "interactiveDesktop", "activateApp"))
    ):
        findings.append("windows: Notepad foreground activation proof is missing")
    if not (
        _nested(checks, "interactiveDesktop", "unicodeTypeVerified") is True
        and _windows_unicode_type_proof(_nested(checks, "interactiveDesktop", "type"))
    ):
        findings.append("windows: Unicode typing proof is missing")
    if not (
        _nested(checks, "interactiveDesktop", "selectAllKeypressVerified") is True
        and _windows_keypress_proof(_nested(checks, "interactiveDesktop", "keypress"), "a", ["control"])
    ):
        findings.append("windows: keyboard shortcut mapping proof is missing")
    if not (
        _windows_native_action_proof(_nested(checks, "interactiveDesktop", "desktopActSetText"), "ValuePattern")
        and _nested(checks, "interactiveDesktop", "nativeValueVerified") is True
        and _snapshot_contains_text_value(
            _nested(checks, "interactiveDesktop", "desktopSnapshotAfter"),
            WINDOWS_INTERACTIVE_NATIVE_TEXT,
        )
    ):
        findings.append("windows: Notepad desktop.act native ValuePattern text proof is missing")
    if not _windows_clipboard_round_trip_proof(_nested(checks, "interactiveDesktop", "clipboardRoundTrip")):
        findings.append("windows: Notepad clipboard exact round-trip proof is missing")


def _check_source_consistency(loaded: dict[str, dict[str, Any]], results: dict[str, Any], findings: list[str]) -> None:
    if set(loaded) != set(OS_LABELS):
        return
    source_fingerprints = {
        label: _nested(data, "source", "sourceFingerprint")
        for label, data in loaded.items()
    }
    if all(isinstance(value, str) and value for value in source_fingerprints.values()):
        unique_fingerprints = set(source_fingerprints.values())
        if len(unique_fingerprints) != 1:
            detail = ", ".join(f"{label}={value}" for label, value in sorted(source_fingerprints.items()))
            finding = f"source provenance mismatch: macOS and Windows artifacts were generated from different source fingerprints ({detail})"
            findings.append(finding)
            for label in OS_LABELS:
                if label in results:
                    results[label]["ok"] = False
                    results[label].setdefault("findings", []).append(finding)
    parity_run_ids = {
        label: data.get("parityRunId")
        for label, data in loaded.items()
        if _valid_parity_run_id(data.get("parityRunId"))
    }
    if set(parity_run_ids) == set(OS_LABELS):
        unique_run_ids = set(parity_run_ids.values())
        if len(unique_run_ids) != 1:
            detail = ", ".join(f"{label}={value}" for label, value in sorted(parity_run_ids.items()))
            finding = f"parity run mismatch: macOS and Windows artifacts were not generated for the same parity run ({detail})"
            findings.append(finding)
            for label in OS_LABELS:
                if label in results:
                    results[label]["ok"] = False
                    results[label].setdefault("findings", []).append(finding)
    git_heads = {
        label: _nested(data, "source", "gitHead")
        for label, data in loaded.items()
    }
    if all(isinstance(value, str) and value for value in git_heads.values()):
        unique_heads = set(git_heads.values())
        if len(unique_heads) != 1:
            detail = ", ".join(f"{label}={value}" for label, value in sorted(git_heads.items()))
            finding = f"source gitHead mismatch: macOS and Windows artifacts were generated from different commits ({detail})"
            findings.append(finding)
            for label in OS_LABELS:
                if label in results:
                    results[label]["ok"] = False
                    results[label].setdefault("findings", []).append(finding)


def _check_current_source_consistency(
    loaded: dict[str, dict[str, Any]],
    results: dict[str, Any],
    findings: list[str],
    current_source: dict[str, Any],
) -> None:
    current_fingerprint = current_source.get("sourceFingerprint")
    if isinstance(current_fingerprint, str) and len(current_fingerprint) == 64:
        for label, data in loaded.items():
            fingerprint = _nested(data, "source", "sourceFingerprint")
            if isinstance(fingerprint, str) and len(fingerprint) == 64 and fingerprint != current_fingerprint:
                finding = (
                    f"{label}: artifact sourceFingerprint does not match current HostBridge source "
                    f"(artifact={fingerprint}, current={current_fingerprint})"
                )
                findings.append(finding)
                if label in results:
                    results[label]["ok"] = False
                    results[label].setdefault("findings", []).append(finding)
    current_git_head = current_source.get("gitHead")
    if isinstance(current_git_head, str) and len(current_git_head) == 40:
        for label, data in loaded.items():
            git_head = _nested(data, "source", "gitHead")
            if isinstance(git_head, str) and len(git_head) == 40 and git_head != current_git_head:
                finding = (
                    f"{label}: artifact gitHead does not match current checkout "
                    f"(artifact={git_head}, current={current_git_head})"
                )
                findings.append(finding)
                if label in results:
                    results[label]["ok"] = False
                    results[label].setdefault("findings", []).append(finding)


def evaluate_artifacts(
    macos_path: Path | None,
    windows_path: Path | None,
    *,
    generated_at_ms: int | None = None,
    now_ms: int | None = None,
    max_artifact_age_hours: float = DEFAULT_MAX_ARTIFACT_AGE_HOURS,
    current_source: dict[str, Any] | None = None,
    enforce_current_source: bool = True,
    artifact_source_paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    report_generated_at = int(generated_at_ms if generated_at_ms is not None else time.time() * 1000)
    current_time_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    resolved_current_source = current_source if current_source is not None else (host_bridge_source_provenance(ROOT) if enforce_current_source else None)
    results: dict[str, Any] = {}
    findings: list[str] = []
    loaded_artifacts: dict[str, dict[str, Any]] = {}
    artifacts = {
        "macos": macos_path,
        "windows": windows_path,
    }
    for label, path in artifacts.items():
        source_path = _source_path_for(label, artifact_source_paths)
        transfer_hint = _transfer_hint(label, path, source_path)
        if path is None:
            findings.append(f"{label}: live proof artifact path was not provided")
            results[label] = {
                "present": False,
                "path": None,
                "artifactSourcePath": source_path,
                "copyHint": transfer_hint,
                "ok": False,
                "findings": [findings[-1]],
            }
            continue
        data, load_error = _load_json(path)
        item_findings: list[str] = []
        if load_error or data is None:
            item_findings.append(f"{label}: {load_error or 'artifact could not be loaded'}")
            if transfer_hint:
                item_findings.append(f"{label}: {transfer_hint}")
            findings.extend(item_findings)
            results[label] = {
                "present": False,
                "path": str(path),
                "artifactSourcePath": source_path,
                "copyHint": transfer_hint,
                "ok": False,
                "findings": item_findings,
            }
            continue
        try:
            file_metadata = _artifact_file_metadata(path)
        except OSError as exc:
            item_findings.append(f"{label}: artifact file metadata could not be read: {type(exc).__name__}: {exc}")
            findings.extend(item_findings)
            results[label] = {
                "present": False,
                "path": str(path),
                "artifactSourcePath": source_path,
                "copyHint": transfer_hint,
                "ok": False,
                "findings": item_findings,
            }
            continue
        loaded_artifacts[label] = data
        _check_common(
            label,
            data,
            item_findings,
            now_ms=current_time_ms,
            max_artifact_age_hours=max_artifact_age_hours,
        )
        if label == "macos":
            _check_macos(data, item_findings)
        else:
            _check_windows(data, item_findings)
        findings.extend(item_findings)
        status = data.get("status") if isinstance(data.get("status"), dict) else {}
        results[label] = {
            "present": True,
            "path": str(path),
            "artifactSourcePath": source_path,
            "proofSchemaVersion": PROOF_SCHEMA_VERSION,
            **file_metadata,
            "ok": not item_findings,
            "mode": data.get("mode"),
            "schemaVersion": data.get("schemaVersion"),
            "generatedAt": data.get("generatedAt"),
            "parityRunId": data.get("parityRunId"),
            "sourceFingerprint": _nested(data, "source", "sourceFingerprint"),
            "gitHead": _nested(data, "source", "gitHead"),
            "gitDirty": _nested(data, "source", "gitDirty"),
            "hostFingerprint": _nested(data, "host", "hostFingerprint"),
            "hostPlatform": _nested(data, "host", "platform"),
            "hostName": _nested(data, "host", "hostname"),
            "hostMachine": _nested(data, "host", "machine"),
            "probeOk": data.get("ok"),
            "platform": status.get("platform"),
            "desktopAutomationReady": status.get("desktopAutomationReady"),
            "proofs": _proof_summary(label, data),
            "findings": item_findings,
        }
    _check_source_consistency(loaded_artifacts, results, findings)
    if enforce_current_source and isinstance(resolved_current_source, dict):
        _check_current_source_consistency(loaded_artifacts, results, findings, resolved_current_source)
    proof_id = host_bridge_parity_proof_id(results, resolved_current_source, enforce_current_source=enforce_current_source)
    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "proofSchemaVersion": PROOF_SCHEMA_VERSION,
        "generatedAt": report_generated_at,
        "proofId": proof_id,
        "ok": not findings,
        "summary": "full live HostBridge parity proof is complete" if not findings else "full live HostBridge parity proof is incomplete",
        "currentSource": {
            "sourceFingerprint": resolved_current_source.get("sourceFingerprint") if isinstance(resolved_current_source, dict) else None,
            "gitHead": resolved_current_source.get("gitHead") if isinstance(resolved_current_source, dict) else None,
            "gitDirty": resolved_current_source.get("gitDirty") if isinstance(resolved_current_source, dict) else None,
        },
        "results": results,
        "findings": findings,
    }


def write_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--macos", type=Path, help="JSON artifact from `ops/macos_host_bridge_probe.py --full`.")
    parser.add_argument("--windows", type=Path, help="JSON artifact from `ops/windows_host_bridge_probe.py --full`.")
    parser.add_argument("--output", type=Path, help="Optional path to persist the report JSON for ATRIUM's connector proof UI.")
    parser.add_argument("--max-artifact-age-hours", type=float, default=DEFAULT_MAX_ARTIFACT_AGE_HOURS, help="Reject full probe artifacts older than this many hours; set <= 0 only for deliberate offline audits.")
    parser.add_argument("--skip-current-source-check", action="store_true", help="Allow offline audits of historical artifacts without requiring their source fingerprint to match this checkout.")
    parser.add_argument("--windows-source-path", default=DEFAULT_WINDOWS_ARTIFACT_SOURCE_PATH, help="Windows-host path where the full-probe artifact was written before it was copied to the local --windows path.")
    args = parser.parse_args()

    report = evaluate_artifacts(
        args.macos,
        args.windows,
        max_artifact_age_hours=args.max_artifact_age_hours,
        enforce_current_source=not args.skip_current_source_check,
        artifact_source_paths={"windows": args.windows_source_path},
    )
    if args.output:
        write_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
