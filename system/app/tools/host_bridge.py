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
from dataclasses import dataclass, field
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
_MACOS_PREFLIGHT_CACHE_TTL_SECONDS = 10.0
_MACOS_PREFLIGHT_CACHE: tuple[float, dict[str, Any]] | None = None
_MACOS_ACCESSIBILITY_CACHE_TTL_SECONDS = 10.0
_MACOS_ACCESSIBILITY_CACHE: tuple[float, bool | None] | None = None
_BROWSER_PLAYWRIGHT_PACKAGE_CACHE_TTL_SECONDS = 10.0
_BROWSER_PLAYWRIGHT_PACKAGE_CACHE: tuple[float, str, dict[str, Any]] | None = None
_WINDOWS_VISUAL_PREFLIGHT_TOOLS = {
    "browser.screenshot",
    "browser.click",
    "browser.type",
    "browser.keypress",
    "browser.paste_text",
    "browser.scroll",
    "desktop.screenshot",
    "desktop.snapshot",
    "desktop.act",
    "desktop.click",
    "desktop.type",
    "desktop.keypress",
    "desktop.paste_text",
    "desktop.scroll",
    "notify.send",
}
_MACOS_VISUAL_PREFLIGHT_TOOLS = {
    "browser.screenshot",
    "browser.click",
    "browser.type",
    "browser.keypress",
    "browser.paste_text",
    "browser.scroll",
    "desktop.screenshot",
    "desktop.snapshot",
    "desktop.act",
    "desktop.click",
    "desktop.type",
    "desktop.keypress",
    "desktop.paste_text",
    "desktop.scroll",
    "notify.send",
}
_MACOS_FOREGROUND_SESSION_TOOLS = {
    "browser.click",
    "browser.type",
    "browser.keypress",
    "browser.paste_text",
    "browser.scroll",
    "desktop.activate_app",
    "desktop.act",
    "desktop.click",
    "desktop.type",
    "desktop.keypress",
    "desktop.paste_text",
    "desktop.scroll",
}
_BROWSER_PLAYWRIGHT_CONTROL_TOOLS = {"browser.snapshot", "browser.act"}


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
    browser_playwright_ready: bool
    browser_playwright_package: str | None
    browser_playwright_error: str | None
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
    macos_visual_preflight_checked: bool = False
    macos_visual_preflight_ok: bool | None = None
    macos_visual_preflight_error: str | None = None
    macos_visual_preflight_checks: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

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
            "browserPlaywrightReady": self.browser_playwright_ready,
            "browserPlaywrightPackage": self.browser_playwright_package,
            "browserPlaywrightError": self.browser_playwright_error,
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
            "macosVisualPreflightChecked": self.macos_visual_preflight_checked,
            "macosVisualPreflightOk": self.macos_visual_preflight_ok,
            "macosVisualPreflightError": self.macos_visual_preflight_error,
            "macosVisualPreflightChecks": self.macos_visual_preflight_checks,
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
        macos_visual_preflight = {
            "checked": False,
            "ok": None,
            "error": None,
            "checks": {},
        }
        if platform == "darwin":
            browser_executable = _first_executable(("open",))
            desktop_executable = _first_executable(("osascript",))
            macos_visual_preflight = _macos_visual_preflight()
            macos_checks = macos_visual_preflight.get("checks") if isinstance(macos_visual_preflight.get("checks"), dict) else {}
            browser = bool(browser_executable and shutil.which("osascript"))
            browser_ready = browser and macos_checks.get("open") is True and macos_checks.get("osascript") is True and macos_checks.get("screencapture") is True
            desktop = bool(desktop_executable and shutil.which("screencapture"))
            desktop_ready = desktop and macos_visual_preflight.get("ok") is True
            if macos_visual_preflight.get("checked") is True and macos_visual_preflight.get("ok") is False:
                detail = str(macos_visual_preflight.get("error") or "visual preflight failed")
                notes.append(f"macOS visual automation preflight failed: {detail}")
            if desktop_ready:
                notes.append("macOS foreground switching is not proven by non-invasive status; run ops/macos_host_bridge_probe.py --full for live desktop proof.")
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
        node_executable = _node_executable()
        playwright_package_status = _browser_playwright_package_status(node_executable) if node_executable else {
            "ok": False,
            "package": None,
            "error": "Node.js is required for browser.snapshot/browser.act",
        }
        browser_playwright_ready = bool(node_executable and isolated_browser_profile_ready and playwright_package_status.get("ok"))
        if browser_ready and not isolated_browser_profile_ready:
            notes.append("Isolated browser profiles require Chrome, Edge, Brave, or Chromium.")
        if browser_ready and isolated_browser_profile_ready and node_executable and not playwright_package_status.get("ok"):
            notes.append("browser.snapshot/browser.act require the Playwright package (`playwright` or `@playwright/test`).")
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
            browser_playwright_ready=browser_playwright_ready,
            browser_playwright_package=playwright_package_status.get("package"),
            browser_playwright_error=playwright_package_status.get("error"),
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
            macos_visual_preflight_checked=bool(macos_visual_preflight.get("checked")),
            macos_visual_preflight_ok=macos_visual_preflight.get("ok"),
            macos_visual_preflight_error=macos_visual_preflight.get("error"),
            macos_visual_preflight_checks=macos_visual_preflight.get("checks") if isinstance(macos_visual_preflight.get("checks"), dict) else {},
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
            if tool_name in _BROWSER_PLAYWRIGHT_CONTROL_TOOLS:
                if _requests_user_browser_profile(args):
                    return False, "browser.snapshot/browser.act require an isolated browser profile"
                if not _node_executable():
                    return False, "Node.js is required for browser.snapshot/browser.act"
                if not status.isolated_browser_profile_ready:
                    return False, "deterministic browser control requires Chrome, Edge, Brave, or Chromium"
                if not status.browser_playwright_ready:
                    detail = str(status.browser_playwright_error or "").strip()
                    return False, detail or "Playwright package is required for browser.snapshot/browser.act"
            if tool_name in _WINDOWS_VISUAL_PREFLIGHT_TOOLS and status.windows_visual_preflight_checked and status.windows_visual_preflight_ok is False:
                return False, _windows_visual_preflight_reason(status)
            if tool_name == "browser.open" and _requests_isolated_browser_profile(args) and not status.isolated_browser_profile_ready:
                return False, "isolated browser profile requires Chrome, Edge, Brave, or Chromium"
            if status.platform == "darwin":
                if tool_name == "browser.open" and _macos_preflight_failed_for(status, {"open"}):
                    return False, _macos_visual_preflight_reason(status, {"open"})
                if tool_name == "browser.screenshot" and _macos_preflight_failed_for(status, {"screencapture"}):
                    return False, _macos_visual_preflight_reason(status, {"screencapture"})
                if tool_name == "browser.paste_text" and _macos_preflight_failed_for(status, {"osascript"}):
                    return False, _macos_visual_preflight_reason(status, {"osascript"})
                if tool_name in _MACOS_FOREGROUND_SESSION_TOOLS and _macos_preflight_failed_for(status, {"foregroundSession"}):
                    return False, _macos_visual_preflight_reason(status, {"foregroundSession"})
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
                if tool_name == "desktop.open_app" and _macos_preflight_failed_for(status, {"open"}):
                    return False, _macos_visual_preflight_reason(status, {"open"})
                if tool_name in {"desktop.apps", "desktop.activate_app", "desktop.quit_app", "desktop.paste_text"} and _macos_preflight_failed_for(status, {"osascript"}):
                    return False, _macos_visual_preflight_reason(status, {"osascript"})
                if tool_name in {"desktop.snapshot", "desktop.act"} and _macos_preflight_failed_for(status, {"osascript", "accessibility"}):
                    return False, _macos_visual_preflight_reason(status, {"osascript", "accessibility"})
                if tool_name == "desktop.screenshot" and _macos_preflight_failed_for(status, {"screencapture"}):
                    return False, _macos_visual_preflight_reason(status, {"screencapture"})
                if tool_name in _MACOS_FOREGROUND_SESSION_TOOLS and _macos_preflight_failed_for(status, {"foregroundSession"}):
                    if tool_name == "desktop.act" and _requests_native_desktop_ref_action(args):
                        return True, None
                    return False, _macos_visual_preflight_reason(status, {"foregroundSession"})
                if tool_name == "desktop.open_app" and not shutil.which("open"):
                    return False, "macOS open command is unavailable"
                if tool_name in {"desktop.apps", "desktop.snapshot", "desktop.activate_app", "desktop.quit_app"} and not shutil.which("osascript"):
                    return False, "macOS osascript bridge is unavailable"
                if tool_name in {"desktop.snapshot", "desktop.act"} and _macos_accessibility_enabled() is False:
                    return False, "macOS Accessibility permission is disabled for System Events"
                if tool_name == "desktop.screenshot" and not shutil.which("screencapture"):
                    return False, "macOS screencapture is unavailable"
            if tool_name not in {"desktop.apps", "desktop.snapshot", "desktop.open_app", "desktop.activate_app", "desktop.quit_app", "desktop.screenshot"} and not status.desktop_bridge:
                return False, f"{status.platform} desktop bridge unavailable"
        if tool_name == "notify.send":
            if status.platform == "win32" and status.interactive_session is False:
                return False, "win32 interactive desktop session unavailable"
            if status.platform == "win32" and status.interactive_session is None:
                return False, "win32 interactive desktop session could not be verified"
            if status.platform == "win32" and status.windows_visual_preflight_checked and status.windows_visual_preflight_ok is False:
                return False, _windows_visual_preflight_reason(status)
            if status.platform == "darwin" and _macos_preflight_failed_for(status, {"osascript"}):
                return False, _macos_visual_preflight_reason(status, {"osascript"})
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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _node_executable() -> str | None:
    return shutil.which("node") or shutil.which("node.exe")


def _browser_playwright_package_status(node_executable: str | None = None) -> dict[str, Any]:
    global _BROWSER_PLAYWRIGHT_PACKAGE_CACHE
    node = node_executable or _node_executable()
    if not node:
        return {
            "ok": False,
            "package": None,
            "path": None,
            "error": "Node.js is required for browser.snapshot/browser.act",
        }
    now = time.monotonic()
    if (
        _BROWSER_PLAYWRIGHT_PACKAGE_CACHE
        and _BROWSER_PLAYWRIGHT_PACKAGE_CACHE[1] == node
        and now - _BROWSER_PLAYWRIGHT_PACKAGE_CACHE[0] < _BROWSER_PLAYWRIGHT_PACKAGE_CACHE_TTL_SECONDS
    ):
        return dict(_BROWSER_PLAYWRIGHT_PACKAGE_CACHE[2])
    ui_root = _repo_root() / "ui"
    cwd = ui_root if ui_root.exists() else _repo_root()
    script = (
        "const packages = ['playwright', '@playwright/test'];"
        "const errors = [];"
        "for (const name of packages) {"
        "  try {"
        "    const resolved = require.resolve(name);"
        "    require(name);"
        "    console.log(JSON.stringify({ok:true, package:name, path:resolved}));"
        "    process.exit(0);"
        "  } catch (error) {"
        "    errors.push(`${name}: ${error && error.message ? error.message : String(error)}`);"
        "  }"
        "}"
        "console.log(JSON.stringify({ok:false, package:null, path:null, error:errors.join(' | ')}));"
        "process.exit(1);"
    )
    try:
        completed = subprocess.run(
            [node, "-e", script],
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=5.0,
        )
        raw = (completed.stdout or "").strip().splitlines()[-1] if (completed.stdout or "").strip() else ""
        parsed = json.loads(raw) if raw else {}
        if not isinstance(parsed, dict):
            parsed = {}
        status = {
            "ok": bool(parsed.get("ok")),
            "package": str(parsed.get("package") or "") or None,
            "path": str(parsed.get("path") or "") or None,
            "error": str(parsed.get("error") or completed.stderr or "").strip() or None,
        }
    except Exception as exc:
        status = {
            "ok": False,
            "package": None,
            "path": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    _BROWSER_PLAYWRIGHT_PACKAGE_CACHE = (now, node, status)
    return dict(status)


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


def _requests_user_browser_profile(args: dict[str, Any] | None) -> bool:
    if not args:
        return False
    raw = args.get("profile") or args.get("browserProfile")
    value = str(raw or "").strip().lower()
    return value in {"user", "default", "host", "personal"}


def _truthy_arg(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _requests_native_desktop_ref_action(args: dict[str, Any] | None) -> bool:
    if not args:
        return False
    ref = str(args.get("ref") or "").strip()
    return bool(ref and _truthy_arg(args.get("requireNative")))


def _windows_visual_preflight_reason(status: HostBridgeStatus) -> str:
    detail = str(status.windows_visual_preflight_error or "").strip()
    return f"win32 visual automation preflight failed: {detail}" if detail else "win32 visual automation preflight failed"


def _macos_visual_preflight_reason(status: HostBridgeStatus, required: set[str] | None = None) -> str:
    detail = str(status.macos_visual_preflight_error or "").strip()
    checks = status.macos_visual_preflight_checks if isinstance(status.macos_visual_preflight_checks, dict) else {}
    failed = [name for name in sorted(required or set(checks)) if checks.get(name) is not True]
    if failed:
        failed_text = f"failed checks: {', '.join(failed)}"
        detail = f"{detail}; {failed_text}" if detail and failed_text not in detail else (detail or failed_text)
    return f"darwin visual automation preflight failed: {detail}" if detail else "darwin visual automation preflight failed"


def _macos_preflight_failed_for(status: HostBridgeStatus, required: set[str]) -> bool:
    if status.platform != "darwin" or status.macos_visual_preflight_checked is not True:
        return False
    checks = status.macos_visual_preflight_checks if isinstance(status.macos_visual_preflight_checks, dict) else {}
    for name in required:
        value = checks.get(name)
        if name == "foregroundSession" and value is None:
            continue
        if value is not True:
            return True
    return False


def _macos_foreground_session_status() -> dict[str, Any]:
    if sys.platform != "darwin":
        return {"checked": False, "ok": None, "error": None, "details": {}}
    try:
        from . import visual_bridge

        helper = visual_bridge._ensure_snapshot_helper()
    except Exception as exc:
        return {
            "checked": False,
            "ok": None,
            "error": f"native foreground session helper unavailable: {exc}",
            "details": {},
        }
    if helper is None:
        return {
            "checked": False,
            "ok": None,
            "error": "native foreground session helper unavailable",
            "details": {},
        }
    try:
        completed = subprocess.run(
            [str(helper), "", "", "5", "1"],
            text=True,
            capture_output=True,
            timeout=5.0,
            check=False,
        )
    except Exception as exc:
        return {
            "checked": True,
            "ok": False,
            "error": f"native foreground session check failed: {exc}",
            "details": {},
        }
    parsed: Any = None
    raw_stdout = str(completed.stdout or "").strip()
    if raw_stdout:
        try:
            parsed = json.loads(raw_stdout)
        except json.JSONDecodeError:
            parsed = None
    details = parsed if isinstance(parsed, dict) else {}
    app_name = str(details.get("appName") or "").strip()
    process_id = details.get("processId")
    if completed.returncode != 0:
        detail = str(details.get("error") or completed.stderr or completed.stdout or "foreground session check failed").strip()
        return {
            "checked": True,
            "ok": False,
            "error": detail,
            "details": details,
        }
    if not app_name:
        return {
            "checked": True,
            "ok": False,
            "error": "native foreground session check did not return an active app",
            "details": details,
        }
    if app_name.lower() == "loginwindow":
        return {
            "checked": True,
            "ok": False,
            "error": "macOS foreground session is loginwindow; user GUI session is not foreground-controllable",
            "details": details,
        }
    return {
        "checked": True,
        "ok": True,
        "error": None,
        "details": {
            "appName": app_name,
            "processId": process_id,
            "title": details.get("title"),
            "windowCount": details.get("windowCount"),
            "method": details.get("method") or "native_ax_snapshot",
        },
    }


def _macos_accessibility_enabled() -> bool | None:
    global _MACOS_ACCESSIBILITY_CACHE
    if sys.platform != "darwin":
        return None
    now = time.monotonic()
    if _MACOS_ACCESSIBILITY_CACHE and now - _MACOS_ACCESSIBILITY_CACHE[0] < _MACOS_ACCESSIBILITY_CACHE_TTL_SECONDS:
        return _MACOS_ACCESSIBILITY_CACHE[1]
    osascript = shutil.which("osascript")
    if not osascript:
        _MACOS_ACCESSIBILITY_CACHE = (now, None)
        return None
    try:
        completed = subprocess.run(
            [osascript, "-e", 'tell application "System Events" to UI elements enabled'],
            text=True,
            capture_output=True,
            timeout=3.0,
            check=False,
        )
    except Exception:
        _MACOS_ACCESSIBILITY_CACHE = (now, None)
        return None
    value = str(completed.stdout or "").strip().lower()
    enabled = True if value == "true" else (False if value == "false" else None)
    _MACOS_ACCESSIBILITY_CACHE = (now, enabled)
    return enabled


def _macos_visual_preflight() -> dict[str, Any]:
    global _MACOS_PREFLIGHT_CACHE
    if sys.platform != "darwin":
        return {"checked": False, "ok": None, "error": None, "checks": {}}
    now = time.monotonic()
    if _MACOS_PREFLIGHT_CACHE and now - _MACOS_PREFLIGHT_CACHE[0] < _MACOS_PREFLIGHT_CACHE_TTL_SECONDS:
        return dict(_MACOS_PREFLIGHT_CACHE[1])
    accessibility = _macos_accessibility_enabled()
    foreground_session = (
        _macos_foreground_session_status()
        if accessibility is True
        else {"checked": False, "ok": None, "error": None, "details": {}}
    )
    checks: dict[str, Any] = {
        "open": bool(shutil.which("open")),
        "osascript": bool(shutil.which("osascript")),
        "screencapture": bool(shutil.which("screencapture")),
        "accessibility": accessibility is True,
        "foregroundSession": foreground_session.get("ok") if foreground_session.get("checked") else None,
    }
    foreground_details = foreground_session.get("details") if isinstance(foreground_session.get("details"), dict) else {}
    if foreground_details.get("appName"):
        checks["foregroundAppName"] = foreground_details.get("appName")
    if foreground_details.get("processId") is not None:
        checks["foregroundProcessId"] = foreground_details.get("processId")
    if foreground_details.get("windowCount") is not None:
        checks["foregroundWindowCount"] = foreground_details.get("windowCount")
    errors: dict[str, str] = {}
    if accessibility is None:
        errors["accessibility"] = "macOS Accessibility permission could not be verified"
    elif accessibility is False:
        errors["accessibility"] = "macOS Accessibility permission is disabled for System Events"
    if foreground_session.get("checked") and foreground_session.get("ok") is False:
        errors["foregroundSession"] = str(foreground_session.get("error") or "macOS foreground session could not be verified")
    failed_checks = []
    for name, value in checks.items():
        if name in {"foregroundAppName", "foregroundProcessId", "foregroundWindowCount"}:
            continue
        if name == "foregroundSession" and value is None:
            continue
        if value is not True:
            failed_checks.append(name)
    ok = not failed_checks
    detail = "; ".join(f"{key}: {value}" for key, value in errors.items() if value)
    if failed_checks:
        failed_text = f"failed checks: {', '.join(failed_checks)}"
        detail = f"{detail}; {failed_text}" if detail else failed_text
    result = {"checked": True, "ok": ok, "error": detail or None, "checks": checks}
    _MACOS_PREFLIGHT_CACHE = (now, result)
    return dict(result)


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
