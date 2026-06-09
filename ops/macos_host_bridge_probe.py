#!/usr/bin/env python3
"""Probe macOS HostBridge routing and native desktop automation readiness.

Use `--simulate` from any host for branch coverage. Use `--full` on a signed-in
macOS desktop session to run non-destructive screenshot, notification, browser,
and Calculator Accessibility checks.
"""
from __future__ import annotations

import argparse
import json
import locale
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from unittest.mock import patch
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = ROOT / "system"
sys.path.insert(0, str(SYSTEM))

from app.config import get_settings  # noqa: E402
from app.host_bridge_proof import host_bridge_host_identity, host_bridge_source_provenance  # noqa: E402
from app.tools import ExecutorRouter, build_default_tool_registry  # noqa: E402
from app.tools.host_bridge import HostBridge  # noqa: E402
from app.tools import host_bridge as host_bridge_module  # noqa: E402
from app.tools import visual_bridge  # noqa: E402

VISUAL_TOOLS = [
    "browser.profiles",
    "browser.open",
    "browser.snapshot",
    "browser.act",
    "browser.screenshot",
    "browser.click",
    "browser.type",
    "browser.keypress",
    "browser.paste_text",
    "browser.scroll",
    "desktop.apps",
    "desktop.snapshot",
    "desktop.act",
    "desktop.open_app",
    "desktop.activate_app",
    "desktop.quit_app",
    "desktop.screenshot",
    "desktop.click",
    "desktop.type",
    "desktop.keypress",
    "desktop.paste_text",
    "desktop.scroll",
    "notify.send",
]

PROBE_SCHEMA_VERSION = 1
MACOS_APPLESCRIPT_CLIPBOARD_TEXT = "ATRIUM macOS AppleScript probe ไทย"
MACOS_TEXTEDIT_EXPECTED_TEXT = "ATRIUM macOS TextEdit probe ไทย"
MACOS_TEXTEDIT_NATIVE_SCROLL_ACTIONS = {"AXScrollDownByPage", "setScrollBarValue"}
MACOS_CALCULATOR_EXPECTED_VALUE = "1"


def _stamp_result(
    result: dict[str, Any],
    *,
    parity_run_id: str | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stamped = {
        "schemaVersion": PROBE_SCHEMA_VERSION,
        "generatedAt": int(time.time() * 1000),
        "source": source if isinstance(source, dict) else host_bridge_source_provenance(ROOT),
        "host": host_bridge_host_identity(),
        **result,
    }
    if parity_run_id:
        stamped["parityRunId"] = parity_run_id
    return stamped


def _hex64(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _source_preflight_result(
    expected_source_fingerprint: str | None,
    expected_source_manifest_sha256: str | None = None,
    expected_source_file_count: int | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    expected = str(expected_source_fingerprint or "").strip().lower()
    expected_manifest = str(expected_source_manifest_sha256 or "").strip().lower()
    if not expected and not expected_manifest and expected_source_file_count is None:
        return None
    source = source if isinstance(source, dict) else host_bridge_source_provenance(ROOT)
    actual = str(source.get("sourceFingerprint") or "").strip().lower()
    actual_manifest = str(source.get("sourceManifestSha256") or "").strip().lower()
    actual_file_count = source.get("sourceFileCount")
    findings: list[str] = []
    if expected and not _hex64(expected):
        findings.append(f"expected source fingerprint is invalid: {expected_source_fingerprint!r}")
    elif expected and actual != expected:
        findings.append(f"source fingerprint mismatch: current={actual}; expected={expected}")
    if expected_manifest and not _hex64(expected_manifest):
        findings.append(f"expected source manifest sha256 is invalid: {expected_source_manifest_sha256!r}")
    elif expected_manifest and actual_manifest != expected_manifest:
        findings.append(f"source manifest sha256 mismatch: current={actual_manifest}; expected={expected_manifest}")
    if expected_source_file_count is not None and actual_file_count != expected_source_file_count:
        findings.append(f"source file count mismatch: current={actual_file_count}; expected={expected_source_file_count}")
    if not findings:
        return None
    return {
        "ok": False,
        "mode": "source_preflight_failed",
        "error": "HostBridge source fingerprint preflight failed",
        "sourcePreflight": {
            "ok": False,
            "expectedSourceFingerprint": expected,
            "expectedSourceManifestSha256": expected_manifest,
            "expectedSourceFileCount": expected_source_file_count,
            "actualSourceFingerprint": actual,
            "actualSourceManifestSha256": actual_manifest,
            "actualSourceFileCount": actual_file_count,
            "findings": findings,
        },
    }


def _write_output(result: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _decode_process_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return _decode_process_bytes(value)
    return str(value)


def _decode_process_bytes(value: bytes) -> str:
    if not value:
        return ""
    encodings = ["utf-8-sig", "utf-8", locale.getpreferredencoding(False), "mac_roman"]
    seen: set[str] = set()
    for encoding in encodings:
        normalized = str(encoding or "").strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        try:
            return value.decode(normalized)
        except (LookupError, UnicodeDecodeError):
            continue
    return value.decode("utf-8", errors="replace")


def _run_process(command: list[str], *, timeout: float | None = 10.0, cwd: Path | None = None, **_: Any) -> dict[str, Any]:
    import subprocess

    try:
        completed = subprocess.run(command, cwd=cwd, text=False, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if exc.stdout is not None else getattr(exc, "output", None)
        return {
            "command": command,
            "cwd": str(cwd) if cwd else None,
            "returnCode": None,
            "timeout": True,
            "stdout": _decode_process_output(stdout),
            "stderr": _decode_process_output(exc.stderr) or f"command timed out after {timeout}s",
        }
    return {
        "command": command,
        "cwd": str(cwd) if cwd else None,
        "returnCode": completed.returncode,
        "stdout": _decode_process_output(completed.stdout),
        "stderr": _decode_process_output(completed.stderr),
    }


def _routes() -> dict[str, dict[str, Any]]:
    router = ExecutorRouter(build_default_tool_registry())
    return {tool: router.route(tool).to_dict() for tool in VISUAL_TOOLS}


def _route_for(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    router = ExecutorRouter(build_default_tool_registry())
    return router.route(tool, args).to_dict()


def _runtime_blocks() -> dict[str, dict[str, str | None]]:
    try:
        from app import chat_tools as chat_tools_module  # noqa: WPS433
        from app import main as main_module  # noqa: WPS433
    except Exception as exc:
        detail = f"runtime import failed: {type(exc).__name__}: {exc}"
        return {tool: {"api": detail, "chat": detail} for tool in VISUAL_TOOLS}

    blocks: dict[str, dict[str, str | None]] = {}
    for tool in VISUAL_TOOLS:
        args: dict[str, Any] = {}
        if tool in {"browser.snapshot", "browser.act"}:
            args["profile"] = "atrium"
        run = {"tool": tool, "args": args, "departmentId": "exec"}
        blocks[tool] = {
            "api": main_module._tool_runtime_block_reason(run),  # noqa: SLF001
            "chat": chat_tools_module._owner_runtime_block(run),  # noqa: SLF001
        }
    return blocks


def _runtime_block_for(tool: str, args: dict[str, Any]) -> dict[str, str | None]:
    try:
        from app import chat_tools as chat_tools_module  # noqa: WPS433
        from app import main as main_module  # noqa: WPS433
    except Exception as exc:
        detail = f"runtime import failed: {type(exc).__name__}: {exc}"
        return {"api": detail, "chat": detail}
    run = {"tool": tool, "args": args, "departmentId": "exec"}
    return {
        "api": main_module._tool_runtime_block_reason(run),  # noqa: SLF001
        "chat": chat_tools_module._owner_runtime_block(run),  # noqa: SLF001
    }


def _route_ok(routes: dict[str, dict[str, Any]]) -> bool:
    return all(not detail.get("blockReason") for detail in routes.values())


def _runtime_blocks_clear(blocks: dict[str, dict[str, str | None]]) -> bool:
    return all(not detail.get("api") and not detail.get("chat") for detail in blocks.values())


def _blocked_runtime_tools(blocks: dict[str, dict[str, str | None]], tools: set[str]) -> dict[str, dict[str, str | None]]:
    return {
        tool: detail
        for tool, detail in blocks.items()
        if tool in tools and (detail.get("api") or detail.get("chat"))
    }


def _commands_ok(group: dict[str, Any]) -> bool:
    for value in group.values():
        if not isinstance(value, dict):
            continue
        if value.get("degraded") is True:
            continue
        if value.get("ok") is False:
            return False
        if value.get("timeout") is True and value.get("returnCode") != 0:
            return False
        if "returnCode" in value and value.get("returnCode") != 0:
            return False
    return True


def _shell_probe(status: dict[str, Any], run_process: Any = _run_process) -> dict[str, Any]:
    shell = status.get("shellExecutable")
    if not shell:
        return {"returnCode": None, "containsExpected": False, "stderr": "HostBridge did not report a shell executable"}
    result = run_process([str(shell), "-lc", "printf ATRIUM_MACOS_SHELL_OK"], timeout=5.0)
    result["containsExpected"] = "ATRIUM_MACOS_SHELL_OK" in str(result.get("stdout") or "")
    return result


def _applescript_clipboard_probe(run_process: Any = _run_process) -> dict[str, Any]:
    script = "\n".join([
        f"set expectedText to {visual_bridge._applescript_string(MACOS_APPLESCRIPT_CLIPBOARD_TEXT)}",  # noqa: SLF001
        "set previousText to \"\"",
        "try",
        "  set previousText to the clipboard as text",
        "end try",
        "try",
        "  set the clipboard to expectedText",
        "  set clipboardText to the clipboard as text",
        "  set verifiedText to clipboardText is expectedText",
        "  set textLength to length of clipboardText",
        "  set the clipboard to previousText",
        "  if verifiedText then",
        "    return \"OK\" & tab & textLength & tab & clipboardText",
        "  end if",
        "  return \"MISMATCH\" & tab & textLength & tab & clipboardText",
        "on error errMsg number errNum",
        "  try",
        "    set the clipboard to previousText",
        "  end try",
        "  return \"FAIL\" & tab & errNum & tab & errMsg",
        "end try",
    ])
    result = run_process(["osascript", "-e", script], timeout=5.0)
    raw_stdout = str(result.get("stdout") or "").strip()
    parts = raw_stdout.split("\t", 2)
    status = parts[0].strip().upper() if parts and parts[0].strip() else ""
    text_length: int | None = None
    if len(parts) > 1:
        try:
            text_length = int(parts[1])
        except ValueError:
            text_length = None
    result.update({
        "method": "osascript",
        "expected": MACOS_APPLESCRIPT_CLIPBOARD_TEXT,
        "expectedLength": len(MACOS_APPLESCRIPT_CLIPBOARD_TEXT),
        "expectedBytes": len(MACOS_APPLESCRIPT_CLIPBOARD_TEXT.encode("utf-8")),
        "verifyStatus": status or None,
        "verified": result.get("returnCode") == 0 and status == "OK",
        "textLength": text_length,
        "textPreview": parts[2].strip() if len(parts) > 2 else "",
    })
    return result


def _png_file_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "path": str(path), "exists": False}
    data = path.read_bytes()
    width, height = visual_bridge._png_dimensions(data)  # noqa: SLF001
    return {
        "ok": bool(width and height and width > 0 and height > 0),
        "path": str(path),
        "exists": True,
        "bytes": len(data),
        "width": width,
        "height": height,
    }


def _first_desktop_press_ref(snapshot_result: dict[str, Any]) -> str | None:
    snapshot = snapshot_result.get("snapshot") if isinstance(snapshot_result.get("snapshot"), dict) else {}
    elements = snapshot.get("elements") if isinstance(snapshot.get("elements"), list) else []
    preferred_names = {"1", "one", "หนึ่ง"}
    role_match_ref: str | None = None
    for element in elements:
        if not isinstance(element, dict):
            continue
        ref = str(element.get("ref") or "").strip()
        if not ref:
            continue
        role = str(element.get("role") or "").strip().lower()
        name = str(element.get("name") or "").strip().lower()
        if role in {"axbutton", "button"} and name in preferred_names:
            native_actions = {str(item).strip().lower() for item in element.get("nativeSupportedActions") or []}
            if "click" in native_actions:
                return ref
            if role_match_ref is None:
                role_match_ref = ref
    return role_match_ref


def _running_app_names(apps_result: dict[str, Any]) -> set[str]:
    rows = apps_result.get("running")
    if not isinstance(rows, list):
        return set()
    names: set[str] = set()
    for row in rows:
        if isinstance(row, str):
            names.add(row.strip().lower())
        elif isinstance(row, dict):
            names.add(str(row.get("name") or "").strip().lower())
    return {name for name in names if name}


def _running_app_process_ids(apps_result: dict[str, Any], app_name: str) -> set[int]:
    rows = apps_result.get("running")
    if not isinstance(rows, list):
        return set()
    needle = app_name.strip().lower()
    process_ids: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip().lower()
        if name != needle:
            continue
        raw_pid = row.get("processId")
        try:
            pid = int(raw_pid)
        except (TypeError, ValueError):
            continue
        if pid > 0:
            process_ids.add(pid)
    return process_ids


def _first_desktop_text_ref(snapshot_result: dict[str, Any]) -> str | None:
    snapshot = snapshot_result.get("snapshot") if isinstance(snapshot_result.get("snapshot"), dict) else {}
    elements = snapshot.get("elements") if isinstance(snapshot.get("elements"), list) else []
    role_match_ref: str | None = None
    for element in elements:
        if not isinstance(element, dict):
            continue
        ref = str(element.get("ref") or "").strip()
        if not ref:
            continue
        role = str(element.get("role") or "").strip().lower()
        native_actions = element.get("nativeSupportedActions") if isinstance(element.get("nativeSupportedActions"), list) else []
        supported_actions = element.get("supportedActions") if isinstance(element.get("supportedActions"), list) else []
        normalized_native = {str(item).strip().lower() for item in native_actions}
        if "paste" in normalized_native or "type" in normalized_native:
            return ref
        if role in {"axtextarea", "axtextfield", "axcombobox"}:
            if role_match_ref is None:
                role_match_ref = ref
        if "setValue" in supported_actions and role not in {"axmenu", "axmenubar", "axmenubaritem", "axmenuitem"}:
            return ref
    return role_match_ref


def _first_desktop_scroll_ref(snapshot_result: dict[str, Any]) -> str | None:
    snapshot = snapshot_result.get("snapshot") if isinstance(snapshot_result.get("snapshot"), dict) else {}
    elements = snapshot.get("elements") if isinstance(snapshot.get("elements"), list) else []
    fallback_ref: str | None = None
    for element in elements:
        if not isinstance(element, dict):
            continue
        ref = str(element.get("ref") or "").strip()
        if not ref:
            continue
        native_actions = {str(item).strip().lower() for item in element.get("nativeSupportedActions") or []}
        ax_actions = {str(item).strip().lower() for item in element.get("axActions") or []}
        if "scroll" in native_actions and "axscrolldownbypage" in ax_actions:
            return ref
        if "scroll" in native_actions and fallback_ref is None:
            fallback_ref = ref
    return fallback_ref


def _snapshot_element_by_ref(snapshot_result: dict[str, Any], ref: str | None) -> dict[str, Any]:
    snapshot = snapshot_result.get("snapshot") if isinstance(snapshot_result.get("snapshot"), dict) else {}
    elements = snapshot.get("elements") if isinstance(snapshot.get("elements"), list) else []
    for element in elements:
        if isinstance(element, dict) and str(element.get("ref") or "").strip() == str(ref or "").strip():
            return element
    return {}


def _macos_native_ref_metadata_verified(
    snapshot_result: dict[str, Any],
    ref: str | None,
    *,
    native_action: str | None = None,
    supported_action: str | None = None,
    settable_attribute: str | None = None,
) -> bool:
    element = _snapshot_element_by_ref(snapshot_result, ref)
    if not element:
        return False
    native_supported = {str(item).strip().lower() for item in element.get("nativeSupportedActions") or []}
    ax_actions = {str(item).strip().lower() for item in element.get("axActions") or []}
    settable = {str(item).strip().lower() for item in element.get("settableAttributes") or []}
    if supported_action and supported_action.strip().lower() not in native_supported:
        return False
    if native_action and native_action.strip().lower() not in ax_actions:
        return False
    if settable_attribute and settable_attribute.strip().lower() not in settable:
        return False
    return True


def _snapshot_contains_text_value(snapshot_result: dict[str, Any], expected_text: str) -> bool:
    snapshot = snapshot_result.get("snapshot") if isinstance(snapshot_result.get("snapshot"), dict) else {}
    elements = snapshot.get("elements") if isinstance(snapshot.get("elements"), list) else []
    for element in elements:
        if not isinstance(element, dict):
            continue
        value = str(element.get("value") or "")
        if expected_text in value:
            return True
    return False


def _native_action_verified(result: dict[str, Any], expected_action: str | set[str]) -> bool:
    expected_actions = {expected_action} if isinstance(expected_action, str) else expected_action
    native_attempt = result.get("nativeAttempt") if isinstance(result.get("nativeAttempt"), dict) else {}
    return (
        result.get("returnCode") == 0
        and result.get("usedNativeAction") is True
        and native_attempt.get("returnCode") == 0
        and native_attempt.get("nativeStatus") == "OK"
        and native_attempt.get("nativeAction") in expected_actions
    )


def _snapshot_has_window(snapshot_result: dict[str, Any]) -> bool:
    snapshot = snapshot_result.get("snapshot") if isinstance(snapshot_result.get("snapshot"), dict) else {}
    return isinstance(snapshot.get("window"), dict)


def _foreground_limited_snapshot(snapshot_result: dict[str, Any]) -> bool:
    if snapshot_result.get("timeout") is True:
        return True
    return snapshot_result.get("returnCode") == 0 and snapshot_result.get("refCount", 0) > 0 and not _snapshot_has_window(snapshot_result)


def _browser_snapshot_act_probe(profile: str, run_process: Any = _run_process) -> dict[str, Any]:
    html = (
        "<!doctype html><meta charset='utf-8'><title>ATRIUM Browser Probe</title>"
        "<button id='atrium-probe' onclick=\"this.textContent='ATRIUM clicked';"
        "document.body.dataset.atrium='clicked'\">ATRIUM browser probe</button>"
    )
    url = "data:text/html," + urllib.parse.quote(html)
    checks: dict[str, Any] = {"url": url, "profile": profile}
    snapshot_args = {
        "url": url,
        "profile": profile,
        "headless": True,
        "maxElements": 20,
        "timeoutMs": 45_000,
    }
    checks["browserSnapshotRoute"] = _route_for("browser.snapshot", snapshot_args)
    checks["browserSnapshotRuntimeBlocks"] = _runtime_block_for("browser.snapshot", snapshot_args)
    if checks["browserSnapshotRoute"].get("blockReason") or checks["browserSnapshotRuntimeBlocks"].get("api") or checks["browserSnapshotRuntimeBlocks"].get("chat"):
        checks["browserSnapshot"] = {"returnCode": None, "skipped": True, "stderr": "browser.snapshot blocked by runtime readiness"}
        checks["error"] = "browser.snapshot blocked by runtime readiness"
        return checks
    checks["browserSnapshot"] = visual_bridge.execute_browser_snapshot(snapshot_args, run_process)
    if checks["browserSnapshot"].get("returnCode") != 0 or checks["browserSnapshot"].get("refCount", 0) <= 0:
        checks["error"] = "browser.snapshot did not expose DOM refs"
        return checks
    elements = ((checks["browserSnapshot"].get("snapshot") or {}).get("elements") or [])
    button_ref = None
    for element in elements:
        if not isinstance(element, dict):
            continue
        if str(element.get("role") or "").lower() == "button" and "atrium browser probe" in str(element.get("name") or "").lower():
            button_ref = element.get("ref")
            break
    if not button_ref:
        checks["error"] = "browser.snapshot did not expose the probe button ref"
        return checks
    checks["buttonRef"] = button_ref
    act_args = {
        "ref": button_ref,
        "action": "click",
        "url": url,
        "profile": profile,
        "headless": True,
        "maxElements": 20,
        "timeoutMs": 45_000,
        "waitAfterMs": 100,
    }
    checks["browserActRoute"] = _route_for("browser.act", act_args)
    checks["browserActRuntimeBlocks"] = _runtime_block_for("browser.act", act_args)
    if checks["browserActRoute"].get("blockReason") or checks["browserActRuntimeBlocks"].get("api") or checks["browserActRuntimeBlocks"].get("chat"):
        checks["browserActClick"] = {"returnCode": None, "skipped": True, "stderr": "browser.act blocked by runtime readiness"}
        checks["error"] = "browser.act blocked by runtime readiness"
        return checks
    checks["browserActClick"] = visual_bridge.execute_browser_act(act_args, run_process)
    if checks["browserActClick"].get("returnCode") != 0:
        checks["error"] = "browser.act failed to click the probe button ref"
        return checks
    after_elements = ((checks["browserActClick"].get("snapshot") or {}).get("elements") or [])
    checks["containsExpected"] = any(
        isinstance(element, dict) and "atrium clicked" in str(element.get("name") or "").lower()
        for element in after_elements
    )
    if checks["containsExpected"] is not True:
        checks["error"] = "browser.act did not produce the expected post-click DOM snapshot"
    return checks


def _interactive_calculator_probe(run_process: Any = _run_process) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    app_name = "Calculator"
    should_quit = False
    process_id: int | None = None
    try:
        checks["appsBefore"] = visual_bridge.execute_list_apps({"includeRunning": True, "includeInstalled": False, "limit": 80}, run_process)
        was_running = app_name.lower() in _running_app_names(checks["appsBefore"])
        before_pids = _running_app_process_ids(checks["appsBefore"], app_name)
        checks["openApp"] = visual_bridge.execute_open_app({"appName": app_name}, run_process)
        if checks["openApp"].get("returnCode") != 0:
            checks["error"] = "desktop.open_app failed for Calculator"
            return checks
        should_quit = not was_running
        for process_attempt in range(10):
            checks["appsAfterOpen"] = visual_bridge.execute_list_apps(
                {"includeRunning": True, "includeInstalled": False, "query": app_name, "limit": 80},
                run_process,
            )
            after_pids = _running_app_process_ids(checks["appsAfterOpen"], app_name)
            new_pids = sorted(after_pids - before_pids)
            if new_pids:
                process_id = new_pids[0]
                break
            if after_pids:
                process_id = sorted(after_pids)[0]
                break
            time.sleep(0.2)
        checks["processId"] = process_id
        checks["processLookupAttempts"] = process_attempt + 1
        target_args = {"appName": app_name, "processId": process_id} if process_id else {"appName": app_name}
        for attempt in range(3):
            checks["activateApp"] = visual_bridge.execute_activate_app(target_args, run_process)
            if checks["activateApp"].get("returnCode") == 0 and checks["activateApp"].get("timeout") is not True and checks["activateApp"].get("ok") is not False:
                break
            time.sleep(0.3)
        checks["activateAttempts"] = attempt + 1
        if checks["activateApp"].get("returnCode") != 0 or checks["activateApp"].get("timeout") is True or checks["activateApp"].get("ok") is False:
            checks["activationDegraded"] = True
            checks["activationWarning"] = "desktop.activate_app did not make Calculator foreground; continuing native Accessibility proof by explicit app target"
            checks["activateApp"]["degraded"] = True
        else:
            time.sleep(0.2)
        press_ref: str | None = None
        for snapshot_attempt in range(4):
            checks["desktopSnapshot"] = visual_bridge.execute_desktop_snapshot({**target_args, "maxElements": 200, "maxDepth": 8}, run_process)
            press_ref = _first_desktop_press_ref(checks["desktopSnapshot"])
            if checks["desktopSnapshot"].get("returnCode") == 0 and checks["desktopSnapshot"].get("refCount", 0) > 0 and press_ref:
                break
            if checks.get("activationDegraded") and _foreground_limited_snapshot(checks["desktopSnapshot"]):
                checks["foregroundControlLimited"] = True
                checks["foregroundControlWarning"] = "macOS did not foreground Calculator; snapshot exposed no app window/control refs"
                break
            time.sleep(0.5)
        checks["snapshotAttempts"] = snapshot_attempt + 1
        if checks["desktopSnapshot"].get("returnCode") != 0 or checks["desktopSnapshot"].get("refCount", 0) <= 0:
            checks["error"] = (
                "macOS foreground-control limitation prevented Calculator window refs"
                if checks.get("foregroundControlLimited")
                else "desktop.snapshot did not expose Calculator Accessibility refs"
            )
            return checks
        if not press_ref:
            checks["error"] = (
                "macOS foreground-control limitation prevented Calculator digit refs"
                if checks.get("foregroundControlLimited")
                else "desktop.snapshot did not expose the Calculator digit 1 ref for desktop.act"
            )
            return checks
        checks["nativeActionMetadataVerified"] = _macos_native_ref_metadata_verified(
            checks["desktopSnapshot"],
            press_ref,
            native_action="AXPress",
            supported_action="click",
        )
        checks["desktopActClick"] = visual_bridge.execute_desktop_act(
            {
                "ref": press_ref,
                "action": "click",
                "requireNative": True,
                "macosUseAxHelper": True,
                "snapshotAfter": True,
                "maxElements": 200,
                "maxDepth": 8,
                "waitAfterMs": 100,
            },
            run_process,
        )
        if checks["desktopActClick"].get("returnCode") != 0:
            checks["error"] = "desktop.act failed to press a Calculator button through Accessibility"
            return checks
        checks["nativeActionVerified"] = _native_action_verified(checks["desktopActClick"], "AXPress")
        if checks["nativeActionVerified"] is not True:
            checks["error"] = "desktop.act did not prove Calculator AXPress native Accessibility action"
            return checks
        checks["desktopSnapshotAfter"] = checks["desktopActClick"].get("after") if isinstance(checks["desktopActClick"].get("after"), dict) else {}
        checks["displayValueVerified"] = _snapshot_contains_text_value(checks["desktopSnapshotAfter"], MACOS_CALCULATOR_EXPECTED_VALUE)
        if checks["desktopSnapshotAfter"].get("returnCode") != 0 or checks["displayValueVerified"] is not True:
            checks["error"] = "desktop.snapshot did not confirm Calculator display value after desktop.act"
        return checks
    finally:
        if should_quit:
            quit_target: dict[str, Any] = {
                "appName": app_name,
                "force": True,
                "forceDelaySeconds": 0.2,
            }
            for source_name in ("activateApp", "desktopSnapshot", "openApp"):
                source = checks.get(source_name)
                if isinstance(source, dict) and source.get("processId"):
                    quit_target["processId"] = source["processId"]
                    break
            checks["quitApp"] = visual_bridge.execute_quit_app(
                quit_target,
                run_process,
            )


def _interactive_textedit_probe(run_process: Any = _run_process) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    app_name = "TextEdit"
    process_id: int | None = None
    expected_text = MACOS_TEXTEDIT_EXPECTED_TEXT
    probe_file: Path | None = None
    try:
        checks["appsBefore"] = visual_bridge.execute_list_apps(
            {"includeRunning": True, "includeInstalled": False, "query": app_name, "limit": 80},
            run_process,
        )
        before_pids = _running_app_process_ids(checks["appsBefore"], app_name)
        if before_pids:
            checks["error"] = "TextEdit is already running; refusing ambiguous non-destructive text probe"
            checks["preexistingProcessIds"] = sorted(before_pids)
            return checks
        probe_dir = get_settings().data_dir
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe_file = (probe_dir / f"macos-hostbridge-textedit-probe-{time.time_ns()}.txt").resolve()
        scroll_fixture = "\n".join(
            f"ATRIUM macOS HostBridge scroll fixture line {line_number:03d}"
            for line_number in range(1, 121)
        )
        probe_file.write_text(scroll_fixture, encoding="utf-8")
        checks["probeFile"] = str(probe_file)
        checks["openApp"] = visual_bridge.execute_open_app(
            {"appName": app_name, "target": str(probe_file)},
            run_process,
        )
        if checks["openApp"].get("returnCode") != 0:
            checks["error"] = "desktop.open_app failed for TextEdit temp-file text probe"
            return checks
        for attempt in range(10):
            checks["appsAfterOpen"] = visual_bridge.execute_list_apps(
                {"includeRunning": True, "includeInstalled": False, "query": app_name, "limit": 80},
                run_process,
            )
            after_pids = _running_app_process_ids(checks["appsAfterOpen"], app_name)
            new_pids = sorted(after_pids - before_pids)
            if new_pids:
                process_id = new_pids[0]
                break
            if not before_pids and after_pids:
                process_id = sorted(after_pids)[0]
                break
            time.sleep(0.3)
        checks["processId"] = process_id
        checks["activateAttempts"] = attempt + 1
        if not process_id:
            checks["error"] = "TextEdit processId could not be isolated; refusing broad text input"
            return checks
        target = {"appName": app_name, "processId": process_id}
        for activation_attempt in range(3):
            checks["activateApp"] = visual_bridge.execute_activate_app(target, run_process)
            if checks["activateApp"].get("returnCode") == 0 and checks["activateApp"].get("timeout") is not True and checks["activateApp"].get("ok") is not False:
                break
            time.sleep(0.3)
        checks["activationAttempts"] = activation_attempt + 1
        if checks["activateApp"].get("returnCode") != 0 or checks["activateApp"].get("timeout") is True or checks["activateApp"].get("ok") is False:
            checks["activationDegraded"] = True
            checks["activationWarning"] = "desktop.activate_app did not make TextEdit foreground; continuing native Accessibility proof by explicit processId"
            checks["activateApp"]["degraded"] = True
        else:
            time.sleep(0.2)
        text_ref: str | None = None
        for snapshot_attempt in range(4):
            checks["desktopSnapshot"] = visual_bridge.execute_desktop_snapshot(
                {"processId": process_id, "maxElements": 80, "maxDepth": 3, "timeoutSeconds": 5},
                run_process,
            )
            text_ref = _first_desktop_text_ref(checks["desktopSnapshot"])
            if checks["desktopSnapshot"].get("returnCode") == 0 and checks["desktopSnapshot"].get("refCount", 0) > 0 and text_ref:
                break
            if checks.get("activationDegraded") and _foreground_limited_snapshot(checks["desktopSnapshot"]):
                checks["foregroundControlLimited"] = True
                checks["foregroundControlWarning"] = "macOS did not foreground TextEdit; snapshot exposed no document window/text refs"
                break
            time.sleep(0.5)
        checks["snapshotAttempts"] = snapshot_attempt + 1
        if checks["desktopSnapshot"].get("returnCode") != 0 or checks["desktopSnapshot"].get("refCount", 0) <= 0:
            checks["error"] = (
                "macOS foreground-control limitation prevented TextEdit window refs"
                if checks.get("foregroundControlLimited")
                else "desktop.snapshot did not expose TextEdit Accessibility refs"
            )
            return checks
        if not text_ref:
            checks["error"] = (
                "macOS foreground-control limitation prevented TextEdit text refs"
                if checks.get("foregroundControlLimited")
                else "desktop.snapshot did not expose a TextEdit text ref for desktop.act"
            )
            return checks
        scroll_ref = _first_desktop_scroll_ref(checks["desktopSnapshot"])
        if not scroll_ref:
            checks["error"] = "desktop.snapshot did not expose a TextEdit native scroll ref for desktop.act"
            return checks
        checks["nativeActionMetadataVerified"] = _macos_native_ref_metadata_verified(
            checks["desktopSnapshot"],
            text_ref,
            supported_action="paste",
            settable_attribute="AXValue",
        )
        checks["nativeScrollMetadataVerified"] = _macos_native_ref_metadata_verified(
            checks["desktopSnapshot"],
            scroll_ref,
            native_action="AXScrollDownByPage",
            supported_action="scroll",
        )
        checks["desktopActScroll"] = visual_bridge.execute_desktop_act(
            {
                "ref": scroll_ref,
                "action": "scroll",
                "direction": "down",
                "unit": "page",
                "amount": 1,
                "requireNative": True,
                "macosUseAxHelper": True,
                "snapshotAfter": False,
                "waitAfterMs": 100,
            },
            run_process,
        )
        if checks["desktopActScroll"].get("returnCode") != 0:
            checks["error"] = "desktop.act failed to scroll TextEdit through Accessibility"
            return checks
        checks["nativeScrollVerified"] = _native_action_verified(
            checks["desktopActScroll"],
            MACOS_TEXTEDIT_NATIVE_SCROLL_ACTIONS,
        )
        if checks["nativeScrollVerified"] is not True:
            checks["error"] = "desktop.act did not prove TextEdit AXScrollDownByPage native Accessibility action"
            return checks
        checks["desktopSnapshotBeforeSetText"] = visual_bridge.execute_desktop_snapshot(
            {"processId": process_id, "maxElements": 80, "maxDepth": 3, "timeoutSeconds": 5},
            run_process,
        )
        refreshed_text_ref = _first_desktop_text_ref(checks["desktopSnapshotBeforeSetText"])
        if checks["desktopSnapshotBeforeSetText"].get("returnCode") != 0 or not refreshed_text_ref:
            checks["error"] = "desktop.snapshot did not expose a fresh TextEdit text ref after native scroll"
            return checks
        checks["nativeActionMetadataVerified"] = _macos_native_ref_metadata_verified(
            checks["desktopSnapshotBeforeSetText"],
            refreshed_text_ref,
            supported_action="paste",
            settable_attribute="AXValue",
        )
        if checks["nativeActionMetadataVerified"] is not True:
            checks["error"] = "desktop.snapshot did not prove fresh TextEdit setValue metadata after native scroll"
            return checks
        checks["refreshedTextRefAfterScroll"] = refreshed_text_ref
        checks["desktopActSetText"] = visual_bridge.execute_desktop_act(
            {
                "ref": refreshed_text_ref,
                "action": "paste",
                "text": expected_text,
                "requireNative": True,
                "macosUseAxHelper": True,
                "snapshotAfter": False,
                "waitAfterMs": 100,
            },
            run_process,
        )
        if checks["desktopActSetText"].get("returnCode") != 0:
            checks["error"] = "desktop.act failed to set TextEdit text through Accessibility"
            return checks
        checks["nativeActionVerified"] = _native_action_verified(checks["desktopActSetText"], "setValue")
        if checks["nativeActionVerified"] is not True:
            checks["error"] = "desktop.act did not prove TextEdit setValue native Accessibility action"
            return checks
        checks["desktopSnapshotAfter"] = visual_bridge.execute_desktop_snapshot(
            {"processId": process_id, "maxElements": 80, "maxDepth": 3, "timeoutSeconds": 5},
            run_process,
        )
        checks["textValueVerified"] = _snapshot_contains_text_value(checks["desktopSnapshotAfter"], expected_text)
        if checks["desktopSnapshotAfter"].get("returnCode") != 0 or checks["textValueVerified"] is not True:
            checks["error"] = "desktop.snapshot did not confirm TextEdit text value after desktop.act"
        return checks
    finally:
        if process_id:
            checks["quitApp"] = visual_bridge.execute_quit_app(
                {"processId": process_id, "force": True, "forceDelaySeconds": 0.2},
                run_process,
            )
        if probe_file:
            try:
                probe_file.unlink(missing_ok=True)
                checks["probeFileRemoved"] = True
            except OSError as exc:
                checks["probeFileRemoved"] = False
                checks["probeFileRemoveError"] = f"{type(exc).__name__}: {exc}"


def _fake_png(width: int = 100, height: int = 80) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + int(width).to_bytes(4, "big")
        + int(height).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )


def _simulate() -> dict[str, Any]:
    original_platform = sys.platform
    calls: list[list[str]] = []
    fake_textedit_pid = 456
    fake_textedit_text = ""
    fake_textedit_running = False
    fake_calculator_display = "0"

    def fake_which(name: str) -> str | None:
        if name in {"bash", "zsh", "sh", "osascript", "open", "screencapture", "pbcopy", "node", "git", "docker"}:
            return f"/usr/bin/{name}"
        return None

    def fake_run(command: list[str], **_: Any) -> dict[str, Any]:
        nonlocal fake_textedit_running, fake_textedit_text, fake_calculator_display
        calls.append(command)
        joined = " ".join(str(part) for part in command)
        if command and Path(str(command[0])).name == "node" and len(command) >= 3:
            try:
                payload = json.loads(str(command[2]))
            except json.JSONDecodeError:
                payload = {}
            mode = payload.get("mode")
            if mode == "snapshot":
                stdout = json.dumps(
                    {
                        "returnCode": 0,
                        "ok": True,
                        "backend": "playwright",
                        "profile": payload.get("profile") or "atrium",
                        "profileKind": "isolated",
                        "url": payload.get("url"),
                        "title": "ATRIUM Browser Probe",
                        "refCount": 1,
                        "snapshot": {
                            "elements": [
                                {
                                    "ref": "b1",
                                    "role": "button",
                                    "name": "ATRIUM browser probe",
                                    "selector": "#atrium-probe",
                                    "tag": "button",
                                    "enabled": True,
                                }
                            ]
                        },
                    }
                )
                return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}
            if mode == "act":
                stdout = json.dumps(
                    {
                        "returnCode": 0,
                        "ok": True,
                        "backend": "playwright",
                        "profile": payload.get("profile") or "atrium",
                        "profileKind": "isolated",
                        "url": payload.get("url"),
                        "title": "ATRIUM Browser Probe",
                        "refCount": 1,
                        "action": {"action": payload.get("action"), "ref": payload.get("ref"), "selector": "#atrium-probe"},
                        "snapshot": {
                            "elements": [
                                {
                                    "ref": "b1",
                                    "role": "button",
                                    "name": "ATRIUM clicked",
                                    "selector": "#atrium-probe",
                                    "tag": "button",
                                    "enabled": True,
                                }
                            ]
                        },
                    }
                )
                return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}
        if command and Path(str(command[0])).name == "macos_snapshot":
            target_pid = str(command[1]) if len(command) > 1 else ""
            target_name = str(command[2]) if len(command) > 2 else ""
            if target_pid == str(fake_textedit_pid) or "TextEdit" in target_name:
                stdout = json.dumps(
                    {
                        "ok": True,
                        "appName": "TextEdit",
                        "processId": fake_textedit_pid,
                        "title": "macos-hostbridge-textedit-probe.txt",
                        "window": {"x": 40, "y": 60, "width": 640, "height": 480},
                        "windowCount": 1,
                        "elements": [
                            {
                                "path": "w1",
                                "role": "AXWindow",
                                "name": "macos-hostbridge-textedit-probe.txt",
                                "enabled": True,
                                "x": 40,
                                "y": 60,
                                "width": 640,
                                "height": 480,
                                "axActions": [],
                                "settableAttributes": [],
                                "children": 1,
                            },
                            {
                                "path": "w1.1",
                                "role": "AXScrollArea",
                                "name": "Document",
                                "enabled": True,
                                "x": 60,
                                "y": 100,
                                "width": 600,
                                "height": 360,
                                "axActions": ["AXScrollDownByPage", "AXScrollUpByPage"],
                                "settableAttributes": ["AXFocused"],
                                "children": 1,
                            },
                            {
                                "path": "w1.1.1",
                                "role": "AXTextArea",
                                "name": "Text Area",
                                "value": fake_textedit_text,
                                "enabled": True,
                                "x": 60,
                                "y": 100,
                                "width": 600,
                                "height": 360,
                                "axActions": [],
                                "settableAttributes": ["AXFocused", "AXValue"],
                                "children": 0,
                            },
                        ],
                    }
                )
            else:
                stdout = json.dumps(
                    {
                        "ok": True,
                        "appName": "Calculator",
                        "processId": 123,
                        "title": "Calculator",
                        "window": {"x": 20, "y": 40, "width": 360, "height": 520},
                        "windowCount": 1,
                        "elements": [
                            {
                                "path": "w1",
                                "role": "AXWindow",
                                "name": "Calculator",
                                "enabled": True,
                                "x": 20,
                                "y": 40,
                                "width": 360,
                                "height": 520,
                                "axActions": [],
                                "settableAttributes": [],
                                "children": 2,
                            },
                            {
                                "path": "w1.1",
                                "role": "AXStaticText",
                                "name": "Display",
                                "value": fake_calculator_display,
                                "enabled": True,
                                "x": 40,
                                "y": 90,
                                "width": 300,
                                "height": 80,
                                "axActions": [],
                                "settableAttributes": [],
                                "children": 0,
                            },
                            {
                                "path": "w1.2",
                                "role": "AXButton",
                                "name": "1",
                                "enabled": True,
                                "x": 60,
                                "y": 420,
                                "width": 60,
                                "height": 50,
                                "axActions": ["AXPress"],
                                "settableAttributes": [],
                                "children": 0,
                            },
                        ],
                    }
                )
            return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}
        if command and Path(str(command[0])).name == "macos_ax_action":
            action = str(command[4]) if len(command) > 4 else ""
            if action == "click":
                fake_calculator_display = MACOS_CALCULATOR_EXPECTED_VALUE
                stdout = json.dumps(
                    {
                        "ok": True,
                        "nativeAction": "AXPress",
                        "inputMethod": "accessibility",
                        "path": command[3] if len(command) > 3 else "",
                        "action": action,
                        "name": "1",
                        "role": "AXButton",
                    }
                )
                return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}
            if action in {"paste", "type"}:
                fake_textedit_text = str(command[5]) if len(command) > 5 else ""
                stdout = json.dumps(
                    {
                        "ok": True,
                        "nativeAction": "setValue",
                        "inputMethod": "accessibility",
                        "path": command[3] if len(command) > 3 else "",
                        "action": action,
                        "name": "ATRIUM macOS HostBridge probe",
                        "role": "AXTextArea",
                    }
                )
                return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}
            if action == "scroll":
                stdout = json.dumps(
                    {
                        "ok": True,
                        "nativeAction": "AXScrollDownByPage",
                        "inputMethod": "accessibility",
                        "path": command[3] if len(command) > 3 else "",
                        "action": action,
                        "direction": "down",
                        "unit": "page",
                        "amount": 1,
                        "performed": 1,
                        "name": "Document",
                        "role": "AXScrollArea",
                    }
                )
                return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}
        if "ATRIUM_MACOS_SHELL_OK" in joined:
            return {"command": command, "returnCode": 0, "stdout": "ATRIUM_MACOS_SHELL_OK", "stderr": ""}
        if command[:2] == ["screencapture", "-x"]:
            Path(command[-1]).write_bytes(_fake_png(1440, 900))
            return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
        if command[:2] == ["/bin/kill", "-TERM"] and str(fake_textedit_pid) in command:
            fake_textedit_running = False
            return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
        if command[:2] == ["/bin/kill", "-0"] and str(fake_textedit_pid) in command:
            return {"command": command, "returnCode": 1 if not fake_textedit_running else 0, "stdout": "", "stderr": ""}
        if command and command[0] == "open":
            if "TextEdit" in command:
                fake_textedit_running = True
                fake_textedit_text = "ATRIUM macOS HostBridge probe"
            return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
        if command and command[0] == "osascript":
            script = str(command[-1])
            if MACOS_APPLESCRIPT_CLIPBOARD_TEXT in script:
                return {
                    "command": command,
                    "returnCode": 0,
                    "stdout": f"OK\t{len(MACOS_APPLESCRIPT_CLIPBOARD_TEXT)}\t{MACOS_APPLESCRIPT_CLIPBOARD_TEXT}",
                    "stderr": "",
                }
            if "display notification" in script:
                return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
            if "make new document" in script and "TextEdit" in script:
                fake_textedit_text = "ATRIUM macOS HostBridge probe"
                return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
            if "application processes whose background only is false" in script:
                rows = [
                    "ROW\tCodex\t764\tCodex\tcom.openai.codex\ttrue",
                    "ROW\tCalculator\t123\tCalculator\tcom.apple.calculator\tfalse",
                ]
                if fake_textedit_running:
                    rows.append(f"ROW\tTextEdit\t{fake_textedit_pid}\tmacos-hostbridge-textedit-probe.txt\tcom.apple.TextEdit\tfalse")
                stdout = "\n".join(rows)
                return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}
            if "FOREGROUND" in script:
                if str(fake_textedit_pid) in script or "TextEdit" in script:
                    return {
                        "command": command,
                        "returnCode": 0,
                        "stdout": f"FOREGROUND\ttrue\tTextEdit\t{fake_textedit_pid}\tTextEdit\t{fake_textedit_pid}",
                        "stderr": "",
                    }
                return {
                    "command": command,
                    "returnCode": 0,
                    "stdout": "FOREGROUND\ttrue\tCalculator\t123\tCalculator\t123",
                    "stderr": "",
                }
            if " to activate" in script or " to quit" in script:
                return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}
            if "appendElement" in script:
                if str(fake_textedit_pid) in script or "TextEdit" in script:
                    stdout = "\n".join(
                        [
                            "META\tappName\tTextEdit",
                            f"META\tprocessId\t{fake_textedit_pid}",
                            "META\ttitle\tmacos-hostbridge-textedit-probe.txt",
                            "META\twindowX\t40",
                            "META\twindowY\t60",
                            "META\twindowWidth\t640",
                            "META\twindowHeight\t480",
                            "ROW\tw1\tAXWindow\t\tmacos-hostbridge-textedit-probe.txt\t\t\ttrue\t40\t60\t640\t480\t1",
                            f"ROW\tw1.1\tAXTextArea\t\tText Area\t\t{fake_textedit_text}\ttrue\t60\t100\t600\t360\t0",
                        ]
                    )
                else:
                    stdout = "\n".join(
                        [
                            "META\tappName\tCalculator",
                            "META\tprocessId\t123",
                            "META\ttitle\tCalculator",
                            "META\twindowX\t20",
                            "META\twindowY\t40",
                            "META\twindowWidth\t360",
                            "META\twindowHeight\t520",
                            "ROW\tw1\tAXWindow\t\tCalculator\t\t\ttrue\t20\t40\t360\t520\t2",
                            f"ROW\tw1.1\tAXStaticText\t\tDisplay\t\t{fake_calculator_display}\ttrue\t40\t90\t300\t80\t0",
                            "ROW\tw1.2\tAXButton\t\t1\t\t\ttrue\t60\t420\t60\t50\t0",
                        ]
                    )
                return {"command": command, "returnCode": 0, "stdout": stdout, "stderr": ""}
            if "set value of targetElement to textValue" in script and str(fake_textedit_pid) in script:
                fake_textedit_text = "ATRIUM macOS TextEdit probe ไทย"
                return {"command": command, "returnCode": 0, "stdout": "OK\tsetValue", "stderr": ""}
            if 'perform action "AXPress"' in script:
                fake_calculator_display = MACOS_CALCULATOR_EXPECTED_VALUE
                return {"command": command, "returnCode": 0, "stdout": "OK\tAXPress", "stderr": ""}
        return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}

    try:
        sys.platform = "darwin"
        with (
            patch.object(host_bridge_module.shutil, "which", fake_which),
            patch.object(host_bridge_module, "_macos_accessibility_enabled", return_value=True),
            patch.object(
                host_bridge_module,
                "_macos_foreground_session_status",
                return_value={
                    "checked": True,
                    "ok": True,
                    "error": None,
                    "details": {"appName": "Codex", "processId": 764, "windowCount": 1},
                },
            ),
            patch.object(
                host_bridge_module,
                "_browser_playwright_package_status",
                return_value={"ok": True, "package": "@playwright/test", "path": "/mock/@playwright/test", "error": None},
            ),
            patch.object(visual_bridge.shutil, "which", fake_which),
            patch.object(visual_bridge, "_ensure_activate_helper", return_value=None),
            patch.object(visual_bridge, "_ensure_apps_helper", return_value=None),
            patch.object(visual_bridge, "_ensure_snapshot_helper", return_value=Path("/tmp/macos_snapshot")),
            patch.object(visual_bridge, "_ensure_ax_action_helper", return_value=Path("/tmp/macos_ax_action")),
            patch.object(
                visual_bridge,
                "list_browser_profiles",
                lambda: {
                    "platform": "darwin",
                    "ownProfile": "atrium",
                    "defaultProfile": "user",
                    "browserApp": {"name": "Google Chrome", "path": "/Applications/Google Chrome.app"},
                    "profiles": [],
                },
            ),
        ):
            status = HostBridge().status().to_dict()
            routes = _routes()
            runtime_blocks = _runtime_blocks()
            shell = _shell_probe(status, fake_run)
            apps = visual_bridge.execute_list_apps({"includeRunning": True, "includeInstalled": False}, fake_run)
            shot = Path("/tmp/atrium-macos-probe-sim.png")
            screenshot_result = visual_bridge.execute_screenshot_capture(shot, fake_run)
            notification_result = visual_bridge.execute_notification({"title": "ATRIUM macOS HostBridge", "body": "ok"}, fake_run)
            applescript_clipboard = _applescript_clipboard_probe(fake_run)
            browser_ref = _browser_snapshot_act_probe("atrium", fake_run)
            interactive_calculator = _interactive_calculator_probe(fake_run)
            interactive_textedit = _interactive_textedit_probe(fake_run)
        return {
            "ok": status.get("platform") == "darwin"
            and shell.get("containsExpected") is True
            and status.get("browserBridge") is True
            and status.get("desktopBridge") is True
            and status.get("desktopAutomationReady") is True
            and _route_ok(routes)
            and _runtime_blocks_clear(runtime_blocks)
            and screenshot_result.get("returnCode") == 0
            and _png_file_status(shot).get("ok") is True
            and notification_result.get("returnCode") == 0
            and applescript_clipboard.get("verified") is True
            and not browser_ref.get("error")
            and (browser_ref.get("browserSnapshot") or {}).get("returnCode") == 0
            and (browser_ref.get("browserActClick") or {}).get("returnCode") == 0
            and browser_ref.get("containsExpected") is True
            and not interactive_calculator.get("error")
            and interactive_calculator.get("nativeActionVerified") is True
            and interactive_calculator.get("displayValueVerified") is True
            and _commands_ok(interactive_calculator)
            and not interactive_textedit.get("error")
            and interactive_textedit.get("nativeActionVerified") is True
            and interactive_textedit.get("textValueVerified") is True
            and _commands_ok(interactive_textedit),
            "mode": "simulate",
            "status": status,
            "shell": shell,
            "apps": apps,
            "routes": routes,
            "runtimeBlocks": runtime_blocks,
            "screenshot": screenshot_result,
            "screenshotFile": _png_file_status(shot),
            "notification": notification_result,
            "appleScriptClipboard": applescript_clipboard,
            "browserRef": browser_ref,
            "interactiveCalculator": interactive_calculator,
            "interactiveTextEdit": interactive_textedit,
            "commandCount": len(calls),
        }
    finally:
        sys.platform = original_platform


def _live(
    *,
    screenshot: bool,
    notification: bool,
    applescript_clipboard: bool,
    browser_url: str | None,
    browser_profile: str,
    interactive: bool,
) -> dict[str, Any]:
    status = HostBridge().status().to_dict()
    routes = _routes()
    runtime_blocks = _runtime_blocks()
    checks: dict[str, Any] = {
        "shell": _shell_probe(status),
        "profiles": visual_bridge.list_browser_profiles(),
        "apps": visual_bridge.execute_list_apps({"limit": 20}, _run_process),
    }
    if screenshot:
        shot = (get_settings().data_dir / "macos-hostbridge-probe.png").resolve()
        checks["screenshot"] = visual_bridge.execute_screenshot_capture(shot, _run_process)
        checks["screenshotFile"] = _png_file_status(shot)
    if notification:
        checks["notification"] = visual_bridge.execute_notification(
            {"title": "ATRIUM macOS HostBridge", "body": "Notification probe passed."},
            _run_process,
        )
    if applescript_clipboard:
        checks["appleScriptClipboard"] = _applescript_clipboard_probe(_run_process)
    if browser_url:
        browser_open_args = {"url": browser_url, "profile": browser_profile}
        checks["browserOpenRoute"] = _route_for("browser.open", browser_open_args)
        checks["browserOpenRuntimeBlocks"] = _runtime_block_for("browser.open", browser_open_args)
        if checks["browserOpenRoute"].get("blockReason") or checks["browserOpenRuntimeBlocks"].get("api") or checks["browserOpenRuntimeBlocks"].get("chat"):
            checks["browserOpen"] = {"returnCode": None, "skipped": True, "stderr": "browser.open blocked by runtime readiness"}
        else:
            checks["browserOpen"] = visual_bridge.execute_browser_open(browser_open_args, _run_process)
        checks["browserRef"] = _browser_snapshot_act_probe(browser_profile, _run_process)
    if interactive:
        checks["foregroundSnapshot"] = visual_bridge.execute_desktop_snapshot({"maxElements": 40, "maxDepth": 3}, _run_process)
        native_act_args = {"ref": "__atrium_native_probe_ref__", "action": "click", "requireNative": True, "snapshotAfter": False}
        checks["interactiveNativeActRuntimeBlock"] = _runtime_block_for("desktop.act", native_act_args)
        interactive_blocks = _blocked_runtime_tools(runtime_blocks, {"desktop.snapshot"})
        if (
            checks["interactiveNativeActRuntimeBlock"].get("api")
            or checks["interactiveNativeActRuntimeBlock"].get("chat")
        ):
            interactive_blocks["desktop.act.requireNative"] = checks["interactiveNativeActRuntimeBlock"]
        if interactive_blocks:
            checks["interactiveSkipped"] = {
                "skipped": True,
                "reason": "interactive desktop probes blocked by HostBridge runtime readiness",
                "blockedTools": interactive_blocks,
            }
            checks["interactiveCalculator"] = {"skipped": True, "error": "interactive desktop probe skipped by runtime readiness"}
            checks["interactiveTextEdit"] = {"skipped": True, "error": "interactive desktop probe skipped by runtime readiness"}
        else:
            checks["interactiveCalculator"] = _interactive_calculator_probe()
            checks["interactiveTextEdit"] = _interactive_textedit_probe()

    live_ok = sys.platform == "darwin" and (checks.get("shell") or {}).get("containsExpected") is True
    live_ok = live_ok and status.get("browserBridge") is True and status.get("desktopBridge") is True
    live_ok = live_ok and status.get("desktopAutomationReady") is True and _route_ok(routes) and _runtime_blocks_clear(runtime_blocks)
    live_ok = live_ok and (checks.get("apps") or {}).get("returnCode") == 0
    if screenshot:
        live_ok = live_ok and (checks.get("screenshot") or {}).get("returnCode") == 0 and bool((checks.get("screenshotFile") or {}).get("ok"))
    if notification:
        live_ok = live_ok and (checks.get("notification") or {}).get("returnCode") == 0
    if applescript_clipboard:
        live_ok = live_ok and (checks.get("appleScriptClipboard") or {}).get("verified") is True
    if browser_url:
        live_ok = live_ok and not (checks.get("browserOpenRoute") or {}).get("blockReason")
        live_ok = live_ok and not (checks.get("browserOpenRuntimeBlocks") or {}).get("api") and not (checks.get("browserOpenRuntimeBlocks") or {}).get("chat")
        live_ok = live_ok and (checks.get("browserOpen") or {}).get("returnCode") == 0
        browser_ref = checks.get("browserRef") if isinstance(checks.get("browserRef"), dict) else {}
        live_ok = live_ok and not browser_ref.get("error")
        live_ok = live_ok and (browser_ref.get("browserSnapshot") or {}).get("returnCode") == 0
        live_ok = live_ok and (browser_ref.get("browserActClick") or {}).get("returnCode") == 0
        live_ok = live_ok and browser_ref.get("containsExpected") is True
    if interactive:
        live_ok = live_ok and (checks.get("foregroundSnapshot") or {}).get("returnCode") == 0 and (checks.get("foregroundSnapshot") or {}).get("refCount", 0) > 0
        interactive_checks = checks.get("interactiveCalculator") if isinstance(checks.get("interactiveCalculator"), dict) else {}
        live_ok = live_ok and (interactive_checks.get("desktopActClick") or {}).get("returnCode") == 0
        live_ok = live_ok and interactive_checks.get("nativeActionVerified") is True
        live_ok = live_ok and interactive_checks.get("displayValueVerified") is True
        live_ok = live_ok and not interactive_checks.get("error") and _commands_ok(interactive_checks)
        textedit_checks = checks.get("interactiveTextEdit") if isinstance(checks.get("interactiveTextEdit"), dict) else {}
        live_ok = live_ok and (textedit_checks.get("desktopActSetText") or {}).get("returnCode") == 0
        live_ok = live_ok and textedit_checks.get("nativeActionVerified") is True
        live_ok = live_ok and textedit_checks.get("textValueVerified") is True
        live_ok = live_ok and not textedit_checks.get("error") and _commands_ok(textedit_checks)
    return {
        "ok": live_ok,
        "mode": "live",
        "status": status,
        "routes": routes,
        "runtimeBlocks": runtime_blocks,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true", help="Simulate darwin platform and fake OS commands for branch coverage.")
    parser.add_argument("--full", action="store_true", help="Run the full live macOS parity probe: screenshot, notification, AppleScript clipboard, isolated browser launch, Calculator AXPress, and TextEdit native scroll/text control.")
    parser.add_argument("--screenshot", action="store_true", help="In live macOS mode, capture a screenshot probe PNG.")
    parser.add_argument("--notification", action="store_true", help="In live macOS mode, send a local notification probe.")
    parser.add_argument("--applescript-clipboard", action="store_true", help="In live macOS mode, set/verify/restore clipboard text through AppleScript.")
    parser.add_argument("--browser-url", help="In live macOS mode, open a URL through browser.open using --browser-profile.")
    parser.add_argument("--browser-profile", default="atrium", help="Browser profile for --browser-url, default atrium to verify ATRIUM's isolated profile path.")
    parser.add_argument("--interactive", action="store_true", help="In live macOS mode, open Calculator/TextEdit and probe activate/snapshot/native actions/quit.")
    parser.add_argument("--output", type=Path, help="Optional path to write the stamped probe JSON artifact.")
    parser.add_argument("--parity-run-id", help="Shared ID to stamp on paired macOS/Windows full-probe artifacts for the cross-OS verifier.")
    parser.add_argument("--expect-source-fingerprint", help="Refuse to run unless the current HostBridge source fingerprint matches this value.")
    parser.add_argument("--expect-source-manifest-sha256", help="Refuse to run unless the current HostBridge source manifest SHA-256 matches this value.")
    parser.add_argument("--expect-source-file-count", type=int, help="Refuse to run unless the current HostBridge proof-bound source file count matches this value.")
    args = parser.parse_args()
    full_browser_url = args.browser_url or ("https://example.com" if args.full else None)
    source_snapshot = host_bridge_source_provenance(ROOT)
    result = _source_preflight_result(
        args.expect_source_fingerprint,
        args.expect_source_manifest_sha256,
        args.expect_source_file_count,
        source_snapshot,
    )
    if result is None:
        result = _simulate() if args.simulate else _live(
            screenshot=args.screenshot or args.full,
            notification=args.notification or args.full,
            applescript_clipboard=args.applescript_clipboard or args.full,
            browser_url=full_browser_url,
            browser_profile=args.browser_profile,
            interactive=args.interactive or args.full,
        )
    result = _stamp_result(result, parity_run_id=args.parity_run_id, source=source_snapshot)
    if args.output:
        _write_output(result, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
