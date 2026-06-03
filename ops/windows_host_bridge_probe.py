#!/usr/bin/env python3
"""Probe Windows HostBridge routing and visual bridge readiness.

Use `--simulate` on non-Windows machines for branch coverage. Use default/live
mode on Windows to inspect real HostBridge readiness and run non-destructive
browser/profile/app/screenshot probes.
"""
from __future__ import annotations

import argparse
import json
import locale
import os
import re
import sys
import time
from pathlib import Path
from unittest.mock import patch
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = ROOT / "system"
sys.path.insert(0, str(SYSTEM))

from app.tools import ExecutorRouter, build_default_tool_registry  # noqa: E402
from app.tools.host_bridge import HostBridge  # noqa: E402
from app.tools import host_bridge as host_bridge_module  # noqa: E402
from app.tools import visual_bridge  # noqa: E402
from app.config import get_settings  # noqa: E402

VISUAL_TOOLS = [
    "browser.profiles",
    "browser.open",
    "browser.screenshot",
    "browser.click",
    "browser.type",
    "browser.keypress",
    "browser.paste_text",
    "browser.scroll",
    "desktop.apps",
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
DISCOVERY_TOOLS = {"browser.profiles"}
WINDOWS_COMMAND_RISK_CASES = [
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


def _decode_process_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return _decode_process_bytes(value)
    return str(value)


def _decode_process_bytes(value: bytes) -> str:
    if not value:
        return ""
    encodings = ["utf-8-sig", "utf-8", locale.getpreferredencoding(False)]
    if sys.platform == "win32":
        encodings.extend(["mbcs", "cp65001", "cp874", "cp1252", "cp437", "cp850"])
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
        run = {"tool": tool, "args": {}, "departmentId": "exec"}
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


def _windows_command_risk_checks() -> dict[str, Any]:
    try:
        from app import chat_tools as chat_tools_module  # noqa: WPS433
        from app import main as main_module  # noqa: WPS433
    except Exception as exc:
        return {"ok": False, "error": f"runtime import failed: {type(exc).__name__}: {exc}", "cases": []}
    rows: list[dict[str, Any]] = []
    ok = True
    for command, expected in WINDOWS_COMMAND_RISK_CASES:
        run = {
            "tool": "shell.exec",
            "departmentId": "exec",
            "args": {"command": command},
        }
        api_risk = main_module._tool_risk_class(run)  # noqa: SLF001
        chat_risk = chat_tools_module._owner_tool_risk(run)  # noqa: SLF001
        case_ok = api_risk == expected and chat_risk == expected
        ok = ok and case_ok
        rows.append({
            "command": command,
            "expected": expected,
            "api": api_risk,
            "chat": chat_risk,
            "ok": case_ok,
        })
    return {"ok": ok, "cases": rows}


def _route_ok(routes: dict[str, dict[str, Any]]) -> bool:
    return all(route.get("blockReason") is None for route in routes.values())


def _runtime_blocks_clear(blocks: dict[str, dict[str, str | None]]) -> bool:
    return all(item.get("api") is None and item.get("chat") is None for item in blocks.values())


def _runtime_blocks_present(blocks: dict[str, dict[str, str | None]]) -> bool:
    return all(item.get("api") and item.get("chat") for item in blocks.values())


def _runtime_blocks_present_for(blocks: dict[str, dict[str, str | None]], tools: set[str]) -> bool:
    return all(blocks.get(tool, {}).get("api") and blocks.get(tool, {}).get("chat") for tool in tools)


def _json_rows_from_stdout(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw = str(result.get("stdout") or "").strip()
    if not raw:
        return []
    parsed: Any = None
    candidates = [raw, *(line.strip() for line in reversed(raw.splitlines()) if line.strip())]
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if parsed is None:
        for idx, char in enumerate(raw):
            if char not in "[{":
                continue
            try:
                parsed, _end = decoder.raw_decode(raw[idx:].strip())
                break
            except json.JSONDecodeError:
                continue
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def _process_id_from_result(result: dict[str, Any]) -> int | None:
    raw = result.get("processId")
    if raw is None:
        rows = _json_rows_from_stdout(result)
        raw = rows[0].get("processId") if rows else None
    try:
        process_id = int(raw)
    except (TypeError, ValueError):
        return None
    return process_id if process_id > 0 else None


def _fake_png(width: int = 1920, height: int = 1080) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + int(width).to_bytes(4, "big")
        + int(height).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )


def _shell_probe(status: dict[str, Any], run_process: Any = _run_process) -> dict[str, Any]:
    expected = "ATRIUM_WINDOWS_SHELL_OK"
    executable = str(status.get("shellExecutable") or "").strip()
    if not executable:
        return {
            "returnCode": 127,
            "stdout": "",
            "stderr": "HostBridge did not report a shell executable",
            "expected": expected,
            "containsExpected": False,
        }
    name = Path(executable).name.lower()
    if name in {"powershell.exe", "powershell", "pwsh.exe", "pwsh"}:
        command = [executable, "-NoProfile", "-Command", f"Write-Output {expected}"]
    elif name == "cmd.exe" or name == "cmd":
        command = [executable, "/d", "/s", "/c", f"echo {expected}"]
    else:
        command = [executable, "-lc", f"echo {expected}"]
    result = run_process(command, timeout=8.0, cwd=ROOT)
    result.update({
        "expected": expected,
        "containsExpected": expected in str(result.get("stdout") or ""),
    })
    return result


def _clipboard_round_trip(expected: str, run_process: Any = _run_process) -> dict[str, Any]:
    script = "\n".join([
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
        "$value = Get-Clipboard -Raw -ErrorAction Stop",
        "if ($null -eq $value) { $value = '' }",
        f"$expected = {visual_bridge._ps_string(expected)}",  # noqa: SLF001
        "$preview = $value",
        "if ($preview.Length -gt 200) { $preview = $preview.Substring(0, 200) }",
        "[PSCustomObject]@{ textLength=$value.Length; textPreview=$preview; expected=$expected; containsExpected=$value.Contains($expected) } | ConvertTo-Json -Compress",
    ])
    result = visual_bridge._run_windows_powershell(script, run_process, timeout=5.0, sta=True)  # noqa: SLF001
    rows = _json_rows_from_stdout(result)
    row = rows[0] if rows else {}
    result.update({
        "textLength": row.get("textLength"),
        "textPreview": row.get("textPreview"),
        "expected": row.get("expected") or expected,
        "containsExpected": bool(row.get("containsExpected")),
    })
    return result


def _set_clipboard_text(value: str, run_process: Any = _run_process) -> dict[str, Any]:
    script = "\n".join([
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
        f"$expected = {visual_bridge._ps_string(value)}",  # noqa: SLF001
        "Set-Clipboard -Value $expected",
        "$value = Get-Clipboard -Raw -ErrorAction Stop",
        "if ($null -eq $value) { $value = '' }",
        "$preview = $value",
        "if ($preview.Length -gt 200) { $preview = $preview.Substring(0, 200) }",
        "[PSCustomObject]@{ textLength=$value.Length; textPreview=$preview; verified=$value.Equals($expected) } | ConvertTo-Json -Compress",
    ])
    result = visual_bridge._run_windows_powershell(script, run_process, timeout=5.0, sta=True)  # noqa: SLF001
    rows = _json_rows_from_stdout(result)
    row = rows[0] if rows else {}
    result.update({
        "textLength": row.get("textLength"),
        "textPreview": row.get("textPreview"),
        "verified": bool(row.get("verified")),
    })
    return result


def _png_file_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "path": str(path), "exists": False}
    data = path.read_bytes()
    width, height = visual_bridge._png_dimensions(data)  # noqa: SLF001
    ok = bool(width and height and width > 0 and height > 0)
    return {
        "ok": ok,
        "path": str(path),
        "exists": True,
        "bytes": len(data),
        "width": width,
        "height": height,
    }


def _missing_bridge_routes_block() -> dict[str, Any]:
    original_platform = sys.platform

    def fake_which(name: str) -> str | None:
        if name.lower() in {"git", "docker", "cmd.exe"}:
            return f"C:/Windows/System32/{name}"
        return None

    try:
        sys.platform = "win32"
        with patch.object(host_bridge_module.shutil, "which", fake_which), patch.object(visual_bridge.shutil, "which", fake_which), patch.dict(os.environ, {"SESSIONNAME": "Console"}, clear=False):
            status = HostBridge().status().to_dict()
            routes = _routes()
            runtime_blocks = _runtime_blocks()
    finally:
        sys.platform = original_platform
    blocked = {
        tool: route.get("blockReason")
        for tool, route in routes.items()
        if route.get("blockReason")
    }
    expected_blocked = set(VISUAL_TOOLS) - DISCOVERY_TOOLS
    return {
        "ok": status.get("browserBridge") is False and status.get("desktopBridge") is False and set(blocked) == expected_blocked and _runtime_blocks_present_for(runtime_blocks, expected_blocked),
        "status": status,
        "blocked": blocked,
        "expectedBlocked": sorted(expected_blocked),
        "runtimeBlocks": runtime_blocks,
    }


def _simulate() -> dict[str, Any]:
    original_platform = sys.platform
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        if name.lower() in {"powershell.exe", "powershell", "pwsh.exe", "pwsh", "cmd.exe", "git", "docker"}:
            return f"C:/Windows/System32/{name}"
        return None

    def fake_run(command: list[str], **_: Any) -> dict[str, Any]:
        calls.append(command)
        joined = " ".join(str(part) for part in command)
        if any("ATRIUM_WINDOWS_SHELL_OK" in str(part) for part in command):
            return {
                "command": command,
                "returnCode": 0,
                "stdout": "ATRIUM_WINDOWS_SHELL_OK\n",
                "stderr": "",
            }
        if any("selftest" == str(part) for part in command):
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"ok":true,"mode":"selftest","screenWidth":1920,"screenHeight":1080,"virtualLeft":0,"virtualTop":0,"virtualWidth":1920,"virtualHeight":1080,"dpiAwareness":"per_monitor_v2"}',
                "stderr": "",
            }
        if any("scroll" == str(part) for part in command):
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"ok":true,"mode":"scroll","direction":"down","unit":"line","amount":1,"steps":1,"wheelDelta":-120,"horizontal":false,"inputMethod":"sendinput","dpiAwareness":"per_monitor_v2"}',
                "stderr": "",
            }
        if any("click" == str(part) for part in command):
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"ok":true,"mode":"click","x":10,"y":20,"button":"left","inputMethod":"sendinput","dpiAwareness":"per_monitor_v2"}',
                "stderr": "",
            }
        if any("keypress" == str(part) for part in command):
            payload: dict[str, Any] = {}
            for part in reversed(command):
                raw_part = str(part).strip()
                if not raw_part.startswith("{"):
                    continue
                try:
                    loaded = json.loads(raw_part)
                except json.JSONDecodeError:
                    continue
                if isinstance(loaded, dict):
                    payload = loaded
                    break
            raw_keys = payload.get("keys") if isinstance(payload.get("keys"), list) else []
            normalized = [str(item).strip().lower() for item in raw_keys if str(item).strip()]
            modifier_aliases = {
                "ctrl": "control",
                "control": "control",
                "cmd": "control",
                "command": "control",
                "meta": "control",
                "shift": "shift",
                "alt": "alt",
                "option": "alt",
                "win": "win",
                "windows": "win",
                "super": "win",
            }
            modifiers = [modifier_aliases[item] for item in normalized if item in modifier_aliases]
            key_parts = [item for item in normalized if item not in modifier_aliases]
            key_aliases = {
                "forward_delete": "forwarddelete",
                "del": "forwarddelete",
                "ins": "insert",
                "page_down": "pagedown",
                "page up": "pageup",
                "page_up": "pageup",
                "page down": "pagedown",
            }
            key = key_aliases.get(key_parts[0], key_parts[0]) if key_parts else "unknown"
            return {
                "command": command,
                "returnCode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "mode": "keypress",
                        "key": key,
                        "modifiers": modifiers,
                        "inputMethod": "sendinput",
                        "dpiAwareness": "per_monitor_v2",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "stderr": "",
            }
        if any("type" == str(part) for part in command):
            payload: dict[str, Any] = {}
            for part in reversed(command):
                raw_part = str(part).strip()
                if not raw_part.startswith("{"):
                    continue
                try:
                    loaded = json.loads(raw_part)
                except json.JSONDecodeError:
                    continue
                if isinstance(loaded, dict):
                    payload = loaded
                    break
            text = payload.get("text") if isinstance(payload.get("text"), str) else ""
            return {
                "command": command,
                "returnCode": 0,
                "stdout": json.dumps(
                    {
                        "ok": True,
                        "mode": "type",
                        "textBytes": len(text.encode("utf-8")),
                        "textCharacters": len(text),
                        "textUnits": len(text.encode("utf-16-le", errors="surrogatepass")) // 2,
                        "inputMethod": "sendinput",
                        "dpiAwareness": "per_monitor_v2",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "stderr": "",
            }
        if any("setClipboardCommand" in str(part) for part in command):
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"ok":true,"checks":{"winForms":true,"drawing":true,"virtualScreen":true,"systemIcon":true,"setClipboardCommand":true,"getClipboardCommand":true,"dpiAwareness":true},"virtualScreen":{"left":0,"top":0,"width":1920,"height":1080},"errors":{},"powerShell":"5.1.19041.1"}',
                "stderr": "",
            }
        if any("ShowBalloonTip" in str(part) for part in command):
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"shown":true,"disposed":true,"timeoutMs":5000,"titleLength":6,"bodyLength":2}',
                "stderr": "",
            }
        if any("CopyFromScreen" in str(part) for part in command):
            Path("/tmp/atrium-win-shot.png").write_bytes(_fake_png(1920, 1080))
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"path":"/tmp/atrium-win-shot.png","left":0,"top":0,"width":1920,"height":1080,"dpiAwareness":true}',
                "stderr": "",
            }
        if any("Get-Clipboard" in str(part) for part in command):
            expected_match = re.search(r"\$expected = '((?:''|[^'])*)'", joined)
            expected = (expected_match.group(1).replace("''", "'") if expected_match else "ATRIUM paste probe ไทย")
            return {
                "command": command,
                "returnCode": 0,
                "stdout": json.dumps(
                    {
                        "textLength": len(expected),
                        "textPreview": expected,
                        "expected": expected,
                        "containsExpected": True,
                        "verified": True,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "stderr": "",
            }
        if any("SetForegroundWindow" in str(part) for part in command):
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"name":"notepad","title":"Untitled - Notepad","processId":10,"foreground":true,"activeProcessId":10,"setForeground":true,"bringToTop":true,"showWindow":true,"attachedCurrent":true,"attachedForeground":false}',
                "stderr": "",
            }
        if any("quitVerified" in str(part) for part in command):
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '{"matched":1,"gracefulCloseSent":1,"force":false,"remaining":0,"quitVerified":true}',
                "stderr": "",
            }
        if any("Start-Process" in str(part) for part in command):
            if "UrlAssociations" in joined:
                stdout = '{"processId":12,"startedProcessId":12,"processName":"msedge.exe","launchPath":"C:/Program Files/Microsoft/Edge/Application/msedge.exe","browserName":"msedge","source":"defaultBrowserRegistry","processVerified":true,"progId":"MSEdgeHTM"}'
            elif "--user-data-dir" in joined or "profileProcessLookup" in joined or "Get-CimInstance" in joined:
                stdout = '{"processId":11,"startedProcessId":11,"processName":"chrome.exe","launchPath":"C:/Program Files/Google/Chrome/Application/chrome.exe","source":"profileProcessLookup","profileVerified":true}'
            else:
                stdout = '{"processId":10,"processName":"notepad","launchPath":"notepad","source":"startProcess"}'
            return {
                "command": command,
                "returnCode": 0,
                "stdout": stdout,
                "stderr": "",
            }
        if any("Get-Process" in str(part) for part in command):
            return {
                "command": command,
                "returnCode": 0,
                "stdout": '[{"name":"notepad","title":"Untitled - Notepad","processId":10,"path":"C:/Windows/notepad.exe"}]',
                "stderr": "",
            }
        return {"command": command, "returnCode": 0, "stdout": "", "stderr": ""}

    try:
        sys.platform = "win32"
        with patch.object(host_bridge_module.shutil, "which", fake_which), patch.object(visual_bridge.shutil, "which", fake_which), patch.dict(os.environ, {"SESSIONNAME": "Console"}, clear=False):
            status = HostBridge().status().to_dict()
            shell = _shell_probe(status, fake_run)
            routes = _routes()
            runtime_blocks = _runtime_blocks()
            command_risk = _windows_command_risk_checks()
            default_browser_open = visual_bridge.execute_browser_open({"url": "https://example.com"}, fake_run)
            helper_selftest = visual_bridge.execute_windows_visual_selftest(fake_run)
            powershell_preflight = visual_bridge.execute_windows_powershell_visual_preflight(fake_run)
            visual_bridge.execute_screenshot_capture(Path("/tmp/atrium-win-shot.png"), fake_run)
            visual_bridge.execute_click({"x": 10, "y": 20}, fake_run)
            visual_bridge.execute_keypress({"keys": ["cmd", "l"]}, fake_run)
            windows_key = visual_bridge.execute_keypress({"keys": ["win", "r"]}, fake_run)
            forward_delete_key = visual_bridge.execute_keypress({"keys": ["forward_delete"]}, fake_run)
            type_text = visual_bridge.execute_type_text({"text": "ไทย"}, fake_run)
            visual_bridge.execute_paste_text({"text": "paste"}, fake_run)
            visual_bridge.execute_scroll({"direction": "down", "unit": "line", "amount": 1}, fake_run)
            apps = visual_bridge.execute_list_apps({"includeInstalled": False}, fake_run)
            open_app = visual_bridge.execute_open_app({"appName": "notepad"}, fake_run)
            if not open_app.get("processId"):
                raise AssertionError(f"open_app did not expose processId: {open_app}")
            visual_bridge.execute_activate_app({"appName": "notepad"}, fake_run)
            visual_bridge.execute_quit_app({"appName": "notepad"}, fake_run)
            notification_result = visual_bridge.execute_notification({"title": "ATRIUM", "body": "ok"}, fake_run)
            round_trip = _clipboard_round_trip("ATRIUM paste probe ไทย", fake_run)
            if not round_trip.get("containsExpected"):
                raise AssertionError(f"clipboard round trip did not verify expected text: {round_trip}")
            interactive_desktop = _interactive_desktop_probe(fake_run)
        missing_bridge = _missing_bridge_routes_block()
        return {
            "ok": status.get("platform") == "win32" and shell.get("containsExpected") is True and status.get("browserBridge") is True and status.get("desktopBridge") is True and _route_ok(routes) and _runtime_blocks_clear(runtime_blocks) and command_risk.get("ok") is True and default_browser_open.get("returnCode") == 0 and default_browser_open.get("profileKind") == "user" and default_browser_open.get("processVerified") is True and windows_key.get("returnCode") == 0 and windows_key.get("modifiers") == ["win"] and forward_delete_key.get("returnCode") == 0 and forward_delete_key.get("key") == "forwarddelete" and type_text.get("returnCode") == 0 and type_text.get("textBytes") == len("ไทย".encode("utf-8")) and type_text.get("textCharacters") == len("ไทย") and type_text.get("textUnits") == len("ไทย".encode("utf-16-le")) // 2 and notification_result.get("returnCode") == 0 and notification_result.get("shown") is True and notification_result.get("disposed") is True and helper_selftest.get("returnCode") == 0 and helper_selftest.get("ok") is True and powershell_preflight.get("returnCode") == 0 and powershell_preflight.get("ok") is True and bool(apps.get("running")) and missing_bridge.get("ok") is True and not interactive_desktop.get("error") and _commands_ok(interactive_desktop),
            "mode": "simulate",
            "status": status,
            "shell": shell,
            "defaultBrowserOpen": default_browser_open,
            "windowsKeypress": windows_key,
            "forwardDeleteKeypress": forward_delete_key,
            "typeText": type_text,
            "notification": notification_result,
            "helperSelftest": helper_selftest,
            "powershellPreflight": powershell_preflight,
            "routes": routes,
            "runtimeBlocks": runtime_blocks,
            "commandRisk": command_risk,
            "interactiveDesktop": interactive_desktop,
            "commandCount": len(calls),
            "missingBridge": missing_bridge,
        }
    finally:
        sys.platform = original_platform


def _interactive_desktop_probe(run_process: Any = _run_process) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    process_id: int | None = None
    try:
        checks["openApp"] = visual_bridge.execute_open_app({"appName": "notepad.exe"}, run_process)
        process_id = _process_id_from_result(checks["openApp"])
        if checks["openApp"].get("returnCode") != 0:
            checks["error"] = "desktop.open_app failed; refusing broad interactive control"
            return checks
        if not process_id:
            checks["error"] = "desktop.open_app did not return a processId; refusing broad interactive control"
            return checks
        target = {"processId": process_id}
        for attempt in range(10):
            checks["activateApp"] = visual_bridge.execute_activate_app(target, run_process)
            if checks["activateApp"].get("returnCode") == 0 and checks["activateApp"].get("timeout") is not True and checks["activateApp"].get("ok") is not False:
                break
            time.sleep(0.5)
        checks["activateAttempts"] = attempt + 1
        if checks["activateApp"].get("returnCode") != 0 or checks["activateApp"].get("timeout") is True or checks["activateApp"].get("ok") is False:
            checks["error"] = "desktop.activate_app failed; refusing to type into an unverified foreground app"
            return checks
        time.sleep(0.3)
        checks["type"] = visual_bridge.execute_type_text({"text": "ATRIUM Windows HostBridge probe ไทย"}, run_process)
        checks["keypress"] = visual_bridge.execute_keypress({"keys": ["control", "a"]}, run_process)
        expected_text = "ATRIUM paste probe ไทย"
        checks["pasteText"] = visual_bridge.execute_paste_text({"text": expected_text}, run_process)
        checks["scroll"] = visual_bridge.execute_scroll({"direction": "down", "unit": "line", "amount": 1}, run_process)
        checks["copyBackSelectAll"] = visual_bridge.execute_keypress({"keys": ["control", "a"]}, run_process)
        checks["clipboardClearBeforeCopy"] = _set_clipboard_text("ATRIUM clipboard cleared before copy-back verification", run_process)
        if checks["clipboardClearBeforeCopy"].get("returnCode") != 0 or checks["clipboardClearBeforeCopy"].get("verified") is not True:
            checks["error"] = "clipboard clear failed before copy-back verification"
            return checks
        checks["copyBackCopy"] = visual_bridge.execute_keypress({"keys": ["control", "c"]}, run_process)
        time.sleep(0.2)
        checks["clipboardRoundTrip"] = _clipboard_round_trip(expected_text, run_process)
        if checks["clipboardRoundTrip"].get("returnCode") != 0 or not checks["clipboardRoundTrip"].get("containsExpected"):
            checks["error"] = "clipboard round-trip did not confirm that typed/pasted text reached the target app"
        return checks
    finally:
        if process_id:
            checks["quitApp"] = visual_bridge.execute_quit_app({"processId": process_id, "force": True, "forceDelaySeconds": 0.2}, run_process)


def _commands_ok(group: dict[str, Any]) -> bool:
    for value in group.values():
        if not isinstance(value, dict):
            continue
        if value.get("timeout") is True:
            return False
        if value.get("ok") is False:
            return False
        if value.get("quitVerified") is False:
            return False
        if "returnCode" in value and value.get("returnCode") != 0:
            return False
    return True


def _live(*, screenshot: bool, notification: bool, browser_url: str | None, browser_profile: str, interactive: bool) -> dict[str, Any]:
    status = HostBridge().status().to_dict()
    routes = _routes()
    runtime_blocks = _runtime_blocks()
    checks: dict[str, Any] = {
        "shell": _shell_probe(status),
        "helperSelftest": visual_bridge.execute_windows_visual_selftest(_run_process),
        "powershellPreflight": visual_bridge.execute_windows_powershell_visual_preflight(_run_process),
        "profiles": visual_bridge.list_browser_profiles(),
        "apps": visual_bridge.execute_list_apps({"limit": 10}, _run_process),
        "commandRisk": _windows_command_risk_checks(),
    }
    if screenshot:
        shot = (get_settings().data_dir / "windows-hostbridge-probe.png").resolve()
        checks["screenshot"] = visual_bridge.execute_screenshot_capture(shot, _run_process)
        checks["screenshotFile"] = _png_file_status(shot)
    if notification:
        checks["notification"] = visual_bridge.execute_notification(
            {"title": "ATRIUM Windows HostBridge", "body": "Notification probe passed."},
            _run_process,
        )
    if browser_url:
        browser_open_args = {"url": browser_url, "profile": browser_profile}
        checks["browserOpenRoute"] = _route_for("browser.open", browser_open_args)
        checks["browserOpenRuntimeBlocks"] = _runtime_block_for("browser.open", browser_open_args)
        if checks["browserOpenRoute"].get("blockReason") or checks["browserOpenRuntimeBlocks"].get("api") or checks["browserOpenRuntimeBlocks"].get("chat"):
            checks["browserOpen"] = {"returnCode": None, "skipped": True, "stderr": "browser.open blocked by runtime readiness"}
            checks["browserOpenProcessId"] = None
        else:
            checks["browserOpen"] = visual_bridge.execute_browser_open(browser_open_args, _run_process)
            checks["browserOpenProcessId"] = _process_id_from_result(checks["browserOpen"])
    if interactive:
        checks["interactiveDesktop"] = _interactive_desktop_probe()
    live_ok = sys.platform == "win32" and (checks.get("shell") or {}).get("containsExpected") is True and status.get("browserBridge") is True and status.get("desktopBridge") is True and _route_ok(routes) and _runtime_blocks_clear(runtime_blocks)
    live_ok = live_ok and (checks.get("helperSelftest") or {}).get("returnCode") == 0 and (checks.get("helperSelftest") or {}).get("ok") is True
    live_ok = live_ok and (checks.get("powershellPreflight") or {}).get("returnCode") == 0 and (checks.get("powershellPreflight") or {}).get("ok") is True
    live_ok = live_ok and (checks.get("apps") or {}).get("returnCode") == 0 and (checks.get("apps") or {}).get("timeout") is not True
    live_ok = live_ok and (checks.get("commandRisk") or {}).get("ok") is True
    if screenshot:
        live_ok = live_ok and (checks.get("screenshot") or {}).get("returnCode") == 0 and bool((checks.get("screenshotFile") or {}).get("ok"))
    if notification:
        notification_result = checks.get("notification") or {}
        live_ok = live_ok and notification_result.get("returnCode") == 0
        live_ok = live_ok and notification_result.get("shown") is True and notification_result.get("disposed") is True
    if browser_url:
        live_ok = live_ok and not (checks.get("browserOpenRoute") or {}).get("blockReason")
        live_ok = live_ok and not (checks.get("browserOpenRuntimeBlocks") or {}).get("api") and not (checks.get("browserOpenRuntimeBlocks") or {}).get("chat")
        live_ok = live_ok and (checks.get("browserOpen") or {}).get("returnCode") == 0
        try:
            browser_profile_kind = visual_bridge.normalize_browser_profile(browser_profile)
        except ValueError:
            browser_profile_kind = browser_profile
        if browser_profile_kind != "user":
            live_ok = live_ok and checks.get("browserOpenProcessId") is not None
    if interactive:
        interactive_checks = checks.get("interactiveDesktop") if isinstance(checks.get("interactiveDesktop"), dict) else {}
        live_ok = live_ok and not interactive_checks.get("error") and _commands_ok(interactive_checks)
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
    parser.add_argument("--simulate", action="store_true", help="Simulate win32 platform and fake OS commands for branch coverage.")
    parser.add_argument("--full", action="store_true", help="Run the full live Windows parity probe: screenshot, notification, isolated browser launch, and Notepad desktop control.")
    parser.add_argument("--screenshot", action="store_true", help="In live Windows mode, capture a screenshot probe PNG.")
    parser.add_argument("--notification", action="store_true", help="In live Windows mode, send a local notification probe.")
    parser.add_argument("--browser-url", help="In live Windows mode, open a URL through browser.open using --browser-profile.")
    parser.add_argument("--browser-profile", default="atrium", help="Browser profile for --browser-url, default atrium to verify ATRIUM's isolated profile path.")
    parser.add_argument("--interactive", action="store_true", help="In live Windows mode, open Notepad and probe activate/type/paste/keypress/scroll/quit using its processId.")
    args = parser.parse_args()
    full_browser_url = args.browser_url or ("https://example.com" if args.full else None)
    result = _simulate() if args.simulate else _live(
        screenshot=args.screenshot or args.full,
        notification=args.notification or args.full,
        browser_url=full_browser_url,
        browser_profile=args.browser_profile,
        interactive=args.interactive or args.full,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
