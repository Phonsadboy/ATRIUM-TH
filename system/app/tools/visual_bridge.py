"""Shared helpers for local visual automation tools."""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from ..clock import now_ms
from ..config import get_settings
from ..file_intake import safe_filename
from ..ids import uid
from ..schema import Artifact, ArtifactVersion


_SCROLL_KEY_CODES: dict[tuple[str, str], int] = {
    ("down", "page"): 121,
    ("up", "page"): 116,
    ("down", "line"): 125,
    ("up", "line"): 126,
    ("right", "page"): 124,
    ("left", "page"): 123,
    ("right", "line"): 124,
    ("left", "line"): 123,
}
_VISUAL_PROCESS_TOOLS = {
    "browser.profiles",
    "browser.open",
    "browser.screenshot",
    "browser.click",
    "browser.type",
    "browser.keypress",
    "browser.paste_text",
    "browser.scroll",
    "desktop.screenshot",
    "desktop.apps",
    "desktop.open_app",
    "desktop.activate_app",
    "desktop.quit_app",
    "desktop.click",
    "desktop.type",
    "desktop.keypress",
    "desktop.paste_text",
    "desktop.scroll",
    "notify.send",
}
_USER_BROWSER_PROFILE_ALIASES = {"", "user", "default", "host", "personal"}
_OWN_BROWSER_PROFILE_ALIASES = {"atrium", "own", "agent", "system", "isolated"}
_BROWSER_PROFILE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}$")
_BROWSER_APP_CANDIDATES = [
    ("Google Chrome", Path("/Applications/Google Chrome.app")),
    ("Google Chrome", Path.home() / "Applications/Google Chrome.app"),
    ("Brave Browser", Path("/Applications/Brave Browser.app")),
    ("Brave Browser", Path.home() / "Applications/Brave Browser.app"),
    ("Microsoft Edge", Path("/Applications/Microsoft Edge.app")),
    ("Microsoft Edge", Path.home() / "Applications/Microsoft Edge.app"),
    ("Chromium", Path("/Applications/Chromium.app")),
    ("Chromium", Path.home() / "Applications/Chromium.app"),
    ("Google Chrome Canary", Path("/Applications/Google Chrome Canary.app")),
    ("Google Chrome Canary", Path.home() / "Applications/Google Chrome Canary.app"),
]


def _windows_browser_candidates() -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    standard_roots = [
        Path("C:/Program Files"),
        Path("C:/Program Files (x86)"),
    ]
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if not base:
            continue
        root = Path(base)
        if root not in standard_roots:
            standard_roots.append(root)
    for root in standard_roots:
        candidates.extend([
            ("Google Chrome", root / "Google/Chrome/Application/chrome.exe"),
            ("Microsoft Edge", root / "Microsoft/Edge/Application/msedge.exe"),
            ("Brave Browser", root / "BraveSoftware/Brave-Browser/Application/brave.exe"),
            ("Chromium", root / "Chromium/Application/chrome.exe"),
        ])
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        root = Path(local_app_data)
        candidates.extend([
            ("Google Chrome", root / "Google/Chrome/Application/chrome.exe"),
            ("Microsoft Edge", root / "Microsoft/Edge/Application/msedge.exe"),
            ("Brave Browser", root / "BraveSoftware/Brave-Browser/Application/brave.exe"),
        ])
    return candidates


def _windows_start_menu_dirs() -> list[Path]:
    dirs: list[Path] = []
    program_data = os.environ.get("ProgramData")
    app_data = os.environ.get("APPDATA")
    if program_data:
        dirs.append(Path(program_data) / "Microsoft/Windows/Start Menu/Programs")
    if app_data:
        dirs.append(Path(app_data) / "Microsoft/Windows/Start Menu/Programs")
    return dirs


_APP_SEARCH_DIRS = [
    Path("/Applications"),
    Path("/Applications/Utilities"),
    Path("/System/Applications"),
    Path("/System/Applications/Utilities"),
    Path.home() / "Applications",
]
_WINDOWS_POWERSHELL_CANDIDATES = (
    "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    "C:/Windows/SysWOW64/WindowsPowerShell/v1.0/powershell.exe",
    "C:/Program Files/PowerShell/7/pwsh.exe",
    "C:/Program Files (x86)/PowerShell/7/pwsh.exe",
)

_WINDOWS_VISUAL_HELPER_SOURCE = r'''
import ctypes
import json
import sys
import time

user32 = ctypes.windll.user32

def _configure_user32_signatures():
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware.argtypes = ()
        user32.SetProcessDPIAware.restype = ctypes.c_bool
    except Exception:
        pass
    try:
        user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        user32.SetCursorPos.restype = ctypes.c_bool
    except Exception:
        pass
    try:
        user32.VkKeyScanW.argtypes = (ctypes.c_wchar,)
        user32.VkKeyScanW.restype = ctypes.c_short
    except Exception:
        pass
    try:
        user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
        user32.GetSystemMetrics.restype = ctypes.c_int
    except Exception:
        pass

_configure_user32_signatures()
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_EXTENDEDKEY = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000
WHEEL_DELTA = 120
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

def _enable_dpi_awareness():
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per_monitor_v2"
    except Exception:
        pass
    try:
        if user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass
    return "unverified"

_DPI_AWARENESS = _enable_dpi_awareness()
_EXTENDED_KEY_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x6F}

def _payload():
    return json.loads(sys.argv[2] if len(sys.argv) > 2 else "{}")

def _ok(**extra):
    print(json.dumps({"ok": True, "dpiAwareness": _DPI_AWARENESS, **extra}, separators=(",", ":")))

def _fail(message, **extra):
    print(json.dumps({"ok": False, "error": str(message), "dpiAwareness": _DPI_AWARENESS, **extra}, separators=(",", ":")))
    raise SystemExit(1)

def _virtual_bounds():
    left = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    top = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    return left, top, width, height

def _assert_point_in_virtual_screen(x, y):
    left, top, width, height = _virtual_bounds()
    if width <= 0 or height <= 0:
        return
    if int(x) < left or int(y) < top or int(x) >= left + width or int(y) >= top + height:
        raise OSError(f"coordinates outside virtual screen bounds: {x},{y} not in {left},{top},{width}x{height}")

def _send_vk(vk, down=True):
    flags = KEYEVENTF_EXTENDEDKEY if int(vk) in _EXTENDED_KEY_VKS else 0
    if not down:
        flags |= KEYEVENTF_KEYUP
    _send_key_event(vk, flags=flags)

def _click():
    data = _payload()
    x = int(float(data.get("x")))
    y = int(float(data.get("y")))
    button = str(data.get("button") or "left").lower()
    _assert_point_in_virtual_screen(x, y)
    if not user32.SetCursorPos(x, y):
        raise OSError("SetCursorPos failed")
    time.sleep(0.03)
    events = {
        "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    }
    down, up = events.get(button, events["left"])
    _send_mouse_event(down)
    time.sleep(0.05)
    _send_mouse_event(up)
    _ok(mode="click", x=x, y=y, button=button, inputMethod="sendinput")

def _keypress():
    data = _payload()
    raw_keys = data.get("keys")
    if not isinstance(raw_keys, list) or not raw_keys:
        raise SystemExit("keypress requires keys list")
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
    modifier_vks = {"control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B}
    modifiers = [modifier_aliases[item] for item in normalized if item in modifier_aliases]
    key_parts = [item for item in normalized if item not in modifier_aliases]
    if len(key_parts) != 1:
        raise SystemExit("keypress requires exactly one non-modifier key")
    aliases = {
        "enter": "return",
        "esc": "escape",
        "backspace": "delete",
        "forward_delete": "forwarddelete",
        "del": "forwarddelete",
        "ins": "insert",
        "page_down": "pagedown",
        "page up": "pageup",
        "page_up": "pageup",
        "page down": "pagedown",
    }
    key = aliases.get(key_parts[0], key_parts[0])
    special = {
        "return": 0x0D,
        "tab": 0x09,
        "space": 0x20,
        "delete": 0x08,
        "escape": 0x1B,
        "left": 0x25,
        "up": 0x26,
        "right": 0x27,
        "down": 0x28,
        "home": 0x24,
        "end": 0x23,
        "pageup": 0x21,
        "pagedown": 0x22,
        "forwarddelete": 0x2E,
        "insert": 0x2D,
    }
    implicit_modifiers = []
    if key in special:
        vk = special[key]
    elif len(key) == 1:
        code = int(user32.VkKeyScanW(key))
        if code == -1:
            raise SystemExit(f"unsupported key: {key}")
        vk = code & 0xFF
        shift_state = (code >> 8) & 0xFF
        if shift_state & 1:
            implicit_modifiers.append("shift")
        if shift_state & 2:
            implicit_modifiers.append("control")
        if shift_state & 4:
            implicit_modifiers.append("alt")
    else:
        raise SystemExit(f"unsupported key: {key}")
    final_modifiers = []
    for mod in [*modifiers, *implicit_modifiers]:
        if mod not in final_modifiers:
            final_modifiers.append(mod)
    for mod in final_modifiers:
        _send_vk(modifier_vks[mod], True)
        time.sleep(0.01)
    _send_vk(vk, True)
    time.sleep(0.03)
    _send_vk(vk, False)
    for mod in reversed(final_modifiers):
        time.sleep(0.01)
        _send_vk(modifier_vks[mod], False)
    _ok(mode="keypress", key=key, modifiers=final_modifiers, inputMethod="sendinput")

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_short), ("wParamH", ctypes.c_ushort)]

class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", INPUT_UNION)]

def _configure_sendinput_signature():
    try:
        user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
        user32.SendInput.restype = ctypes.c_uint
    except Exception:
        pass

_configure_sendinput_signature()

def _send_mouse_event(flags, data=0):
    inp = INPUT(type=0, union=INPUT_UNION(mi=MOUSEINPUT(0, 0, int(data), flags, 0, 0)))
    sent = user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))
    if sent != 1:
        raise OSError("SendInput mouse event failed")

def _send_key_event(vk, scan=0, flags=0):
    inp = INPUT(type=1, union=INPUT_UNION(ki=KEYBDINPUT(int(vk), int(scan), int(flags), 0, 0)))
    sent = user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))
    if sent != 1:
        raise OSError("SendInput key event failed")

def _send_mouse_wheel(delta, horizontal=False):
    flags = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    inp = INPUT(type=0, union=INPUT_UNION(mi=MOUSEINPUT(0, 0, int(delta), flags, 0, 0)))
    sent = user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))
    if sent != 1:
        raise OSError("SendInput mouse wheel failed")

def _scroll():
    data = _payload()
    direction = str(data.get("direction") or "down").lower()
    unit = str(data.get("unit") or "page").lower()
    amount = max(1, min(int(data.get("amount") or 1), 40))
    x = data.get("x")
    y = data.get("y")
    if x is not None and y is not None:
        target_x = int(float(x))
        target_y = int(float(y))
        _assert_point_in_virtual_screen(target_x, target_y)
        if not user32.SetCursorPos(target_x, target_y):
            raise OSError("SetCursorPos failed")
        time.sleep(0.02)
    horizontal = direction in {"left", "right"}
    base_steps = 5 if unit == "page" else 1
    steps = max(1, min(amount * base_steps, 80))
    sign = 1 if direction in {"up", "right"} else -1
    delta = WHEEL_DELTA * sign
    delay = max(0.0, min(float(data.get("delayMs") or 25) / 1000.0, 0.5))
    for _ in range(steps):
        _send_mouse_wheel(delta, horizontal)
        if delay:
            time.sleep(delay)
    _ok(mode="scroll", direction=direction, unit=unit, amount=amount, steps=steps, wheelDelta=delta, horizontal=horizontal, x=x, y=y, inputMethod="sendinput")

def _send_unicode_unit(unit, keyup=False):
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if keyup else 0)
    inp = INPUT(type=1, union=INPUT_UNION(ki=KEYBDINPUT(0, int(unit), flags, 0, 0)))
    sent = user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))
    if sent != 1:
        raise OSError("SendInput failed")

def _type_text():
    data = _payload()
    text = data.get("text")
    if not isinstance(text, str):
        raise SystemExit("type requires text")
    raw = text.encode("utf-16-le", errors="surrogatepass")
    units = [raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2)]
    for unit in units:
        _send_unicode_unit(unit, False)
        _send_unicode_unit(unit, True)
        time.sleep(0.003)
    _ok(mode="type", textBytes=len(text.encode("utf-8")), textCharacters=len(text), textUnits=len(units), inputMethod="sendinput")

def _selftest():
    screen_width = user32.GetSystemMetrics(0)
    screen_height = user32.GetSystemMetrics(1)
    virtual_left, virtual_top, virtual_width, virtual_height = _virtual_bounds()
    _ok(
        mode="selftest",
        screenWidth=int(screen_width),
        screenHeight=int(screen_height),
        virtualLeft=int(virtual_left),
        virtualTop=int(virtual_top),
        virtualWidth=int(virtual_width),
        virtualHeight=int(virtual_height),
    )

mode = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    if mode == "click":
        _click()
    elif mode == "keypress":
        _keypress()
    elif mode == "type":
        _type_text()
    elif mode == "scroll":
        _scroll()
    elif mode == "selftest":
        _selftest()
    else:
        raise ValueError(f"unknown mode: {mode}")
except SystemExit:
    raise
except Exception as exc:
    _fail(exc, mode=mode)
'''

_CLICK_HELPER_SOURCE = r'''
import CoreGraphics
import Foundation

let args = CommandLine.arguments
if args.count < 3 {
    FileHandle.standardError.write(Data("usage: macos_click X Y [left|right]\n".utf8))
    exit(64)
}
guard let x = Double(args[1]), let y = Double(args[2]) else {
    FileHandle.standardError.write(Data("x and y must be numbers\n".utf8))
    exit(64)
}
let rawButton = args.count >= 4 ? args[3].lowercased() : "left"
let button: CGMouseButton = rawButton == "right" ? .right : .left
let downType: CGEventType = rawButton == "right" ? .rightMouseDown : .leftMouseDown
let upType: CGEventType = rawButton == "right" ? .rightMouseUp : .leftMouseUp
let point = CGPoint(x: x, y: y)
let source = CGEventSource(stateID: .hidSystemState)

guard let down = CGEvent(mouseEventSource: source, mouseType: downType, mouseCursorPosition: point, mouseButton: button),
      let up = CGEvent(mouseEventSource: source, mouseType: upType, mouseCursorPosition: point, mouseButton: button) else {
    FileHandle.standardError.write(Data("failed to create mouse events\n".utf8))
    exit(70)
}
down.post(tap: .cghidEventTap)
usleep(50000)
up.post(tap: .cghidEventTap)
'''

_KEY_HELPER_SOURCE = r'''
import CoreGraphics
import Foundation

let keyCodes: [String: CGKeyCode] = [
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
    "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29,
    "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35,
    "return": 36, "enter": 36, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47,
    "tab": 48, "space": 49, "`": 50, "delete": 51, "backspace": 51, "escape": 53, "esc": 53,
    "home": 115, "pageup": 116, "page_up": 116, "forwarddelete": 117, "end": 119, "pagedown": 121, "page_down": 121,
    "left": 123, "right": 124, "down": 125, "up": 126
]

func flags(_ modifiers: [String]) -> CGEventFlags {
    var out = CGEventFlags()
    for raw in modifiers {
        switch raw.lowercased() {
        case "cmd", "command", "meta": out.insert(.maskCommand)
        case "win", "windows", "super": out.insert(.maskCommand)
        case "ctrl", "control": out.insert(.maskControl)
        case "alt", "option": out.insert(.maskAlternate)
        case "shift": out.insert(.maskShift)
        default: break
        }
    }
    return out
}

func postKey(_ keyCode: CGKeyCode, modifiers: [String] = []) {
    let source = CGEventSource(stateID: .hidSystemState)
    let eventFlags = flags(modifiers)
    let down = CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: true)
    down?.flags = eventFlags
    down?.post(tap: .cghidEventTap)
    usleep(15000)
    let up = CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: false)
    up?.flags = eventFlags
    up?.post(tap: .cghidEventTap)
    usleep(15000)
}

func postText(_ text: String) {
    let source = CGEventSource(stateID: .hidSystemState)
    for character in text {
        let units = Array(String(character).utf16)
        units.withUnsafeBufferPointer { buffer in
            guard let base = buffer.baseAddress else { return }
            let down = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true)
            down?.keyboardSetUnicodeString(stringLength: units.count, unicodeString: base)
            down?.post(tap: .cghidEventTap)
            usleep(8000)
            let up = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false)
            up?.keyboardSetUnicodeString(stringLength: units.count, unicodeString: base)
            up?.post(tap: .cghidEventTap)
            usleep(8000)
        }
    }
}

let args = CommandLine.arguments
if args.count < 2 {
    FileHandle.standardError.write(Data("usage: macos_keys type TEXT | press KEY [modifiers...]\n".utf8))
    exit(64)
}

let mode = args[1]
if mode == "type" {
    if args.count < 3 {
        FileHandle.standardError.write(Data("type requires text\n".utf8))
        exit(64)
    }
    postText(args[2])
} else if mode == "press" {
    if args.count < 3 {
        FileHandle.standardError.write(Data("press requires key\n".utf8))
        exit(64)
    }
    let key = args[2].lowercased()
    guard let code = keyCodes[key] else {
        FileHandle.standardError.write(Data("unsupported key: \(key)\n".utf8))
        exit(64)
    }
    postKey(code, modifiers: Array(args.dropFirst(3)))
} else {
    FileHandle.standardError.write(Data("unknown mode: \(mode)\n".utf8))
    exit(64)
}
'''

_MODIFIER_ALIASES = {
    "cmd": "cmd",
    "command": "cmd",
    "meta": "cmd",
    "ctrl": "control",
    "control": "control",
    "alt": "option",
    "option": "option",
    "shift": "shift",
    "win": "win",
    "windows": "win",
    "super": "win",
}
_KEY_ALIASES = {
    "esc": "escape",
    "enter": "return",
    "page down": "pagedown",
    "page_down": "pagedown",
    "page up": "pageup",
    "page_up": "pageup",
    "backspace": "delete",
    "forward_delete": "forwarddelete",
    "del": "forwarddelete",
    "ins": "insert",
}


def _png_dimensions(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None, None
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _is_windows() -> bool:
    return sys.platform == "win32"


def _powershell_executable(*, sta: bool = False) -> str | None:
    if sta and _is_windows():
        for name in ("powershell.exe", "powershell"):
            resolved = shutil.which(name)
            if resolved:
                return resolved
        for candidate in _WINDOWS_POWERSHELL_CANDIDATES[:2]:
            if Path(candidate).exists():
                return candidate
    for name in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    if _is_windows():
        for candidate in _WINDOWS_POWERSHELL_CANDIDATES:
            if Path(candidate).exists():
                return candidate
    return None


def _powershell_command(script: str, *, sta: bool = False) -> list[str] | None:
    executable = _powershell_executable(sta=sta)
    if not executable:
        return None
    if _is_windows():
        script = "\n".join([
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
            "$OutputEncoding = [Console]::OutputEncoding",
            script,
        ])
    base = [executable, "-NoProfile", "-NonInteractive"]
    executable_name = Path(executable).name.lower()
    if _is_windows() and sta and executable_name in {"powershell.exe", "powershell", "pwsh.exe", "pwsh"}:
        base.append("-STA")
    if executable_name.startswith("powershell"):
        base.extend(["-ExecutionPolicy", "Bypass"])
    return [*base, "-Command", script]


def _ps_string(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _windows_argument_list(values: list[Any]) -> str:
    return subprocess.list2cmdline([str(value) for value in values])


def _run_windows_powershell(
    script: str,
    run_process: Callable[..., dict[str, Any]],
    *,
    timeout: float = 15.0,
    sta: bool = False,
) -> dict[str, Any]:
    command = _powershell_command(script, sta=sta)
    if command is None:
        return {
            "command": ["powershell"],
            "returnCode": 127,
            "stdout": "",
            "stderr": "PowerShell is required for Windows desktop/browser bridge tools",
            "method": "powershell",
        }
    result = run_process(command, timeout=timeout)
    result["method"] = "powershell"
    return result


def _windows_helper_paths() -> tuple[Path, Path]:
    helper_dir = (get_settings().data_dir / "tool-helpers").resolve()
    return helper_dir / "windows_visual.py", helper_dir / "windows_visual.sha256"


def _ensure_windows_visual_helper() -> Path:
    source_path, digest_path = _windows_helper_paths()
    source_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(_WINDOWS_VISUAL_HELPER_SOURCE.encode("utf-8")).hexdigest()
    if source_path.exists() and digest_path.exists() and digest_path.read_text(encoding="utf-8").strip() == digest:
        return source_path
    source_path.write_text(_WINDOWS_VISUAL_HELPER_SOURCE, encoding="utf-8")
    digest_path.write_text(digest + "\n", encoding="utf-8")
    return source_path


def _run_windows_visual_helper(
    mode: str,
    payload: dict[str, Any],
    run_process: Callable[..., dict[str, Any]],
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    helper = _ensure_windows_visual_helper()
    result = run_process(
        [sys.executable, str(helper), mode, json.dumps(payload, ensure_ascii=False)],
        timeout=timeout,
    )
    result["method"] = "win32_sendinput" if mode in {"click", "keypress", "type", "scroll"} else "win32"
    rows = _json_rows_from_stdout(result)
    if not rows:
        parsed = _json_value_from_stdout(result.get("stdout"))
        if isinstance(parsed, dict):
            rows = [parsed]
        elif isinstance(parsed, list):
            rows = [item for item in parsed if isinstance(item, dict)]
    if rows:
        result["helper"] = rows[0]
        if "ok" in rows[0]:
            result["ok"] = bool(rows[0]["ok"])
        if rows[0].get("mode"):
            result["helperMode"] = rows[0]["mode"]
        if rows[0].get("inputMethod"):
            result["inputMethod"] = rows[0]["inputMethod"]
        if result.get("returnCode") == 0:
            if rows[0].get("ok") is not True:
                result["returnCode"] = 1
                result["stderr"] = str(rows[0].get("error") or rows[0].get("message") or "Windows visual helper reported ok=false")
            elif rows[0].get("mode") != mode:
                result["returnCode"] = 1
                result["stderr"] = f"Windows visual helper returned mode {rows[0].get('mode')!r}; expected {mode!r}"
    elif result.get("returnCode") == 0:
        result["returnCode"] = 1
        result["stderr"] = "Windows visual helper did not return verification metadata"
    return result


def execute_windows_visual_selftest(run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    if not _is_windows():
        return {
            "returnCode": 64,
            "stdout": "",
            "stderr": "Windows visual helper selftest is only available on win32",
            "method": "win32",
            "platform": sys.platform,
        }
    result = _run_windows_visual_helper("selftest", {}, run_process, timeout=5.0)
    helper = result.get("helper") if isinstance(result.get("helper"), dict) else {}
    result.update({
        "screenWidth": helper.get("screenWidth"),
        "screenHeight": helper.get("screenHeight"),
        "virtualLeft": helper.get("virtualLeft"),
        "virtualTop": helper.get("virtualTop"),
        "virtualWidth": helper.get("virtualWidth"),
        "virtualHeight": helper.get("virtualHeight"),
        "dpiAwareness": helper.get("dpiAwareness"),
        "platform": sys.platform,
    })
    return result


def _windows_dpi_awareness_script_lines() -> list[str]:
    return [
        "$atriumDpiAwareness = $false",
        "try {",
        "  $dpiSig = '[DllImport(\"user32.dll\")] public static extern bool SetProcessDPIAware(); [DllImport(\"user32.dll\")] public static extern bool SetProcessDpiAwarenessContext(System.IntPtr dpiContext);'",
        "  Add-Type -MemberDefinition $dpiSig -Name Win32Dpi -Namespace ATRIUM -ErrorAction SilentlyContinue",
        "  try { if ([ATRIUM.Win32Dpi]::SetProcessDpiAwarenessContext([IntPtr](-4))) { $atriumDpiAwareness = $true } } catch {}",
        "  if (-not $atriumDpiAwareness) { try { if ([ATRIUM.Win32Dpi]::SetProcessDPIAware()) { $atriumDpiAwareness = $true } } catch {} }",
        "} catch {}",
    ]


def execute_windows_powershell_visual_preflight(run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    if not _is_windows():
        return {
            "returnCode": 64,
            "stdout": "",
            "stderr": "Windows PowerShell visual preflight is only available on win32",
            "method": "powershell",
            "platform": sys.platform,
        }
    script = "\n".join([
        "$checks = [ordered]@{",
        "  winForms = $false",
        "  drawing = $false",
        "  virtualScreen = $false",
        "  systemIcon = $false",
        "  setClipboardCommand = $false",
        "  getClipboardCommand = $false",
        "  dpiAwareness = $false",
        "}",
        "$errors = [ordered]@{}",
        "$virtualScreen = [ordered]@{}",
        *_windows_dpi_awareness_script_lines(),
        "$checks.dpiAwareness = [bool]$atriumDpiAwareness",
        "try {",
        "  Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop",
        "  $checks.winForms = $true",
        "  $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen",
        "  $virtualScreen.left = [int]$bounds.Left",
        "  $virtualScreen.top = [int]$bounds.Top",
        "  $virtualScreen.width = [int]$bounds.Width",
        "  $virtualScreen.height = [int]$bounds.Height",
        "  $checks.virtualScreen = ($bounds.Width -gt 0 -and $bounds.Height -gt 0)",
        "} catch {",
        "  $errors.winForms = $_.Exception.Message",
        "}",
        "try {",
        "  Add-Type -AssemblyName System.Drawing -ErrorAction Stop",
        "  $checks.drawing = $true",
        "  $checks.systemIcon = ($null -ne [System.Drawing.SystemIcons]::Information)",
        "} catch {",
        "  $errors.drawing = $_.Exception.Message",
        "}",
        "$checks.setClipboardCommand = ($null -ne (Get-Command Set-Clipboard -ErrorAction SilentlyContinue))",
        "$checks.getClipboardCommand = ($null -ne (Get-Command Get-Clipboard -ErrorAction SilentlyContinue))",
        "$ok = $true",
        "foreach ($name in @('winForms','drawing','virtualScreen','systemIcon','setClipboardCommand','getClipboardCommand','dpiAwareness')) {",
        "  if (-not [bool]$checks[$name]) { $ok = $false }",
        "}",
        "[PSCustomObject]@{ ok = $ok; checks = $checks; virtualScreen = $virtualScreen; errors = $errors; powerShell = $PSVersionTable.PSVersion.ToString() } | ConvertTo-Json -Compress -Depth 5",
    ])
    result = _run_windows_powershell(script, run_process, timeout=8.0, sta=True)
    rows = _json_rows_from_stdout(result)
    row = rows[0] if rows else {}
    result.update({
        "ok": bool(row.get("ok")),
        "checks": row.get("checks") if isinstance(row.get("checks"), dict) else {},
        "virtualScreen": row.get("virtualScreen") if isinstance(row.get("virtualScreen"), dict) else {},
        "errors": row.get("errors") if isinstance(row.get("errors"), dict) else {},
        "powerShell": row.get("powerShell"),
        "platform": sys.platform,
    })
    return result


def execute_screenshot_capture(path: Path, run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if _is_windows():
        script = "\n".join([
            *_windows_dpi_awareness_script_lines(),
            "Add-Type -AssemblyName System.Windows.Forms",
            "Add-Type -AssemblyName System.Drawing",
            "$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen",
            "$bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height",
            "$graphics = [System.Drawing.Graphics]::FromImage($bmp)",
            "$graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bounds.Size)",
            f"$bmp.Save({_ps_string(str(path))}, [System.Drawing.Imaging.ImageFormat]::Png)",
            "$graphics.Dispose()",
            "$bmp.Dispose()",
            f"[PSCustomObject]@{{ path={_ps_string(str(path))}; left=[int]$bounds.Left; top=[int]$bounds.Top; width=[int]$bounds.Width; height=[int]$bounds.Height; dpiAwareness=[bool]$atriumDpiAwareness }} | ConvertTo-Json -Compress",
        ])
        result = _run_windows_powershell(script, run_process, timeout=15.0, sta=True)
        rows = _json_rows_from_stdout(result)
        row = rows[0] if rows else {}
        file_verified = False
        file_bytes = None
        if result.get("returnCode") == 0:
            if not path.is_file():
                result["returnCode"] = 1
                result["stderr"] = "screenshot file was not created"
            else:
                data = path.read_bytes()
                file_bytes = len(data)
                width, height = _png_dimensions(data)
                if not width or not height:
                    result["returnCode"] = 1
                    result["stderr"] = "screenshot file is not a valid PNG"
                else:
                    file_verified = True
                    row = {**row, "width": width, "height": height}
        result.update({
            "path": str(path),
            "left": row.get("left"),
            "top": row.get("top"),
            "width": row.get("width"),
            "height": row.get("height"),
            "dpiAwareness": row.get("dpiAwareness"),
            "fileBytes": file_bytes,
            "fileVerified": file_verified,
            "platform": sys.platform,
        })
        return result
    result = run_process(["screencapture", "-x", str(path)], timeout=10.0)
    width = height = None
    if result.get("returnCode") == 0 and path.is_file():
        width, height = _png_dimensions(path.read_bytes())
    result.update({"path": str(path), "width": width, "height": height, "platform": sys.platform})
    return result


def normalize_browser_profile(raw: Any = None) -> str:
    value = str(raw or "").strip()
    lowered = value.lower()
    if lowered in _USER_BROWSER_PROFILE_ALIASES:
        return "user"
    if lowered in _OWN_BROWSER_PROFILE_ALIASES:
        return "atrium"
    if not _BROWSER_PROFILE_RE.match(value):
        raise ValueError("browser profile must be user, atrium, or a safe name using letters, numbers, '_' or '-'")
    return value


def browser_profile_from_args(args: dict[str, Any]) -> str:
    return normalize_browser_profile(args.get("profile") or args.get("browserProfile"))


def _browser_profiles_root() -> Path:
    return (get_settings().data_dir / "browser-profiles").resolve()


def browser_profile_data_dir(profile: str) -> Path | None:
    normalized = normalize_browser_profile(profile)
    if normalized == "user":
        return None
    return _browser_profiles_root() / normalized


def _browser_app_candidate() -> tuple[str, Path] | None:
    if _is_windows():
        for app_name, app_path in _windows_browser_candidates():
            if app_path.exists():
                return app_name, app_path
        return None
    for app_name, app_path in _BROWSER_APP_CANDIDATES:
        if app_path.exists():
            return app_name, app_path
    return None


def browser_profile_descriptor(profile: str) -> dict[str, Any]:
    normalized = normalize_browser_profile(profile)
    data_dir = browser_profile_data_dir(normalized)
    return {
        "id": normalized,
        "kind": "user" if normalized == "user" else "isolated",
        "isOwnProfile": normalized == "atrium",
        "isDefaultUserProfile": normalized == "user",
        "userDataDir": None if data_dir is None else str(data_dir),
        "exists": None if data_dir is None else data_dir.exists(),
        "aliases": ["default", "host", "personal"] if normalized == "user" else (["own", "agent", "system", "isolated"] if normalized == "atrium" else []),
    }


def list_browser_profiles() -> dict[str, Any]:
    root = _browser_profiles_root()
    profiles: dict[str, dict[str, Any]] = {
        "user": browser_profile_descriptor("user"),
        "atrium": browser_profile_descriptor("atrium"),
    }
    if root.exists():
        for child in sorted(root.iterdir()):
            if child.is_dir() and _BROWSER_PROFILE_RE.match(child.name) and child.name not in profiles:
                profiles[child.name] = browser_profile_descriptor(child.name)
    app = _browser_app_candidate()
    return {
        "ownProfile": "atrium",
        "defaultProfile": "user",
        "platform": sys.platform,
        "profilesRoot": str(root),
        "browserApp": None if app is None else {"name": app[0], "path": str(app[1])},
        "profiles": list(profiles.values()),
    }


def _windows_user_browser_open_script(url: str) -> str:
    argument_list = _windows_argument_list([url])
    return "\n".join([
        f"$url = {_ps_string(url)}",
        f"$argumentList = {_ps_string(argument_list)}",
        "$progIds = @()",
        "foreach ($scheme in @('https','http')) {",
        "  try {",
        "    $choice = Get-ItemProperty -Path \"HKCU:\\Software\\Microsoft\\Windows\\Shell\\Associations\\UrlAssociations\\$scheme\\UserChoice\" -ErrorAction SilentlyContinue",
        "    if ($choice -and $choice.ProgId) { $progIds += [string]$choice.ProgId }",
        "  } catch {}",
        "}",
        "function Get-AtriumDefaultValue([string]$path) {",
        "  try {",
        "    $item = Get-Item -Path $path -ErrorAction Stop",
        "    return [string]$item.GetValue('')",
        "  } catch { return $null }",
        "}",
        "function Resolve-AtriumExecutableFromCommand([string]$commandText) {",
        "  if (-not $commandText) { return $null }",
        "  $trimmed = $commandText.Trim()",
        "  if ($trimmed.StartsWith('\"')) {",
        "    $end = $trimmed.IndexOf('\"', 1)",
        "    if ($end -gt 1) { return $trimmed.Substring(1, $end - 1) }",
        "  }",
        "  $match = [regex]::Match($trimmed, '^[^\\s]+\\.exe')",
        "  if ($match.Success) { return $match.Value }",
        "  return $null",
        "}",
        "$browserPath = $null",
        "$browserName = $null",
        "$browserSource = $null",
        "$selectedProgId = $null",
        "foreach ($candidateProgId in ($progIds | Select-Object -Unique)) {",
        "  foreach ($key in @(",
        "    \"HKCU:\\Software\\Classes\\$candidateProgId\\shell\\open\\command\",",
        "    \"HKLM:\\Software\\Classes\\$candidateProgId\\shell\\open\\command\",",
        "    \"Registry::HKEY_CLASSES_ROOT\\$candidateProgId\\shell\\open\\command\"",
        "  )) {",
        "    $commandText = Get-AtriumDefaultValue $key",
        "    $candidatePath = Resolve-AtriumExecutableFromCommand $commandText",
        "    if ($candidatePath -and (Test-Path -LiteralPath $candidatePath)) {",
        "      $browserPath = $candidatePath",
        "      $browserName = [System.IO.Path]::GetFileNameWithoutExtension($browserPath)",
        "      $browserSource = 'defaultBrowserRegistry'",
        "      $selectedProgId = $candidateProgId",
        "      break",
        "    }",
        "  }",
        "  if ($browserPath) { break }",
        "}",
        "$row = $null",
        "if ($browserPath) {",
        "  $proc = Start-Process -FilePath $browserPath -ArgumentList $argumentList -PassThru",
        "  $startedProcessId = if ($proc) { $proc.Id } else { $null }",
        "  $exeName = [System.IO.Path]::GetFileName($browserPath)",
        "  for ($attempt = 0; $attempt -lt 10 -and -not $row; $attempt++) {",
        "    Start-Sleep -Milliseconds 250",
        "    $candidate = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {",
        "      ($_.Name -eq $exeName) -and (",
        "        ($startedProcessId -and $_.ProcessId -eq $startedProcessId) -or",
        "        ($_.CommandLine -and $_.CommandLine.IndexOf($url, [StringComparison]::OrdinalIgnoreCase) -ge 0)",
        "      )",
        "    } | Sort-Object CreationDate -Descending | Select-Object -First 1)",
        "    if ($candidate) {",
        "      $row = [PSCustomObject]@{ processId=[int]$candidate.ProcessId; processName=$candidate.Name; launchPath=$browserPath; browserName=$browserName; source=$browserSource; startedProcessId=$startedProcessId; processVerified=$true; progId=$selectedProgId }",
        "    }",
        "  }",
        "  if (-not $row -and $proc) {",
        "    $row = [PSCustomObject]@{ processId=[int]$proc.Id; processName=$proc.ProcessName; launchPath=$browserPath; browserName=$browserName; source='startProcess'; startedProcessId=$startedProcessId; processVerified=$false; progId=$selectedProgId }",
        "  }",
        "} else {",
        "  $proc = Start-Process -FilePath $url -PassThru",
        "  if ($proc) {",
        "    $row = [PSCustomObject]@{ processId=[int]$proc.Id; processName=$proc.ProcessName; launchPath=$url; browserName=$null; source='shellAssociation'; startedProcessId=$proc.Id; processVerified=$false; progId=$null }",
        "  }",
        "}",
        "if ($row) { $row | ConvertTo-Json -Compress }",
    ])


def execute_browser_open(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    url = args.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("browser.open requires url")
    url = url.strip()
    profile = browser_profile_from_args(args)
    if profile == "user":
        if _is_windows():
            result = _run_windows_powershell(_windows_user_browser_open_script(url), run_process, timeout=12.0)
            launched = _json_rows_from_stdout(result)
            launched_row = launched[0] if launched else {}
            result.update({
                "profile": profile,
                "profileKind": "user",
                "url": url,
                "browserApp": launched_row.get("browserName"),
                "browserAppPath": launched_row.get("launchPath") if launched_row.get("source") != "shellAssociation" else None,
                "processId": launched_row.get("processId"),
                "startedProcessId": launched_row.get("startedProcessId"),
                "processName": launched_row.get("processName"),
                "processVerified": launched_row.get("processVerified"),
                "source": launched_row.get("source"),
                "progId": launched_row.get("progId"),
                "platform": sys.platform,
            })
            return result
        result = run_process(["open", url], timeout=10.0)
        result.update({"profile": profile, "profileKind": "user", "url": url})
        return result

    app = _browser_app_candidate()
    if app is None:
        return {
            "returnCode": 127,
            "stdout": "",
            "stderr": "No supported Chromium browser app found for isolated browser profiles",
            "profile": profile,
            "profileKind": "isolated",
            "url": url,
        }
    app_name, app_path = app
    data_dir = browser_profile_data_dir(profile)
    assert data_dir is not None
    data_dir.mkdir(parents=True, exist_ok=True)
    if _is_windows():
        argument_values = [
            f"--user-data-dir={data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            url,
        ]
        argument_list = _windows_argument_list(argument_values)
        script = "\n".join([
            f"$launchPath = {_ps_string(str(app_path))}",
            f"$profileDir = {_ps_string(str(data_dir))}",
            f"$argumentList = {_ps_string(argument_list)}",
            f"$proc = Start-Process -FilePath {_ps_string(str(app_path))} -ArgumentList $argumentList -PassThru",
            "$startedProcessId = if ($proc) { $proc.Id } else { $null }",
            "$row = $null",
            "$exeName = [System.IO.Path]::GetFileName($launchPath)",
            "for ($attempt = 0; $attempt -lt 10 -and -not $row; $attempt++) {",
            "  Start-Sleep -Milliseconds 250",
            "  $candidate = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {",
            "    ($_.Name -eq $exeName) -and ($_.CommandLine -and $_.CommandLine.IndexOf($profileDir, [StringComparison]::OrdinalIgnoreCase) -ge 0)",
            "  } | Sort-Object CreationDate -Descending | Select-Object -First 1)",
            "  if ($candidate) {",
            "    $row = [PSCustomObject]@{ processId=[int]$candidate.ProcessId; processName=$candidate.Name; launchPath=$launchPath; source='profileProcessLookup'; startedProcessId=$startedProcessId; profileVerified=$true }",
            "  }",
            "}",
            "if ($row) { $row | ConvertTo-Json -Compress }",
        ])
        result = _run_windows_powershell(script, run_process, timeout=10.0)
        launched = _json_rows_from_stdout(result)
        launched_row = launched[0] if launched else {}
        process_id = launched_row.get("processId")
        result.update({
            "profile": profile,
            "profileKind": "isolated",
            "isOwnProfile": profile == "atrium",
            "userDataDir": str(data_dir),
            "browserApp": app_name,
            "browserAppPath": str(app_path),
            "processId": process_id,
            "processName": launched_row.get("processName"),
            "startedProcessId": launched_row.get("startedProcessId"),
            "profileVerified": launched_row.get("profileVerified"),
            "source": launched_row.get("source"),
            "url": url,
            "platform": sys.platform,
        })
        if result.get("returnCode") == 0:
            if not process_id:
                result["returnCode"] = 1
                result["stderr"] = "isolated browser profile process was not found after launch"
            elif launched_row.get("profileVerified") is not True:
                result["returnCode"] = 1
                result["stderr"] = "isolated browser profile process did not verify requested profile"
        return result
    command = [
        "open",
        "-na",
        app_name,
        "--args",
        f"--user-data-dir={data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        url,
    ]
    result = run_process(command, timeout=10.0)
    result.update({
        "profile": profile,
        "profileKind": "isolated",
        "isOwnProfile": profile == "atrium",
        "userDataDir": str(data_dir),
        "browserApp": app_name,
        "browserAppPath": str(app_path),
        "url": url,
    })
    return result


def _applescript_string(value: Any) -> str:
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _app_target(args: dict[str, Any]) -> dict[str, str | int | None]:
    raw_path = args.get("path") or args.get("appPath")
    raw_bundle = args.get("bundleId") or args.get("bundle_id")
    raw_name = args.get("appName") or args.get("name") or args.get("app")
    raw_process_id = args.get("processId") or args.get("pid")
    path = str(raw_path).strip() if raw_path is not None and str(raw_path).strip() else None
    bundle_id = str(raw_bundle).strip() if raw_bundle is not None and str(raw_bundle).strip() else None
    name = str(raw_name).strip() if raw_name is not None and str(raw_name).strip() else None
    process_id: int | None = None
    if raw_process_id is not None and str(raw_process_id).strip():
        try:
            process_id = int(raw_process_id)
        except (TypeError, ValueError):
            raise ValueError("processId must be a positive integer") from None
        if process_id <= 0:
            raise ValueError("processId must be a positive integer")
    if not (path or bundle_id or name or process_id):
        raise ValueError("desktop app tools require appName, bundleId, path, or processId")
    return {"path": path, "bundleId": bundle_id, "name": name, "processId": process_id}


def _app_target_label(target: dict[str, str | int | None]) -> str:
    return str(target.get("name") or target.get("bundleId") or target.get("path") or target.get("processId") or "app")


def _windows_process_needle(target: dict[str, str | int | None]) -> str:
    if target.get("path"):
        return Path(str(target["path"])).stem
    label = _app_target_label(target)
    return label[:-4] if label.lower().endswith(".exe") else label


def _windows_start_menu_shortcuts(*, query: str = "", max_items: int = 80) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    needle = query.strip().lower()
    for base in _windows_start_menu_dirs():
        if not base.exists():
            continue
        for shortcut in sorted(base.rglob("*.lnk")):
            key = str(shortcut).lower()
            if key in seen:
                continue
            name = shortcut.stem
            haystack = f"{name} {shortcut}".lower()
            if needle and needle not in haystack:
                continue
            seen.add(key)
            rows.append({"name": name, "path": str(shortcut), "kind": "shortcut"})
            if len(rows) >= max_items:
                return rows
    return rows


def _windows_find_start_menu_shortcut(app_name: str) -> Path | None:
    needle = app_name.strip().lower()
    if not needle:
        return None
    matches = _windows_start_menu_shortcuts(query=needle, max_items=200)
    exact = [row for row in matches if row.get("name", "").strip().lower() == needle]
    selected = exact[0] if exact else (matches[0] if matches else None)
    if not selected:
        return None
    path = Path(selected["path"])
    return path if path.exists() else None


def _windows_launch_path(target: dict[str, str | int | None]) -> str | None:
    raw_path = target.get("path")
    if raw_path:
        path = Path(raw_path)
        if path.exists():
            return str(path)
        if path.suffix.lower() == ".exe":
            return str(path)
    raw_name = target.get("name")
    if raw_name:
        shortcut = _windows_find_start_menu_shortcut(raw_name)
        if shortcut:
            return str(shortcut)
        return raw_name
    raw_bundle = target.get("bundleId")
    return str(raw_bundle) if raw_bundle else None


def _app_tell_prefix(target: dict[str, str | int | None]) -> str:
    if target.get("bundleId"):
        return f"tell application id {_applescript_string(target['bundleId'])}"
    return f"tell application {_applescript_string(_app_target_label(target))}"


def _app_bundle_info(app_path: Path) -> dict[str, str | None]:
    info_path = app_path / "Contents" / "Info.plist"
    info: dict[str, Any] = {}
    try:
        with info_path.open("rb") as f:
            loaded = plistlib.load(f)
            if isinstance(loaded, dict):
                info = loaded
    except Exception:
        info = {}
    name = (
        info.get("CFBundleDisplayName")
        or info.get("CFBundleName")
        or info.get("CFBundleExecutable")
        or app_path.stem
    )
    return {
        "name": str(name) if name else app_path.stem,
        "path": str(app_path),
        "bundleId": str(info.get("CFBundleIdentifier")) if info.get("CFBundleIdentifier") else None,
    }


def _json_value_from_stdout(raw_stdout: Any) -> Any:
    raw = str(raw_stdout or "").strip()
    if not raw:
        return None
    candidates = [raw, *(line.strip() for line in reversed(raw.splitlines()) if line.strip())]
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    for idx, char in enumerate(raw):
        if char not in "[{":
            continue
        try:
            parsed, _end = decoder.raw_decode(raw[idx:].strip())
        except json.JSONDecodeError:
            continue
        return parsed
    return None


def _json_rows_from_stdout(result: dict[str, Any]) -> list[dict[str, Any]]:
    if result.get("returnCode") != 0:
        return []
    parsed = _json_value_from_stdout(result.get("stdout"))
    try:
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    except TypeError:
        return []
    return []


def _windows_list_apps(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    include_installed = bool(args.get("includeInstalled", True))
    include_running = bool(args.get("includeRunning", True))
    query = str(args.get("query") or args.get("search") or "").strip().lower()
    try:
        max_items = max(1, min(int(args.get("limit") or 80), 300))
    except (TypeError, ValueError):
        max_items = 80

    running: list[dict[str, Any]] = []
    installed: list[dict[str, Any]] = []
    running_result: dict[str, Any] | None = None
    installed_result: dict[str, Any] | None = None
    if include_running:
        script = "\n".join([
            "$rows = @(Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle } | ForEach-Object {",
            "  $path = $null; try { $path = $_.Path } catch {}",
            "  [PSCustomObject]@{ name=$_.ProcessName; title=$_.MainWindowTitle; processId=$_.Id; path=$path }",
            "})",
            "$rows | ConvertTo-Json -Compress -Depth 4",
        ])
        running_result = _run_windows_powershell(script, run_process, timeout=10.0)
        running = _json_rows_from_stdout(running_result)
        if query:
            running = [
                row
                for row in running
                if query in f"{row.get('name') or ''} {row.get('title') or ''} {row.get('path') or ''}".lower()
            ]
    if include_installed:
        script = "\n".join([
            "$roots = @(",
            "  'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',",
            "  'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',",
            "  'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*'",
            ")",
            "$rows = @(Get-ItemProperty $roots -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName } | ForEach-Object {",
            "  [PSCustomObject]@{ name=$_.DisplayName; path=$_.InstallLocation; version=$_.DisplayVersion; publisher=$_.Publisher }",
            "})",
            "$rows | ConvertTo-Json -Compress -Depth 4",
        ])
        installed_result = _run_windows_powershell(script, run_process, timeout=15.0)
        installed = _json_rows_from_stdout(installed_result)
        if query:
            installed = [
                row
                for row in installed
                if query in f"{row.get('name') or ''} {row.get('publisher') or ''} {row.get('path') or ''}".lower()
            ]
        shortcut_rows = _windows_start_menu_shortcuts(query=query, max_items=max_items)
        installed = [*shortcut_rows, *({**row, "kind": row.get("kind") or "registry"} for row in installed)]
        deduped_installed: list[dict[str, Any]] = []
        seen_installed: set[str] = set()
        for row in installed:
            key = f"{row.get('name') or ''}\0{row.get('path') or ''}".lower()
            if key in seen_installed:
                continue
            seen_installed.add(key)
            deduped_installed.append(row)
            if len(deduped_installed) >= max_items:
                break
        installed = deduped_installed
    running_ok = running_result is not None and running_result.get("returnCode") == 0
    installed_ok = installed_result is not None and (installed_result.get("returnCode") == 0 or bool(installed))
    no_discovery_requested = not include_running and not include_installed
    return_code = 0 if no_discovery_requested or running_ok or installed_ok else (
        running_result or installed_result or {"returnCode": 0}
    ).get("returnCode", 0)
    stderr_parts = [
        str(item.get("stderr") or "")
        for item in (running_result, installed_result)
        if item is not None and item.get("stderr")
    ]
    installed_error = None
    if installed_result is not None and installed_result.get("returnCode") != 0:
        installed_error = str(installed_result.get("stderr") or installed_result.get("stdout") or "installed app registry discovery failed")
    return {
        "returnCode": return_code,
        "running": running[:max_items],
        "installed": installed,
        "installedCount": len(installed),
        "query": query,
        "stderr": "\n".join(part for part in stderr_parts if part),
        "runningReturnCode": None if running_result is None else running_result.get("returnCode"),
        "installedReturnCode": None if installed_result is None else installed_result.get("returnCode"),
        "installedError": installed_error,
        "platform": sys.platform,
    }


def _installed_apps(*, query: str = "", max_items: int = 80) -> list[dict[str, str | None]]:
    rows: list[dict[str, str | None]] = []
    seen: set[str] = set()
    needle = query.strip().lower()
    for base in _APP_SEARCH_DIRS:
        if not base.exists():
            continue
        for app_path in sorted(base.glob("*.app")):
            key = str(app_path.resolve())
            if key in seen:
                continue
            info = _app_bundle_info(app_path)
            haystack = f"{info.get('name') or ''} {info.get('bundleId') or ''} {info.get('path') or ''}".lower()
            if needle and needle not in haystack:
                continue
            seen.add(key)
            rows.append(info)
            if len(rows) >= max_items:
                return rows
    return rows


def _running_apps(run_process: Callable[..., dict[str, Any]]) -> tuple[list[str], dict[str, Any]]:
    script = (
        'tell application "System Events" to get name of every application process '
        "whose background only is false"
    )
    result = run_process(["osascript", "-e", script], timeout=10.0)
    stdout = str(result.get("stdout") or "").strip()
    names = [part.strip() for part in stdout.replace("\r", "\n").replace(", ", "\n").splitlines() if part.strip()]
    return names, result


def execute_list_apps(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    if _is_windows():
        return _windows_list_apps(args, run_process)
    include_installed = bool(args.get("includeInstalled", True))
    include_running = bool(args.get("includeRunning", True))
    query = str(args.get("query") or args.get("search") or "").strip()
    try:
        max_items = max(1, min(int(args.get("limit") or 80), 300))
    except (TypeError, ValueError):
        max_items = 80

    running: list[str] = []
    running_result: dict[str, Any] | None = None
    if include_running:
        running, running_result = _running_apps(run_process)
    installed = _installed_apps(query=query, max_items=max_items) if include_installed else []
    if query and running:
        needle = query.lower()
        running = [name for name in running if needle in name.lower()]
    return {
        "returnCode": 0 if running_result is None else running_result.get("returnCode", 0),
        "running": running,
        "installed": installed,
        "installedCount": len(installed),
        "query": query,
        "stderr": "" if running_result is None else running_result.get("stderr", ""),
    }


def execute_open_app(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    target = _app_target(args)
    if _is_windows():
        app = _windows_launch_path(target)
        if not app:
            raise ValueError("desktop.open_app requires appName or path on Windows")
        open_target = args.get("target") or args.get("file") or args.get("url")
        raw_app_args = args.get("arguments") if isinstance(args.get("arguments"), list) else args.get("args")
        argument_values: list[Any] = []
        if open_target is not None and str(open_target).strip():
            argument_values.append(str(open_target).strip())
        if isinstance(raw_app_args, list):
            argument_values.extend(str(item) for item in raw_app_args)
        argument_list = _windows_argument_list(argument_values) if argument_values else ""
        needle = _windows_process_needle(target)
        script_lines = [
            f"$launchPath = {_ps_string(app)}",
            f"$needle = {_ps_string(needle)}",
        ]
        if argument_values:
            script_lines.append(f"$argumentList = {_ps_string(argument_list)}")
        script_lines.append(f"$proc = Start-Process -FilePath {_ps_string(app)} -PassThru")
        if argument_values:
            script_lines[-1] += " -ArgumentList $argumentList"
        script_lines.extend([
            "$row = $null",
            "if ($proc) {",
            "  $row = [PSCustomObject]@{ processId=$proc.Id; processName=$proc.ProcessName; launchPath=$launchPath; source='startProcess' }",
            "}",
            "if ((-not $row -or -not $row.processId) -or $launchPath.ToLowerInvariant().EndsWith('.lnk')) {",
            "  Start-Sleep -Milliseconds 500",
            "  $candidate = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {",
            "    ($_.ProcessName -like \"*$needle*\") -or ($_.MainWindowTitle -like \"*$needle*\")",
            "  } | Sort-Object StartTime -Descending -ErrorAction SilentlyContinue | Select-Object -First 1)",
            "  if ($candidate) {",
            "    $row = [PSCustomObject]@{ processId=$candidate.Id; processName=$candidate.ProcessName; launchPath=$launchPath; source='processLookup' }",
            "  }",
            "}",
            "if ($row) { $row | ConvertTo-Json -Compress }",
        ])
        result = _run_windows_powershell(
            "\n".join(script_lines),
            run_process,
            timeout=max(5.0, min(float(args.get("timeoutSeconds") or 15), 120.0)),
        )
        launched = _json_rows_from_stdout(result)
        launched_row = launched[0] if launched else {}
        process_id = launched_row.get("processId")
        result.update({
            "appName": target.get("name"),
            "bundleId": target.get("bundleId"),
            "path": target.get("path"),
            "launchPath": app,
            "processId": process_id,
            "processName": launched_row.get("processName"),
            "processVerified": bool(process_id),
            "source": launched_row.get("source"),
            "target": str(open_target).strip() if open_target is not None else None,
            "platform": sys.platform,
        })
        if not process_id and result.get("returnCode") == 0:
            result["returnCode"] = 1
            result["stderr"] = "desktop app process was not found after launch"
        return result
    command = ["open"]
    if args.get("newInstance") or args.get("new_instance"):
        command.append("-n")
    if target.get("bundleId"):
        command.extend(["-b", str(target["bundleId"])])
    elif target.get("path"):
        command.extend(["-a", str(target["path"])])
    else:
        command.extend(["-a", str(target["name"])])

    open_target = args.get("target") or args.get("file") or args.get("url")
    if open_target is not None and str(open_target).strip():
        command.append(str(open_target).strip())
    raw_app_args = args.get("arguments") if isinstance(args.get("arguments"), list) else args.get("args")
    if isinstance(raw_app_args, list) and raw_app_args:
        command.append("--args")
        command.extend(str(item) for item in raw_app_args)
    result = run_process(command, timeout=max(5.0, min(float(args.get("timeoutSeconds") or 15), 120.0)))
    result.update({
        "appName": target.get("name"),
        "bundleId": target.get("bundleId"),
        "path": target.get("path"),
        "target": str(open_target).strip() if open_target is not None else None,
    })
    return result


def execute_activate_app(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    target = _app_target(args)
    if _is_windows():
        process_id = target.get("processId")
        script_lines = [
            "$sig = '[DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr hWnd); [DllImport(\"user32.dll\")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow); [DllImport(\"user32.dll\")] public static extern bool BringWindowToTop(IntPtr hWnd); [DllImport(\"user32.dll\")] public static extern IntPtr GetForegroundWindow(); [DllImport(\"user32.dll\")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId); [DllImport(\"kernel32.dll\")] public static extern uint GetCurrentThreadId(); [DllImport(\"user32.dll\")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);'",
            "Add-Type -MemberDefinition $sig -Name Win32Window -Namespace ATRIUM",
        ]
        if process_id:
            script_lines.extend([
                f"$proc = Get-Process -Id {int(process_id)} -ErrorAction SilentlyContinue | Where-Object {{ $_.MainWindowHandle -ne 0 }} | Select-Object -First 1",
            ])
        else:
            needle = _windows_process_needle(target)
            script_lines.extend([
                f"$needle = {_ps_string(needle)}",
                "$proc = Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and (($_.ProcessName -like \"*$needle*\") -or ($_.MainWindowTitle -like \"*$needle*\")) } | Select-Object -First 1",
            ])
        script_lines.extend([
            "if (-not $proc) { Write-Error \"window not found\"; exit 1 }",
            "$targetPid = [uint32]0",
            "$targetThread = [ATRIUM.Win32Window]::GetWindowThreadProcessId($proc.MainWindowHandle, [ref]$targetPid)",
            "$foregroundWindow = [ATRIUM.Win32Window]::GetForegroundWindow()",
            "$foregroundPid = [uint32]0",
            "$foregroundThread = if ($foregroundWindow -ne [IntPtr]::Zero) { [ATRIUM.Win32Window]::GetWindowThreadProcessId($foregroundWindow, [ref]$foregroundPid) } else { [uint32]0 }",
            "$currentThread = [ATRIUM.Win32Window]::GetCurrentThreadId()",
            "$attachedCurrent = $false",
            "$attachedForeground = $false",
            "if ($targetThread -and $currentThread -and $targetThread -ne $currentThread) { $attachedCurrent = [ATRIUM.Win32Window]::AttachThreadInput($currentThread, $targetThread, $true) }",
            "if ($targetThread -and $foregroundThread -and $foregroundThread -ne $targetThread) { $attachedForeground = [ATRIUM.Win32Window]::AttachThreadInput($foregroundThread, $targetThread, $true) }",
            "$showWindow = $false",
            "$bringToTop = $false",
            "$setForeground = $false",
            "try {",
            "  $showWindow = [ATRIUM.Win32Window]::ShowWindowAsync($proc.MainWindowHandle, 9)",
            "  $bringToTop = [ATRIUM.Win32Window]::BringWindowToTop($proc.MainWindowHandle)",
            "  $setForeground = [ATRIUM.Win32Window]::SetForegroundWindow($proc.MainWindowHandle)",
            "} finally {",
            "  if ($attachedForeground) { [ATRIUM.Win32Window]::AttachThreadInput($foregroundThread, $targetThread, $false) | Out-Null }",
            "  if ($attachedCurrent) { [ATRIUM.Win32Window]::AttachThreadInput($currentThread, $targetThread, $false) | Out-Null }",
            "}",
            "Start-Sleep -Milliseconds 200",
            "$activeWindow = [ATRIUM.Win32Window]::GetForegroundWindow()",
            "$activePid = [uint32]0",
            "$activeThread = if ($activeWindow -ne [IntPtr]::Zero) { [ATRIUM.Win32Window]::GetWindowThreadProcessId($activeWindow, [ref]$activePid) } else { [uint32]0 }",
            "$isForeground = ($activeWindow -eq $proc.MainWindowHandle) -or ($activePid -eq [uint32]$proc.Id)",
            "[PSCustomObject]@{ name=$proc.ProcessName; title=$proc.MainWindowTitle; processId=$proc.Id; foreground=$isForeground; activeProcessId=[int]$activePid; activeThreadId=[int]$activeThread; setForeground=$setForeground; bringToTop=$bringToTop; showWindow=$showWindow; attachedCurrent=$attachedCurrent; attachedForeground=$attachedForeground } | ConvertTo-Json -Compress",
        ])
        script = "\n".join(script_lines)
        result = _run_windows_powershell(script, run_process, timeout=10.0)
        rows = _json_rows_from_stdout(result)
        row = rows[0] if rows else {}
        result.update({
            "appName": target.get("name"),
            "bundleId": target.get("bundleId"),
            "path": target.get("path"),
            "processId": row.get("processId") or process_id,
            "requestedProcessId": process_id,
            "processName": row.get("name"),
            "title": row.get("title"),
            "foreground": row.get("foreground"),
            "activeProcessId": row.get("activeProcessId"),
            "activeThreadId": row.get("activeThreadId"),
            "setForeground": row.get("setForeground"),
            "bringToTop": row.get("bringToTop"),
            "showWindow": row.get("showWindow"),
            "attachedCurrent": row.get("attachedCurrent"),
            "attachedForeground": row.get("attachedForeground"),
            "platform": sys.platform,
        })
        if result.get("returnCode") == 0:
            if not row:
                result["returnCode"] = 1
                result["stderr"] = "window activation did not return foreground verification metadata"
            elif row.get("foreground") is not True:
                result["returnCode"] = 1
                result["stderr"] = "window did not become foreground"
        return result
    script = f"{_app_tell_prefix(target)} to activate"
    result = run_process(["osascript", "-e", script], timeout=10.0)
    result.update({"appName": target.get("name"), "bundleId": target.get("bundleId"), "path": target.get("path")})
    return result


def execute_quit_app(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    target = _app_target(args)
    force = bool(args.get("force") or args.get("forceQuit") or args.get("force_quit"))
    if _is_windows():
        process_id = target.get("processId")
        delay_ms = int(max(0.0, min(float(args.get("forceDelaySeconds") or 1.0), 10.0)) * 1000)
        script_lines = []
        if process_id:
            script_lines.append(f"$procs = @(Get-Process -Id {int(process_id)} -ErrorAction SilentlyContinue)")
        else:
            needle = _windows_process_needle(target)
            script_lines.extend([
                f"$needle = {_ps_string(needle)}",
                "$procs = @(Get-Process | Where-Object { ($_.ProcessName -like \"*$needle*\") -or ($_.MainWindowTitle -like \"*$needle*\") })",
            ])
        script_lines.extend([
            "if (-not $procs) { Write-Error \"process not found\"; exit 1 }",
            "$closed = 0",
            "foreach ($proc in $procs) { if ($proc.MainWindowHandle -ne 0 -and $proc.CloseMainWindow()) { $closed++ } }",
            f"Start-Sleep -Milliseconds {delay_ms}",
            *(
                [
                    "foreach ($proc in $procs) {",
                    "  try { if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } } catch {}",
                    "}",
                    "Start-Sleep -Milliseconds 200",
                ]
                if force
                else []
            ),
            "$remaining = 0",
            "foreach ($proc in $procs) {",
            "  try { if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) { $remaining++ } } catch {}",
            "}",
            "$quitVerified = ($remaining -eq 0)",
            "[PSCustomObject]@{ matched=$procs.Count; gracefulCloseSent=$closed; force=$" + str(force).lower() + "; remaining=$remaining; quitVerified=$quitVerified } | ConvertTo-Json -Compress",
        ])
        script = "\n".join(script_lines)
        result = _run_windows_powershell(
            script,
            run_process,
            timeout=max(5.0, min(float(args.get("timeoutSeconds") or 15), 120.0)),
        )
        rows = _json_rows_from_stdout(result)
        row = rows[0] if rows else {}
        result.update({
            "appName": target.get("name"),
            "bundleId": target.get("bundleId"),
            "path": target.get("path"),
            "processId": process_id,
            "force": force,
            "matched": row.get("matched"),
            "gracefulCloseSent": row.get("gracefulCloseSent"),
            "remaining": row.get("remaining"),
            "quitVerified": row.get("quitVerified"),
            "platform": sys.platform,
        })
        if result.get("returnCode") == 0:
            if not row:
                result["returnCode"] = 1
                result["stderr"] = "desktop app quit did not return process verification metadata"
            elif row.get("quitVerified") is not True:
                result["returnCode"] = 1
                result["stderr"] = "desktop app process did not exit"
        return result
    script = f"{_app_tell_prefix(target)} to quit"
    result = run_process(["osascript", "-e", script], timeout=max(5.0, min(float(args.get("timeoutSeconds") or 15), 120.0)))
    result.update({
        "appName": target.get("name"),
        "bundleId": target.get("bundleId"),
        "path": target.get("path"),
        "force": force,
    })
    if not force or result.get("returnCode") != 0:
        return result

    time.sleep(max(0.0, min(float(args.get("forceDelaySeconds") or 1.0), 10.0)))
    if target.get("name"):
        force_result = run_process(["pkill", "-x", str(target["name"])], timeout=10.0)
        result["forceResult"] = force_result
        if force_result.get("returnCode") in {0, 1}:
            result["returnCode"] = 0
            return result
        result["returnCode"] = force_result.get("returnCode")
        result["stderr"] = force_result.get("stderr") or result.get("stderr")
    return result


def _click_helper_paths() -> tuple[Path, Path]:
    helper_dir = (get_settings().data_dir / "tool-helpers").resolve()
    return helper_dir / "macos_click.swift", helper_dir / "macos_click"


def _key_helper_paths() -> tuple[Path, Path]:
    helper_dir = (get_settings().data_dir / "tool-helpers").resolve()
    return helper_dir / "macos_keys.swift", helper_dir / "macos_keys"


def _ensure_click_helper() -> Path | None:
    swiftc = shutil.which("swiftc") or "/Library/Developer/CommandLineTools/usr/bin/swiftc"
    if not swiftc or not Path(swiftc).exists():
        return None
    source_path, binary_path = _click_helper_paths()
    source_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(_CLICK_HELPER_SOURCE.encode("utf-8")).hexdigest()
    digest_path = binary_path.with_suffix(".sha256")
    if binary_path.exists() and digest_path.exists() and digest_path.read_text(encoding="utf-8").strip() == digest:
        return binary_path
    source_path.write_text(_CLICK_HELPER_SOURCE, encoding="utf-8")
    completed = subprocess.run(
        [swiftc, str(source_path), "-o", str(binary_path)],
        text=True,
        capture_output=True,
        timeout=30.0,
        check=False,
    )
    if completed.returncode != 0:
        return None
    digest_path.write_text(digest + "\n", encoding="utf-8")
    return binary_path


def _ensure_key_helper() -> Path | None:
    swiftc = shutil.which("swiftc") or "/Library/Developer/CommandLineTools/usr/bin/swiftc"
    if not swiftc or not Path(swiftc).exists():
        return None
    source_path, binary_path = _key_helper_paths()
    source_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(_KEY_HELPER_SOURCE.encode("utf-8")).hexdigest()
    digest_path = binary_path.with_suffix(".sha256")
    if binary_path.exists() and digest_path.exists() and digest_path.read_text(encoding="utf-8").strip() == digest:
        return binary_path
    source_path.write_text(_KEY_HELPER_SOURCE, encoding="utf-8")
    completed = subprocess.run(
        [swiftc, str(source_path), "-o", str(binary_path)],
        text=True,
        capture_output=True,
        timeout=30.0,
        check=False,
    )
    if completed.returncode != 0:
        return None
    digest_path.write_text(digest + "\n", encoding="utf-8")
    return binary_path


def execute_click(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    x = int(float(args.get("x")))
    y = int(float(args.get("y")))
    button = str(args.get("button") or args.get("mouseButton") or "left").strip().lower()
    if button not in {"left", "right"}:
        raise ValueError("click button must be left or right")

    if _is_windows():
        result = _run_windows_visual_helper("click", {"x": x, "y": y, "button": button}, run_process, timeout=8.0)
        result.update({"x": x, "y": y, "button": button, "platform": sys.platform})
        return result

    helper = _ensure_click_helper()
    if helper:
        result = run_process([str(helper), str(x), str(y), button], timeout=8.0)
        result.update({"x": x, "y": y, "button": button, "method": "coregraphics"})
        if result.get("returnCode") == 0:
            return result

    script = f'tell application "System Events" to click at {{{x}, {y}}}'
    result = run_process(["osascript", "-e", script], timeout=15.0)
    result.update({"x": x, "y": y, "button": button, "method": "osascript"})
    return result


def _normalized_key_parts(keys: Any) -> tuple[str, list[str]]:
    if not isinstance(keys, list) or not keys or not all(isinstance(key, str) for key in keys):
        raise ValueError("keypress tools require keys as a string list")
    normalized = [key.strip().lower() for key in keys if key.strip()]
    modifiers = [_MODIFIER_ALIASES[key] for key in normalized if key in _MODIFIER_ALIASES]
    key_parts = [key for key in normalized if key not in _MODIFIER_ALIASES]
    if len(key_parts) != 1:
        raise ValueError("keypress tools require exactly one non-modifier key")
    key = _KEY_ALIASES.get(key_parts[0], key_parts[0])
    return key, modifiers


def execute_keypress(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    key, modifiers = _normalized_key_parts(args.get("keys"))
    if _is_windows():
        result = _run_windows_visual_helper("keypress", {"keys": args.get("keys")}, run_process, timeout=8.0)
        helper = result.get("helper") if isinstance(result.get("helper"), dict) else {}
        actual_key = helper.get("key") if isinstance(helper.get("key"), str) else key
        actual_modifiers = helper.get("modifiers") if isinstance(helper.get("modifiers"), list) else modifiers
        result.update({
            "key": actual_key,
            "modifiers": [str(item) for item in actual_modifiers],
            "requestedKey": key,
            "requestedModifiers": modifiers,
            "platform": sys.platform,
        })
        return result
    helper = _ensure_key_helper()
    if helper:
        result = run_process([str(helper), "press", key, *modifiers], timeout=8.0)
        result.update({"key": key, "modifiers": modifiers, "method": "coregraphics"})
        if result.get("returnCode") == 0:
            return result
    modifier_map = {"cmd": "command", "control": "control", "option": "option", "shift": "shift", "win": "command"}
    suffix = f" using {{{', '.join(f'{modifier_map[mod]} down' for mod in modifiers)}}}" if modifiers else ""
    if len(key) == 1:
        script = f'tell application "System Events" to keystroke {_applescript_string(key)}{suffix}'
    else:
        key_code = _SCROLL_KEY_CODES.get((key.replace("page", ""), "page"))
        if key == "return":
            key_code = 36
        elif key == "tab":
            key_code = 48
        elif key == "space":
            key_code = 49
        elif key == "delete":
            key_code = 51
        elif key == "forwarddelete":
            key_code = 117
        elif key == "escape":
            key_code = 53
        elif key == "insert":
            raise ValueError("unsupported key name: insert")
        elif key in {"left", "right", "down", "up"}:
            key_code = _SCROLL_KEY_CODES[(key, "line")]
        if key_code is None:
            raise ValueError(f"unsupported key name: {key}")
        script = f'tell application "System Events" to key code {key_code}{suffix}'
    result = run_process(["osascript", "-e", script], timeout=5.0)
    result.update({"key": key, "modifiers": modifiers, "method": "osascript"})
    return result


def execute_type_text(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    text = args.get("text")
    if not isinstance(text, str):
        raise ValueError("type tools require text")
    if _is_windows():
        result = _run_windows_visual_helper("type", {"text": text}, run_process, timeout=max(5.0, min(float(args.get("timeoutSeconds") or 15), 60.0)))
        helper = result.get("helper") if isinstance(result.get("helper"), dict) else {}
        result.update({
            "textBytes": len(text.encode("utf-8")),
            "textCharacters": len(text),
            "textUnits": helper.get("textUnits"),
            "platform": sys.platform,
        })
        return result
    helper = _ensure_key_helper()
    timeout = max(5.0, min(float(args.get("timeoutSeconds") or 15), 60.0))
    if helper:
        result = run_process([str(helper), "type", text], timeout=timeout)
        result.update({"textBytes": len(text.encode("utf-8")), "method": "coregraphics"})
        if result.get("returnCode") == 0:
            return result
    script = f'tell application "System Events" to keystroke {_applescript_string(text)}'
    result = run_process(["osascript", "-e", script], timeout=timeout)
    result.update({"textBytes": len(text.encode("utf-8")), "method": "osascript"})
    return result


def execute_paste_text(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    text = args.get("text")
    if not isinstance(text, str):
        raise ValueError("paste_text tools require text")
    if _is_windows():
        set_clipboard = _run_windows_powershell(f"Set-Clipboard -Value {_ps_string(text)}", run_process, timeout=5.0, sta=True)
        if set_clipboard.get("returnCode") != 0:
            set_clipboard.update({"textBytes": len(text.encode("utf-8")), "platform": sys.platform})
            return set_clipboard
        verify_script = "\n".join([
            "$value = Get-Clipboard -Raw -ErrorAction Stop",
            "if ($null -eq $value) { $value = '' }",
            f"$expected = {_ps_string(text)}",
            "$preview = $value",
            "if ($preview.Length -gt 200) { $preview = $preview.Substring(0, 200) }",
            "[PSCustomObject]@{ textLength=$value.Length; textPreview=$preview; containsExpected=$value.Equals($expected); verified=$value.Equals($expected) } | ConvertTo-Json -Compress",
        ])
        clipboard_check = _run_windows_powershell(verify_script, run_process, timeout=5.0, sta=True)
        clipboard_rows = _json_rows_from_stdout(clipboard_check)
        clipboard_row = clipboard_rows[0] if clipboard_rows else {}
        clipboard_meta = {
            "setReturnCode": set_clipboard.get("returnCode"),
            "setStderr": set_clipboard.get("stderr", ""),
            "setMethod": set_clipboard.get("method"),
            "verifyReturnCode": clipboard_check.get("returnCode"),
            "verifyStderr": clipboard_check.get("stderr", ""),
            "verifyMethod": clipboard_check.get("method"),
            "verified": bool(clipboard_row.get("verified")),
            "containsExpected": bool(clipboard_row.get("containsExpected")),
            "textLength": clipboard_row.get("textLength"),
            "textPreview": clipboard_row.get("textPreview"),
        }
        if clipboard_check.get("returnCode") != 0 or not clipboard_meta["verified"]:
            return {
                "command": ["Set-Clipboard", "Get-Clipboard"],
                "returnCode": clipboard_check.get("returnCode") if clipboard_check.get("returnCode") != 0 else 1,
                "stdout": clipboard_check.get("stdout", ""),
                "stderr": clipboard_check.get("stderr") or "clipboard round-trip did not verify expected text",
                "textBytes": len(text.encode("utf-8")),
                "method": clipboard_check.get("method"),
                "clipboard": clipboard_meta,
                "platform": sys.platform,
            }
        paste = execute_keypress({"keys": ["control", "v"]}, run_process)
        return {
            "command": ["Set-Clipboard", paste.get("method", "keypress"), "paste"],
            "returnCode": paste.get("returnCode"),
            "stdout": paste.get("stdout", ""),
            "stderr": paste.get("stderr", ""),
            "textBytes": len(text.encode("utf-8")),
            "method": paste.get("method"),
            "inputMethod": paste.get("inputMethod"),
            "ok": paste.get("ok"),
            "helper": paste.get("helper"),
            "helperMode": paste.get("helperMode"),
            "clipboard": clipboard_meta,
            "platform": sys.platform,
        }
    pbcopy = subprocess.run(["pbcopy"], input=text, text=True, capture_output=True, timeout=5.0, check=False)
    if pbcopy.returncode != 0:
        return {
            "command": ["pbcopy"],
            "returnCode": pbcopy.returncode,
            "stdout": pbcopy.stdout or "",
            "stderr": pbcopy.stderr or "",
            "textBytes": len(text.encode("utf-8")),
        }
    paste = execute_keypress({"keys": ["cmd", "v"]}, run_process)
    return {
        "command": ["pbcopy", paste.get("method", "keypress"), "paste"],
        "returnCode": paste.get("returnCode"),
        "stdout": paste.get("stdout", ""),
        "stderr": paste.get("stderr", ""),
        "textBytes": len(text.encode("utf-8")),
        "method": paste.get("method"),
    }


def visual_process_error(tool: str, result: Any) -> str | None:
    if tool not in _VISUAL_PROCESS_TOOLS or not isinstance(result, dict):
        return None
    return_code = result.get("returnCode")
    stderr = str(result.get("stderr") or "").strip()
    stdout = str(result.get("stdout") or "").strip()
    if result.get("timeout") is True:
        detail = stderr or stdout or "command timed out"
        return f"{tool} bridge command failed: {detail[:1000]}"
    if result.get("ok") is False:
        helper = result.get("helper") if isinstance(result.get("helper"), dict) else {}
        detail = stderr or str(helper.get("error") or helper.get("message") or "") or stdout or "ok=false"
        return f"{tool} bridge command failed: {detail[:1000]}"
    if return_code is None:
        helper = result.get("helper") if isinstance(result.get("helper"), dict) else {}
        detail = stderr or str(result.get("error") or helper.get("error") or helper.get("message") or "") or stdout
        return f"{tool} bridge command failed: {detail[:1000]}" if detail else None
    if return_code == 0:
        return None
    detail = stderr or stdout or f"returnCode={return_code}"
    return f"{tool} bridge command failed: {detail[:1000]}"


def _scroll_direction(args: dict[str, Any]) -> str:
    raw = str(args.get("direction") or "").strip().lower()
    if not raw:
        delta_y = args.get("deltaY") if "deltaY" in args else args.get("dy")
        delta_x = args.get("deltaX") if "deltaX" in args else args.get("dx")
        try:
            if delta_y is not None and float(delta_y) != 0:
                raw = "down" if float(delta_y) > 0 else "up"
            elif delta_x is not None and float(delta_x) != 0:
                raw = "right" if float(delta_x) > 0 else "left"
        except (TypeError, ValueError):
            raw = ""
    aliases = {
        "d": "down",
        "u": "up",
        "r": "right",
        "l": "left",
        "pagedown": "down",
        "page_down": "down",
        "pageup": "up",
        "page_up": "up",
    }
    direction = aliases.get(raw, raw or "down")
    if direction not in {"down", "up", "left", "right"}:
        raise ValueError("scroll direction must be one of down, up, left, or right")
    return direction


def _scroll_unit(args: dict[str, Any]) -> str:
    raw = str(args.get("unit") or "").strip().lower()
    if raw in {"line", "lines", "row", "rows"}:
        return "line"
    if raw in {"page", "pages", ""}:
        return "page"
    if raw in {"pixel", "pixels", "px"}:
        return "page"
    raise ValueError("scroll unit must be page or line")


def _scroll_amount(args: dict[str, Any], unit: str) -> int:
    raw = args.get("amount")
    if raw is None and unit == "page":
        raw = args.get("pages")
    if raw is None and unit == "line":
        raw = args.get("lines")
    if raw is None:
        raw = 1
    try:
        amount = int(abs(float(raw)))
    except (TypeError, ValueError):
        amount = 1
    return max(1, min(amount, 10 if unit == "page" else 40))


def execute_scroll(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    direction = _scroll_direction(args)
    unit = _scroll_unit(args)
    amount = _scroll_amount(args, unit)
    delay_ms = max(0, min(int(args.get("delayMs") or 40), 500))
    if _is_windows():
        payload: dict[str, Any] = {"direction": direction, "unit": unit, "amount": amount, "delayMs": delay_ms}
        x = y = None
        if args.get("x") is not None or args.get("y") is not None:
            if args.get("x") is None or args.get("y") is None:
                raise ValueError("scroll x and y must be provided together")
            x = int(float(args["x"]))
            y = int(float(args["y"]))
            payload.update({"x": x, "y": y})
        result = _run_windows_visual_helper(
            "scroll",
            payload,
            run_process,
            timeout=max(5.0, min(5.0 + amount * 0.4, 30.0)),
        )
        helper = result.get("helper") if isinstance(result.get("helper"), dict) else {}
        result.update({
            "direction": direction,
            "unit": unit,
            "amount": amount,
            "steps": helper.get("steps"),
            "wheelDelta": helper.get("wheelDelta"),
            "horizontal": helper.get("horizontal"),
            "x": helper.get("x") if helper.get("x") is not None else x,
            "y": helper.get("y") if helper.get("y") is not None else y,
            "platform": sys.platform,
        })
        return result
    key_code = _SCROLL_KEY_CODES[(direction, unit)]
    delay_s = delay_ms / 1000
    script = "\n".join(
        [
            'tell application "System Events"',
            f"  repeat {amount} times",
            f"    key code {key_code}",
            f"    delay {delay_s:.3f}",
            "  end repeat",
            "end tell",
        ]
    )
    result = run_process(["osascript", "-e", script], timeout=max(5.0, min(5.0 + amount * 0.2, 20.0)))
    result.update({
        "direction": direction,
        "unit": unit,
        "amount": amount,
        "keyCode": key_code,
    })
    return result


def execute_notification(args: dict[str, Any], run_process: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    title = str(args.get("title") or "ATRIUM")
    body = str(args.get("body") or "")
    if _is_windows():
        timeout_ms = max(1000, min(int(args.get("timeoutMs") or args.get("durationMs") or 5000), 10000))
        script = "\n".join([
            "Add-Type -AssemblyName System.Windows.Forms",
            "Add-Type -AssemblyName System.Drawing",
            "$shown = $false",
            "$disposed = $false",
            "$notify = New-Object System.Windows.Forms.NotifyIcon",
            "try {",
            "  $notify.Icon = [System.Drawing.SystemIcons]::Information",
            "  $notify.Visible = $true",
            f"  $notify.ShowBalloonTip({timeout_ms}, {_ps_string(title)}, {_ps_string(body)}, [System.Windows.Forms.ToolTipIcon]::Info)",
            "  $shown = $true",
            f"  Start-Sleep -Milliseconds {min(timeout_ms + 250, 11000)}",
            "} finally {",
            "  if ($notify) { $notify.Dispose(); $disposed = $true }",
            "}",
            f"[PSCustomObject]@{{ shown=$shown; disposed=$disposed; timeoutMs={timeout_ms}; titleLength={len(title)}; bodyLength={len(body)} }} | ConvertTo-Json -Compress",
        ])
        command_timeout = max(10.0, (timeout_ms + 1500) / 1000.0)
        result = _run_windows_powershell(script, run_process, timeout=command_timeout, sta=True)
        rows = _json_rows_from_stdout(result)
        row = rows[0] if rows else {}
        result.update({
            "title": title,
            "bodyBytes": len(body.encode("utf-8")),
            "shown": row.get("shown"),
            "disposed": row.get("disposed"),
            "timeoutMs": row.get("timeoutMs"),
            "titleLength": row.get("titleLength"),
            "bodyLength": row.get("bodyLength"),
            "platform": sys.platform,
        })
        if result.get("returnCode") == 0 and row.get("shown") is not True:
            result["returnCode"] = 1
            result["stderr"] = "Windows notification did not return ShowBalloonTip verification metadata"
        return result
    safe_title = title.replace('"', "'")
    safe_body = body.replace('"', "'")
    result = run_process(["osascript", "-e", f'display notification "{safe_body}" with title "{safe_title}"'], timeout=5.0)
    result.update({"title": title, "bodyBytes": len(body.encode("utf-8")), "platform": sys.platform})
    return result


async def persist_screenshot_artifact(
    repo: Any,
    *,
    path: Path,
    owner_dept: str,
    created_by: str,
    source_tool: str,
    artifact_name: str | None = None,
    browser_profile: str | None = None,
) -> dict[str, Any]:
    now = now_ms()
    data = path.read_bytes()
    width, height = _png_dimensions(data)
    artifact_id = uid("art")
    name = safe_filename(artifact_name or path.name or f"{source_tool.replace('.', '-')}-{int(time.time())}.png")
    stored_path = (get_settings().workspace_dir / owner_dept / "artifacts" / artifact_id / "v1.png").resolve()
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    if stored_path != path.resolve():
        stored_path.write_bytes(data)
    path = stored_path
    tags = ["screenshot", source_tool.replace(".", "_")]
    preview = {"kind": "screenshot", "uri": str(path)}
    artifact = Artifact(
        id=artifact_id,
        name=name,
        kind="image",
        mime="image/png",
        owner_dept=owner_dept,
        task_ids=[],
        project_id=None,
        version=1,
        status="approved",
        uri=str(path),
        storage="filesystem",
        content_hash=hashlib.sha256(data).hexdigest(),
        content_size_bytes=len(data),
        content_mime="image/png",
        tags=tags,
        links=[str(path)],
        preview=preview,
        created_at=now,
        created_by=created_by,
        updated_at=now,
        updated_by=created_by,
    ).dump()
    artifact["visualAutomation"] = {
        "sourceTool": source_tool,
        "coordinateSpace": "screen_pixels",
        "width": width,
        "height": height,
    }
    if browser_profile:
        artifact["visualAutomation"]["browserProfile"] = normalize_browser_profile(browser_profile)
    version = ArtifactVersion(
        artifact_id=artifact_id,
        version=1,
        author=created_by,
        ts=now,
        note=f"captured by {source_tool}",
        uri=str(path),
        storage="filesystem",
        content_hash=artifact["contentHash"],
        content_size_bytes=len(data),
        content_mime="image/png",
        preview=preview,
    ).dump()
    version["visualAutomation"] = artifact["visualAutomation"]
    await repo.put_entity("artifact", artifact, dept=owner_dept, project=None, status="approved", ts=now)
    await repo.put_entity(
        "artifact_version",
        {**version, "id": f"{artifact_id}:1"},
        dept=owner_dept,
        project=None,
        status="approved",
        ts=now,
    )
    return artifact
