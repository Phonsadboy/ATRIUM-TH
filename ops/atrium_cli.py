#!/usr/bin/env python3
"""ATRIUM local setup shortcut.

This module intentionally uses only the Python standard library so `./atrium
doctor` works immediately after a fresh clone.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
SYSTEM_DIR = ROOT / "system"
UI_DIR = ROOT / "ui"
LOG_DIR = SYSTEM_DIR / "logs"
SYSTEM_ENV = SYSTEM_DIR / ".env"
SYSTEM_ENV_EXAMPLE = SYSTEM_DIR / ".env.example"
UI_ENV = UI_DIR / ".env.local"
EXPECTED_REMOTE_SLUG = "Phonsadboy/ATRIUM-TH"
BACKEND_URL = "http://127.0.0.1:8787"
FRONTEND_URL = "http://127.0.0.1:5173"
BACKEND_SCREEN = "ai-company-backend"
UI_SCREEN = "ai-company-ui"
BACKEND_PID = LOG_DIR / "backend.pid"
UI_PID = LOG_DIR / "ui.pid"
HOST_BRIDGE_PARITY_REPORT = SYSTEM_DIR / "data" / "host-bridge-parity-report.json"
PORTS = {
    8787: "backend",
    5173: "frontend",
    5432: "postgres",
    11434: "ollama",
}
SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH")
PROVIDER_AUTH_REDACT_KEYS = {
    "access",
    "accesstoken",
    "accountid",
    "apikey",
    "authorizationurl",
    "clientsecret",
    "email",
    "idtoken",
    "password",
    "refresh",
    "refreshtoken",
    "secret",
    "token",
}
FULL_STACK_DEFAULTS = {
    "ATRIUM_AGENT_BACKEND": "native",
    "ATRIUM_DATABASE_URL": "postgresql+asyncpg://atrium:atrium@127.0.0.1:5432/atrium",
    "ATRIUM_DATA_DIR": "./data",
    "ATRIUM_GRAPH_BACKEND": "auto",
    "ATRIUM_HOST": "127.0.0.1",
    "ATRIUM_PORT": "8787",
    "ATRIUM_CORS_ORIGINS": "http://localhost:5173,http://127.0.0.1:5173",
    "ATRIUM_OLLAMA_BASE_URL": "http://127.0.0.1:11434",
    "ATRIUM_OLLAMA_EMBEDDING_MODEL": "bge-m3",
    "ATRIUM_EMBEDDING_DIM": "1024",
    "ATRIUM_OBJECT_STORE_ENABLED": "true",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "ATRIUM_CHAT_REPLY_WORKER_CONCURRENCY": "5",
    "ATRIUM_DEPARTMENT_WORKER_CONCURRENCY": "5",
}
UI_DEFAULTS = {"VITE_ATRIUM_API_URL": BACKEND_URL}
PARITY_COMMAND_KEYS = (
    "parityRunId",
    "sourceFingerprint",
    "sourceManifestSha256",
    "sourceFileCount",
    "backendSourceFingerprint",
    "backendSourceManifestSha256",
    "sourceFingerprintStatus",
    "macosSourceValidate",
    "macosProbe",
    "macosArtifactValidate",
    "windowsHandoff",
    "windowsHandoffArtifact",
    "windowsRunIdSet",
    "windowsSourceValidate",
    "windowsProbe",
    "windowsLiveProofRunner",
    "windowsArtifactValidateOnWindows",
    "windowsArtifactCopyHint",
    "windowsArtifactValidateLocal",
    "automationReport",
    "report",
    "verify",
    "legacyParityReport",
)
DEFAULT_WINDOWS_PROOF_PATH = "C:\\Temp\\atrium_host_bridge_windows_live.json"
DEFAULT_WINDOWS_LOCAL_COPY_PATH = "/tmp/atrium_host_bridge_windows_live.json"
DEFAULT_WINDOWS_HANDOFF_PATH = "/tmp/atrium_windows_handoff.json"


def common_path_candidates() -> list[str]:
    candidates = [
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/Applications/Docker.app/Contents/Resources/bin",
    ]
    if platform.system() == "Windows":
        candidates.extend(
            [
                str(Path.home() / ".local" / "bin"),
                str(Path.home() / "AppData" / "Local" / "Programs" / "Python" / "Launcher"),
                str(Path.home() / "AppData" / "Local" / "Microsoft" / "WindowsApps"),
                str(Path.home() / "AppData" / "Roaming" / "npm"),
                "C:\\Program Files\\Docker\\Docker\\resources\\bin",
                "C:\\Program Files\\Git\\cmd",
                "C:\\Program Files\\nodejs",
            ]
        )
    return candidates


def ensure_common_paths() -> None:
    candidates = common_path_candidates()
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    changed = False
    for candidate in reversed(candidates):
        if Path(candidate).exists() and candidate not in parts:
            parts.insert(0, candidate)
            changed = True
    if changed:
        os.environ["PATH"] = os.pathsep.join(parts)


def persist_windows_user_paths() -> None:
    if platform.system() != "Windows":
        return
    powershell = powershell_command()
    if not powershell:
        return
    paths = [candidate for candidate in common_path_candidates() if Path(candidate).exists()]
    if not paths:
        return
    script = (
        "$paths = ConvertFrom-Json @'\n"
        + json.dumps(paths)
        + "\n'@; "
        "$current = [Environment]::GetEnvironmentVariable('Path','User'); "
        "$parts = New-Object 'System.Collections.Generic.List[string]'; "
        "foreach ($part in (($current -as [string]) -split [System.IO.Path]::PathSeparator)) { if ($part) { $parts.Add($part) } }; "
        "$changed = $false; "
        "foreach ($path in $paths) { if ((Test-Path -LiteralPath $path) -and -not $parts.Contains($path)) { $parts.Add($path); $changed = $true } }; "
        "if ($changed) { [Environment]::SetEnvironmentVariable('Path', ($parts -join [System.IO.Path]::PathSeparator), 'User') }"
    )
    run([powershell, "-NoProfile", "-Command", script], timeout=10)


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass
class EnvUpdate:
    action: str
    path: Path
    created: bool
    changed_keys: list[str]
    preserved_keys: list[str]


class StepFailure(RuntimeError):
    def __init__(self, message: str, *, next_step: str | None = None):
        super().__init__(message)
        self.next_step = next_step


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def print_header(title: str) -> None:
    print(f"\n== {title} ==")


def print_check(ok: bool, label: str, detail: str = "") -> None:
    status = "OK" if ok else "BLOCKED"
    suffix = f" - {detail}" if detail else ""
    print(f"[{status}] {label}{suffix}")


def print_info(label: str, detail: str = "") -> None:
    suffix = f" - {detail}" if detail else ""
    print(f"[INFO] {label}{suffix}")


def redact_value(key: str, value: str | None) -> str:
    if value is None or value == "":
        return "missing"
    upper = key.upper()
    if any(marker in upper for marker in SECRET_MARKERS):
        return "set"
    if "postgresql" in value and "@" in value:
        return re.sub(r":([^:@/]+)@", ":***@", value)
    return value


def redact_text(text: str) -> str:
    redacted: list[str] = []
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            redacted.append(f"{key}={redact_value(key.strip(), value.strip())}")
        else:
            redacted.append(line)
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(redacted) + suffix


def redact_json_value(value: object, *, parent_key: str = "") -> object:
    key = parent_key.lower().replace("_", "")
    if key in PROVIDER_AUTH_REDACT_KEYS or any(marker in key for marker in ("token", "secret", "password", "apikey")):
        return "set" if value not in (None, "", [], {}) else "missing"
    if isinstance(value, dict):
        return {str(k): redact_json_value(v, parent_key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_json_value(item, parent_key=parent_key) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def run(
    args: Sequence[str],
    *,
    cwd: Path = ROOT,
    timeout: int = 30,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> CommandResult:
    try:
        completed = subprocess.run(
            list(args),
            cwd=str(cwd),
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        result = CommandResult(127, "", f"{args[0]} not found")
    except subprocess.TimeoutExpired as exc:
        result = CommandResult(124, exc.stdout or "", exc.stderr or f"timed out after {timeout}s")
    else:
        result = CommandResult(completed.returncode, completed.stdout, completed.stderr)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise StepFailure(f"{' '.join(args)} failed: {detail[:1000]}")
    return result


def run_interactive(
    args: Sequence[str],
    *,
    cwd: Path = ROOT,
    dry_run: bool = False,
    env: dict[str, str] | None = None,
) -> CommandResult:
    if dry_run:
        print(f"[DRY-RUN] {' '.join(args)}")
        return CommandResult(0, "", "")
    print(f"$ {' '.join(args)}")
    try:
        completed = subprocess.run(list(args), cwd=str(cwd), env=env)
    except FileNotFoundError:
        raise StepFailure(f"{args[0]} not found") from None
    if completed.returncode != 0:
        raise StepFailure(f"{' '.join(args)} failed with exit code {completed.returncode}")
    return CommandResult(completed.returncode, "", "")


def command_path(name: str) -> str | None:
    if name == "brew":
        for candidate in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
            if Path(candidate).exists():
                return candidate
    if name == "docker":
        for candidate in (
            "/Applications/Docker.app/Contents/Resources/bin/docker",
            "C:/Program Files/Docker/Docker/resources/bin/docker.exe",
        ):
            if Path(candidate).exists():
                return candidate
    if name == "python3" and platform.system() == "Windows":
        return windows_python3_command()
    if platform.system() == "Windows" and not name.lower().endswith((".exe", ".cmd", ".bat")):
        for suffix in (".exe", ".cmd", ".bat"):
            resolved = shutil.which(f"{name}{suffix}")
            if resolved:
                return resolved
    return shutil.which(name)


def _which_windows_executable(name: str) -> str | None:
    resolved = shutil.which(name)
    if resolved:
        return resolved
    if not name.lower().endswith((".exe", ".cmd", ".bat")):
        for suffix in (".exe", ".cmd", ".bat"):
            resolved = shutil.which(f"{name}{suffix}")
            if resolved:
                return resolved
    return None


def windows_python3_command() -> str | None:
    if platform.system() != "Windows":
        return shutil.which("python3")
    for name, args in (("py", ["-3", "--version"]), ("python", ["--version"]), ("python3", ["--version"])):
        exe = _which_windows_executable(name)
        if not exe:
            continue
        try:
            completed = subprocess.run(
                [exe, *args],
                text=True,
                capture_output=True,
                timeout=8.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        version_text = f"{completed.stdout or ''} {completed.stderr or ''}"
        if completed.returncode == 0 and re.search(r"Python 3\.", version_text):
            return exe
    return None


def powershell_command() -> str | None:
    return command_path("powershell.exe") or command_path("powershell") or command_path("pwsh")


def docker_compose_cmd() -> list[str] | None:
    docker = command_path("docker")
    if not docker:
        return None
    compose = run([docker, "compose", "version"], timeout=10)
    if compose.returncode == 0:
        return [docker, "compose"]
    legacy = command_path("docker-compose")
    if legacy:
        return [legacy]
    return None


def prompt_enter(message: str, *, assume_yes: bool = False, always: bool = False) -> None:
    if assume_yes and not always:
        return
    if not sys.stdin.isatty():
        print_info("manual step", message)
        return
    print(message)
    input("Press Enter when ready...")


def install_homebrew(*, dry_run: bool = False) -> None:
    if platform.system() != "Darwin":
        raise StepFailure(
            "Homebrew auto-install is only supported on macOS",
            next_step=f"Install the missing tools for this OS, then rerun {local_cli_command('setup')}.",
        )
    if command_path("brew"):
        return
    print_header("Install Homebrew")
    env = os.environ.copy()
    env.setdefault("NONINTERACTIVE", "1")
    run_interactive(
        [
            "/bin/bash",
            "-c",
            '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
        ],
        dry_run=dry_run,
        env=env,
    )
    ensure_common_paths()
    if not command_path("brew"):
        raise StepFailure(
            "Homebrew installed but brew is not on PATH yet",
            next_step='Restart Terminal, or run: eval "$(/opt/homebrew/bin/brew shellenv)"',
        )


def open_docker_desktop() -> bool:
    if platform.system() == "Darwin" and Path("/Applications/Docker.app").exists():
        run(["open", "-a", "Docker"], timeout=10)
        return True
    if platform.system() == "Windows":
        powershell = powershell_command()
        if not powershell:
            return False
        for candidate in (
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe",
            Path(os.environ.get("LocalAppData", "")) / "Docker" / "Docker Desktop.exe",
        ):
            if candidate.exists():
                path_literal = json.dumps(str(candidate))
                run([powershell, "-NoProfile", "-Command", f"Start-Process -FilePath {path_literal}"], timeout=20)
                return True
    return False


def docker_ready() -> bool:
    docker = command_path("docker")
    if not docker:
        return False
    return run([docker, "info"], timeout=15).returncode == 0


def wait_for_docker_ready(*, seconds: int = 240, assume_yes: bool = False, prompt: bool = True) -> bool:
    if docker_ready():
        return True
    opened = open_docker_desktop()
    if opened:
        print_info("Docker Desktop", f"opened; waiting up to {seconds}s")
    deadline = time.time() + seconds
    while time.time() < deadline:
        if docker_ready():
            print_check(True, "Docker", "running")
            return True
        time.sleep(3)
    if prompt:
        prompt_enter(
            "Open Docker Desktop and finish any first-run, license, password, or Windows prompt.",
            assume_yes=assume_yes,
            always=True,
        )
    return docker_ready()


def open_url(url: str) -> None:
    if platform.system() == "Darwin":
        run(["open", url], timeout=10)
        return
    if platform.system() == "Windows":
        startfile = getattr(os, "startfile", None)
        if callable(startfile):
            startfile(url)
            return
        powershell = powershell_command()
        if powershell:
            run([powershell, "-NoProfile", "-Command", f"Start-Process -FilePath {json.dumps(url)}"], timeout=10)
            return
        cmd = command_path("cmd.exe") or command_path("cmd")
        if cmd:
            run([cmd, "/C", "start", "", url], timeout=10)
            return
    opener = command_path("xdg-open")
    if opener:
        run([opener, url], timeout=10)


def open_frontend_url() -> None:
    open_url(FRONTEND_URL)


def ensure_repo_root() -> None:
    if not (ROOT / "docker-compose.yml").exists() or not SYSTEM_DIR.exists() or not UI_DIR.exists():
        raise StepFailure(f"{ROOT} does not look like an ATRIUM repo root")


def remote_ok() -> tuple[bool, str]:
    if not (ROOT / ".git").exists():
        return False, "not a git checkout"
    result = run(["git", "remote", "-v"], timeout=10)
    remotes = result.stdout.strip()
    if result.returncode != 0:
        return False, (result.stderr or "git remote failed").strip()
    normalized = remotes.replace(":", "/")
    return EXPECTED_REMOTE_SLUG in normalized, remotes or "no remotes configured"


def git_status_summary() -> str:
    result = run(["git", "status", "--short", "--branch"], timeout=10)
    if result.returncode != 0:
        return (result.stderr or "git status failed").strip()
    return result.stdout.strip() or "clean"


def is_i_cloud_risky(path: Path) -> tuple[bool, str]:
    expanded = path.expanduser().resolve()
    home = Path.home().resolve()
    if platform.system() == "Windows":
        raw_lower = str(expanded).lower()
        if "onedrive" in raw_lower:
            return True, f"{expanded} is under OneDrive storage"
        for folder in ("Desktop", "Documents", "Downloads"):
            candidate = home / folder
            try:
                expanded.relative_to(candidate)
            except ValueError:
                continue
            return True, f"{expanded} is under {folder}, which is unsafe for runtime-heavy files"
        return False, str(expanded)
    risky_parts = ("Library/Mobile Documents", "iCloud Drive")
    raw = str(expanded)
    if any(part in raw for part in risky_parts):
        return True, f"{expanded} is under iCloud storage"
    for folder in ("Desktop", "Documents"):
        candidate = home / folder
        try:
            expanded.relative_to(candidate)
        except ValueError:
            continue
        mobile_docs = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / folder
        if mobile_docs.exists():
            return True, f"{expanded} is under {folder}, which may be iCloud-synced"
    return False, str(expanded)


def memory_gb() -> str:
    if platform.system() == "Darwin":
        result = run(["sysctl", "-n", "hw.memsize"], timeout=5)
        if result.returncode == 0:
            try:
                return f"{int(result.stdout.strip()) / 1024**3:.1f} GB"
            except ValueError:
                pass
    powershell = powershell_command() if platform.system() == "Windows" else None
    if powershell:
        result = run(
            [
                powershell,
                "-NoProfile",
                "-Command",
                "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)",
            ],
            timeout=8,
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"{result.stdout.strip()} GB"
    return "unknown"


def browser_installed() -> bool:
    mac_apps = (
        Path("/Applications/Google Chrome.app"),
        Path.home() / "Applications/Google Chrome.app",
        Path("/Applications/Microsoft Edge.app"),
        Path.home() / "Applications/Microsoft Edge.app",
        Path("/Applications/Brave Browser.app"),
        Path.home() / "Applications/Brave Browser.app",
        Path("/Applications/Chromium.app"),
        Path.home() / "Applications/Chromium.app",
    )
    if any(path.exists() for path in mac_apps):
        return True
    if any(command_path(name) is not None for name in ("google-chrome", "msedge", "brave", "brave-browser", "chromium")):
        return True
    if platform.system() == "Windows":
        roots = [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("LocalAppData"),
        ]
        for root in roots:
            if not root:
                continue
            base = Path(root)
            for candidate in (
                base / "Google" / "Chrome" / "Application" / "chrome.exe",
                base / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                base / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
                base / "Chromium" / "Application" / "chrome.exe",
            ):
                if candidate.exists():
                    return True
    return False


def chrome_installed() -> bool:
    return browser_installed()


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def port_owner(port: int) -> str:
    powershell = powershell_command() if platform.system() == "Windows" else None
    if powershell:
        script = (
            f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | "
            "Select-Object -First 3; "
            "if (-not $c) { 'free'; exit 0 }; "
            "$c | ForEach-Object { "
            "$p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue; "
            "\"PID=$($_.OwningProcess) Process=$($p.ProcessName) Local=$($_.LocalAddress):$($_.LocalPort)\" "
            "}"
        )
        result = run([powershell, "-NoProfile", "-Command", script], timeout=8)
        text = result.stdout.strip()
        if result.returncode == 0 and text:
            return text.replace("\n", "; ")
        return "free"
    result = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], timeout=5)
    if result.returncode != 0 or not result.stdout.strip():
        return "free"
    lines = result.stdout.strip().splitlines()
    return "; ".join(line.strip() for line in lines[1:4]) if len(lines) > 1 else lines[0]


def http_get_json(url: str, *, timeout: float = 3.0) -> tuple[bool, str, object | None]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return False, str(exc), None
    except TimeoutError as exc:
        return False, str(exc), None
    try:
        return True, body, json.loads(body)
    except json.JSONDecodeError:
        return True, body, None


def http_json_request(
    url: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    timeout: float = 5.0,
) -> tuple[bool, str, object | None]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return False, body, json.loads(body)
        except json.JSONDecodeError:
            return False, body or str(exc), None
    except urllib.error.URLError as exc:
        return False, str(exc), None
    except TimeoutError as exc:
        return False, str(exc), None
    try:
        return True, body, json.loads(body)
    except json.JSONDecodeError:
        return True, body, None


def backend_json(path: str, *, timeout: float = 5.0) -> tuple[bool, str, object | None]:
    return http_get_json(f"{BACKEND_URL}{path}", timeout=timeout)


def backend_json_request(
    path: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    timeout: float = 5.0,
) -> tuple[bool, str, object | None]:
    return http_json_request(f"{BACKEND_URL}{path}", method=method, payload=payload, timeout=timeout)


def bool_text(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "unknown"
    return str(value)


def summarize_provider_auth_payload(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["provider-auth=unavailable"]
    lines: list[str] = []
    chatgpt = payload.get("chatgptAccount")
    if isinstance(chatgpt, dict):
        login = chatgpt.get("login") if isinstance(chatgpt.get("login"), dict) else {}
        parts = [
            f"ready={bool_text(chatgpt.get('ready'))}",
            f"source={chatgpt.get('source') or 'unknown'}",
            f"plan={chatgpt.get('chatgptPlanType') or 'unknown'}",
            f"expired={bool_text(chatgpt.get('expired'))}",
        ]
        lines.append(f"ChatGPT account login: {', '.join(parts)}")
        if login:
            login_parts = [
                f"status={login.get('status') or 'unknown'}",
                f"redirectUri={login.get('redirectUri') or 'unknown'}",
                f"expiresAt={login.get('expiresAt') or 'unknown'}",
            ]
            if login.get("error"):
                login_parts.append(f"error={str(login.get('error'))[:180]}")
            lines.append(f"ChatGPT login session: {', '.join(login_parts)}")
    claude = payload.get("claudeCode")
    if isinstance(claude, dict):
        parts = [
            f"ready={bool_text(claude.get('ready'))}",
            f"installed={bool_text(claude.get('installed'))}",
            f"loggedIn={bool_text(claude.get('loggedIn'))}",
            f"subscription={claude.get('subscriptionType') or 'unknown'}",
            f"stale={bool_text(claude.get('stale'))}",
        ]
        lines.append(f"Claude Code account login: {', '.join(parts)}")
    return lines or ["provider-auth=unavailable"]


PROVIDER_AUTH_TARGETS = {
    "chatgpt": "chatgpt",
    "chatgpt-account": "chatgpt",
    "claude": "claude-code",
    "claude-code": "claude-code",
}


def normalize_provider_auth_target(value: str) -> str:
    try:
        return PROVIDER_AUTH_TARGETS[value.strip().lower()]
    except KeyError:
        raise StepFailure(
            f"Unknown provider auth target: {value}",
            next_step="Use one of: chatgpt, chatgpt-account, claude, claude-code.",
        ) from None


def provider_ready_from_status(payload: object, target: str) -> bool:
    if not isinstance(payload, dict):
        return False
    if target == "chatgpt":
        status = payload.get("chatgptAccount")
        return isinstance(status, dict) and bool(status.get("ready"))
    if target == "claude-code":
        status = payload.get("claudeCode")
        return isinstance(status, dict) and bool(status.get("ready"))
    return False


def provider_auth_start_path(target: str) -> str:
    return f"/api/provider-auth/{target}/start"


def provider_auth_disconnect_path(target: str) -> str:
    return f"/api/provider-auth/{target}/disconnect"


def provider_login_summary(target: str, payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return [f"{target} login: started=unknown"]
    lines: list[str] = []
    if target == "chatgpt":
        lines.append(
            "ChatGPT login: "
            f"status={payload.get('status') or 'unknown'}, "
            f"redirectUri={payload.get('redirectUri') or 'unknown'}, "
            f"expiresAt={payload.get('expiresAt') or 'unknown'}"
        )
        if payload.get("authorizationUrl"):
            lines.append("ChatGPT login URL is ready; complete the OAuth flow in a browser.")
        return lines
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    lines.append(
        "Claude Code login: "
        f"started={bool_text(payload.get('started'))}, "
        f"mode={payload.get('mode') or 'unknown'}, "
        f"ready={bool_text(status.get('ready'))}, "
        f"installed={bool_text(status.get('installed'))}, "
        f"loggedIn={bool_text(status.get('loggedIn'))}, "
        f"subscription={status.get('subscriptionType') or 'unknown'}"
    )
    command = payload.get("command")
    if isinstance(command, str) and command.strip():
        lines.append(f"Claude Code login command: {command.strip()}")
    return lines


def provider_disconnect_summary(target: str, payload: object) -> list[str]:
    if target == "chatgpt":
        ok, _raw, status = backend_json("/api/provider-auth/status?probe=true", timeout=15.0)
        if ok:
            return summarize_provider_auth_payload(status)
        return ["ChatGPT disconnect: completed; provider status unavailable"]
    if isinstance(payload, dict):
        status = payload.get("status")
        lines = [
            (
                "Claude Code disconnect: "
                f"ok={bool_text(payload.get('ok'))}, "
                f"mode={payload.get('mode') or 'unknown'}, "
                f"started={bool_text(payload.get('started'))}"
            )
        ]
        if isinstance(status, dict):
            lines.extend(summarize_provider_auth_payload({"claudeCode": status}))
        return lines
    return [f"{target} disconnect: completed"]


def wait_provider_ready(target: str, timeout_s: float) -> tuple[bool, object | None]:
    deadline = time.time() + max(0.0, timeout_s)
    last_payload: object | None = None
    while True:
        ok, _raw, payload = backend_json("/api/provider-auth/status?probe=true", timeout=15.0)
        if ok:
            last_payload = payload
            if provider_ready_from_status(payload, target):
                return True, payload
        if time.time() >= deadline:
            return False, last_payload
        time.sleep(2)


def uv_python_command(script: str, extra_args: Sequence[str] = ()) -> list[str]:
    uv = command_path("uv")
    if not uv:
        raise StepFailure("uv is missing", next_step="Install uv, then rerun the ATRIUM command.")
    return [uv, "--project", "system", "run", "python", script, *extra_args]


def append_optional_flag(command: list[str], enabled: bool, flag: str) -> None:
    if enabled:
        command.append(flag)


def append_optional_value(command: list[str], flag: str, value: object | None) -> None:
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    command.extend([flag, text])


def summarize_full_autonomy(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["permission=unavailable"]
    status = payload.get("fullAutonomyStatus")
    if not isinstance(status, dict):
        status = payload
    entitlements = status.get("entitlements") if isinstance(status.get("entitlements"), dict) else {}
    entitlement_text = ", ".join(
        f"{key}={bool_text(entitlements.get(key))}"
        for key in ("hostShell", "hostFilesystem", "browserAutomation", "desktopAutomation", "externalSend", "credentials")
    )
    return [
        (
            "Owner permissions: "
            f"mode={status.get('mode') or payload.get('mode') or 'unknown'}, "
            f"active={bool_text(status.get('active'))}, "
            f"agentFullAccess={bool_text(status.get('agentFullAccess') if 'agentFullAccess' in status else payload.get('agentFullAccess'))}, "
            f"approvalGatesDisabled={bool_text(status.get('approvalGatesDisabled'))}, "
            f"decision={status.get('effectivePolicyDecision') or 'unknown'}"
        ),
        f"Owner entitlements: {entitlement_text}",
    ]


def summarize_host_bridge_payload(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["HostBridge=unavailable"]
    v2 = payload.get("v2") if isinstance(payload.get("v2"), dict) else {}
    host_bridge = v2.get("hostBridge") if isinstance(v2.get("hostBridge"), dict) else payload
    lines = [
        (
            "HostBridge: "
            f"platform={host_bridge.get('platform') or 'unknown'}, "
            f"shellReady={bool_text(host_bridge.get('shellReady'))}, "
            f"browserReady={bool_text(host_bridge.get('browserAutomationReady'))}, "
            f"desktopReady={bool_text(host_bridge.get('desktopAutomationReady'))}, "
            f"playwrightReady={bool_text(host_bridge.get('browserPlaywrightReady'))}, "
            f"isolatedProfile={bool_text(host_bridge.get('isolatedBrowserProfileReady'))}"
        )
    ]
    if host_bridge.get("platform") == "win32":
        lines.append(
            "Windows automation preflight: "
            f"interactiveSession={bool_text(host_bridge.get('interactiveSession'))}, "
            f"session={host_bridge.get('interactiveSessionName') or 'unknown'}, "
            f"checked={bool_text(host_bridge.get('windowsVisualPreflightChecked'))}, "
            f"ok={bool_text(host_bridge.get('windowsVisualPreflightOk'))}, "
            f"error={host_bridge.get('windowsVisualPreflightError') or 'none'}"
        )
    if host_bridge.get("platform") == "darwin":
        lines.append(
            "macOS automation preflight: "
            f"checked={bool_text(host_bridge.get('macosVisualPreflightChecked'))}, "
            f"ok={bool_text(host_bridge.get('macosVisualPreflightOk'))}, "
            f"error={host_bridge.get('macosVisualPreflightError') or 'none'}"
        )
    notes = host_bridge.get("notes")
    if isinstance(notes, list):
        for note in notes[:3]:
            lines.append(f"HostBridge note: {note}")
    return lines


def summarize_runtime_ai_tools(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["runtime=unavailable"]
    lines: list[str] = []
    provider = payload.get("provider") if isinstance(payload.get("provider"), dict) else {}
    lines.append(
        "AI providers: "
        f"ready={bool_text(provider.get('ready'))}, "
        f"openaiKey={bool_text(provider.get('hasOpenAIKey'))}, "
        f"chatgptOAuth={bool_text(provider.get('hasChatGPTAccountOAuth'))}, "
        f"claudeCode={bool_text(provider.get('hasClaudeCodeAccount'))}, "
        f"embeddings={provider.get('embeddings') or 'unknown'}"
    )
    v2 = payload.get("v2") if isinstance(payload.get("v2"), dict) else {}
    lines.append(
        "Tool registry: "
        f"default={v2.get('toolRegistryCount', 'unknown')}, "
        f"custom={v2.get('customToolCount', 'unknown')}"
    )
    full_autonomy = v2.get("fullAutonomy") if isinstance(v2.get("fullAutonomy"), dict) else None
    if full_autonomy:
        lines.extend(summarize_full_autonomy({"fullAutonomyStatus": full_autonomy}))
    lines.extend(summarize_host_bridge_payload(payload))
    return lines


def summarize_tool_catalog_payload(payload: object, *, limit: int = 8) -> list[str]:
    if not isinstance(payload, list):
        return ["tool-catalog=unavailable"]
    executors: dict[str, int] = {}
    risks: dict[str, int] = {}
    sample: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        executor = str(item.get("executor") or "unknown")
        risk = str(item.get("riskClass") or item.get("risk") or "unknown")
        executors[executor] = executors.get(executor, 0) + 1
        risks[risk] = risks.get(risk, 0) + 1
        name = str(item.get("name") or item.get("tool") or item.get("id") or "").strip()
        if name and len(sample) < limit:
            sample.append(name)
    executor_text = ", ".join(f"{key}={value}" for key, value in sorted(executors.items())) or "none"
    risk_text = ", ".join(f"{key}={value}" for key, value in sorted(risks.items())) or "none"
    lines = [
        f"Tool catalog: count={len(payload)}, executors={executor_text}, risks={risk_text}",
    ]
    if sample:
        lines.append(f"Tool catalog sample: {', '.join(sample)}")
    return lines


def summarize_connectors_payload(payload: object) -> list[str]:
    if not isinstance(payload, list):
        return ["connectors=unavailable"]
    interesting = {"web", "browser", "desktop", "git", "sandbox", "mcp"}
    lines: list[str] = []
    for connector in payload:
        if not isinstance(connector, dict) or connector.get("id") not in interesting:
            continue
        lines.append(
            "connector."
            f"{connector.get('id')}: "
            f"status={connector.get('status') or 'unknown'}, "
            f"read={bool_text(connector.get('readReady'))}, "
            f"write={bool_text(connector.get('writeReady'))}, "
            f"runtime={connector.get('runtimeStatus') or 'unknown'}, "
            f"proof={connector.get('proofStatus') or 'unknown'}"
        )
    return lines or ["connectors=unavailable"]


def docker_report_lines() -> list[str]:
    docker = command_path("docker")
    compose_cmd = docker_compose_cmd() if docker else None
    lines = [
        f"docker.cli={'present' if docker else 'missing'}",
        f"docker.compose={' '.join(compose_cmd) if compose_cmd else 'missing'}",
    ]
    if docker:
        lines.append(f"docker.running={bool_text(run([docker, 'info'], timeout=10).returncode == 0)}")
    else:
        lines.append("docker.running=false")
    return lines


def _host_bridge_gap_text(value: object) -> str:
    text = str(value or "").strip()
    if "ops/host_bridge_parity_report.py" not in text:
        return text
    return (
        f"Run {local_cli_command('automation', 'report')} with macOS and Windows --full probe artifacts, "
        f"then {local_cli_command('automation', 'audit')}, before claiming cross-OS HostBridge parity."
    )


def openclaw_requirement_gap_label(item: dict[str, object]) -> str:
    item_id = str(item.get("id") or item.get("label") or "unknown")
    details: list[str] = []
    if item.get("degradedByLocalFallback") is True:
        details.append("local fallback only")
    if item.get("requiresWriteReady") is True and item.get("writeReady") is False:
        details.append("write not ready")
    if item.get("readReady") is False:
        details.append("read not ready")
    external_requires = item.get("externalWriteRequires")
    if isinstance(external_requires, list):
        details.extend(str(value) for value in external_requires[:2] if str(value).strip())
    if not details:
        detail = str(item.get("runtimeStatus") or item.get("path") or item.get("requiredEvidence") or "").strip()
        if detail:
            details.append(detail)
    return item_id + (f": {'; '.join(details)}" if details else "")


def _command_option(command: str, option: str) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    for index, part in enumerate(parts[:-1]):
        if part == option:
            return parts[index + 1]
    return None


def _legacy_report_to_automation_report(command: str) -> str | None:
    if "ops/host_bridge_parity_report.py" not in command:
        return None
    macos = _command_option(command, "--macos")
    windows = _command_option(command, "--windows")
    if not macos or not windows:
        return None
    launcher = ".\\atrium.ps1" if windows_native() else "./atrium"
    parts = [launcher, "automation", "report", "--macos", shell_quote(macos), "--windows", shell_quote(windows)]
    output = _command_option(command, "--output")
    default_outputs = {
        HOST_BRIDGE_PARITY_REPORT,
        HOST_BRIDGE_PARITY_REPORT.relative_to(ROOT),
        HOST_BRIDGE_PARITY_REPORT.relative_to(SYSTEM_DIR),
    }
    if output and Path(output) not in default_outputs:
        parts.extend(["--output", shell_quote(output)])
    max_age = _command_option(command, "--max-artifact-age-hours")
    if max_age:
        parts.extend(["--max-artifact-age-hours", shell_quote(max_age)])
    windows_source_path = _command_option(command, "--windows-source-path")
    if windows_source_path:
        parts.extend(["--windows-source-path", shell_quote(windows_source_path)])
    if "--skip-current-source-check" in command:
        parts.append("--skip-current-source-check")
    return " ".join(parts)


def current_source_summary() -> dict[str, object] | None:
    command = uv_python_command("ops/host_bridge_source_summary.py")
    result = run(command, cwd=ROOT, timeout=15)
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def current_source_fingerprint() -> str | None:
    payload = current_source_summary()
    value = payload.get("sourceFingerprint") if isinstance(payload, dict) else None
    if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
        return value
    return None


def _artifact_summary(
    artifact: str,
    *,
    label: str,
    expect_parity_run_id: str | None = None,
    expect_source_fingerprint: str | None = None,
    expect_source_manifest_sha256: str | None = None,
    expect_source_file_count: int | None = None,
    max_artifact_age_hours: float = 24.0,
) -> dict[str, object]:
    command = uv_python_command("ops/host_bridge_artifact_summary.py")
    append_optional_value(command, "--label", label)
    append_optional_value(command, "--expect-parity-run-id", expect_parity_run_id)
    append_optional_value(command, "--expect-source-fingerprint", expect_source_fingerprint)
    append_optional_value(command, "--expect-source-manifest-sha256", expect_source_manifest_sha256)
    append_optional_value(command, "--expect-source-file-count", expect_source_file_count)
    append_optional_value(command, "--max-artifact-age-hours", max_artifact_age_hours)
    command.append(artifact)
    result = run(command, cwd=ROOT, timeout=30)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        detail = (result.stderr or result.stdout or "").strip()
        raise StepFailure(f"HostBridge artifact validation did not return JSON: {detail[:1000]}") from None
    if not isinstance(payload, dict):
        raise StepFailure("HostBridge artifact validation returned a non-object payload")
    if result.returncode != 0 or payload.get("ok") is not True:
        findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
        detail = "; ".join(str(item) for item in findings[:6]) or (result.stderr or "artifact validation failed").strip()
        raise StepFailure(f"HostBridge {label} artifact is not valid for handoff: {detail[:1000]}")
    return payload


def ps_single_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_windows_handoff_payload(
    *,
    macos_artifact: str,
    macos_summary: dict[str, object],
    source_summary: dict[str, object],
    windows_output: str,
    windows_local_copy: str,
) -> dict[str, object]:
    parity_run_id = str(macos_summary.get("parityRunId") or "")
    source_fingerprint = str(source_summary.get("sourceFingerprint") or "")
    source_manifest_sha256 = str(source_summary.get("sourceManifestSha256") or "")
    source_file_count = int(source_summary.get("sourceFileCount") or 0)
    windows_live = (
        ".\\atrium.ps1 automation windows-live-proof "
        f"--parity-run-id {ps_single_quote(parity_run_id)} "
        f"--source-fingerprint {source_fingerprint} "
        f"--source-manifest-sha256 {source_manifest_sha256} "
        f"--source-file-count {source_file_count} "
        f"--output {ps_single_quote(windows_output)}"
    )
    windows_source = (
        ".\\atrium.ps1 automation source "
        f"--expect-source-fingerprint {source_fingerprint} "
        f"--expect-source-manifest-sha256 {source_manifest_sha256} "
        f"--expect-source-file-count {source_file_count}"
    )
    windows_artifact = (
        ".\\atrium.ps1 automation artifact --label windows "
        f"--expect-parity-run-id {ps_single_quote(parity_run_id)} "
        f"--expect-source-fingerprint {source_fingerprint} "
        f"--expect-source-manifest-sha256 {source_manifest_sha256} "
        f"--expect-source-file-count {source_file_count} "
        f"{ps_single_quote(windows_output)}"
    )
    report = (
        f"{local_cli_command('automation', 'report')} "
        f"--macos {shell_quote(macos_artifact)} "
        f"--windows {shell_quote(windows_local_copy)} "
        f"--windows-source-path {shell_quote(windows_output)}"
    )
    audit = local_cli_command("automation", "audit")
    return {
        "ok": True,
        "schemaVersion": 1,
        "kind": "atrium.hostBridge.windowsProofHandoff",
        "generatedAt": int(time.time() * 1000),
        "repoRoot": str(ROOT),
        "source": {
            "sourceFingerprint": source_fingerprint,
            "sourceManifestSha256": source_manifest_sha256,
            "sourceFileCount": source_file_count,
            "gitHead": source_summary.get("gitHead"),
        },
        "macosArtifact": {
            "path": macos_artifact,
            "artifactSha256": macos_summary.get("artifactSha256"),
            "parityRunId": parity_run_id,
            "hostPlatform": macos_summary.get("hostPlatform"),
            "hostFingerprint": macos_summary.get("hostFingerprint"),
            "generatedAt": macos_summary.get("generatedAt"),
        },
        "windowsProof": {
            "outputPath": windows_output,
            "localCopyPath": windows_local_copy,
            "commands": {
                "sourceValidate": windows_source,
                "liveProof": windows_live,
                "artifactValidate": windows_artifact,
            },
            "copyInstruction": f"Copy {windows_output} from the Windows host to {windows_local_copy} on this host.",
        },
        "finalVerification": {
            "commands": {
                "report": report,
                "audit": audit,
            },
            "requiredGate": "Run report with copied Windows artifact, then audit must pass before claiming OpenClaw-level Windows parity.",
        },
    }


def _load_json_file(path: Path) -> tuple[dict[str, object] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    except OSError as exc:
        return None, f"read failed: {type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return None, "JSON root is not an object"
    return payload, None


def _source_fingerprint_status(value: object, current_source: dict[str, object]) -> str:
    current = current_source.get("sourceFingerprint")
    if not isinstance(value, str) or not value.strip():
        return "missing"
    if isinstance(current, str) and value == current:
        return "current"
    return "stale"


def collect_local_proof_artifacts(current_source: dict[str, object] | None = None) -> dict[str, object]:
    source = current_source if isinstance(current_source, dict) else current_source_summary()
    source = source if isinstance(source, dict) else {}
    current_fingerprint = source.get("sourceFingerprint")

    macos_path = Path("/tmp/atrium_host_bridge_macos_live.json")
    windows_path = Path(DEFAULT_WINDOWS_LOCAL_COPY_PATH)
    handoff_path = Path(DEFAULT_WINDOWS_HANDOFF_PATH)
    artifacts: dict[str, object] = {
        "currentSourceFingerprint": current_fingerprint if isinstance(current_fingerprint, str) else None,
    }
    for label, path in (("macos", macos_path), ("windowsLocal", windows_path)):
        payload, error = _load_json_file(path)
        item: dict[str, object] = {"path": str(path), "exists": payload is not None}
        if error:
            item["status"] = error
        if isinstance(payload, dict):
            artifact_source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
            fingerprint = artifact_source.get("sourceFingerprint")
            item.update({
                "ok": payload.get("ok") is True,
                "mode": payload.get("mode"),
                "parityRunId": payload.get("parityRunId"),
                "sourceFingerprint": fingerprint,
                "sourceStatus": _source_fingerprint_status(fingerprint, source),
                "generatedAt": payload.get("generatedAt"),
            })
        artifacts[label] = item
    handoff, handoff_error = _load_json_file(handoff_path)
    handoff_item: dict[str, object] = {"path": str(handoff_path), "exists": handoff is not None}
    if handoff_error:
        handoff_item["status"] = handoff_error
    if isinstance(handoff, dict):
        handoff_source = handoff.get("source") if isinstance(handoff.get("source"), dict) else {}
        macos_artifact = handoff.get("macosArtifact") if isinstance(handoff.get("macosArtifact"), dict) else {}
        fingerprint = handoff_source.get("sourceFingerprint")
        handoff_item.update({
            "ok": handoff.get("ok") is True,
            "kind": handoff.get("kind"),
            "parityRunId": macos_artifact.get("parityRunId"),
            "sourceFingerprint": fingerprint,
            "sourceStatus": _source_fingerprint_status(fingerprint, source),
            "generatedAt": handoff.get("generatedAt"),
        })
    artifacts["handoff"] = handoff_item
    return artifacts


def summarize_local_proof_artifacts(payload: object) -> list[str]:
    if isinstance(payload, dict) and isinstance(payload.get("localArtifacts"), dict):
        artifacts = payload.get("localArtifacts")
    else:
        artifacts = payload
    if not isinstance(artifacts, dict):
        return ["local proof artifacts=unavailable"]
    lines: list[str] = []
    for label in ("macos", "handoff", "windowsLocal"):
        item = artifacts.get(label)
        if not isinstance(item, dict):
            lines.append(f"{label}: exists=false, status=missing")
            continue
        exists = bool_text(item.get("exists"))
        status = item.get("status")
        if item.get("exists") is not True:
            lines.append(f"{label}: exists={exists}, status={status or 'missing'}")
            continue
        parts = [
            f"exists={exists}",
            f"ok={bool_text(item.get('ok'))}",
            f"source={item.get('sourceStatus') or 'unknown'}",
        ]
        run_id = item.get("parityRunId")
        if isinstance(run_id, str) and run_id:
            parts.append(f"run={run_id}")
        lines.append(f"{label}: " + ", ".join(parts))
    return lines


def normalize_parity_commands(commands: object, *, current_source: dict[str, object] | str | None = None) -> dict[str, object]:
    if not isinstance(commands, dict):
        return {}
    normalized = dict(commands)
    if isinstance(current_source, str):
        current_summary: dict[str, object] = {"sourceFingerprint": current_source}
    elif isinstance(current_source, dict):
        current_summary = current_source
    else:
        current_summary = {}
    current_fingerprint = current_summary.get("sourceFingerprint")
    current_manifest_sha = current_summary.get("sourceManifestSha256")
    current_file_count = current_summary.get("sourceFileCount")
    backend_fingerprint = normalized.get("sourceFingerprint")
    backend_manifest_sha = normalized.get("sourceManifestSha256")
    if (
        isinstance(current_fingerprint, str)
        and isinstance(backend_fingerprint, str)
        and re.fullmatch(r"[0-9a-fA-F]{64}", backend_fingerprint)
        and backend_fingerprint.lower() != current_fingerprint.lower()
    ):
        for key, value in list(normalized.items()):
            if isinstance(value, str):
                normalized[key] = value.replace(backend_fingerprint, current_fingerprint)
        normalized["backendSourceFingerprint"] = backend_fingerprint
        normalized["sourceFingerprint"] = current_fingerprint
        normalized["sourceFingerprintStatus"] = "rewritten_from_stale_backend"
    elif isinstance(current_fingerprint, str) and "sourceFingerprint" not in normalized:
        normalized["sourceFingerprint"] = current_fingerprint
    if isinstance(current_manifest_sha, str) and re.fullmatch(r"[0-9a-f]{64}", current_manifest_sha):
        if isinstance(backend_manifest_sha, str) and backend_manifest_sha != current_manifest_sha:
            normalized["backendSourceManifestSha256"] = backend_manifest_sha
        normalized["sourceManifestSha256"] = current_manifest_sha
    if isinstance(current_file_count, int) and current_file_count > 0:
        normalized["sourceFileCount"] = str(current_file_count)
    if isinstance(current_manifest_sha, str) and isinstance(current_file_count, int) and current_file_count > 0:
        provenance_command_keys = (
            "macosSourceValidate",
            "windowsSourceValidate",
            "macosProbe",
            "windowsProbe",
            "macosArtifactValidate",
            "windowsArtifactValidateOnWindows",
            "windowsArtifactValidateLocal",
        )
        for key in provenance_command_keys:
            value = normalized.get(key)
            if isinstance(value, str) and (
                "ops/host_bridge_source_summary.py" in value
                or "ops/macos_host_bridge_probe.py" in value
                or "ops/windows_host_bridge_probe.py" in value
                or "ops/host_bridge_artifact_summary.py" in value
                or "automation source" in value
                or "automation windows-probe" in value
                or "automation artifact" in value
            ):
                if "--expect-source-manifest-sha256" in value:
                    value = re.sub(
                        r"--expect-source-manifest-sha256\s+\S+",
                        f"--expect-source-manifest-sha256 {current_manifest_sha}",
                        value,
                    )
                else:
                    value = f"{value} --expect-source-manifest-sha256 {current_manifest_sha}"
                if "--expect-source-file-count" in value:
                    value = re.sub(
                        r"--expect-source-file-count\s+\S+",
                        f"--expect-source-file-count {current_file_count}",
                        value,
                    )
                else:
                    value = f"{value} --expect-source-file-count {current_file_count}"
                normalized[key] = value
        live = normalized.get("windowsLiveProofRunner")
        if isinstance(live, str) and (
            "windows_host_bridge_live_proof.ps1" in live
            or "automation windows-live-proof" in live
        ):
            live_uses_atrium_cli = "automation windows-live-proof" in live
            if "-SourceManifestSha256" in live:
                live = re.sub(r"-SourceManifestSha256\s+\S+", f"-SourceManifestSha256 {current_manifest_sha}", live)
            elif "--source-manifest-sha256" in live:
                live = re.sub(
                    r"--source-manifest-sha256\s+\S+",
                    f"--source-manifest-sha256 {current_manifest_sha}",
                    live,
                )
            else:
                flag = "--source-manifest-sha256" if live_uses_atrium_cli else "-SourceManifestSha256"
                live = f"{live} {flag} {current_manifest_sha}"
            if "-SourceFileCount" in live:
                live = re.sub(r"-SourceFileCount\s+\S+", f"-SourceFileCount {current_file_count}", live)
            elif "--source-file-count" in live:
                live = re.sub(r"--source-file-count\s+\S+", f"--source-file-count {current_file_count}", live)
            else:
                flag = "--source-file-count" if live_uses_atrium_cli else "-SourceFileCount"
                live = f"{live} {flag} {current_file_count}"
            normalized["windowsLiveProofRunner"] = live
    verify = normalized.get("verify")
    legacy = normalized.get("legacyParityReport")
    if not isinstance(legacy, str) and isinstance(verify, str) and "ops/host_bridge_parity_report.py" in verify:
        normalized["legacyParityReport"] = verify
        legacy = verify
    if not isinstance(normalized.get("automationReport"), str) and isinstance(legacy, str):
        report = _legacy_report_to_automation_report(legacy)
        if report:
            normalized["automationReport"] = report
            normalized.setdefault("report", report)
    if isinstance(verify, str) and "ops/host_bridge_parity_report.py" in verify:
        normalized["verify"] = ".\\atrium.ps1 automation audit" if windows_native() else "./atrium automation audit"
    return normalized


def normalize_parity_payload_for_cli(payload: object, *, current_source: dict[str, object] | None = None) -> object:
    if not isinstance(payload, dict):
        return payload
    source_summary = current_source if current_source is not None else current_source_summary()
    normalized = dict(payload)
    commands = normalize_parity_commands(normalized.get("commands"), current_source=source_summary)
    if commands:
        normalized["commands"] = commands
    gaps = normalized.get("gaps")
    if isinstance(gaps, list):
        normalized["gaps"] = [_host_bridge_gap_text(gap) for gap in gaps if _host_bridge_gap_text(gap)]
    report = normalized.get("report")
    if isinstance(report, dict) and isinstance(report.get("findings"), list):
        normalized_report = dict(report)
        normalized_report["findings"] = [
            _host_bridge_gap_text(finding)
            for finding in report.get("findings", [])
            if _host_bridge_gap_text(finding)
        ]
        normalized["report"] = normalized_report
    connectors = normalized.get("connectors")
    if isinstance(connectors, list):
        normalized_connectors: list[object] = []
        for connector in connectors:
            if not isinstance(connector, dict):
                normalized_connectors.append(connector)
                continue
            normalized_connector = dict(connector)
            proof_gaps = normalized_connector.get("proofGaps")
            if isinstance(proof_gaps, list):
                normalized_connector["proofGaps"] = [
                    _host_bridge_gap_text(gap)
                    for gap in proof_gaps
                    if _host_bridge_gap_text(gap)
                ]
            normalized_connectors.append(normalized_connector)
        normalized["connectors"] = normalized_connectors
    if isinstance(source_summary, dict):
        normalized["cliSource"] = {
            key: source_summary.get(key)
            for key in ("sourceFingerprint", "sourceManifestSha256", "sourceFileCount", "gitHead", "gitDirty")
            if key in source_summary
        }
    normalized["cliNormalized"] = True
    normalized["cliContractPresent"] = isinstance(normalized.get("contract"), dict)
    return normalized


def summarize_parity_payload(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["HostBridge parity=unavailable"]
    lines = [
        (
            "HostBridge parity: "
            f"ok={bool_text(payload.get('ok'))}, "
            f"status={payload.get('status') or 'unknown'}, "
            f"summary={payload.get('summary') or 'unknown'}"
        )
    ]
    gaps = payload.get("gaps")
    if isinstance(gaps, list):
        for gap in gaps[:2]:
            text = _host_bridge_gap_text(gap)
            if text:
                lines.append(f"HostBridge parity gap: {text}")
    contract = payload.get("contract")
    if not isinstance(contract, dict):
        lines.append("OpenClaw Windows contract: missing from backend payload")
    else:
        lines.append(
            "OpenClaw Windows contract: "
            f"status={contract.get('status') or 'unknown'}, "
            f"summary={contract.get('summary') or 'unknown'}"
        )
        for group_key, group_label in (
            ("localRequirements", "local"),
            ("apiSurfaceRequirements", "api"),
            ("reportRequirements", "report"),
            ("featureRequirements", "feature"),
            ("connectorRequirements", "connector"),
            ("windowsProofRequirements", "proof"),
            ("proofRequirements", "proof"),
        ):
            requirements = contract.get(group_key)
            if not isinstance(requirements, list):
                continue
            missing: list[str] = []
            for item in requirements:
                if not isinstance(item, dict):
                    continue
                if "proved" in item:
                    ready = item.get("proved")
                elif "ready" in item:
                    ready = item.get("ready")
                elif "registered" in item:
                    ready = item.get("registered")
                else:
                    ready = item.get("currentReady")
                applies = item.get("currentHostApplies")
                if ready is False and applies is not False:
                    missing.append(openclaw_requirement_gap_label(item))
            if missing:
                lines.append(f"OpenClaw Windows contract {group_label} gap: {', '.join(missing[:4])}")
        seen_contract_gaps: set[str] = set()
        for gap in openclaw_contract_gap_lines(payload)[:20]:
            if gap in seen_contract_gaps:
                continue
            seen_contract_gaps.add(gap)
            lines.append(f"OpenClaw Windows gap: {gap}")
    return lines


def summarize_parity_command_payload(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["commands=unavailable"]
    commands = normalize_parity_commands(payload.get("commands"), current_source=current_source_summary())
    if not commands:
        return ["commands=unavailable"]
    lines: list[str] = []
    for key in PARITY_COMMAND_KEYS:
        value = commands.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(f"{key}={value}")
    return lines or ["commands=unavailable"]


def openclaw_contract_gap_lines(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["parity payload unavailable"]
    gaps: list[str] = []
    for gap in payload.get("gaps") if isinstance(payload.get("gaps"), list) else []:
        text = _host_bridge_gap_text(gap)
        if text:
            gaps.append(text)
    contract = payload.get("contract")
    if not isinstance(contract, dict):
        return gaps or ["OpenClaw Windows contract missing from backend payload"]
    for group_key, group_label in (
        ("localRequirements", "local"),
        ("apiSurfaceRequirements", "api"),
        ("reportRequirements", "report"),
        ("featureRequirements", "feature"),
        ("connectorRequirements", "connector"),
        ("windowsProofRequirements", "proof"),
        ("proofRequirements", "proof"),
    ):
        requirements = contract.get(group_key)
        if not isinstance(requirements, list):
            continue
        for item in requirements:
            if not isinstance(item, dict):
                continue
            if "proved" in item:
                ready = item.get("proved")
            elif "ready" in item:
                ready = item.get("ready")
            elif "registered" in item:
                ready = item.get("registered")
            else:
                ready = item.get("currentReady")
            applies = item.get("currentHostApplies")
            required = item.get("required")
            if ready is False and applies is not False and required is not False:
                gaps.append(f"{group_label}.{openclaw_requirement_gap_label(item)}")
    seen: set[str] = set()
    unique: list[str] = []
    for gap in gaps:
        if gap in seen:
            continue
        seen.add(gap)
        unique.append(gap)
    return unique


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def configured_ollama_url() -> str:
    values = parse_env_file(SYSTEM_ENV)
    return (
        os.environ.get("ATRIUM_OLLAMA_BASE_URL")
        or values.get("ATRIUM_OLLAMA_BASE_URL")
        or ""
    ).strip()


def uses_external_ollama(url: str | None = None) -> bool:
    value = (url if url is not None else configured_ollama_url()).strip().lower()
    if not value:
        return False
    return not any(
        marker in value
        for marker in ("://127.0.0.1", "://localhost", "://ollama:")
    )


def render_env_status(path: Path, keys: Iterable[str]) -> list[str]:
    values = parse_env_file(path)
    return [f"{key}={redact_value(key, values.get(key))}" for key in keys]


def merge_env_text(existing: str, defaults: dict[str, str]) -> tuple[str, list[str], list[str]]:
    lines = existing.splitlines()
    present: dict[str, int] = {}
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        present[key] = idx

    changed: list[str] = []
    preserved: list[str] = []
    for key, value in defaults.items():
        idx = present.get(key)
        if idx is None:
            changed.append(key)
            lines.append(f"{key}={value}")
            continue
        current = lines[idx].split("=", 1)[1].strip()
        if current:
            preserved.append(key)
            continue
        changed.append(key)
        lines[idx] = f"{key}={value}"

    text = "\n".join(lines).rstrip() + "\n"
    return text, changed, preserved


def update_env_file(
    path: Path,
    defaults: dict[str, str],
    *,
    template: Path | None = None,
    dry_run: bool = False,
) -> EnvUpdate:
    created = not path.exists()
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    elif template and template.exists():
        existing = template.read_text(encoding="utf-8")
    new_text, changed, preserved = merge_env_text(existing, defaults)
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, encoding="utf-8")
    return EnvUpdate("update-env", path, created, changed, preserved)


def run_or_plan(args: Sequence[str], *, cwd: Path = ROOT, dry_run: bool = False, timeout: int = 120) -> CommandResult:
    if dry_run:
        print(f"[DRY-RUN] {' '.join(args)}")
        return CommandResult(0, "", "")
    print(f"$ {' '.join(args)}")
    result = run(args, cwd=cwd, timeout=timeout)
    if result.stdout.strip():
        print(redact_text(result.stdout.strip()))
    if result.returncode != 0:
        detail = redact_text((result.stderr or result.stdout).strip())
        raise StepFailure(f"{' '.join(args)} failed", next_step=detail[:1200])
    return result


def install_missing_tools(
    dry_run: bool,
    *,
    auto_homebrew: bool = False,
    wait_docker: bool = False,
    assume_yes: bool = False,
) -> None:
    if windows_native():
        install_missing_windows_tools(dry_run, wait_docker=wait_docker, assume_yes=assume_yes)
        return
    required = ["git", "node", "pnpm", "uv"]
    missing = [tool for tool in required if not command_path(tool)]
    brew = command_path("brew")
    if missing:
        if not brew:
            if auto_homebrew and platform.system() == "Darwin":
                install_homebrew(dry_run=dry_run)
                brew = command_path("brew")
            if not brew:
                next_step = "Install Homebrew from https://brew.sh, then run ./atrium setup again."
                raise StepFailure(
                    f"missing tools: {', '.join(missing)}",
                    next_step=next_step,
                )
        assert brew is not None
        run_interactive([brew, "install", *missing], dry_run=dry_run)
        ensure_common_paths()
    if not command_path("docker"):
        if not brew:
            if auto_homebrew and platform.system() == "Darwin":
                install_homebrew(dry_run=dry_run)
                brew = command_path("brew")
            if not brew:
                next_step = "Install Docker Desktop, open it once, then run ./atrium setup again."
                raise StepFailure(
                    "Docker is missing",
                    next_step=next_step,
                )
        assert brew is not None
        run_interactive([brew, "install", "--cask", "docker"], dry_run=dry_run)
        ensure_common_paths()
        if dry_run:
            return
        if wait_docker and not dry_run:
            if wait_for_docker_ready(assume_yes=assume_yes):
                pass
            else:
                raise StepFailure(
                    "Docker Desktop is installed but not ready",
                    next_step="Open Docker Desktop, finish first-run setup, then run ./atrium setup again.",
                )
        else:
            raise StepFailure(
                "Docker Desktop may need a GUI start after installation",
                next_step="Open Docker Desktop and wait until it says it is running, then run ./atrium setup again.",
            )
    if not chrome_installed() and brew:
        run_interactive([brew, "install", "--cask", "google-chrome"], dry_run=dry_run)


def install_missing_windows_tools(dry_run: bool, *, wait_docker: bool = False, assume_yes: bool = False) -> None:
    winget = command_path("winget")
    installs = {
        "git": ["Git.Git"],
        "node": ["OpenJS.NodeJS.LTS"],
    }
    missing = [tool for tool in ("git", "node") if not command_path(tool)]
    if missing:
        if not winget:
            raise StepFailure(
                f"missing tools: {', '.join(missing)}",
                next_step="Install Git and Node.js 20 for Windows, then run .\\atrium.ps1 setup again.",
            )
        for tool in missing:
            package_id = installs[tool][0]
            run_interactive(
                [winget, "install", "--id", package_id, "--exact", "--accept-source-agreements", "--accept-package-agreements"],
                dry_run=dry_run,
            )
        ensure_common_paths()
    if not command_path("python3"):
        if not winget:
            raise StepFailure(
                "Python 3 is missing",
                next_step="Install Python 3 for Windows, restart PowerShell, then run .\\atrium.ps1 setup again.",
            )
        run_interactive(
            [winget, "install", "--id", "Python.Python.3.12", "--exact", "--accept-source-agreements", "--accept-package-agreements"],
            dry_run=dry_run,
        )
        ensure_common_paths()
        if not dry_run and not command_path("python3"):
            raise StepFailure(
                "Python 3 installation did not expose a runnable Python 3 command",
                next_step="Restart PowerShell and rerun .\\atrium.ps1 setup, or install Python 3 manually.",
            )
    if not command_path("uv"):
        powershell = powershell_command()
        if not powershell:
            raise StepFailure("uv is missing", next_step="Install uv for Windows, then run .\\atrium.ps1 setup again.")
        run_interactive(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "irm https://astral.sh/uv/install.ps1 | iex"],
            dry_run=dry_run,
        )
        ensure_common_paths()
    if not command_path("pnpm"):
        corepack = command_path("corepack")
        if not corepack:
            raise StepFailure("pnpm is missing", next_step="Install Node.js 20 with Corepack, then run .\\atrium.ps1 setup again.")
        run_interactive([corepack, "enable"], dry_run=dry_run)
        run_interactive([corepack, "prepare", "pnpm@10.15.0", "--activate"], dry_run=dry_run)
        ensure_common_paths()
    if not command_path("docker"):
        if not winget:
            raise StepFailure("Docker Desktop is missing", next_step="Install Docker Desktop for Windows, open it once, then run .\\atrium.ps1 setup again.")
        run_interactive(
            [winget, "install", "--id", "Docker.DockerDesktop", "--exact", "--accept-source-agreements", "--accept-package-agreements"],
            dry_run=dry_run,
        )
        ensure_common_paths()
        if not dry_run:
            if wait_docker:
                if not wait_for_docker_ready(assume_yes=assume_yes):
                    raise StepFailure(
                        "Docker Desktop is installed but not ready",
                        next_step="Open Docker Desktop, accept any Windows prompts, wait until it is running, then run .\\atrium.ps1 setup again.",
                    )
            else:
                raise StepFailure(
                    "Docker Desktop may need first-run setup",
                    next_step="Open Docker Desktop, accept any Windows prompts, wait until it is running, then run .\\atrium.ps1 setup again.",
                )
    if not browser_installed() and winget:
        try:
            run_interactive(
                [winget, "install", "--id", "Google.Chrome", "--exact", "--accept-source-agreements", "--accept-package-agreements"],
                dry_run=dry_run,
            )
        except StepFailure as exc:
            print_check(False, "Chromium browser", f"winget install failed; install Chrome/Edge/Brave/Chromium manually, then run .\\atrium.ps1 automation status --commands; {str(exc)[:180]}")
    if not command_path("claude"):
        winget_error = ""
        if winget:
            try:
                run_interactive(
                    [winget, "install", "--id", "Anthropic.ClaudeCode", "--exact", "--accept-source-agreements", "--accept-package-agreements"],
                    dry_run=dry_run,
                )
            except StepFailure as exc:
                winget_error = str(exc)
            ensure_common_paths()
        if not dry_run and command_path("claude"):
            persist_windows_user_paths()
            return
        if not command_path("claude"):
            npm = command_path("npm")
            if npm:
                run_interactive([npm, "install", "-g", "@anthropic-ai/claude-code"], dry_run=dry_run)
                ensure_common_paths()
            else:
                detail = "missing; install Claude Code, then run .\\atrium.ps1 provider login claude-code"
                if winget_error:
                    detail = f"{detail}; winget failed: {winget_error[:180]}"
                print_check(False, "Claude Code", detail)
    persist_windows_user_paths()


def assert_docker_ready() -> None:
    docker = command_path("docker")
    if not docker:
        raise StepFailure(
            "Docker is not installed",
            next_step=f"Install Docker Desktop and run {local_cli_command('bootstrap', '--full')} again.",
        )
    result = run([docker, "info"], timeout=15)
    if result.returncode != 0:
        raise StepFailure(
            "Docker is not running",
            next_step=f"Open Docker Desktop, wait until Docker is ready, then run {local_cli_command('bootstrap', '--full')} again.",
        )


def compose(args: Sequence[str], *, dry_run: bool = False, timeout: int = 300) -> CommandResult:
    cmd = docker_compose_cmd()
    if not cmd:
        raise StepFailure("Docker Compose is unavailable", next_step=f"Install/update Docker Desktop, then run {local_cli_command('doctor')}.")
    return run_or_plan([*cmd, *args], dry_run=dry_run, timeout=timeout)


def screen_sessions() -> str:
    if not command_path("screen"):
        return "screen not installed"
    result = run(["screen", "-ls"], timeout=10)
    output = (result.stdout + result.stderr).strip()
    return output or "no screen sessions"


def screen_session_exists(name: str) -> bool:
    output = screen_sessions()
    return f".{name}" in output or f"\t{name}" in output


def windows_native() -> bool:
    return platform.system() == "Windows"


def local_cli_command(*parts: str) -> str:
    launcher = r".\atrium.ps1" if windows_native() else "./atrium"
    suffix = " ".join(part for part in parts if part)
    return f"{launcher} {suffix}".strip()


def read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
        return int(text)
    except (OSError, ValueError):
        return None


def process_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    powershell = powershell_command() if platform.system() == "Windows" else None
    if powershell:
        result = run(
            [powershell, "-NoProfile", "-Command", f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}"],
            timeout=5,
        )
        return result.returncode == 0
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def pid_status(label: str, pid_path: Path, log_path: Path) -> str:
    pid = read_pid(pid_path)
    if process_running(pid):
        return f"{label} pid={pid} running; log {rel(log_path)}"
    if pid is not None:
        return f"{label} pid={pid} not running; log {rel(log_path)}"
    return f"{label} not started by ATRIUM native launcher; log {rel(log_path)}"


def pid_detail(label: str, pid_path: Path, log_path: Path) -> dict[str, object]:
    pid = read_pid(pid_path)
    running = process_running(pid)
    return {
        "label": label,
        "pid": pid,
        "running": running,
        "pidFile": str(pid_path),
        "pidFileExists": pid_path.exists(),
        "logPath": str(log_path),
        "logExists": log_path.exists(),
        "ownedByAtriumLauncher": pid is not None,
    }


def windows_process_details() -> dict[str, object]:
    return {
        "backend": pid_detail("backend", BACKEND_PID, LOG_DIR / "backend.log"),
        "frontend": pid_detail("frontend", UI_PID, LOG_DIR / "ui.log"),
    }


def windows_process_status() -> str:
    return " | ".join(
        (
            pid_status("backend", BACKEND_PID, LOG_DIR / "backend.log"),
            pid_status("frontend", UI_PID, LOG_DIR / "ui.log"),
        )
    )


def windows_start_process(label: str, command: Sequence[str], cwd: Path, log_path: Path, pid_path: Path) -> None:
    pid = read_pid(pid_path)
    if process_running(pid):
        print_info(label, f"already running pid={pid}; log {rel(log_path)}")
        return
    if pid_path.exists():
        pid_path.unlink()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = 0
    if platform.system() == "Windows":
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            close_fds=True,
        )
    pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
    print_info(label, f"started pid={process.pid}; log {rel(log_path)}")


def windows_stop_process_tree(pid: int) -> CommandResult:
    powershell = powershell_command()
    if not powershell:
        try:
            os.kill(pid, 15)
            return CommandResult(0, "", "")
        except OSError as exc:
            return CommandResult(1, "", str(exc))
    script = (
        f"$root = {pid}; "
        "$all = Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId; "
        "$ids = New-Object 'System.Collections.Generic.List[int]'; "
        "function Add-Children([int]$parent) { "
        "  foreach ($child in ($all | Where-Object { $_.ParentProcessId -eq $parent })) { "
        "    Add-Children ([int]$child.ProcessId); "
        "    $ids.Add([int]$child.ProcessId); "
        "  } "
        "} "
        "Add-Children $root; "
        "$ids.Add([int]$root); "
        "$unique = $ids | Select-Object -Unique; "
        "foreach ($id in $unique) { "
        "  Stop-Process -Id $id -Force -ErrorAction SilentlyContinue; "
        "} "
        "$unique -join ','"
    )
    return run([powershell, "-NoProfile", "-Command", script], timeout=15)


def windows_stop_process(label: str, pid_path: Path) -> bool:
    pid = read_pid(pid_path)
    if not process_running(pid):
        print_info(label, "not running")
        if pid_path.exists():
            pid_path.unlink()
        return True
    assert pid is not None
    result = windows_stop_process_tree(pid)
    stopped = result.stdout.strip() or str(pid)
    deadline = time.time() + 5
    while time.time() < deadline and process_running(pid):
        time.sleep(0.25)
    still_running = process_running(pid)
    if pid_path.exists() and not still_running:
        pid_path.unlink()
    detail = f"stopped pid tree={stopped}"
    if result.returncode != 0 and result.stderr.strip():
        detail = f"{detail}; warning={result.stderr.strip()[:240]}"
    if still_running:
        log_name = "ui.log" if label == "frontend" else f"{label}.log"
        print_check(False, label, f"stop requested but pid={pid} is still running; log {rel(LOG_DIR / log_name)}")
        return False
    print_info(label, detail)
    return True


def native_backend_command() -> list[str]:
    uv = command_path("uv")
    if not uv:
        raise StepFailure("uv is missing", next_step=f"Install uv, then run {local_cli_command('setup')} again.")
    return [uv, "run", "--extra", "live", "--extra", "postgres", "--extra", "graph", "python", "-m", "app"]


def native_ui_command() -> list[str]:
    pnpm = command_path("pnpm")
    if not pnpm:
        raise StepFailure("pnpm is missing", next_step=f"Install Node.js 20 and pnpm, then run {local_cli_command('setup')} again.")
    return [pnpm, "dev", "--host", "127.0.0.1", "--port", "5173"]


def provider_status_from_env() -> list[str]:
    values = parse_env_file(SYSTEM_ENV)
    statuses: list[str] = []
    for key, label in (
        ("ATRIUM_OPENAI_API_KEY", "OpenAI Platform API key"),
        ("ATRIUM_ANTHROPIC_AUTH_TOKEN", "Anthropic API key"),
        ("ATRIUM_IMAGE_GENERATION_API_KEY", "Image generation API key"),
    ):
        statuses.append(f"{label}: {redact_value(key, values.get(key))}")
    claude = command_path("claude")
    if claude:
        result = run([claude, "auth", "status", "--json"], timeout=8)
        if result.returncode == 0:
            statuses.append("Claude Code account: command available, auth status responded")
        else:
            statuses.append("Claude Code account: command available, auth status not ready")
    else:
        statuses.append("Claude Code account: claude command missing")
    return statuses


def doctor_tool_names() -> tuple[str, ...]:
    if windows_native():
        return ("git", "node", "pnpm", "uv", "python3", "docker", "winget", "powershell.exe", "claude")
    return ("git", "brew", "node", "pnpm", "uv", "python3", "docker")


def report_tool_names() -> tuple[str, ...]:
    if windows_native():
        return ("git", "node", "pnpm", "uv", "python3", "docker", "winget", "powershell.exe", "claude")
    return ("git", "brew", "node", "pnpm", "uv", "python3", "docker", "screen")


def command_provider(args: argparse.Namespace) -> int:
    ensure_repo_root()
    action = args.provider_action
    target = normalize_provider_auth_target(getattr(args, "provider", "chatgpt"))

    if action == "status":
        probe = bool(getattr(args, "probe", False))
        suffix = "?probe=true" if probe else ""
        ok, _raw, payload = backend_json(f"/api/provider-auth/status{suffix}", timeout=15.0 if probe else 5.0)
        if not ok:
            raise StepFailure(
                "ATRIUM backend is not reachable for provider status",
                next_step="Start ATRIUM first, then rerun provider status.\nWindows: .\\atrium.ps1 start\nmacOS: ./atrium start",
            )
        if getattr(args, "json", False):
            print(json.dumps(redact_json_value(payload), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print_header("Provider Auth")
        for line in summarize_provider_auth_payload(payload):
            print_info(line)
        return 0

    if action == "login":
        payload = {"timeoutS": args.timeout_seconds} if target == "chatgpt" else None
        ok, raw, result = backend_json_request(
            provider_auth_start_path(target),
            method="POST",
            payload=payload,
            timeout=20.0,
        )
        if not ok:
            raise StepFailure(
                f"Could not start {target} provider login through ATRIUM backend",
                next_step=(
                    f"Backend response: {redact_text(raw)[:800]}\n"
                    "Start ATRIUM and retry from the native terminal.\n"
                    "Windows: .\\atrium.ps1 start\nmacOS: ./atrium start"
                ),
            )
        print_header("Provider Login")
        for line in provider_login_summary(target, result):
            print_info(line)

        if target == "chatgpt" and isinstance(result, dict):
            authorization_url = result.get("authorizationUrl")
            if isinstance(authorization_url, str) and authorization_url.strip() and not args.no_open:
                open_url(authorization_url.strip())
                print_info("ChatGPT browser", "opened OAuth URL")
            elif isinstance(authorization_url, str) and authorization_url.strip():
                print_info("ChatGPT login URL", authorization_url.strip())
            if args.wait_seconds > 0:
                ready, status_payload = wait_provider_ready(target, args.wait_seconds)
                print_header("Provider Auth")
                for line in summarize_provider_auth_payload(status_payload):
                    print_info(line)
                if not ready:
                    print_check(False, "ChatGPT account login", "not ready yet; finish the browser OAuth flow and rerun provider status")
                    return 1
            return 0

        if target == "claude-code" and isinstance(result, dict):
            mode = str(result.get("mode") or "")
            already_ready = mode == "already_ready"
            if not already_ready and mode == "manual" and not args.no_interactive:
                claude = command_path("claude")
                if claude:
                    print_header("Claude Code Interactive Login")
                    run_interactive([claude, "auth", "login", "--claudeai"], cwd=ROOT)
                    ready, status_payload = wait_provider_ready(target, args.wait_seconds)
                    print_header("Provider Auth")
                    for line in summarize_provider_auth_payload(status_payload):
                        print_info(line)
                    return 0 if ready else 1
                print_check(False, "claude command", "missing; install Claude Code and rerun provider login claude-code")
                return 1
            if args.wait_seconds > 0 and not already_ready:
                ready, status_payload = wait_provider_ready(target, args.wait_seconds)
                print_header("Provider Auth")
                for line in summarize_provider_auth_payload(status_payload):
                    print_info(line)
                return 0 if ready else 1
            return 0

    if action == "disconnect":
        ok, raw, result = backend_json_request(provider_auth_disconnect_path(target), method="POST", timeout=30.0)
        if not ok:
            raise StepFailure(
                f"Could not disconnect {target} provider through ATRIUM backend",
                next_step=f"Backend response: {redact_text(raw)[:800]}",
            )
        print_header("Provider Disconnect")
        for line in provider_disconnect_summary(target, result):
            print_info(line)
        return 0

    raise StepFailure(f"Unknown provider action: {action}")


def collect_tools_status_payload() -> tuple[dict[str, object], dict[str, bool], dict[str, str]]:
    ok_runtime, raw_runtime, runtime_payload = backend_json("/api/runtime", timeout=15.0)
    ok_catalog, raw_catalog, catalog_payload = backend_json("/api/tools/catalog", timeout=8.0)
    ok_connectors, raw_connectors, connectors_payload = backend_json("/api/connectors", timeout=8.0)
    payload = {
        "runtime": runtime_payload if ok_runtime else {"ok": False, "error": raw_runtime[:400]},
        "toolCatalog": catalog_payload if ok_catalog else {"ok": False, "error": raw_catalog[:400]},
        "connectors": connectors_payload if ok_connectors else {"ok": False, "error": raw_connectors[:400]},
    }
    ok = {"runtime": ok_runtime, "toolCatalog": ok_catalog, "connectors": ok_connectors}
    raw = {"runtime": raw_runtime, "toolCatalog": raw_catalog, "connectors": raw_connectors}
    return payload, ok, raw


def collect_automation_status_payload() -> tuple[bool, dict[str, object]]:
    ok, raw, payload = backend_json("/api/host-bridge/parity", timeout=8.0)
    if not ok or not isinstance(payload, dict):
        return False, {"ok": False, "backendReachable": False, "error": raw[:400]}
    source_summary = current_source_summary()
    normalized = normalize_parity_payload_for_cli(payload, current_source=source_summary)
    normalized["backendReachable"] = True
    normalized["localArtifacts"] = collect_local_proof_artifacts(source_summary)
    ok_permission, raw_permission, permission_payload = backend_json("/api/permissions/mode", timeout=5.0)
    normalized["permissionMode"] = permission_payload if ok_permission else {"ok": False, "error": raw_permission[:400]}
    return True, normalized


def command_tools(args: argparse.Namespace) -> int:
    ensure_repo_root()
    action = args.tools_action
    tools_payload, tool_ok, tool_raw = collect_tools_status_payload()
    runtime_payload = tools_payload.get("runtime") if tool_ok["runtime"] else None
    catalog_payload = tools_payload.get("toolCatalog") if tool_ok["toolCatalog"] else None
    connectors_payload = tools_payload.get("connectors") if tool_ok["connectors"] else None
    raw_runtime = tool_raw["runtime"]
    raw_catalog = tool_raw["toolCatalog"]
    ok_runtime = tool_ok["runtime"]
    ok_catalog = tool_ok["toolCatalog"]

    if action == "status":
        if not ok_runtime and not ok_catalog:
            raise StepFailure(
                "ATRIUM backend is not reachable for AI tools status",
                next_step=(
                    f"runtime: {redact_text(raw_runtime)[:400]}\n"
                    f"catalog: {redact_text(raw_catalog)[:400]}\n"
                    "Start ATRIUM first, then rerun tools status.\nWindows: .\\atrium.ps1 start\nmacOS: ./atrium start"
                ),
            )
        if getattr(args, "json", False):
            print(json.dumps(redact_json_value(tools_payload), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print_header("AI Tools")
        for line in summarize_runtime_ai_tools(runtime_payload if ok_runtime else None):
            print_info(line)
        for line in summarize_tool_catalog_payload(catalog_payload if ok_catalog else None, limit=args.limit):
            print_info(line)
        if tool_ok["connectors"]:
            print_header("Connectors")
            for line in summarize_connectors_payload(connectors_payload):
                print_info(line)
        return 0

    if action == "catalog":
        if not ok_catalog:
            raise StepFailure(
                "ATRIUM backend is not reachable for AI tool catalog",
                next_step=redact_text(raw_catalog)[:800] or "Start ATRIUM first, then rerun tools catalog.",
            )
        if args.json:
            print(json.dumps(redact_json_value(catalog_payload), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print_header("AI Tool Catalog")
        for line in summarize_tool_catalog_payload(catalog_payload, limit=args.limit):
            print_info(line)
        if isinstance(catalog_payload, list):
            for item in catalog_payload[: args.limit]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or item.get("tool") or item.get("id") or "unknown")
                executor = str(item.get("executor") or "unknown")
                risk = str(item.get("riskClass") or item.get("risk") or "unknown")
                mutates = bool_text(item.get("mutates"))
                print_info(name, f"executor={executor}; risk={risk}; mutates={mutates}")
        return 0

    raise StepFailure(f"Unknown tools action: {action}")


def command_automation(args: argparse.Namespace) -> int:
    ensure_repo_root()
    action = args.automation_action

    if action == "status":
        backend_ok, payload = collect_automation_status_payload()
        if not backend_ok:
            raise StepFailure(
                "ATRIUM backend is not reachable for automation status",
                next_step="Start ATRIUM first, then rerun automation status.\nWindows: .\\atrium.ps1 start\nmacOS: ./atrium start",
            )
        if getattr(args, "json", False):
            print(json.dumps(redact_json_value(payload), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print_header("Automation Permission")
        for line in summarize_parity_payload(payload):
            print_info(line)
        print_header("Local Proof Artifacts")
        for line in summarize_local_proof_artifacts(payload):
            print_info(line)
        print_header("Owner Permissions")
        for line in summarize_full_autonomy(payload.get("permissionMode")):
            print_info(line)
        if getattr(args, "commands", False) and isinstance(payload, dict):
            print_header("Parity Commands")
            for line in summarize_parity_command_payload(payload):
                if "=" in line:
                    key, value = line.split("=", 1)
                    print_info(key, value)
                else:
                    print_info(line)
        return 0

    if action == "audit":
        ok, raw, payload = backend_json("/api/host-bridge/parity", timeout=8.0)
        if not ok or not isinstance(payload, dict):
            raise StepFailure(
                "ATRIUM backend is not reachable for OpenClaw Windows audit",
                next_step=raw or "Start ATRIUM first, then rerun automation audit.",
            )
        payload = normalize_parity_payload_for_cli(payload)
        if args.json:
            print(json.dumps(redact_json_value(payload), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print_header("OpenClaw Windows Audit")
            for line in summarize_parity_payload(payload):
                print_info(line)
        contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else {}
        passed = payload.get("ok") is True and contract.get("status") == "cross_os_verified"
        if passed:
            if not args.json:
                print_check(True, "OpenClaw Windows contract", "cross_os_verified")
            return 0
        gaps = openclaw_contract_gap_lines(payload)
        if not args.json:
            print_header("OpenClaw Windows Gaps")
            for gap in gaps[:20]:
                print_check(False, gap)
            commands = payload.get("commands")
            commands = normalize_parity_commands(commands, current_source=current_source_summary())
            if commands:
                live = commands.get("windowsLiveProofRunner")
                report = commands.get("automationReport") or commands.get("report")
                verify = commands.get("verify")
                if isinstance(live, str) and live.strip():
                    print_info("windowsLiveProofRunner", live)
                if isinstance(report, str) and report.strip():
                    print_info("automationReport", report)
                if isinstance(verify, str) and verify.strip():
                    print_info("verify", verify)
        return 2

    if action == "source":
        command = uv_python_command("ops/host_bridge_source_summary.py")
        append_optional_value(command, "--expect-source-fingerprint", args.expect_source_fingerprint)
        append_optional_value(command, "--expect-source-manifest-sha256", args.expect_source_manifest_sha256)
        append_optional_value(command, "--expect-source-file-count", args.expect_source_file_count)
        run_interactive(command, cwd=ROOT)
        return 0

    if action == "windows-probe":
        command = uv_python_command("ops/windows_host_bridge_probe.py")
        append_optional_flag(command, args.simulate, "--simulate")
        append_optional_flag(command, args.full, "--full")
        append_optional_flag(command, args.screenshot, "--screenshot")
        append_optional_flag(command, args.notification, "--notification")
        append_optional_flag(command, args.interactive, "--interactive")
        append_optional_value(command, "--browser-url", args.browser_url)
        append_optional_value(command, "--browser-profile", args.browser_profile)
        append_optional_value(command, "--parity-run-id", args.parity_run_id)
        append_optional_value(command, "--expect-source-fingerprint", args.expect_source_fingerprint)
        append_optional_value(command, "--expect-source-manifest-sha256", args.expect_source_manifest_sha256)
        append_optional_value(command, "--expect-source-file-count", args.expect_source_file_count)
        append_optional_value(command, "--output", args.output)
        run_interactive(command, cwd=ROOT)
        return 0

    if action == "windows-live-proof":
        powershell = powershell_command()
        if not powershell:
            raise StepFailure(
                "PowerShell is required for the Windows HostBridge live proof runner",
                next_step=(
                    "Run this command from a signed-in Windows PowerShell session:\n"
                    ".\\atrium.ps1 automation windows-live-proof --parity-run-id <run-id> "
                    "--source-fingerprint <fingerprint> --source-manifest-sha256 <manifest> "
                    "--source-file-count <count>"
                ),
            )
        script = ROOT / "ops" / "windows_host_bridge_live_proof.ps1"
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-ParityRunId",
            args.parity_run_id,
            "-SourceFingerprint",
            args.source_fingerprint,
            "-SourceManifestSha256",
            args.source_manifest_sha256,
            "-SourceFileCount",
            str(args.source_file_count),
        ]
        append_optional_value(command, "-Output", args.output)
        if args.max_artifact_age_hours is not None:
            append_optional_value(command, "-MaxArtifactAgeHours", args.max_artifact_age_hours)
        run_interactive(command, cwd=ROOT)
        return 0

    if action == "handoff":
        source = current_source_summary()
        if not isinstance(source, dict):
            raise StepFailure(
                "Could not compute the current HostBridge source fingerprint",
                next_step=f"Run {local_cli_command('automation', 'source')} and fix any source summary error before creating a Windows proof handoff.",
            )
        source_fingerprint = source.get("sourceFingerprint")
        source_manifest_sha256 = source.get("sourceManifestSha256")
        source_file_count = source.get("sourceFileCount")
        if not isinstance(source_fingerprint, str) or not isinstance(source_manifest_sha256, str) or not isinstance(source_file_count, int):
            raise StepFailure("Current HostBridge source summary is missing fingerprint, manifest, or file count")
        macos = _artifact_summary(
            args.macos,
            label="macos",
            expect_source_fingerprint=source_fingerprint,
            expect_source_manifest_sha256=source_manifest_sha256,
            expect_source_file_count=source_file_count,
            max_artifact_age_hours=args.max_artifact_age_hours,
        )
        payload = build_windows_handoff_payload(
            macos_artifact=args.macos,
            macos_summary=macos,
            source_summary=source,
            windows_output=args.windows_output,
            windows_local_copy=args.windows_local_copy,
        )
        output_path = Path(args.output).expanduser()
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print_check(True, "Windows proof handoff", f"wrote {output_path}")
            print_info("parityRunId", str(macos.get("parityRunId") or ""))
            print_info("sourceFingerprint", source_fingerprint)
            print_info("sourceManifestSha256", source_manifest_sha256)
            print_info("sourceFileCount", str(source_file_count))
            commands = payload["windowsProof"]["commands"] if isinstance(payload.get("windowsProof"), dict) else {}
            if isinstance(commands, dict):
                print_info("windows.sourceValidate", str(commands.get("sourceValidate") or ""))
                print_info("windows.liveProof", str(commands.get("liveProof") or ""))
                print_info("windows.artifactValidate", str(commands.get("artifactValidate") or ""))
            final = payload["finalVerification"]["commands"] if isinstance(payload.get("finalVerification"), dict) else {}
            if isinstance(final, dict):
                print_info("report", str(final.get("report") or ""))
                print_info("audit", str(final.get("audit") or ""))
        return 0

    if action == "artifact":
        command = uv_python_command("ops/host_bridge_artifact_summary.py")
        append_optional_value(command, "--label", args.label)
        append_optional_value(command, "--expect-parity-run-id", args.expect_parity_run_id)
        append_optional_value(command, "--expect-source-fingerprint", args.expect_source_fingerprint)
        append_optional_value(command, "--expect-source-manifest-sha256", args.expect_source_manifest_sha256)
        append_optional_value(command, "--expect-source-file-count", args.expect_source_file_count)
        command.append(args.artifact)
        run_interactive(command, cwd=ROOT)
        return 0

    if action == "report":
        output_path = Path(args.output).expanduser()
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        if args.skip_current_source_check and output_path.resolve() == HOST_BRIDGE_PARITY_REPORT.resolve():
            raise StepFailure(
                "--skip-current-source-check is only allowed for offline historical audits written to a custom --output path",
                next_step=(
                    "Rerun without --skip-current-source-check to install the current backend parity report, "
                    "or pass --output <custom-json> for a historical audit artifact."
                ),
            )
        command = uv_python_command("ops/host_bridge_parity_report.py")
        append_optional_value(command, "--macos", args.macos)
        append_optional_value(command, "--windows", args.windows)
        append_optional_value(command, "--output", args.output)
        append_optional_value(command, "--max-artifact-age-hours", args.max_artifact_age_hours)
        append_optional_value(command, "--windows-source-path", args.windows_source_path)
        append_optional_flag(command, args.skip_current_source_check, "--skip-current-source-check")
        run_interactive(command, cwd=ROOT)
        destination = "backend default report path" if output_path == HOST_BRIDGE_PARITY_REPORT else "custom report path"
        print_check(True, "HostBridge parity report", f"verified and wrote {destination}: {rel(output_path)}")
        return 0

    raise StepFailure(f"Unknown automation action: {action}")


def command_doctor(_args: argparse.Namespace) -> int:
    ensure_repo_root()
    print_header("Machine")
    print_info("repo", str(ROOT))
    print_info("OS", platform.platform())
    print_info("CPU", platform.machine() or "unknown")
    print_info("RAM", memory_gb())
    risky, detail = is_i_cloud_risky(ROOT)
    print_check(not risky, "install path", detail)

    print_header("Git")
    ok, detail = remote_ok()
    print_check(ok, "remote", detail.replace("\n", " | "))
    print_info("status", git_status_summary().replace("\n", " | "))

    print_header("Tools")
    for tool in doctor_tool_names():
        found = command_path(tool)
        print_check(bool(found), tool, found or "missing")
    compose_cmd = docker_compose_cmd()
    print_check(bool(compose_cmd), "docker compose", " ".join(compose_cmd) if compose_cmd else "missing")
    print_check(browser_installed(), "Chromium browser", "Chrome/Edge/Brave/Chromium installed" if browser_installed() else "missing")

    print_header("Ports")
    for port, label in PORTS.items():
        open_now = port_open(port)
        detail = port_owner(port) if open_now else "free"
        print_info(f"{label} :{port}", detail)

    print_header("Env")
    print_check(SYSTEM_ENV.exists(), rel(SYSTEM_ENV), "exists" if SYSTEM_ENV.exists() else "missing")
    for line in render_env_status(SYSTEM_ENV, FULL_STACK_DEFAULTS.keys()):
        print_info(line)
    print_check(UI_ENV.exists(), rel(UI_ENV), "exists" if UI_ENV.exists() else "missing")
    for line in render_env_status(UI_ENV, UI_DEFAULTS.keys()):
        print_info(line)

    print_header("Runtime")
    runtime_payload: object | None = None
    provider_auth_payload: object | None = None
    permission_payload: object | None = None
    for path in ("/health", "/api/runtime", "/api/provider-auth/status", "/api/permissions/mode"):
        ok, raw, payload = backend_json(path, timeout=15.0 if path == "/api/runtime" else 3.0)
        if path == "/api/runtime" and ok:
            runtime_payload = payload
        if path == "/api/provider-auth/status" and ok:
            provider_auth_payload = payload
        if path == "/api/permissions/mode" and ok:
            permission_payload = payload
        if ok and isinstance(payload, dict):
            summary = f"ok={payload.get('ok', 'unknown')}"
            if path == "/api/runtime":
                summary += f", running={payload.get('running', 'unknown')}"
            print_check(True, path, summary)
        elif ok:
            print_check(True, path, raw[:120])
        else:
            print_check(False, path, raw)

    print_header("Provider")
    for line in provider_status_from_env():
        print_info(line)
    for line in summarize_provider_auth_payload(provider_auth_payload):
        print_info(line)

    print_header("Owner Permissions")
    for line in summarize_full_autonomy(permission_payload):
        print_info(line)

    if runtime_payload is not None:
        print_header("AI Tools")
        for line in summarize_runtime_ai_tools(runtime_payload):
            print_info(line)

    ok, _raw, connectors_payload = backend_json("/api/connectors", timeout=8.0)
    if ok:
        print_header("Connectors")
        for line in summarize_connectors_payload(connectors_payload):
            print_info(line)

    ok, _raw, parity_payload = backend_json("/api/host-bridge/parity", timeout=8.0)
    if ok:
        parity_payload = normalize_parity_payload_for_cli(parity_payload)
        print_header("Automation Permission")
        for line in summarize_parity_payload(parity_payload):
            print_info(line)
    return 0


def command_bootstrap(args: argparse.Namespace) -> int:
    ensure_repo_root()
    if not args.full:
        raise StepFailure("bootstrap currently supports only --full", next_step=local_cli_command("bootstrap", "--full"))

    risky, detail = is_i_cloud_risky(ROOT)
    if risky:
        raise StepFailure("install path is not safe for local runtime files", next_step=detail)
    ok, detail = remote_ok()
    if not ok:
        raise StepFailure(
            "repo remote does not match ATRIUM-TH",
            next_step=f"Expected a remote containing {EXPECTED_REMOTE_SLUG}. Current remote: {detail}",
        )

    print_header("Prepare Env")
    system_update = update_env_file(SYSTEM_ENV, FULL_STACK_DEFAULTS, template=SYSTEM_ENV_EXAMPLE, dry_run=args.dry_run)
    ui_update = update_env_file(UI_ENV, UI_DEFAULTS, dry_run=args.dry_run)
    for update in (system_update, ui_update):
        created = "created" if update.created else "preserved"
        print_info(rel(update.path), f"{created}; filled {len(update.changed_keys)} keys; preserved {len(update.preserved_keys)} keys")
        if update.changed_keys:
            print_info("filled", ", ".join(update.changed_keys))

    print_header("Install Tools")
    install_missing_tools(
        args.dry_run,
        auto_homebrew=bool(getattr(args, "auto_homebrew", False)),
        wait_docker=bool(getattr(args, "wait_docker", False)),
        assume_yes=bool(getattr(args, "yes", False)),
    )

    print_header("Install Dependencies")
    uv = command_path("uv") or ("uv" if args.dry_run else None)
    pnpm = command_path("pnpm") or ("pnpm" if args.dry_run else None)
    if not uv:
        raise StepFailure("uv is missing", next_step=f"Install uv, then run {local_cli_command('setup')} again.")
    if not pnpm:
        raise StepFailure("pnpm is missing", next_step=f"Install Node.js 20 and pnpm, then run {local_cli_command('setup')} again.")
    run_or_plan([uv, "sync", "--extra", "live", "--extra", "postgres", "--extra", "graph"], cwd=SYSTEM_DIR, dry_run=args.dry_run, timeout=1200)
    run_or_plan([pnpm, "install"], cwd=UI_DIR, dry_run=args.dry_run, timeout=1200)

    print_header("Docker Stack")
    if not args.dry_run:
        if bool(getattr(args, "wait_docker", False)):
            if not wait_for_docker_ready(assume_yes=bool(getattr(args, "yes", False))):
                raise StepFailure(
                    "Docker is not running",
                    next_step=f"Open Docker Desktop, wait until Docker is ready, then run {local_cli_command('setup')} again.",
                )
        else:
            assert_docker_ready()
    else:
        print("[DRY-RUN] docker info")
    ollama_url = configured_ollama_url()
    if uses_external_ollama(ollama_url):
        print_info("Ollama", f"using external service at {ollama_url}; skipping container and model pull")
        compose(["up", "-d", "postgres"], dry_run=args.dry_run, timeout=600)
    else:
        compose(["up", "-d", "postgres", "ollama"], dry_run=args.dry_run, timeout=600)
        try:
            compose(["exec", "ollama", "ollama", "pull", "bge-m3"], dry_run=args.dry_run, timeout=1200)
        except StepFailure as exc:
            print()
            print("[WARN] Could not pull bge-m3 in the Ollama container.")
            reason = str(exc).strip()
            if reason:
                print(f"       Reason: {reason[:200]}")
            print("       Continuing; embeddings will not work until the model is available.")
            print("       Resolve with either of:")
            print("         1) docker compose exec ollama ollama pull bge-m3")
            print("         2) set ATRIUM_OLLAMA_BASE_URL to an Ollama service that already has bge-m3")

    print_header("Database")
    run_or_plan([uv, "run", "--extra", "postgres", "alembic", "-c", "alembic.ini", "upgrade", "head"], cwd=SYSTEM_DIR, dry_run=args.dry_run, timeout=600)
    print(f"\nBootstrap complete. Run {local_cli_command('start')}, then {local_cli_command('status')}.")
    return 0


def assert_port_available_for_start(port: int, label: str) -> None:
    if not port_open(port):
        return
    owner = port_owner(port)
    raise StepFailure(
        f"{label} port {port} is already in use",
        next_step=f"Inspect this listener before starting ATRIUM:\n{owner}",
    )


def atrium_services_running() -> bool:
    health, _, _ = http_get_json(f"{BACKEND_URL}/health", timeout=2.0)
    return health and port_open(5173)


def start_docker_stack() -> None:
    print_header("Start Docker")
    docker = command_path("docker")
    compose_cmd = docker_compose_cmd() if docker else None
    if not docker or not compose_cmd:
        if windows_native():
            raise StepFailure("Docker Compose is unavailable", next_step=f"Install/update Docker Desktop, then run {local_cli_command('doctor')}.")
        print_check(False, "Docker Compose", "missing")
        return
    if run([docker, "info"], timeout=10).returncode != 0:
        if windows_native():
            if not wait_for_docker_ready(seconds=120, assume_yes=True, prompt=False):
                raise StepFailure(
                    "Docker is not running",
                    next_step=f"Open Docker Desktop, accept any Windows prompts, wait until it is running, then run {local_cli_command('start')} again.",
                )
        else:
            print_check(False, "Docker", "not running; open Docker Desktop if full stack services are missing")
            return
    ollama_url = configured_ollama_url()
    if uses_external_ollama(ollama_url):
        print_info("Ollama", f"using external service at {ollama_url}; skipping container")
        compose(["up", "-d", "postgres"], timeout=300)
    else:
        compose(["up", "-d", "postgres", "ollama"], timeout=300)


def command_setup(args: argparse.Namespace) -> int:
    ensure_repo_root()
    print_header("ATRIUM Guided Setup")
    print_info("goal", f"install, start, verify, then open {FRONTEND_URL}")
    print_info("repo", str(ROOT))
    if args.dry_run:
        print_info("mode", "dry-run; no files, services, or installs will be changed")
    elif not args.yes:
        prompt_enter(
            "This setup can install Homebrew packages, Docker Desktop, Python/Node dependencies, and start local services.",
            assume_yes=args.yes,
        )

    print_header("Preflight")
    command_doctor(args)

    print_header("Bootstrap")
    bootstrap_args = argparse.Namespace(
        full=True,
        dry_run=args.dry_run,
        auto_homebrew=True,
        wait_docker=True,
        yes=args.yes,
    )
    command_bootstrap(bootstrap_args)

    if args.no_start:
        print("\nSetup prepared ATRIUM without starting services.")
        return 0
    if args.dry_run:
        print(f"\n[DRY-RUN] {local_cli_command('start')}")
        print(f"[DRY-RUN] {local_cli_command('status')}")
        return 0

    print_header("Start")
    start_args = argparse.Namespace(force=args.force, wait_seconds=args.wait_seconds)
    command_start(start_args)

    print_header("Verify")
    command_status(args)

    if not args.no_open:
        print_header("Open")
        open_frontend_url()
        print_info("frontend", FRONTEND_URL)
    return 0


def command_start(args: argparse.Namespace) -> int:
    ensure_repo_root()
    if windows_native():
        return command_start_windows(args)
    if not command_path("screen"):
        raise StepFailure("screen is unavailable", next_step="Install screen or start backend/frontend in separate terminals.")
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if not args.force and atrium_services_running():
        print_info("ATRIUM", "backend and frontend already look reachable")
        print(f"\nFrontend: {FRONTEND_URL}")
        print(f"Backend:  {BACKEND_URL}")
        return 0

    if not args.force:
        assert_port_available_for_start(8787, "backend")
        assert_port_available_for_start(5173, "frontend")

    start_docker_stack()

    print_header("Start Services")
    backend_log = LOG_DIR / "backend.log"
    ui_log = LOG_DIR / "ui.log"
    if screen_session_exists(BACKEND_SCREEN):
        print_info("backend", f"screen session already exists: {BACKEND_SCREEN}")
    else:
        backend_cmd = (
            f"cd {shell_quote(str(SYSTEM_DIR))} && "
            f"exec uv run --extra live --extra postgres --extra graph python -m app >>{shell_quote(str(backend_log))} 2>&1"
        )
        run_or_plan(["screen", "-dmS", BACKEND_SCREEN, "zsh", "-lc", backend_cmd], timeout=20)
        print_info("backend", f"started in screen {BACKEND_SCREEN}; log {rel(backend_log)}")

    if screen_session_exists(UI_SCREEN):
        print_info("frontend", f"screen session already exists: {UI_SCREEN}")
    else:
        ui_cmd = (
            f"cd {shell_quote(str(UI_DIR))} && "
            f"exec pnpm dev --host 127.0.0.1 --port 5173 >>{shell_quote(str(ui_log))} 2>&1"
        )
        run_or_plan(["screen", "-dmS", UI_SCREEN, "zsh", "-lc", ui_cmd], timeout=20)
        print_info("frontend", f"started in screen {UI_SCREEN}; log {rel(ui_log)}")

    wait_for_urls(args.wait_seconds)
    print(f"\nFrontend: {FRONTEND_URL}")
    print(f"Backend:  {BACKEND_URL}")
    print(f"Run {local_cli_command('status')} for readiness details.")
    return 0


def command_start_windows(args: argparse.Namespace) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if not args.force and atrium_services_running():
        print_info("ATRIUM", "backend and frontend already look reachable")
        print_info("windows native", windows_process_status())
        if not process_running(read_pid(BACKEND_PID)) or not process_running(read_pid(UI_PID)):
            print_info(
                "ownership",
                ".\\atrium.ps1 stop controls ATRIUM-owned PID-file processes; use status to inspect external listeners",
            )
        print(f"\nFrontend: {FRONTEND_URL}")
        print(f"Backend:  {BACKEND_URL}")
        return 0

    if not args.force:
        assert_port_available_for_start(8787, "backend")
        assert_port_available_for_start(5173, "frontend")

    start_docker_stack()

    print_header("Start Windows Native Services")
    windows_start_process("backend", native_backend_command(), SYSTEM_DIR, LOG_DIR / "backend.log", BACKEND_PID)
    windows_start_process("frontend", native_ui_command(), UI_DIR, LOG_DIR / "ui.log", UI_PID)

    wait_for_urls(args.wait_seconds)
    print(f"\nFrontend: {FRONTEND_URL}")
    print(f"Backend:  {BACKEND_URL}")
    print("Run .\\atrium.ps1 status for readiness details.")
    return 0


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def wait_for_urls(seconds: int) -> None:
    if seconds <= 0:
        return
    deadline = time.time() + seconds
    while time.time() < deadline:
        health, _, _ = http_get_json(f"{BACKEND_URL}/health", timeout=1.5)
        frontend = port_open(5173)
        if health and frontend:
            return
        time.sleep(1)


def summarize_runtime_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return "non-json"
    parts = [f"ok={payload.get('ok', 'unknown')}"]
    if "running" in payload:
        parts.append(f"running={payload.get('running')}")
    agent = payload.get("agentRuntime")
    if isinstance(agent, dict):
        parts.append(f"agentRuntime.ok={agent.get('ok', 'unknown')}")
        parts.append(f"backend={agent.get('configuredBackend', agent.get('backend', 'unknown'))}")
    memory = payload.get("memory")
    if isinstance(memory, dict):
        parts.append(f"memory.ok={memory.get('ok', 'unknown')}")
    graph = payload.get("graph")
    if isinstance(graph, dict):
        parts.append(f"graph={graph.get('backend', graph.get('status', 'unknown'))}")
    return ", ".join(parts)


def collect_status_payload() -> dict[str, object]:
    process_payload = {
        "mode": "windows-native" if windows_native() else "screen-or-macos",
        "summary": windows_process_status() if windows_native() else screen_sessions().replace("\n", " | "),
        "details": windows_process_details() if windows_native() else None,
    }
    ports_payload = {
        label: {
            "port": port,
            "open": port_open(port),
            "owner": port_owner(port) if port_open(port) else "free",
        }
        for port, label in PORTS.items()
    }
    docker = command_path("docker")
    compose_cmd = docker_compose_cmd() if docker else None
    docker_payload: dict[str, object] = {
        "cli": docker or None,
        "compose": compose_cmd,
        "running": bool(docker and run([docker, "info"], timeout=10).returncode == 0),
    }
    http_payload: dict[str, object] = {}
    runtime_payload: object | None = None
    provider_auth_payload: object | None = None
    permission_payload: object | None = None
    for path in ("/health", "/api/runtime", "/api/provider-auth/status", "/api/permissions/mode"):
        ok, raw, payload = backend_json(path, timeout=15.0 if path == "/api/runtime" else 3.0)
        if path == "/api/runtime" and ok:
            runtime_payload = payload
        if path == "/api/provider-auth/status" and ok:
            provider_auth_payload = payload
        if path == "/api/permissions/mode" and ok:
            permission_payload = payload
        http_payload[path] = {
            "ok": ok,
            "summary": summarize_runtime_payload(payload) if ok and path == "/api/runtime" else raw[:180],
            "payload": payload if ok else None,
        }
    ok_connectors, raw_connectors, connectors_payload = backend_json("/api/connectors", timeout=8.0)
    ok_parity, raw_parity, parity_payload = backend_json("/api/host-bridge/parity", timeout=8.0)
    parity_payload = normalize_parity_payload_for_cli(parity_payload) if ok_parity else None
    return {
        "repo": str(ROOT),
        "platform": platform.platform(),
        "launcherMode": "windows-native" if windows_native() else "screen-or-macos",
        "process": process_payload,
        "ports": ports_payload,
        "docker": docker_payload,
        "http": http_payload,
        "providerAuth": provider_auth_payload,
        "permissionMode": permission_payload,
        "runtime": runtime_payload,
        "connectors": connectors_payload if ok_connectors else {"ok": False, "error": raw_connectors[:400]},
        "automationPermission": parity_payload if ok_parity else {"ok": False, "error": raw_parity[:400]},
        "urls": {"frontend": FRONTEND_URL, "backend": BACKEND_URL},
    }


def command_status(args: argparse.Namespace) -> int:
    ensure_repo_root()
    if getattr(args, "json", False):
        print(json.dumps(redact_json_value(collect_status_payload()), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print_header("Processes")
    if windows_native():
        print_info("windows native", windows_process_status())
    else:
        print_info("screen", screen_sessions().replace("\n", " | "))
    for port, label in PORTS.items():
        print_info(f"{label} :{port}", port_owner(port) if port_open(port) else "free")

    print_header("Docker")
    docker = command_path("docker")
    if docker and docker_compose_cmd():
        if run([docker, "info"], timeout=10).returncode == 0:
            cmd = docker_compose_cmd()
            assert cmd is not None
            result = run([*cmd, "ps"], timeout=30)
            if result.stdout.strip():
                print(redact_text(result.stdout.strip()))
        else:
            print_check(False, "Docker", "not running")
    else:
        print_check(False, "Docker Compose", "missing")

    print_header("HTTP")
    runtime_payload: object | None = None
    provider_auth_payload: object | None = None
    permission_payload: object | None = None
    for path in ("/health", "/api/runtime", "/api/provider-auth/status", "/api/permissions/mode"):
        ok, raw, payload = backend_json(path, timeout=15.0 if path == "/api/runtime" else 3.0)
        if path == "/api/runtime" and ok:
            runtime_payload = payload
        if path == "/api/provider-auth/status" and ok:
            provider_auth_payload = payload
        if path == "/api/permissions/mode" and ok:
            permission_payload = payload
        if ok and path == "/api/runtime":
            print_check(True, path, summarize_runtime_payload(payload))
        elif ok and isinstance(payload, dict):
            print_check(True, path, f"ok={payload.get('ok', 'unknown')}")
        elif ok:
            print_check(True, path, raw[:120])
        else:
            print_check(False, path, raw)

    print_header("Provider")
    for line in provider_status_from_env():
        print_info(line)
    for line in summarize_provider_auth_payload(provider_auth_payload):
        print_info(line)

    print_header("Owner Permissions")
    for line in summarize_full_autonomy(permission_payload):
        print_info(line)

    if runtime_payload is not None:
        print_header("AI Tools")
        for line in summarize_runtime_ai_tools(runtime_payload):
            print_info(line)

    ok, _raw, connectors_payload = backend_json("/api/connectors", timeout=8.0)
    if ok:
        print_header("Connectors")
        for line in summarize_connectors_payload(connectors_payload):
            print_info(line)

    ok, _raw, parity_payload = backend_json("/api/host-bridge/parity", timeout=8.0)
    if ok:
        parity_payload = normalize_parity_payload_for_cli(parity_payload)
        print_header("Automation Permission")
        for line in summarize_parity_payload(parity_payload):
            print_info(line)

    print_header("URLs")
    print_info("frontend", FRONTEND_URL)
    print_info("backend", BACKEND_URL)
    return 0


def command_stop(args: argparse.Namespace) -> int:
    ensure_repo_root()
    if windows_native():
        print_header("Stop Windows Native Services")
        frontend_ok = windows_stop_process("frontend", UI_PID)
        backend_ok = windows_stop_process("backend", BACKEND_PID)
        return 0 if frontend_ok and backend_ok else 2
    if not command_path("screen"):
        print_check(False, "screen", "missing")
        return 1
    print_header("Stop Screen Sessions")
    for name in (BACKEND_SCREEN, UI_SCREEN):
        if screen_session_exists(name):
            run_or_plan(["screen", "-S", name, "-X", "quit"], timeout=10)
            print_info(name, "stopped")
        else:
            print_info(name, "not running")
    if args.launchd:
        script = ROOT / "ops" / "launchd" / "atrium-launchd.sh"
        if script.exists():
            run_or_plan([str(script), "uninstall"], timeout=30)
    return 0


def command_restart(args: argparse.Namespace) -> int:
    ensure_repo_root()
    print_header("Restart")
    stop_code = command_stop(argparse.Namespace(launchd=False))
    if stop_code != 0:
        return stop_code
    start_args = argparse.Namespace(force=args.force, wait_seconds=args.wait_seconds)
    return command_start(start_args)


def collect_logs_payload(service: str = "all", lines: int = 80) -> dict[str, object]:
    files = {
        "backend": LOG_DIR / "backend.log",
        "ui": LOG_DIR / "ui.log",
    }
    selected = files if service == "all" else {service: files[service]}
    payload: dict[str, object] = {"logDir": str(LOG_DIR), "service": service, "logs": {}}
    logs_payload: dict[str, object] = {}
    for label, path in selected.items():
        exists = path.exists()
        log_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:] if exists else []
        logs_payload[label] = {
            "path": str(path),
            "exists": exists,
            "lines": redact_text("\n".join(log_lines)).splitlines(),
            "hint": (
                f"Run .\\atrium.ps1 start to create native {label} logs, or .\\atrium.ps1 status to inspect current listeners."
                if windows_native() and not exists
                else None
            ),
        }
    payload["logs"] = logs_payload
    return payload


def command_logs(args: argparse.Namespace) -> int:
    ensure_repo_root()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        "backend": LOG_DIR / "backend.log",
        "ui": LOG_DIR / "ui.log",
    }
    selected = files if args.service == "all" else {args.service: files[args.service]}
    if getattr(args, "json", False):
        print(json.dumps(collect_logs_payload(args.service, args.lines), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    for label, path in selected.items():
        print_header(f"{label} log: {rel(path)}")
        if not path.exists():
            print("missing")
            if windows_native():
                print(f"Run .\\atrium.ps1 start to create native {label} logs, or .\\atrium.ps1 status to inspect current listeners.")
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-args.lines :]
        print(redact_text("\n".join(lines)))
    return 0


def support_bundle_json_payloads() -> dict[str, object]:
    payloads: dict[str, object] = {}
    try:
        payloads["diagnostics/status.json"] = collect_status_payload()
    except Exception as exc:
        payloads["diagnostics/status.json"] = {"ok": False, "error": str(exc)[:400]}
    try:
        payloads["diagnostics/logs.json"] = collect_logs_payload("all", 200)
    except Exception as exc:
        payloads["diagnostics/logs.json"] = {"ok": False, "error": str(exc)[:400]}
    ok_permission, raw_permission, permission_payload = backend_json("/api/permissions/mode", timeout=5.0)
    payloads["diagnostics/permission-mode.json"] = permission_payload if ok_permission else {"ok": False, "error": raw_permission[:400]}
    ok_provider, raw_provider, provider_payload = backend_json("/api/provider-auth/status?probe=true", timeout=15.0)
    payloads["diagnostics/provider-status.json"] = provider_payload if ok_provider else {"ok": False, "error": raw_provider[:400]}
    try:
        tools_payload, _tool_ok, _tool_raw = collect_tools_status_payload()
        payloads["diagnostics/tools-status.json"] = tools_payload
    except Exception as exc:
        payloads["diagnostics/tools-status.json"] = {"ok": False, "error": str(exc)[:400]}
    try:
        _automation_ok, automation_payload = collect_automation_status_payload()
        payloads["diagnostics/automation-status.json"] = automation_payload
    except Exception as exc:
        payloads["diagnostics/automation-status.json"] = {"ok": False, "error": str(exc)[:400]}
    return payloads


def report_lines() -> list[str]:
    lines = [
        "# ATRIUM support report",
        f"repo={ROOT}",
        f"platform={platform.platform()}",
        f"machine={platform.machine()}",
        f"ram={memory_gb()}",
        f"launcher_mode={'windows-native' if windows_native() else 'screen-or-macos'}",
        f"git_status={git_status_summary().replace(chr(10), ' | ')}",
    ]
    ok, remote = remote_ok()
    lines.append(f"remote_ok={ok}")
    lines.append(f"remote={remote.replace(chr(10), ' | ')}")
    risky, detail = is_i_cloud_risky(ROOT)
    lines.append(f"path_risky={risky}")
    lines.append(f"path_detail={detail}")
    if windows_native():
        lines.append(f"process.windows={windows_process_status()}")
    else:
        lines.append(f"process.screen={screen_sessions().replace(chr(10), ' | ')}")
    for tool in report_tool_names():
        lines.append(f"tool.{tool}={'present' if command_path(tool) else 'missing'}")
    lines.extend(docker_report_lines())
    for port, label in PORTS.items():
        lines.append(f"port.{label}.{port}={port_owner(port) if port_open(port) else 'free'}")
    runtime_payload: object | None = None
    provider_auth_payload: object | None = None
    permission_payload: object | None = None
    for path in ("/health", "/api/runtime", "/api/provider-auth/status", "/api/permissions/mode"):
        ok, raw, payload = backend_json(path, timeout=15.0 if path == "/api/runtime" else 3.0)
        if path == "/api/runtime" and ok:
            runtime_payload = payload
        if path == "/api/provider-auth/status" and ok:
            provider_auth_payload = payload
        if path == "/api/permissions/mode" and ok:
            permission_payload = payload
        if ok and path == "/api/runtime":
            lines.append(f"http.{path}={summarize_runtime_payload(payload)}")
        elif ok and isinstance(payload, dict):
            lines.append(f"http.{path}=ok={payload.get('ok', 'unknown')}")
        else:
            lines.append(f"http.{path}={raw[:180]}")
    lines.append("system.env:")
    lines.extend(render_env_status(SYSTEM_ENV, FULL_STACK_DEFAULTS.keys()))
    lines.append("ui.env:")
    lines.extend(render_env_status(UI_ENV, UI_DEFAULTS.keys()))
    lines.append("providers:")
    lines.extend(provider_status_from_env())
    lines.extend(summarize_provider_auth_payload(provider_auth_payload))
    lines.append("owner_permissions:")
    lines.extend(summarize_full_autonomy(permission_payload))
    lines.append("ai_tools:")
    lines.extend(summarize_runtime_ai_tools(runtime_payload))
    ok, _raw, tool_catalog_payload = backend_json("/api/tools/catalog", timeout=8.0)
    lines.append("ai_tool_catalog:")
    lines.extend(summarize_tool_catalog_payload(tool_catalog_payload if ok else None))
    ok, _raw, connectors_payload = backend_json("/api/connectors", timeout=8.0)
    lines.append("connectors:")
    lines.extend(summarize_connectors_payload(connectors_payload if ok else None))
    ok, _raw, parity_payload = backend_json("/api/host-bridge/parity", timeout=8.0)
    parity_payload = normalize_parity_payload_for_cli(parity_payload) if ok else None
    lines.append("automation_permission:")
    lines.extend(summarize_parity_payload(parity_payload))
    lines.append("automation_permission.commands:")
    lines.extend(summarize_parity_command_payload(parity_payload))
    return lines


def command_report(args: argparse.Namespace) -> int:
    ensure_repo_root()
    text = redact_text("\n".join(report_lines()) + "\n")
    if args.output:
        output = Path(args.output).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"wrote {output}")
    else:
        print(text)
    if args.bundle is not None:
        bundle = Path(args.bundle).expanduser() if args.bundle else LOG_DIR / f"atrium-support-report-{int(time.time())}.zip"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("support-report.txt", text)
            manifest = {
                "createdAt": int(time.time()),
                "platform": platform.platform(),
                "launcherMode": "windows-native" if windows_native() else "screen-or-macos",
                "redacted": True,
                "included": ["support-report.txt"],
            }
            for label, path in (("backend", LOG_DIR / "backend.log"), ("ui", LOG_DIR / "ui.log")):
                if not path.exists():
                    continue
                log_text = redact_text(path.read_text(encoding="utf-8", errors="replace"))
                name = f"logs/{label}.log"
                archive.writestr(name, log_text)
                manifest["included"].append(name)
            for name, payload in support_bundle_json_payloads().items():
                archive.writestr(name, json.dumps(redact_json_value(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                manifest["included"].append(name)
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        print(f"wrote bundle {bundle}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ATRIUM local full-stack setup shortcut")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check local setup without changing files").set_defaults(func=command_doctor)

    bootstrap = sub.add_parser("bootstrap", help="prepare the full local stack")
    bootstrap.add_argument("--full", action="store_true", help="prepare Postgres/Ollama/backend/frontend")
    bootstrap.add_argument("--dry-run", action="store_true", help="print planned actions without changing files or services")
    bootstrap.add_argument("--auto-homebrew", action="store_true", help=argparse.SUPPRESS)
    bootstrap.add_argument("--wait-docker", action="store_true", help=argparse.SUPPRESS)
    bootstrap.add_argument("-y", "--yes", action="store_true", help=argparse.SUPPRESS)
    bootstrap.set_defaults(func=command_bootstrap)

    setup = sub.add_parser("setup", help="guided one-command install, start, verify, and open")
    setup.add_argument("-y", "--yes", action="store_true", help="accept default installer choices")
    setup.add_argument("--dry-run", action="store_true", help="print planned actions without changing files or services")
    setup.add_argument("--no-start", action="store_true", help="install/bootstrap without starting services")
    setup.add_argument("--no-open", action="store_true", help="do not open the browser after setup")
    setup.add_argument("--force", action="store_true", help="skip port availability guard when starting")
    setup.add_argument("--wait-seconds", type=int, default=30, help="wait for backend/frontend readiness after starting")
    setup.set_defaults(func=command_setup)

    start = sub.add_parser("start", help="start backend and frontend in detached local sessions")
    start.add_argument("--force", action="store_true", help="skip port availability guard")
    start.add_argument("--wait-seconds", type=int, default=20, help="wait for backend/frontend readiness after starting")
    start.set_defaults(func=command_start)

    restart = sub.add_parser("restart", help="stop then start backend and frontend")
    restart.add_argument("--force", action="store_true", help="skip port availability guard when starting")
    restart.add_argument("--wait-seconds", type=int, default=20, help="wait for backend/frontend readiness after restarting")
    restart.set_defaults(func=command_restart)

    status = sub.add_parser("status", help="show process, Docker, runtime, and provider status")
    status.add_argument("--json", action="store_true", help="print redacted machine-readable runtime status")
    status.set_defaults(func=command_status)

    provider = sub.add_parser("provider", help="manage ChatGPT/Claude Code provider login from the local CLI")
    provider_sub = provider.add_subparsers(dest="provider_action", required=True)
    provider_status = provider_sub.add_parser("status", help="show ChatGPT and Claude Code login readiness")
    provider_status.add_argument("--probe", action="store_true", help="run live account probes where supported")
    provider_status.add_argument("--json", action="store_true", help="print redacted provider auth status JSON")
    provider_status.set_defaults(func=command_provider)

    provider_login = provider_sub.add_parser("login", help="start provider login from the native terminal")
    provider_login.add_argument("provider", choices=tuple(PROVIDER_AUTH_TARGETS.keys()), help="provider login target")
    provider_login.add_argument("--timeout-seconds", type=int, default=300, help="ChatGPT OAuth callback session timeout")
    provider_login.add_argument("--wait-seconds", type=int, default=120, help="wait for login readiness after starting")
    provider_login.add_argument("--no-open", action="store_true", help="do not open the ChatGPT OAuth URL automatically")
    provider_login.add_argument("--no-interactive", action="store_true", help="do not run Claude Code login interactively from this terminal")
    provider_login.set_defaults(func=command_provider)

    provider_disconnect = provider_sub.add_parser("disconnect", help="disconnect a provider account through the backend")
    provider_disconnect.add_argument("provider", choices=tuple(PROVIDER_AUTH_TARGETS.keys()), help="provider disconnect target")
    provider_disconnect.set_defaults(func=command_provider)

    tools = sub.add_parser("tools", help="inspect AI tool registry, catalog, and connector readiness")
    tools_sub = tools.add_subparsers(dest="tools_action", required=True)
    tools_status = tools_sub.add_parser("status", help="show AI tool registry and connector readiness")
    tools_status.add_argument("--limit", type=int, default=8, help="number of catalog sample items to print")
    tools_status.add_argument("--json", action="store_true", help="print redacted runtime/tool/connector JSON")
    tools_status.set_defaults(func=command_tools)
    tools_catalog = tools_sub.add_parser("catalog", help="show AI tool catalog summary and sample rows")
    tools_catalog.add_argument("--limit", type=int, default=20, help="number of catalog rows to print")
    tools_catalog.add_argument("--json", action="store_true", help="print redacted AI tool catalog JSON")
    tools_catalog.set_defaults(func=command_tools)

    automation = sub.add_parser("automation", help="inspect and prove browser/desktop automation readiness")
    automation_sub = automation.add_subparsers(dest="automation_action", required=True)
    automation_status = automation_sub.add_parser("status", help="show HostBridge automation and parity readiness")
    automation_status.add_argument("--commands", action="store_true", help="also print the generated cross-OS parity commands")
    automation_status.add_argument("--json", action="store_true", help="print redacted HostBridge parity JSON")
    automation_status.set_defaults(func=command_automation)

    automation_audit = automation_sub.add_parser("audit", help="fail unless the OpenClaw-level Windows contract is fully verified")
    automation_audit.add_argument("--json", action="store_true", help="print the raw parity payload as JSON")
    automation_audit.set_defaults(func=command_automation)

    automation_source = automation_sub.add_parser("source", help="print the HostBridge source fingerprint for parity handoff")
    automation_source.add_argument("--expect-source-fingerprint", help="fail if the current fingerprint differs")
    automation_source.add_argument("--expect-source-manifest-sha256", help="fail if the current source manifest SHA-256 differs")
    automation_source.add_argument("--expect-source-file-count", type=int, help="fail if the current proof-bound source file count differs")
    automation_source.set_defaults(func=command_automation)

    windows_probe = automation_sub.add_parser("windows-probe", help="run the Windows HostBridge probe through uv")
    windows_probe.add_argument("--simulate", action="store_true", help="simulate Windows branch coverage")
    windows_probe.add_argument("--full", action="store_true", help="run the full live Windows parity probe")
    windows_probe.add_argument("--screenshot", action="store_true", help="capture a screenshot probe")
    windows_probe.add_argument("--notification", action="store_true", help="send a notification probe")
    windows_probe.add_argument("--interactive", action="store_true", help="run interactive Notepad desktop control checks")
    windows_probe.add_argument("--browser-url", help="open a URL through browser.open")
    windows_probe.add_argument("--browser-profile", default="atrium", help="browser profile for browser probes")
    windows_probe.add_argument("--output", help="write the stamped probe artifact JSON")
    windows_probe.add_argument("--parity-run-id", help="shared macOS/Windows parity run ID")
    windows_probe.add_argument("--expect-source-fingerprint", help="fail if the HostBridge source fingerprint differs")
    windows_probe.add_argument("--expect-source-manifest-sha256", help="fail if the current source manifest SHA-256 differs")
    windows_probe.add_argument("--expect-source-file-count", type=int, help="fail if the current proof-bound source file count differs")
    windows_probe.set_defaults(func=command_automation)

    windows_live = automation_sub.add_parser("windows-live-proof", help="run the preferred Windows full live proof runner")
    windows_live.add_argument("--parity-run-id", required=True, help="shared macOS/Windows parity run ID")
    windows_live.add_argument("--source-fingerprint", required=True, help="expected HostBridge source fingerprint")
    windows_live.add_argument("--source-manifest-sha256", required=True, help="expected HostBridge source manifest SHA-256")
    windows_live.add_argument("--source-file-count", type=int, required=True, help="expected HostBridge proof-bound source file count")
    windows_live.add_argument("--output", default="C:\\Temp\\atrium_host_bridge_windows_live.json", help="Windows JSON artifact path")
    windows_live.add_argument("--max-artifact-age-hours", type=float, default=24.0)
    windows_live.set_defaults(func=command_automation)

    handoff = automation_sub.add_parser("handoff", help="validate macOS proof and write the Windows proof handoff packet")
    handoff.add_argument("--macos", required=True, help="local path to the current macOS full live proof artifact JSON")
    handoff.add_argument("--output", default=DEFAULT_WINDOWS_HANDOFF_PATH, help="JSON handoff packet to write")
    handoff.add_argument("--windows-output", default=DEFAULT_WINDOWS_PROOF_PATH, help="Windows-host artifact path to produce")
    handoff.add_argument("--windows-local-copy", default=DEFAULT_WINDOWS_LOCAL_COPY_PATH, help="local path where the copied Windows artifact will be verified")
    handoff.add_argument("--max-artifact-age-hours", type=float, default=24.0)
    handoff.add_argument("--json", action="store_true", help="print the handoff packet JSON after writing it")
    handoff.set_defaults(func=command_automation)

    artifact = automation_sub.add_parser("artifact", help="validate a HostBridge proof artifact through the native CLI")
    artifact.add_argument("artifact", help="path to a stamped HostBridge proof artifact JSON")
    artifact.add_argument("--label", required=True, choices=("macos", "windows"), help="expected artifact OS label")
    artifact.add_argument("--expect-parity-run-id", help="fail if the artifact parity run ID differs")
    artifact.add_argument("--expect-source-fingerprint", help="fail if the artifact source fingerprint differs")
    artifact.add_argument("--expect-source-manifest-sha256", help="fail if the artifact source manifest SHA-256 differs")
    artifact.add_argument("--expect-source-file-count", type=int, help="fail if the artifact proof-bound source file count differs")
    artifact.set_defaults(func=command_automation)

    parity_report = automation_sub.add_parser("report", help="verify macOS/Windows live artifacts and install the backend parity report")
    parity_report.add_argument("--macos", required=True, help="local path to macOS full live proof artifact JSON")
    parity_report.add_argument("--windows", required=True, help="local path to copied Windows full live proof artifact JSON")
    parity_report.add_argument("--output", default=str(HOST_BRIDGE_PARITY_REPORT), help="report path read by the ATRIUM backend")
    parity_report.add_argument("--max-artifact-age-hours", type=float, default=24.0)
    parity_report.add_argument("--windows-source-path", default="C:\\Temp\\atrium_host_bridge_windows_live.json", help="original Windows-host artifact path for transfer hints")
    parity_report.add_argument("--skip-current-source-check", action="store_true", help="allow offline historical audits; not valid for claiming current OpenClaw-level parity")
    parity_report.set_defaults(func=command_automation)

    stop = sub.add_parser("stop", help="stop ATRIUM-owned local sessions")
    stop.add_argument("--launchd", action="store_true", help="also uninstall the ATRIUM LaunchAgent")
    stop.set_defaults(func=command_stop)

    logs = sub.add_parser("logs", help="show recent backend/UI logs")
    logs.add_argument("service", nargs="?", choices=("backend", "ui", "all"), default="all")
    logs.add_argument("-n", "--lines", type=int, default=80)
    logs.add_argument("--json", action="store_true", help="print redacted log payload as JSON")
    logs.set_defaults(func=command_logs)

    report = sub.add_parser("report", help="print a redacted support report")
    report.add_argument("-o", "--output", help="write report to a file instead of stdout")
    report.add_argument("--bundle", nargs="?", const="", help="write a redacted support zip bundle; optionally pass the zip path")
    report.set_defaults(func=command_report)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    ensure_common_paths()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except StepFailure as exc:
        print(f"\n[BLOCKED] {exc}", file=sys.stderr)
        if exc.next_step:
            print("\nNext step:", file=sys.stderr)
            print(redact_text(exc.next_step), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
