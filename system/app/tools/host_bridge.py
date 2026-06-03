"""HostBridge — local operator capability surface."""
from __future__ import annotations

import ctypes
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings


_KNOWN_EXECUTABLES = {
    "powershell.exe": (
        "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "C:/Windows/SysWOW64/WindowsPowerShell/v1.0/powershell.exe",
    ),
    "pwsh.exe": (
        "C:/Program Files/PowerShell/7/pwsh.exe",
        "C:/Program Files (x86)/PowerShell/7/pwsh.exe",
    ),
    "cmd.exe": (
        "C:/Windows/System32/cmd.exe",
        "C:/Windows/SysWOW64/cmd.exe",
    ),
    "git": (
        "C:/Program Files/Git/cmd/git.exe",
        "C:/Program Files/Git/bin/git.exe",
        "C:/Program Files (x86)/Git/cmd/git.exe",
    ),
    "docker": (
        "/usr/local/bin/docker",
        "/opt/homebrew/bin/docker",
        "/Applications/Docker.app/Contents/Resources/bin/docker",
        "C:/Program Files/Docker/Docker/resources/bin/docker.exe",
    ),
}
_WINDOWS_PREFLIGHT_CACHE_TTL_SECONDS = 10.0
_WINDOWS_PREFLIGHT_CACHE: tuple[float, dict[str, Any]] | None = None
_WINDOWS_VISUAL_PREFLIGHT_TOOLS = {
    "browser.screenshot",
    "browser.click",
    "browser.type",
    "browser.keypress",
    "browser.paste_text",
    "browser.scroll",
    "desktop.screenshot",
    "desktop.click",
    "desktop.type",
    "desktop.keypress",
    "desktop.paste_text",
    "desktop.scroll",
    "notify.send",
}


def _has_executable(name: str) -> bool:
    if shutil.which(name):
        return True
    if sys.platform == "win32" and not name.lower().endswith(".exe") and shutil.which(f"{name}.exe"):
        return True
    return any(Path(path).exists() for path in _KNOWN_EXECUTABLES.get(name, ()))


@dataclass
class HostBridgeStatus:
    platform: str
    shell_executable: str | None
    shell: bool
    shell_ready: bool
    filesystem_ready: bool
    git: bool
    http: bool
    browser_bridge_executable: str | None
    browser_bridge: bool
    browser_automation_ready: bool
    isolated_browser_profile_ready: bool
    isolated_browser_profile_app: str | None
    isolated_browser_profile_executable: str | None
    desktop_bridge_executable: str | None
    desktop_bridge: bool
    desktop_automation_ready: bool
    docker: bool
    interactive_session: bool | None
    interactive_session_name: str | None
    interactive_session_id: int | None
    windows_visual_preflight_checked: bool
    windows_visual_preflight_ok: bool | None
    windows_visual_preflight_error: str | None
    windows_visual_preflight_checks: dict[str, Any]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "shellExecutable": self.shell_executable,
            "shell": self.shell,
            "shellReady": self.shell_ready,
            "filesystemReady": self.filesystem_ready,
            "git": self.git,
            "http": self.http,
            "browserBridgeExecutable": self.browser_bridge_executable,
            "browserBridge": self.browser_bridge,
            "browserAutomationReady": self.browser_automation_ready,
            "isolatedBrowserProfileReady": self.isolated_browser_profile_ready,
            "isolatedBrowserProfileApp": self.isolated_browser_profile_app,
            "isolatedBrowserProfileExecutable": self.isolated_browser_profile_executable,
            "desktopBridgeExecutable": self.desktop_bridge_executable,
            "desktopBridge": self.desktop_bridge,
            "desktopAutomationReady": self.desktop_automation_ready,
            "docker": self.docker,
            "interactiveSession": self.interactive_session,
            "interactiveSessionName": self.interactive_session_name,
            "interactiveSessionId": self.interactive_session_id,
            "windowsVisualPreflightChecked": self.windows_visual_preflight_checked,
            "windowsVisualPreflightOk": self.windows_visual_preflight_ok,
            "windowsVisualPreflightError": self.windows_visual_preflight_error,
            "windowsVisualPreflightChecks": self.windows_visual_preflight_checks,
            "notes": self.notes,
        }


class HostBridge:
    """Capability probe + routing metadata for host-local tools."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def status(self) -> HostBridgeStatus:
        notes: list[str] = []
        platform = sys.platform
        shell_executable = _first_executable(
            ("powershell.exe", "powershell", "pwsh.exe", "pwsh", "cmd.exe")
            if platform == "win32"
            else ("bash", "zsh", "sh")
        )
        shell = bool(shell_executable)
        shell_ready = shell
        filesystem_ready = True
        http = True
        interactive_session: bool | None = None
        interactive_session_name: str | None = None
        interactive_session_id: int | None = None
        browser_executable: str | None = None
        desktop_executable: str | None = None
        isolated_browser_profile_ready = False
        isolated_browser_profile_app: str | None = None
        isolated_browser_profile_executable: str | None = None
        windows_visual_preflight = {
            "checked": False,
            "ok": None,
            "error": None,
            "checks": {},
        }
        if platform == "darwin":
            browser_executable = _first_executable(("open",))
            desktop_executable = _first_executable(("osascript",))
            browser = bool(browser_executable and shutil.which("osascript"))
            browser_ready = browser and bool(shutil.which("screencapture") and shutil.which("pbcopy"))
            desktop = bool(desktop_executable and shutil.which("screencapture"))
            desktop_ready = desktop and bool(shutil.which("pbcopy"))
        elif platform == "win32":
            powershell = _first_executable(("powershell.exe", "powershell", "pwsh.exe", "pwsh"))
            interactive_session, interactive_session_name, interactive_session_id = _windows_interactive_session()
            windows_visual_preflight = _windows_visual_preflight(powershell)
            preflight_failed = windows_visual_preflight.get("checked") is True and windows_visual_preflight.get("ok") is False
            browser_executable = powershell
            desktop_executable = powershell
            browser = bool(powershell)
            browser_ready = browser and interactive_session is True and not preflight_failed
            desktop = bool(powershell)
            desktop_ready = desktop and interactive_session is True and not preflight_failed
            if interactive_session is False:
                notes.append("Windows desktop/browser tools are unavailable in a service/non-interactive session.")
            elif interactive_session is None:
                notes.append("Windows desktop/browser tools require an interactive user session; current session could not be verified.")
            else:
                notes.append("Windows desktop/browser tools require an interactive user session.")
            if preflight_failed:
                detail = str(windows_visual_preflight.get("error") or "visual preflight failed")
                notes.append(f"Windows visual automation preflight failed: {detail}")
        else:
            browser = False
            browser_ready = False
            desktop = False
            desktop_ready = False
            notes.append(f"{platform} visual desktop bridge is not implemented.")
        browser_profile_app = _isolated_browser_profile_app()
        if browser_profile_app:
            isolated_browser_profile_app = browser_profile_app.get("name")
            isolated_browser_profile_executable = browser_profile_app.get("path")
            isolated_browser_profile_ready = bool(isolated_browser_profile_executable)
        if browser_ready and not isolated_browser_profile_ready:
            notes.append("Isolated browser profiles require Chrome, Edge, Brave, or Chromium.")
        git = _has_executable("git")
        docker = _has_executable("docker")
        return HostBridgeStatus(
            platform=platform,
            shell_executable=shell_executable,
            shell=shell,
            shell_ready=shell_ready,
            filesystem_ready=filesystem_ready,
            git=git,
            http=http,
            browser_bridge_executable=browser_executable,
            browser_bridge=browser,
            browser_automation_ready=browser_ready,
            isolated_browser_profile_ready=isolated_browser_profile_ready,
            isolated_browser_profile_app=isolated_browser_profile_app,
            isolated_browser_profile_executable=isolated_browser_profile_executable,
            desktop_bridge_executable=desktop_executable,
            desktop_bridge=desktop,
            desktop_automation_ready=desktop_ready,
            docker=docker,
            interactive_session=interactive_session,
            interactive_session_name=interactive_session_name,
            interactive_session_id=interactive_session_id,
            windows_visual_preflight_checked=bool(windows_visual_preflight.get("checked")),
            windows_visual_preflight_ok=windows_visual_preflight.get("ok"),
            windows_visual_preflight_error=windows_visual_preflight.get("error"),
            windows_visual_preflight_checks=windows_visual_preflight.get("checks") if isinstance(windows_visual_preflight.get("checks"), dict) else {},
            notes=notes,
        )

    def can_run(self, tool_name: str, args: dict[str, Any] | None = None) -> tuple[bool, str | None]:
        status = self.status()
        if tool_name.startswith("shell.") or tool_name == "run_command":
            if not status.shell:
                return False, "host shell unavailable"
        if tool_name == "browser.profiles":
            return True, None
        if tool_name.startswith("browser."):
            if not status.browser_bridge:
                return False, f"{status.platform} browser bridge unavailable"
            if status.platform == "win32" and status.interactive_session is False:
                return False, "win32 interactive desktop session unavailable"
            if status.platform == "win32" and status.interactive_session is None:
                return False, "win32 interactive desktop session could not be verified"
            if tool_name in _WINDOWS_VISUAL_PREFLIGHT_TOOLS and status.windows_visual_preflight_checked and status.windows_visual_preflight_ok is False:
                return False, _windows_visual_preflight_reason(status)
            if tool_name == "browser.open" and _requests_isolated_browser_profile(args) and not status.isolated_browser_profile_ready:
                return False, "isolated browser profile requires Chrome, Edge, Brave, or Chromium"
            if status.platform == "darwin":
                if tool_name == "browser.screenshot" and not shutil.which("screencapture"):
                    return False, "macOS screencapture is unavailable"
        if tool_name.startswith("desktop."):
            if status.platform == "win32" and tool_name == "desktop.apps":
                if not status.desktop_bridge:
                    return False, "win32 desktop bridge unavailable"
                return True, None
            if status.platform == "win32" and status.interactive_session is False:
                return False, "win32 interactive desktop session unavailable"
            if status.platform == "win32" and status.interactive_session is None:
                return False, "win32 interactive desktop session could not be verified"
            if status.platform == "win32" and not status.desktop_bridge:
                return False, "win32 desktop bridge unavailable"
            if tool_name in _WINDOWS_VISUAL_PREFLIGHT_TOOLS and status.windows_visual_preflight_checked and status.windows_visual_preflight_ok is False:
                return False, _windows_visual_preflight_reason(status)
            if status.platform == "darwin":
                if tool_name == "desktop.open_app" and not shutil.which("open"):
                    return False, "macOS open command is unavailable"
                if tool_name in {"desktop.apps", "desktop.activate_app", "desktop.quit_app"} and not shutil.which("osascript"):
                    return False, "macOS osascript bridge is unavailable"
                if tool_name == "desktop.screenshot" and not shutil.which("screencapture"):
                    return False, "macOS screencapture is unavailable"
            if tool_name not in {"desktop.apps", "desktop.open_app", "desktop.activate_app", "desktop.quit_app", "desktop.screenshot"} and not status.desktop_bridge:
                return False, f"{status.platform} desktop bridge unavailable"
        if tool_name == "notify.send":
            if status.platform == "win32" and status.interactive_session is False:
                return False, "win32 interactive desktop session unavailable"
            if status.platform == "win32" and status.interactive_session is None:
                return False, "win32 interactive desktop session could not be verified"
            if status.platform == "win32" and status.windows_visual_preflight_checked and status.windows_visual_preflight_ok is False:
                return False, _windows_visual_preflight_reason(status)
            if not status.desktop_bridge:
                return False, f"{status.platform} notification bridge unavailable"
        if tool_name.startswith("git."):
            if not status.git:
                return False, "git not installed"
        return True, None


def _first_executable(names: tuple[str, ...]) -> str | None:
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return resolved
        for candidate in _KNOWN_EXECUTABLES.get(name, ()):
            if Path(candidate).exists():
                return candidate
    return None


def _isolated_browser_profile_app() -> dict[str, str] | None:
    try:
        from .visual_bridge import list_browser_profiles

        app = list_browser_profiles().get("browserApp")
        if isinstance(app, dict) and app.get("path"):
            return {
                "name": str(app.get("name") or ""),
                "path": str(app["path"]),
            }
    except Exception:
        return None
    return None


def _requests_isolated_browser_profile(args: dict[str, Any] | None) -> bool:
    if not args:
        return False
    raw = args.get("profile") or args.get("browserProfile")
    value = str(raw or "").strip().lower()
    return value not in {"", "user", "default", "host", "personal"}


def _windows_visual_preflight_reason(status: HostBridgeStatus) -> str:
    detail = str(status.windows_visual_preflight_error or "").strip()
    return f"win32 visual automation preflight failed: {detail}" if detail else "win32 visual automation preflight failed"


def _windows_visual_preflight(powershell: str | None) -> dict[str, Any]:
    global _WINDOWS_PREFLIGHT_CACHE
    if sys.platform != "win32" or os.name != "nt":
        return {"checked": False, "ok": None, "error": None, "checks": {}}
    if not powershell:
        return {"checked": True, "ok": False, "error": "PowerShell is unavailable", "checks": {}}
    now = time.monotonic()
    if _WINDOWS_PREFLIGHT_CACHE and now - _WINDOWS_PREFLIGHT_CACHE[0] < _WINDOWS_PREFLIGHT_CACHE_TTL_SECONDS:
        return dict(_WINDOWS_PREFLIGHT_CACHE[1])
    script = "\n".join([
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
        "$OutputEncoding = [Console]::OutputEncoding",
        "$checks = [ordered]@{ winForms=$false; drawing=$false; virtualScreen=$false; systemIcon=$false; setClipboardCommand=$false; getClipboardCommand=$false; dpiAwareness=$false }",
        "$errors = [ordered]@{}",
        "$atriumDpiAwareness = $false",
        "try {",
        "  $dpiSig = '[DllImport(\"user32.dll\")] public static extern bool SetProcessDPIAware(); [DllImport(\"user32.dll\")] public static extern bool SetProcessDpiAwarenessContext(System.IntPtr dpiContext);'",
        "  Add-Type -MemberDefinition $dpiSig -Name Win32Dpi -Namespace ATRIUM -ErrorAction SilentlyContinue",
        "  try { if ([ATRIUM.Win32Dpi]::SetProcessDpiAwarenessContext([IntPtr](-4))) { $atriumDpiAwareness = $true } } catch {}",
        "  if (-not $atriumDpiAwareness) { try { if ([ATRIUM.Win32Dpi]::SetProcessDPIAware()) { $atriumDpiAwareness = $true } } catch {} }",
        "} catch { $errors.dpiAwareness = $_.Exception.Message }",
        "$checks.dpiAwareness = [bool]$atriumDpiAwareness",
        "try {",
        "  Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop",
        "  $checks.winForms = $true",
        "  $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen",
        "  $checks.virtualScreen = ($bounds.Width -gt 0 -and $bounds.Height -gt 0)",
        "} catch { $errors.winForms = $_.Exception.Message }",
        "try {",
        "  Add-Type -AssemblyName System.Drawing -ErrorAction Stop",
        "  $checks.drawing = $true",
        "  $checks.systemIcon = ($null -ne [System.Drawing.SystemIcons]::Information)",
        "} catch { $errors.drawing = $_.Exception.Message }",
        "$checks.setClipboardCommand = ($null -ne (Get-Command Set-Clipboard -ErrorAction SilentlyContinue))",
        "$checks.getClipboardCommand = ($null -ne (Get-Command Get-Clipboard -ErrorAction SilentlyContinue))",
        "$ok = $true",
        "foreach ($name in $checks.Keys) { if (-not [bool]$checks[$name]) { $ok = $false } }",
        "[PSCustomObject]@{ ok=$ok; checks=$checks; errors=$errors } | ConvertTo-Json -Compress -Depth 5",
    ])
    command = [powershell, "-NoProfile", "-NonInteractive", "-STA"]
    if Path(powershell).name.lower().startswith("powershell"):
        command.extend(["-ExecutionPolicy", "Bypass"])
    command.extend(["-Command", script])
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=3.0, check=False)
    except Exception as exc:
        result = {"checked": True, "ok": False, "error": f"{type(exc).__name__}: {exc}", "checks": {}}
        _WINDOWS_PREFLIGHT_CACHE = (now, result)
        return dict(result)
    parsed: dict[str, Any] = {}
    raw = str(completed.stdout or "").strip()
    with contextlib.suppress(Exception):
        parsed = json.loads(raw)
    checks = parsed.get("checks") if isinstance(parsed.get("checks"), dict) else {}
    errors = parsed.get("errors") if isinstance(parsed.get("errors"), dict) else {}
    ok = completed.returncode == 0 and parsed.get("ok") is True
    if ok:
        helper_result = _windows_visual_helper_selftest()
        helper_ok = (
            helper_result.get("returnCode") == 0
            and helper_result.get("ok") is True
            and _positive_int(helper_result.get("virtualWidth"))
            and _positive_int(helper_result.get("virtualHeight"))
        )
        checks = {**checks, "sendInputHelper": helper_ok}
        if not helper_ok:
            ok = False
            errors = {
                **errors,
                "sendInputHelper": str(
                    helper_result.get("stderr")
                    or helper_result.get("error")
                    or "Windows visual helper selftest failed"
                ),
            }
    error = None
    if not ok:
        detail = str(completed.stderr or "").strip()
        if errors:
            detail = "; ".join(f"{key}: {value}" for key, value in errors.items() if value) or detail
        failed_checks = [str(key) for key, value in checks.items() if value is not True]
        if failed_checks:
            detail = f"failed checks: {', '.join(failed_checks)}" if not detail else f"{detail}; failed checks: {', '.join(failed_checks)}"
        error = detail or f"PowerShell visual preflight exited with code {completed.returncode}"
    result = {"checked": True, "ok": ok, "error": error, "checks": checks}
    _WINDOWS_PREFLIGHT_CACHE = (now, result)
    return dict(result)


def _positive_int(value: Any) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def _windows_visual_helper_selftest() -> dict[str, Any]:
    try:
        from .visual_bridge import execute_windows_visual_selftest
    except Exception as exc:
        return {"returnCode": 1, "ok": False, "stderr": f"{type(exc).__name__}: {exc}"}
    return execute_windows_visual_selftest(_run_windows_preflight_process)


def _run_windows_preflight_process(
    command: list[str],
    *,
    timeout: float | None = 10.0,
    cwd: Path | None = None,
    **_: Any,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if exc.stdout is not None else getattr(exc, "output", None)
        return {
            "command": command,
            "cwd": str(cwd) if cwd else None,
            "returnCode": None,
            "timeout": True,
            "stdout": _decode_preflight_output(stdout),
            "stderr": _decode_preflight_output(exc.stderr) or f"command timed out after {timeout}s",
        }
    except Exception as exc:
        return {
            "command": command,
            "cwd": str(cwd) if cwd else None,
            "returnCode": 1,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }
    return {
        "command": command,
        "cwd": str(cwd) if cwd else None,
        "returnCode": completed.returncode,
        "stdout": _decode_preflight_output(completed.stdout),
        "stderr": _decode_preflight_output(completed.stderr),
    }


def _decode_preflight_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _windows_current_session_id() -> int | None:
    try:
        session_id = ctypes.c_ulong()
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        ok = kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(session_id))
        return int(session_id.value) if ok else None
    except Exception:
        return None


def _windows_interactive_session() -> tuple[bool | None, str | None, int | None]:
    session_name = str(os.environ.get("SESSIONNAME") or "").strip()
    session_id = _windows_current_session_id()
    lowered = session_name.lower()
    if lowered in {"services", "service"}:
        return False, session_name, session_id
    if session_id == 0:
        return False, session_name or None, session_id
    if lowered == "console" or lowered.startswith(("rdp-", "ica-")):
        return True, session_name, session_id
    if session_id is not None and session_id > 0:
        return True, session_name or None, session_id
    if not session_name:
        return None, None, None
    return None, session_name, session_id
