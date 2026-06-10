#!/usr/bin/env python3
"""ATRIUM local setup shortcut.

This module intentionally uses only the Python standard library so `./atrium
doctor` works immediately after a fresh clone.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
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
import urllib.parse
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
HOST_BRIDGE_PARITY_TIMEOUT_SECONDS = 30.0
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
    "macosSmoke",
    "macosArtifact",
    "macosArtifactValidate",
    "windowsHandoff",
    "windowsHandoffArtifact",
    "windowsRunIdSet",
    "windowsSourceValidate",
    "mcpGatewaySetupJson",
    "mcpGatewayStatusJson",
    "mcpGatewayProbeJson",
    "nativeBrowserDesktopSmoke",
    "windowsProbe",
    "windowsLiveProofRunner",
    "windowsArtifactValidateOnWindows",
    "windowsArtifactSource",
    "windowsArtifactLocal",
    "windowsArtifactCopyHint",
    "windowsArtifactValidateLocal",
    "acceptWindowsArtifact",
    "automationReport",
    "report",
    "verify",
    "legacyParityReport",
)
DEFAULT_WINDOWS_PROOF_PATH = "C:\\Temp\\atrium_host_bridge_windows_live.json"
DEFAULT_WINDOWS_LOCAL_COPY_PATH = "/tmp/atrium_host_bridge_windows_live.json"
DEFAULT_MACOS_PROOF_PATH = "/tmp/atrium_host_bridge_macos_live.json"
DEFAULT_WINDOWS_HANDOFF_PATH = "/tmp/atrium_windows_handoff.json"
DEFAULT_WINDOWS_SMOKE_PATH = "C:\\Temp\\atrium_host_bridge_windows_smoke.json"
DEFAULT_WINDOWS_PROBE_PATH = "C:\\Temp\\atrium_host_bridge_windows_probe.json"
DEFAULT_MACOS_SMOKE_PATH = "/tmp/atrium_host_bridge_macos_smoke.json"
REQUIRED_WINDOWS_PROOF_FACETS = (
    "browserOpen",
    "browserOpenIsolatedProfile",
    "browserSnapshot",
    "browserSnapshotIsolatedPlaywright",
    "browserAct",
    "browserActIsolatedPlaywright",
    "browserActVerified",
    "appsDiscovery",
    "screenshotFile",
    "notification",
    "desktopAutomationReady",
    "interactiveSession",
    "windowsInteractiveSessionIdentity",
    "windowsVisualPreflight",
    "helperSelftest",
    "powershellPreflight",
    "windowsDpiAwareness",
    "windowsVirtualScreen",
    "windowsForegroundActivation",
    "windowsUnicodeTyping",
    "windowsKeyboardShortcut",
    "mcpExternalWriteReady",
    "windowsLiveProofRunner",
    "notepadNativeAct",
    "clipboardRoundTrip",
)
WINDOWS_LIVE_PROOF_FAILURE_STAGE_IDS = (
    "source_validate",
    "mcp_external_write",
    "windows_full_probe",
    "artifact_validate",
)
WINDOWS_LIVE_PROOF_READINESS_GATE_IDS = (
    "source",
    "mcpExternalWrite",
    "browserDesktopSmoke",
    "artifactValidation",
)


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
            line = re.sub(
                r"(?i)(--(?:token|secret|password|api-key|apikey|access-token|refresh-token)\s+)(?:\"[^\"]*\"|'[^']*'|\S+)",
                r"\1<redacted>",
                line,
            )
            line = re.sub(
                r"(?i)(-(?:Token|Secret|Password|ApiKey|AccessToken|RefreshToken)\s+)(?:\"[^\"]*\"|'[^']*'|\S+)",
                r"\1<redacted>",
                line,
            )
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
    if name in {"powershell", "powershell.exe"}:
        for candidate in (
            Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
            Path(os.environ.get("SystemRoot", "C:/Windows")) / "SysWOW64" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        ):
            if candidate.exists():
                return str(candidate)
    if name in {"pwsh", "pwsh.exe"}:
        for candidate in (
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "PowerShell" / "7" / "pwsh.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "PowerShell" / "7" / "pwsh.exe",
        ):
            if candidate.exists():
                return str(candidate)
    if name == "docker":
        for candidate in (
            "/Applications/Docker.app/Contents/Resources/bin/docker",
            "C:/Program Files/Docker/Docker/resources/bin/docker.exe",
        ):
            if Path(candidate).exists():
                return candidate
    if name in {"pg_dump", "psql"} and platform.system() == "Windows":
        program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
        postgres_root = program_files / "PostgreSQL"
        if postgres_root.exists():
            candidates = sorted(postgres_root.glob(f"*/bin/{name}.exe"), reverse=True)
            for candidate in candidates:
                if candidate.exists():
                    return str(candidate)
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


def report_command_path(name: str) -> str | None:
    if name == "powershell":
        return powershell_command()
    return command_path(name)


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
                path_literal = ps_single_quote(str(candidate))
                run([powershell, "-NoProfile", "-Command", f"Start-Process -FilePath {path_literal}"], timeout=20)
                return True
    return False


def docker_ready() -> bool:
    docker = command_path("docker")
    if not docker:
        return False
    return run([docker, "info"], timeout=15).returncode == 0


def configured_database_url() -> str:
    values = parse_env_file(SYSTEM_ENV)
    return (
        os.environ.get("ATRIUM_DATABASE_URL")
        or values.get("ATRIUM_DATABASE_URL")
        or ""
    ).strip()


def configured_embedding_provider() -> str:
    values = parse_env_file(SYSTEM_ENV)
    return (
        os.environ.get("ATRIUM_EMBEDDING_PROVIDER")
        or values.get("ATRIUM_EMBEDDING_PROVIDER")
        or "auto"
    ).strip().lower()


def configured_data_dir() -> Path:
    values = parse_env_file(SYSTEM_ENV)
    value = (
        os.environ.get("ATRIUM_DATA_DIR")
        or values.get("ATRIUM_DATA_DIR")
        or "./data"
    ).strip()
    path = Path(value).expanduser()
    return path if path.is_absolute() else SYSTEM_DIR / path


def configured_backup_dir() -> Path:
    values = parse_env_file(SYSTEM_ENV)
    value = (
        os.environ.get("ATRIUM_BACKUP_DIR")
        or values.get("ATRIUM_BACKUP_DIR")
        or "./data/backups"
    ).strip()
    path = Path(value).expanduser()
    return path if path.is_absolute() else SYSTEM_DIR / path


def uses_postgres_database(url: str | None = None) -> bool:
    value = (url if url is not None else configured_database_url()).strip().lower()
    return value.startswith(("postgresql://", "postgresql+asyncpg://", "postgres://"))


def normalize_pg_tool_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    return url


def _url_host_port(url: str, default_port: int) -> tuple[str, int]:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return "", default_port
    host = parsed.hostname or ""
    try:
        port = int(parsed.port or default_port)
    except ValueError:
        port = default_port
    return host.lower(), port


def _loopback_host(host: str) -> bool:
    normalized = (host or "").strip().lower().strip("[]")
    return normalized in {"", "localhost", "127.0.0.1", "::1"}


def docker_stack_plan() -> dict[str, object]:
    """Describe which Docker-backed services would actually help this host."""
    database_url = configured_database_url()
    ollama_url = configured_ollama_url()
    services: list[str] = []
    required_services: list[str] = []
    satisfied: list[str] = []
    notes: list[str] = []

    if uses_postgres_database(database_url):
        host, port = _url_host_port(database_url, 5432)
        if host == "postgres":
            services.append("postgres")
            required_services.append("postgres")
        elif _loopback_host(host):
            if port_open(port):
                satisfied.append(f"postgres:native:{port}")
            else:
                services.append("postgres")
                required_services.append("postgres")
        else:
            satisfied.append(f"postgres:external:{host}:{port}")
    else:
        satisfied.append("database:sqlite")

    if ollama_url and not uses_external_ollama(ollama_url):
        host, port = _url_host_port(ollama_url, 11434)
        mode = configured_embedding_provider()
        if host == "ollama":
            services.append("ollama")
            if mode in {"local", "ollama"}:
                required_services.append("ollama")
        elif _loopback_host(host):
            if port_open(port):
                satisfied.append(f"ollama:native:{port}")
            else:
                services.append("ollama")
                if mode in {"local", "ollama"}:
                    required_services.append("ollama")
                else:
                    notes.append("ollama missing; auto embeddings can fall back to hash/Voyage/OpenAI settings")
        else:
            satisfied.append(f"ollama:external:{host}:{port}")
    elif ollama_url:
        satisfied.append("ollama:external")

    unique_services = list(dict.fromkeys(services))
    unique_required = list(dict.fromkeys(required_services))
    return {
        "services": unique_services,
        "requiredServices": unique_required,
        "required": bool(unique_required),
        "satisfied": satisfied,
        "notes": notes,
    }


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
            run([powershell, "-NoProfile", "-Command", f"Start-Process -FilePath {ps_single_quote(url)}"], timeout=10)
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
    except (TimeoutError, ConnectionError, OSError) as exc:
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
    except (TimeoutError, ConnectionError, OSError) as exc:
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


PERMISSION_MODE_CHOICES = (
    "deny",
    "allowlist",
    "ask",
    "auto",
    "full",
    "full_auto",
    "approve_everything",
    "approve_all",
    "critical_only",
)


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


def summarize_provider_reference_payload(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["provider reference=unavailable"]
    credentials = payload.get("credentials")
    providers = payload.get("providers")
    subsystems = payload.get("subsystems")
    errors = payload.get("statusErrors")
    lines = [
        f"credentials={len(credentials) if isinstance(credentials, list) else 'unknown'}",
        f"providers={len(providers) if isinstance(providers, list) else 'unknown'}",
        f"subsystems={len(subsystems) if isinstance(subsystems, list) else 'unknown'}",
    ]
    if isinstance(credentials, list):
        configured = [
            str(item.get("id"))
            for item in credentials
            if isinstance(item, dict) and item.get("configured") is True and item.get("id")
        ]
        if configured:
            lines.append("configured=" + ", ".join(sorted(configured)))
    if isinstance(errors, list) and errors:
        lines.append("statusErrors=" + "; ".join(str(item) for item in errors[:4]))
    return lines


def summarize_provider_env_payload(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["provider env=unavailable"]
    groups = payload.get("groups")
    process_overrides = payload.get("processOverrides")
    group_count = len(groups) if isinstance(groups, list) else "unknown"
    field_count = 0
    configured_fields: list[str] = []
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            for field in group.get("fields") or []:
                if not isinstance(field, dict):
                    continue
                field_count += 1
                if field.get("configured") and field.get("key"):
                    configured_fields.append(str(field["key"]))
    lines = [
        f"envPath={payload.get('envPath') or 'unknown'}",
        f"groups={group_count}",
        f"fields={field_count}",
        f"configuredFields={len(configured_fields)}",
        f"restartRecommended={bool_text(payload.get('restartRecommended'))}",
    ]
    if configured_fields:
        lines.append("configured=" + ", ".join(sorted(configured_fields)[:12]))
    if isinstance(process_overrides, list) and process_overrides:
        lines.append("processOverrides=" + ", ".join(str(item) for item in process_overrides[:12]))
    return lines


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


def parse_bool_option(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true/false")


def split_csv_values(values: Sequence[str] | None) -> list[str]:
    if not values:
        return []
    items: list[str] = []
    for value in values:
        for item in str(value).split(","):
            text = item.strip()
            if text:
                items.append(text)
    return items


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
        checks = host_bridge.get("windowsVisualPreflightChecks")
        if isinstance(checks, dict) and checks:
            failed_checks = ", ".join(str(key) for key, value in sorted(checks.items()) if value is not True)
            passed_count = sum(1 for value in checks.values() if value is True)
            lines.append(
                "Windows automation checks: "
                f"passed={passed_count}/{len(checks)}, "
                f"failed={failed_checks or 'none'}"
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
        if connector.get("id") == "mcp":
            external_requires = connector.get("externalWriteRequires")
            require_text = "none"
            if isinstance(external_requires, list) and external_requires:
                require_text = ", ".join(str(value) for value in external_requires if str(value).strip()) or "none"
            lines.append(
                "connector.mcp.externalWrite: "
                f"ready={bool_text(connector.get('writeReady'))}, "
                f"localFallbackOnly={bool_text(connector.get('localFallback'))}, "
                f"requires={require_text}, "
                "statusCommand=curl -fsS http://127.0.0.1:8787/api/connectors"
            )
    return lines or ["connectors=unavailable"]


def _mcp_connector_from_payload(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, list):
        return None
    for connector in payload:
        if isinstance(connector, dict) and connector.get("id") == "mcp":
            return connector
    return None


def mcp_gateway_setup_payload(runtime_payload: object, connectors_payload: object) -> dict[str, object]:
    runtime_v2 = runtime_payload.get("v2") if isinstance(runtime_payload, dict) and isinstance(runtime_payload.get("v2"), dict) else {}
    credential_readiness = (
        runtime_v2.get("credentialReadiness")
        if isinstance(runtime_v2.get("credentialReadiness"), dict)
        else {}
    )
    mcp_connector = _mcp_connector_from_payload(connectors_payload) or {}
    requirements = mcp_connector.get("externalWriteRequires")
    if not isinstance(requirements, list):
        requirements = (
            credential_readiness.get("externalWriteChannels", {})
            .get("mcpExternalWrites", {})
            .get("requirements", [])
            if isinstance(credential_readiness.get("externalWriteChannels"), dict)
            else []
        )
    if not isinstance(requirements, list):
        requirements = []
    windows_commands = [
        "$env:ATRIUM_MCP_GATEWAY_URL='<write-capable MCP gateway URL>'",
        "$env:ATRIUM_MCP_ENABLED_SERVERS='github,email,calendar,notion,drive'",
        "$value = Read-Host -Prompt 'ATRIUM MCP gateway token' -AsSecureString",
        "$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($value)",
        "$plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)",
        "[Environment]::SetEnvironmentVariable('ATRIUM_MCP_GATEWAY_URL', $env:ATRIUM_MCP_GATEWAY_URL, 'User')",
        "[Environment]::SetEnvironmentVariable('ATRIUM_MCP_GATEWAY_TOKEN', $plain, 'User')",
        "[Environment]::SetEnvironmentVariable('ATRIUM_MCP_ENABLED_SERVERS', $env:ATRIUM_MCP_ENABLED_SERVERS, 'User')",
        "[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)",
        ".\\atrium.ps1 restart --force",
        ".\\atrium.ps1 tools status --json",
        ".\\atrium.ps1 tools mcp-probe --json",
    ]
    macos_commands = [
        "export ATRIUM_MCP_GATEWAY_URL='<write-capable MCP gateway URL>'",
        "security add-generic-password -U -s atrium.mcp.gateway -a atrium -w '<secret token>'",
        "export ATRIUM_MCP_GATEWAY_TOKEN_KEYCHAIN_SERVICE='atrium.mcp.gateway'",
        "export ATRIUM_MCP_GATEWAY_TOKEN_KEYCHAIN_ACCOUNT='atrium'",
        "export ATRIUM_MCP_ENABLED_SERVERS='github,email,calendar,notion,drive'",
        "./atrium restart --force",
        "./atrium tools mcp-probe --json",
    ]
    verify_commands = [
        "curl -fsS http://127.0.0.1:8787/api/runtime",
        "curl -fsS 'http://127.0.0.1:8787/api/tools/mcp-gateway?probe=true'",
        ".\\atrium.ps1 tools status --json",
        ".\\atrium.ps1 tools mcp-probe --json",
    ]
    ready = bool(
        mcp_connector.get("status") == "configured"
        and mcp_connector.get("readReady") is True
        and mcp_connector.get("writeReady") is True
        and mcp_connector.get("localFallback") is False
        and not requirements
    )
    return {
        "ok": True,
        "ready": ready,
        "connector": mcp_connector,
        "requirements": requirements,
        "proofBlocker": {
            "blocked": not ready,
            "stage": "mcp_external_write",
            "runner": "ops/windows_host_bridge_live_proof.ps1",
            "proofFacet": "mcpExternalWriteReady",
            "requirements": requirements,
        },
        "redaction": "Secret values are placeholders and are never read back or printed by this command.",
        "env": {
            "ATRIUM_MCP_GATEWAY_URL": "<write-capable MCP gateway URL>",
            "ATRIUM_MCP_GATEWAY_TOKEN": "<secret token, or use Keychain/service storage where available>",
            "ATRIUM_MCP_GATEWAY_TOKEN_KEYCHAIN_SERVICE": "atrium.mcp.gateway",
            "ATRIUM_MCP_GATEWAY_TOKEN_KEYCHAIN_ACCOUNT": "atrium",
            "ATRIUM_MCP_ENABLED_SERVERS": "github,email,calendar,notion,drive",
        },
        "windowsPowerShell": windows_commands,
        "macosShell": macos_commands,
        "verifyCommands": verify_commands,
        "probeCommand": ".\\atrium.ps1 tools mcp-probe --json",
        "requiredBefore": "windows-live-proof",
        "successCondition": "MCP probe reports ready=true, gatewayHealth.ok=true, writeReady=true, localFallback=false, and externalWriteRequires=[]",
    }


def summarize_mcp_gateway_setup_payload(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["MCP gateway setup=unavailable"]
    connector = payload.get("connector") if isinstance(payload.get("connector"), dict) else {}
    requirements = payload.get("requirements") if isinstance(payload.get("requirements"), list) else []
    lines = [
        (
            "MCP external-write: "
            f"ready={bool_text(payload.get('ready'))}, "
            f"status={connector.get('status') or 'unknown'}, "
            f"write={bool_text(connector.get('writeReady'))}, "
            f"localFallback={bool_text(connector.get('localFallback'))}"
        ),
        "MCP success condition: " + str(payload.get("successCondition") or "unknown"),
    ]
    if requirements:
        lines.append("MCP requirements: " + "; ".join(str(item) for item in requirements if str(item).strip()))
    proof_blocker = payload.get("proofBlocker") if isinstance(payload.get("proofBlocker"), dict) else {}
    if proof_blocker.get("blocked") is True and proof_blocker.get("stage"):
        lines.append(
            "Windows live proof blocker: "
            f"{proof_blocker.get('stage')} "
            f"facet={proof_blocker.get('proofFacet') or 'mcpExternalWriteReady'}"
        )
    windows_commands = payload.get("windowsPowerShell")
    if isinstance(windows_commands, list):
        lines.append("Windows PowerShell setup:")
        lines.extend(f"  {item}" for item in windows_commands[:8])
    verify_commands = payload.get("verifyCommands")
    if isinstance(verify_commands, list):
        lines.append("Verify:")
        lines.extend(f"  {item}" for item in verify_commands[:4])
    probe_command = payload.get("probeCommand")
    if isinstance(probe_command, str) and probe_command:
        lines.append(f"Probe: {probe_command}")
    return lines


def docker_report_lines() -> list[str]:
    payload = collect_docker_payload()
    compose_cmd = payload.get("compose")
    docker = command_path("docker")
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    lines = [
        f"docker.cli={'present' if docker else 'missing'}",
        f"docker.compose={' '.join(compose_cmd) if isinstance(compose_cmd, list) else 'missing'}",
        f"docker.required={bool_text(plan.get('required'))}",
    ]
    services = plan.get("services")
    if isinstance(services, list) and services:
        lines.append("docker.services=" + ",".join(str(item) for item in services))
    satisfied = plan.get("satisfied")
    if isinstance(satisfied, list) and satisfied:
        lines.append("docker.satisfied=" + ",".join(str(item) for item in satisfied))
    lines.append(f"docker.running={bool_text(payload.get('running'))}")
    if payload.get("error"):
        lines.append(f"docker.error={payload.get('error')}")
    return lines


def collect_docker_payload() -> dict[str, object]:
    docker = command_path("docker")
    compose_cmd = docker_compose_cmd() if docker else None
    plan = docker_stack_plan()
    payload: dict[str, object] = {
        "cli": docker or None,
        "compose": compose_cmd,
        "running": False,
        "plan": plan,
        "required": bool(plan.get("required")),
        "requiredServices": plan.get("requiredServices", []),
    }
    if not docker:
        payload["error"] = "docker CLI missing"
        return payload
    result = run([docker, "info"], timeout=10)
    payload["running"] = result.returncode == 0
    if result.returncode != 0:
        payload["error"] = redact_text((result.stderr or result.stdout or "docker info failed").strip())[:400]
    return payload


def _host_bridge_gap_text(value: object) -> str:
    text = str(value or "").strip()
    if "ops/host_bridge_parity_report.py" not in text:
        return text
    return (
        f"Run {local_cli_command('automation', 'report')} --max-artifact-age-hours 24.0 with macOS and Windows --full probe artifacts, "
        f"then {local_cli_command('automation', 'audit')}, before claiming cross-OS HostBridge parity."
    )


def openclaw_requirement_gap_label(item: dict[str, object]) -> str:
    item_id = str(item.get("id") or item.get("label") or "unknown")
    details: list[str] = []
    current_details = item.get("currentDetails")
    if item.get("degradedByLocalFallback") is True:
        details.append("local fallback only")
    if item.get("requiresWriteReady") is True and item.get("writeReady") is False:
        details.append("write not ready")
    if item.get("readReady") is False:
        details.append("read not ready")
    if isinstance(current_details, dict):
        if current_details.get("gatewayConfigured") is False:
            details.append("gateway not configured")
        if current_details.get("gatewayHealthy") is False and current_details.get("gatewayConfigured") is True:
            details.append("gateway health not ready")
        required_env = current_details.get("requiredEnvironment")
        if isinstance(required_env, list) and required_env:
            env_text = ", ".join(str(value) for value in required_env[:3] if str(value).strip())
            if env_text:
                details.append(f"env={env_text}")
        status_command = str(current_details.get("statusCommand") or "").strip()
        if status_command:
            details.append(f"check={status_command}")
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


def resolve_repo_output_path(path_value: str | Path) -> Path:
    output_path = Path(path_value).expanduser()
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    return output_path


def is_backend_parity_report_path(path_value: str | Path) -> bool:
    try:
        return resolve_repo_output_path(path_value).resolve() == HOST_BRIDGE_PARITY_REPORT.resolve()
    except OSError:
        return resolve_repo_output_path(path_value).absolute() == HOST_BRIDGE_PARITY_REPORT.absolute()


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


def file_sha256_and_size(path: str | Path) -> tuple[str | None, int | None]:
    try:
        file_path = Path(path)
        digest = hashlib.sha256()
        size = 0
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size
    except OSError:
        return None, None


def build_windows_handoff_payload(
    *,
    macos_artifact: str,
    macos_summary: dict[str, object],
    source_summary: dict[str, object],
    windows_output: str,
    windows_local_copy: str,
    handoff_output: str = DEFAULT_WINDOWS_HANDOFF_PATH,
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
        "--max-artifact-age-hours 24.0 "
        f"--output {ps_single_quote(windows_output)}"
    )
    windows_source = (
        ".\\atrium.ps1 automation source "
        f"--expect-source-fingerprint {source_fingerprint} "
        f"--expect-source-manifest-sha256 {source_manifest_sha256} "
        f"--expect-source-file-count {source_file_count} "
        "--json"
    )
    windows_artifact = (
        ".\\atrium.ps1 automation artifact --label windows "
        f"--expect-parity-run-id {ps_single_quote(parity_run_id)} "
        f"--expect-source-fingerprint {source_fingerprint} "
        f"--expect-source-manifest-sha256 {source_manifest_sha256} "
        f"--expect-source-file-count {source_file_count} "
        "--max-artifact-age-hours 24.0 "
        "--json "
        f"{ps_single_quote(windows_output)}"
    )
    report = (
        f"{local_cli_command('automation', 'report')} "
        f"--macos {shell_quote(macos_artifact)} "
        f"--windows {shell_quote(windows_local_copy)} "
        "--max-artifact-age-hours 24.0 "
        f"--windows-source-path {shell_quote(windows_output)}"
    )
    accept_windows = (
        f"{local_cli_command('automation', 'accept-windows')} "
        f"{shell_quote(windows_local_copy)} "
        f"--handoff {shell_quote(handoff_output)} "
        "--max-artifact-age-hours 24.0 "
        f"--windows-source-path {shell_quote(windows_output)}"
    )
    audit = local_cli_command("automation", "audit")
    native_setup = ".\\atrium.ps1 setup --yes"
    native_start = ".\\atrium.ps1 start"
    native_status = ".\\atrium.ps1 status --json"
    native_logs = ".\\atrium.ps1 logs --json"
    native_report = ".\\atrium.ps1 report --bundle"
    native_stop = ".\\atrium.ps1 stop"
    native_restart = ".\\atrium.ps1 restart --force"
    native_permissions_status = ".\\atrium.ps1 permissions status --json"
    native_permissions_set = ".\\atrium.ps1 permissions set full_auto --agent-full-access true"
    native_provider_status = ".\\atrium.ps1 provider status --probe --json"
    native_provider_reference = ".\\atrium.ps1 provider reference --json"
    native_provider_env = ".\\atrium.ps1 provider env --json"
    native_provider_login_chatgpt = ".\\atrium.ps1 provider login chatgpt"
    native_provider_login_claude = ".\\atrium.ps1 provider login claude-code"
    native_provider_disconnect_chatgpt = ".\\atrium.ps1 provider disconnect chatgpt"
    native_provider_disconnect_claude = ".\\atrium.ps1 provider disconnect claude-code"
    native_tools_status = ".\\atrium.ps1 tools status --json"
    native_tools_catalog = ".\\atrium.ps1 tools catalog --json"
    mcp_gateway_setup = ".\\atrium.ps1 tools mcp-gateway --json"
    mcp_gateway_status = ".\\atrium.ps1 tools status --json"
    mcp_gateway_probe = ".\\atrium.ps1 tools mcp-probe --json"
    native_browser_desktop_smoke = f".\\atrium.ps1 automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium --output {DEFAULT_WINDOWS_SMOKE_PATH}"
    windows_probe = f".\\atrium.ps1 automation windows-probe --full --browser-url http://127.0.0.1:5173 --browser-profile atrium --output {DEFAULT_WINDOWS_PROBE_PATH}"
    failure_stages = {
        "source_validate": {
            "stage": "source_validate",
            "checklistId": "source_validate",
            "commandKey": "sourceValidate",
            "blocks": ["windows_live_proof"],
            "successCondition": "source fingerprint, manifest, and file count match this handoff",
        },
        "mcp_external_write": {
            "stage": "mcp_external_write",
            "checklistId": "mcp_gateway_probe",
            "commandKey": "mcpGatewayProbeJson",
            "proofFacet": "mcpExternalWriteReady",
            "blocks": ["windows_live_proof"],
            "successCondition": "ready=true, gatewayHealth.ok=true, writeReady=true, localFallback=false, and externalWriteRequires=[]",
        },
        "windows_full_probe": {
            "stage": "windows_full_probe",
            "checklistId": "native_browser_desktop_smoke",
            "commandKey": "windowsProbe",
            "blocks": ["windows_live_proof"],
            "successCondition": "raw Windows browser/desktop diagnostic probe can run in the signed-in native desktop session",
        },
        "artifact_validate": {
            "stage": "artifact_validate",
            "checklistId": "artifact_validate_on_windows",
            "commandKey": "artifactValidate",
            "blocks": ["copy_to_repo_host", "accept_windows_artifact"],
            "successCondition": "Windows artifact validates all required proof facets on the Windows host",
        },
    }
    readiness_gates = {
        "source": failure_stages["source_validate"],
        "mcpExternalWrite": failure_stages["mcp_external_write"],
        "browserDesktopSmoke": {
            "stage": "windows_full_probe",
            "checklistId": "native_browser_desktop_smoke",
            "commandKey": "nativeBrowserDesktopSmoke",
            "diagnosticCommandKey": "windowsProbe",
            "blocks": ["windows_live_proof"],
            "successCondition": "native smoke and raw diagnostic probe both produce usable browser/desktop evidence",
        },
        "artifactValidation": failure_stages["artifact_validate"],
    }
    operator_checklist = [
        {
            "step": 1,
            "host": "windows",
            "id": "native_setup_start",
            "command": f"{native_setup}; {native_start}; {native_status}",
            "requiredBefore": "source_validate",
            "successCondition": "setup/start succeed and status JSON shows native backend/frontend process truth",
        },
        {
            "step": 2,
            "host": "windows",
            "id": "native_permissions",
            "command": f"{native_permissions_status}; {native_permissions_set}; {native_permissions_status}",
            "requiredBefore": "windows_live_proof",
            "successCondition": "permission status JSON is readable and owner automation permission mode is full_auto with agent full access",
        },
        {
            "step": 3,
            "host": "windows",
            "id": "native_provider_ai_tools",
            "command": f"{native_provider_status}; {native_provider_reference}; {native_provider_env}; {native_tools_status}; {native_tools_catalog}",
            "loginCommands": [native_provider_login_chatgpt, native_provider_login_claude],
            "accountSwitchCommands": [native_provider_disconnect_chatgpt, native_provider_disconnect_claude],
            "requiredBefore": "windows_live_proof",
            "successCondition": "provider status/reference/env JSON and AI tools status/catalog JSON are readable; run loginCommands if provider accounts are not ready, and accountSwitchCommands only when intentionally changing accounts",
        },
        {
            "step": 4,
            "host": "windows",
            "id": "native_logs_report",
            "command": f"{native_logs}; {native_report}",
            "requiredBefore": "source_validate",
            "successCondition": "logs JSON and support bundle are generated without leaking secrets",
        },
        {
            "step": 5,
            "host": "windows",
            "id": "native_stop_restart",
            "command": f"{native_stop}; {native_restart}; {native_status}",
            "requiredBefore": "source_validate",
            "successCondition": "stop/restart preserve native launcher process truth and status JSON returns ready",
        },
        {
            "step": 6,
            "host": "windows",
            "id": "mcp_gateway_setup",
            "command": mcp_gateway_setup,
            "requiredBefore": "windows_live_proof",
            "successCondition": "MCP gateway settings JSON is readable on the native Windows host",
        },
        {
            "step": 7,
            "host": "windows",
            "id": "mcp_gateway_probe",
            "command": mcp_gateway_probe,
            "requiredBefore": "windows_live_proof",
            "failureStage": "mcp_external_write",
            "proofFacet": "mcpExternalWriteReady",
            "successCondition": "mcp-probe JSON shows ready=true, gatewayHealth.ok=true, writeReady=true, localFallback=false, and no externalWriteRequires",
        },
        {
            "step": 8,
            "host": "windows",
            "id": "mcp_gateway_status",
            "command": mcp_gateway_status,
            "requiredBefore": "windows_live_proof",
            "successCondition": "tools status JSON shows MCP connector catalog readiness on the native Windows host",
        },
        {
            "step": 9,
            "host": "windows",
            "id": "native_browser_desktop_smoke",
            "command": native_browser_desktop_smoke,
            "outputPath": "C:\\Temp\\atrium_host_bridge_windows_smoke.json",
            "requiredBefore": "windows_live_proof",
            "failureStage": "windows_full_probe",
            "successCondition": "diagnostic browser/desktop smoke artifact is generated from native PowerShell; this does not replace windows-live-proof",
        },
        {
            "step": 10,
            "host": "windows",
            "id": "windows_raw_probe",
            "command": windows_probe,
            "diagnosticOutputPath": DEFAULT_WINDOWS_PROBE_PATH,
            "requiredBefore": "windows_live_proof",
            "failureStage": "windows_full_probe",
            "successCondition": "raw Windows HostBridge diagnostic probe artifact is generated for troubleshooting only",
        },
        {
            "step": 11,
            "host": "windows",
            "id": "source_validate",
            "command": windows_source,
            "requiredBefore": "windows_live_proof",
            "failureStage": "source_validate",
            "successCondition": "ok=true and sourceFingerprint/sourceManifestSha256/sourceFileCount match this handoff",
        },
        {
            "step": 12,
            "host": "windows",
            "id": "windows_live_proof",
            "command": windows_live,
            "outputPath": windows_output,
            "failureStages": list(failure_stages),
            "successCondition": "artifact ok=true, mode=live, and full Windows HostBridge proof facets are present",
        },
        {
            "step": 13,
            "host": "windows",
            "id": "artifact_validate_on_windows",
            "command": windows_artifact,
            "requiredBefore": "copy_to_repo_host",
            "failureStage": "artifact_validate",
            "successCondition": "validator returns ok=true for the Windows artifact on the Windows host",
        },
        {
            "step": 14,
            "host": "transfer",
            "id": "copy_to_repo_host",
            "from": windows_output,
            "to": windows_local_copy,
            "successCondition": "the copied file path exists on the repo host before report generation",
        },
        {
            "step": 15,
            "host": "repo",
            "id": "accept_windows_artifact",
            "command": accept_windows,
            "successCondition": "artifact validates, is installed at the local copy path, report is generated, and audit passes",
        },
        {
            "step": 16,
            "host": "repo",
            "id": "generate_report",
            "command": report,
            "successCondition": "fallback manual path: persisted cross-OS report ok=true",
        },
        {
            "step": 17,
            "host": "repo",
            "id": "audit_gate",
            "command": audit,
            "successCondition": "automation audit ok=true before claiming OpenClaw-level Windows parity",
        },
    ]
    macos_artifact_sha256, macos_artifact_bytes = file_sha256_and_size(macos_artifact)
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
            "artifactSha256": macos_summary.get("artifactSha256") or macos_artifact_sha256,
            "artifactBytes": macos_summary.get("artifactBytes") or macos_artifact_bytes,
            "parityRunId": parity_run_id,
            "hostPlatform": macos_summary.get("hostPlatform"),
            "hostFingerprint": macos_summary.get("hostFingerprint"),
            "generatedAt": macos_summary.get("generatedAt"),
        },
        "windowsProof": {
            "outputPath": windows_output,
            "localCopyPath": windows_local_copy,
            "requiredProofFacets": list(REQUIRED_WINDOWS_PROOF_FACETS),
            "proofFacetCount": len(REQUIRED_WINDOWS_PROOF_FACETS),
            "failureStages": failure_stages,
            "readinessGates": readiness_gates,
            "commands": {
                "nativeSetup": native_setup,
                "nativeStart": native_start,
                "nativeStatusJson": native_status,
                "nativeLogsJson": native_logs,
                "nativeReportBundle": native_report,
                "nativeStop": native_stop,
                "nativeRestart": native_restart,
                "nativePermissionsStatusJson": native_permissions_status,
                "nativePermissionsSetFullAuto": native_permissions_set,
                "nativeProviderStatusProbeJson": native_provider_status,
                "nativeProviderReferenceJson": native_provider_reference,
                "nativeProviderEnvJson": native_provider_env,
                "nativeProviderLoginChatGPT": native_provider_login_chatgpt,
                "nativeProviderLoginClaudeCode": native_provider_login_claude,
                "nativeProviderDisconnectChatGPT": native_provider_disconnect_chatgpt,
                "nativeProviderDisconnectClaudeCode": native_provider_disconnect_claude,
                "nativeToolsStatusJson": native_tools_status,
                "nativeToolsCatalogJson": native_tools_catalog,
                "mcpGatewaySetupJson": mcp_gateway_setup,
                "mcpGatewayStatusJson": mcp_gateway_status,
                "mcpGatewayProbeJson": mcp_gateway_probe,
                "nativeBrowserDesktopSmoke": native_browser_desktop_smoke,
                "windowsProbe": windows_probe,
                "sourceValidate": windows_source,
                "liveProof": windows_live,
                "artifactValidate": windows_artifact,
                "windowsArtifactValidateOnWindows": windows_artifact,
                "acceptWindowsArtifact": accept_windows,
            },
            "copyInstruction": f"Copy {windows_output} from the Windows host to {windows_local_copy} on this host.",
            "operatorChecklist": operator_checklist,
        },
        "finalVerification": {
            "commands": {
                "acceptWindowsArtifact": accept_windows,
                "report": report,
                "audit": audit,
            },
            "operatorChecklist": operator_checklist[-3:],
            "requiredGate": "Run accept-windows with the copied Windows artifact; it must validate, write the report, and audit must pass before claiming OpenClaw-level Windows parity.",
        },
    }


def _current_handoff_payload(path: str | Path, current_source: dict[str, object]) -> dict[str, object]:
    handoff_path = Path(path).expanduser()
    if not handoff_path.is_absolute():
        handoff_path = ROOT / handoff_path
    payload, error = _load_json_file(handoff_path)
    if error or not isinstance(payload, dict):
        raise StepFailure(
            "Windows proof handoff is not readable",
            next_step=f"Run {local_cli_command('automation', 'handoff')} after generating a current macOS proof artifact. Detail: {error or 'invalid handoff'}",
        )
    if payload.get("ok") is not True or payload.get("kind") != "atrium.hostBridge.windowsProofHandoff":
        raise StepFailure("Windows proof handoff is not a valid ATRIUM handoff packet")
    handoff_source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    current_fingerprint = current_source.get("sourceFingerprint")
    current_manifest = current_source.get("sourceManifestSha256")
    current_count = current_source.get("sourceFileCount")
    if handoff_source.get("sourceFingerprint") != current_fingerprint:
        raise StepFailure(
            "Windows proof handoff source fingerprint is stale",
            next_step=(
                f"Current source is {current_fingerprint}; handoff has {handoff_source.get('sourceFingerprint')}. "
                f"Regenerate macOS proof and rerun {local_cli_command('automation', 'handoff')}."
            ),
        )
    if handoff_source.get("sourceManifestSha256") != current_manifest or handoff_source.get("sourceFileCount") != current_count:
        raise StepFailure(
            "Windows proof handoff source manifest is stale",
            next_step=f"Regenerate macOS proof and rerun {local_cli_command('automation', 'handoff')} before accepting a Windows artifact.",
        )
    contract_findings = _windows_handoff_contract_findings(payload)
    if contract_findings:
        raise StepFailure(
            "Windows proof handoff contract is not OpenClaw-complete",
            next_step="; ".join(contract_findings[:6]),
        )
    return payload


def _windows_handoff_contract_findings(handoff: dict[str, object]) -> list[str]:
    findings: list[str] = []
    windows_proof = handoff.get("windowsProof") if isinstance(handoff.get("windowsProof"), dict) else {}
    required_facets = windows_proof.get("requiredProofFacets")
    required_set = {str(item) for item in required_facets} if isinstance(required_facets, list) else set()
    expected_set = set(REQUIRED_WINDOWS_PROOF_FACETS)
    if required_set != expected_set:
        missing = sorted(expected_set - required_set)
        extra = sorted(required_set - expected_set)
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing[:6]))
        if extra:
            detail.append("extra=" + ",".join(extra[:6]))
        findings.append("requiredProofFacets mismatch" + (": " + "; ".join(detail) if detail else ""))
    if windows_proof.get("proofFacetCount") != len(REQUIRED_WINDOWS_PROOF_FACETS):
        findings.append(
            "proofFacetCount mismatch: "
            f"handoff={windows_proof.get('proofFacetCount')}; expected={len(REQUIRED_WINDOWS_PROOF_FACETS)}"
        )
    output_path = str(windows_proof.get("outputPath") or "").strip()
    local_copy_path = str(windows_proof.get("localCopyPath") or "").strip()
    if not output_path:
        findings.append("windowsProof.outputPath is missing")
    if not local_copy_path:
        findings.append("windowsProof.localCopyPath is missing")
    failure_stages = windows_proof.get("failureStages") if isinstance(windows_proof.get("failureStages"), dict) else {}
    for stage_id in WINDOWS_LIVE_PROOF_FAILURE_STAGE_IDS:
        stage = failure_stages.get(stage_id) if isinstance(failure_stages, dict) else None
        if not isinstance(stage, dict) or stage.get("stage") != stage_id:
            findings.append(f"failureStages.{stage_id} is missing or invalid")
    readiness_gates = windows_proof.get("readinessGates") if isinstance(windows_proof.get("readinessGates"), dict) else {}
    for gate_id in WINDOWS_LIVE_PROOF_READINESS_GATE_IDS:
        gate = readiness_gates.get(gate_id) if isinstance(readiness_gates, dict) else None
        if not isinstance(gate, dict) or not gate.get("stage"):
            findings.append(f"readinessGates.{gate_id} is missing or invalid")
    mcp_gate = readiness_gates.get("mcpExternalWrite") if isinstance(readiness_gates, dict) else {}
    if not isinstance(mcp_gate, dict) or mcp_gate.get("stage") != "mcp_external_write" or mcp_gate.get("proofFacet") != "mcpExternalWriteReady":
        findings.append("readinessGates.mcpExternalWrite must bind mcp_external_write to mcpExternalWriteReady")
    return findings


def _windows_artifact_summary_contract_findings(summary: dict[str, object], handoff: dict[str, object]) -> list[str]:
    findings: list[str] = []
    windows_proof = handoff.get("windowsProof") if isinstance(handoff.get("windowsProof"), dict) else {}
    expected_facets = windows_proof.get("requiredProofFacets")
    expected_set = {str(item) for item in expected_facets} if isinstance(expected_facets, list) else set(REQUIRED_WINDOWS_PROOF_FACETS)
    summary_facets = summary.get("requiredProofFacets")
    summary_set = {str(item) for item in summary_facets} if isinstance(summary_facets, list) else set()
    if summary_set != expected_set:
        findings.append("Windows artifact requiredProofFacets do not match the current handoff")
    if summary.get("proofFacetCount") != len(expected_set):
        findings.append(
            "Windows artifact proofFacetCount mismatch: "
            f"artifact={summary.get('proofFacetCount')}; expected={len(expected_set)}"
        )
    if summary.get("missingProofFacetCount") not in (0, None):
        findings.append(f"Windows artifact has missing proof facets: {summary.get('missingProofFacets')}")
    return findings


def _normalize_windows_artifact_source_path(value: object) -> str:
    text = str(value or "").strip().strip("\"'")
    return text.replace("/", "\\").rstrip("\\").lower()


def _accept_windows_local_artifact_findings(
    local_artifacts: dict[str, object],
    *,
    parity_run_id: str,
    source_fingerprint: str,
) -> list[str]:
    findings: list[str] = []
    for label in ("macos", "handoff", "windowsLocal"):
        item = local_artifacts.get(label) if isinstance(local_artifacts.get(label), dict) else {}
        if not isinstance(item, dict) or item.get("usable") is not True:
            findings.append(f"localArtifacts.{label}.usable must be true")
    windows_item = local_artifacts.get("windowsLocal") if isinstance(local_artifacts.get("windowsLocal"), dict) else {}
    if isinstance(windows_item, dict):
        if windows_item.get("parityRunId") != parity_run_id:
            findings.append(
                "localArtifacts.windowsLocal.parityRunId mismatch: "
                f"artifact={windows_item.get('parityRunId')}; expected={parity_run_id}"
            )
        if windows_item.get("sourceFingerprint") != source_fingerprint:
            findings.append(
                "localArtifacts.windowsLocal.sourceFingerprint mismatch: "
                f"artifact={windows_item.get('sourceFingerprint')}; expected={source_fingerprint}"
            )
        if windows_item.get("sourceStatus") != "current":
            findings.append(
                "localArtifacts.windowsLocal.sourceStatus must be current: "
                f"got={windows_item.get('sourceStatus')}"
            )
    return findings


def _accept_windows_report_findings(
    audit_payload: dict[str, object],
    source: dict[str, object],
    *,
    parity_run_id: str,
    macos_summary: dict[str, object],
    windows_summary: dict[str, object],
) -> list[str]:
    findings: list[str] = []
    report = audit_payload.get("report") if isinstance(audit_payload.get("report"), dict) else {}
    details = report.get("details") if isinstance(report.get("details"), dict) else {}
    proof_id = details.get("proofId")
    expected_proof_id = details.get("expectedProofId")
    if report.get("ok") is not True:
        findings.append("report.ok must be true after accept-windows")
    if not (
        isinstance(proof_id, str)
        and re.fullmatch(r"[0-9a-f]{64}", proof_id)
        and proof_id == expected_proof_id
    ):
        findings.append("report.details.proofId must match expectedProofId after accept-windows")
    if details.get("currentSourceFingerprint") != source.get("sourceFingerprint"):
        findings.append("report.details.currentSourceFingerprint must match the accepted source")
    if details.get("currentSourceManifestSha256") != source.get("sourceManifestSha256"):
        findings.append("report.details.currentSourceManifestSha256 must match the accepted source")
    if details.get("currentSourceFileCount") != source.get("sourceFileCount"):
        findings.append("report.details.currentSourceFileCount must match the accepted source")
    if source.get("gitHead") and details.get("currentGitHead") != source.get("gitHead"):
        findings.append("report.details.currentGitHead must match the accepted checkout")
    if details.get("parityRunId") != parity_run_id:
        findings.append("report.details.parityRunId must match the accepted handoff")
    artifact_shas = details.get("artifactSha256") if isinstance(details.get("artifactSha256"), dict) else {}
    artifact_bytes = details.get("artifactBytes") if isinstance(details.get("artifactBytes"), dict) else {}
    for label, summary in (("macos", macos_summary), ("windows", windows_summary)):
        expected_sha = summary.get("artifactSha256")
        if not (
            isinstance(expected_sha, str)
            and re.fullmatch(r"[0-9a-f]{64}", expected_sha)
            and artifact_shas.get(label) == expected_sha
        ):
            findings.append(f"report.details.artifactSha256.{label} must match the accepted artifact")
        expected_bytes = summary.get("artifactBytes")
        if not (
            isinstance(expected_bytes, int)
            and not isinstance(expected_bytes, bool)
            and expected_bytes > 0
            and artifact_bytes.get(label) == expected_bytes
        ):
            findings.append(f"report.details.artifactBytes.{label} must match the accepted artifact")
    host_fingerprints = details.get("hostFingerprint") if isinstance(details.get("hostFingerprint"), dict) else {}
    host_platforms = details.get("hostPlatform") if isinstance(details.get("hostPlatform"), dict) else {}
    host_names = details.get("hostName") if isinstance(details.get("hostName"), dict) else {}
    for label, expected_platform in (("macos", "darwin"), ("windows", "win32")):
        fingerprint = host_fingerprints.get(label)
        if not (isinstance(fingerprint, str) and re.fullmatch(r"[0-9a-f]{64}", fingerprint)):
            findings.append(f"report.details.hostFingerprint.{label} must be recorded")
        if host_platforms.get(label) != expected_platform:
            findings.append(f"report.details.hostPlatform.{label} must be {expected_platform}")
        if not str(host_names.get(label) or "").strip():
            findings.append(f"report.details.hostName.{label} must be recorded")
    return findings


def accept_windows_artifact(
    *,
    artifact: str,
    handoff_path: str,
    output: str,
    max_artifact_age_hours: float,
    windows_source_path: str | None,
) -> dict[str, object]:
    source = current_source_summary()
    if not isinstance(source, dict):
        raise StepFailure("Could not compute current HostBridge source summary")
    handoff = _current_handoff_payload(handoff_path, source)
    macos_artifact = handoff.get("macosArtifact") if isinstance(handoff.get("macosArtifact"), dict) else {}
    windows_proof = handoff.get("windowsProof") if isinstance(handoff.get("windowsProof"), dict) else {}
    macos_path = str(macos_artifact.get("path") or DEFAULT_MACOS_PROOF_PATH)
    local_copy = str(windows_proof.get("localCopyPath") or DEFAULT_WINDOWS_LOCAL_COPY_PATH)
    parity_run_id = str(macos_artifact.get("parityRunId") or "")
    source_fingerprint = str(source.get("sourceFingerprint") or "")
    source_manifest = str(source.get("sourceManifestSha256") or "")
    source_count = source.get("sourceFileCount")
    if not parity_run_id:
        raise StepFailure("Windows proof handoff does not include a parityRunId")
    if not isinstance(source_count, int):
        raise StepFailure("Current HostBridge source summary does not include sourceFileCount")
    expected_windows_source = str(windows_proof.get("outputPath") or DEFAULT_WINDOWS_PROOF_PATH)
    if windows_source_path and (
        _normalize_windows_artifact_source_path(windows_source_path)
        != _normalize_windows_artifact_source_path(expected_windows_source)
    ):
        raise StepFailure(
            "Windows artifact source path does not match the current handoff",
            next_step=(
                f"Handoff expects {expected_windows_source}; got {windows_source_path}. "
                "Regenerate the handoff for a custom Windows output path or pass the handoff outputPath."
            ),
        )

    macos_summary = _artifact_summary(
        macos_path,
        label="macos",
        expect_parity_run_id=parity_run_id,
        expect_source_fingerprint=source_fingerprint,
        expect_source_manifest_sha256=source_manifest,
        expect_source_file_count=source_count,
        max_artifact_age_hours=max_artifact_age_hours,
    )
    windows_summary = _artifact_summary(
        artifact,
        label="windows",
        expect_parity_run_id=parity_run_id,
        expect_source_fingerprint=source_fingerprint,
        expect_source_manifest_sha256=source_manifest,
        expect_source_file_count=source_count,
        max_artifact_age_hours=max_artifact_age_hours,
    )
    windows_contract_findings = _windows_artifact_summary_contract_findings(windows_summary, handoff)
    if windows_contract_findings:
        raise StepFailure(
            "Windows artifact does not satisfy the current OpenClaw handoff contract",
            next_step="; ".join(windows_contract_findings[:6]),
        )

    artifact_path = Path(artifact).expanduser()
    if not artifact_path.is_absolute():
        artifact_path = ROOT / artifact_path
    local_path = Path(local_copy).expanduser()
    if not local_path.is_absolute():
        local_path = ROOT / local_path
    copied = False
    if artifact_path.resolve() != local_path.resolve():
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact_path, local_path)
        copied = True

    output_path = resolve_repo_output_path(output)
    backend_report_path = is_backend_parity_report_path(output_path)
    if not backend_report_path:
        raise StepFailure(
            "accept-windows must write the backend parity report path",
            next_step="Use automation report manually for historical/custom output; accept-windows is the current OpenClaw gate.",
        )
    original_windows_source = windows_source_path or expected_windows_source
    report_command = uv_python_command("ops/host_bridge_parity_report.py")
    report_command.extend([
        "--macos",
        macos_path,
        "--windows",
        str(local_path),
        "--output",
        str(output_path),
        "--max-artifact-age-hours",
        str(max_artifact_age_hours),
        "--windows-source-path",
        original_windows_source,
    ])
    report_result = run(report_command, cwd=ROOT, timeout=60)
    if report_result.returncode != 0:
        detail = redact_text((report_result.stderr or report_result.stdout).strip())
        raise StepFailure("HostBridge parity report failed after accepting Windows artifact", next_step=detail[:1200])

    ok, raw, audit_payload = backend_json("/api/host-bridge/parity", timeout=HOST_BRIDGE_PARITY_TIMEOUT_SECONDS)
    if not ok or not isinstance(audit_payload, dict):
        raise StepFailure(
            "Accepted Windows artifact and wrote the parity report, but backend audit is unavailable",
            next_step=raw[:1200] if isinstance(raw, str) else "Start the backend and rerun automation audit.",
        )
    local_artifacts = collect_local_proof_artifacts(source)
    audit_payload = normalize_parity_payload_for_cli(audit_payload, current_source=source)
    audit_payload["localArtifacts"] = local_artifacts
    audit_payload["commands"] = align_parity_commands_with_local_artifacts(audit_payload.get("commands"), local_artifacts)
    contract = audit_payload.get("contract") if isinstance(audit_payload.get("contract"), dict) else {}
    local_findings = _accept_windows_local_artifact_findings(
        local_artifacts,
        parity_run_id=parity_run_id,
        source_fingerprint=source_fingerprint,
    )
    report_findings = _accept_windows_report_findings(
        audit_payload,
        source,
        parity_run_id=parity_run_id,
        macos_summary=macos_summary,
        windows_summary=windows_summary,
    )
    accept_findings = [*report_findings, *local_findings]
    if accept_findings:
        audit_payload["ok"] = False
        audit_payload["status"] = "cross_os_unverified"
        audit_payload.setdefault("gaps", [])
        gaps = audit_payload.get("gaps")
        if isinstance(gaps, list):
            gaps.extend(accept_findings)
    audit_passed = (
        audit_payload.get("ok") is True
        and contract.get("status") == "cross_os_verified"
        and not accept_findings
    )
    return {
        "ok": audit_passed,
        "status": audit_payload.get("status"),
        "parityRunId": parity_run_id,
        "sourceFingerprint": source_fingerprint,
        "windowsArtifact": str(artifact_path),
        "windowsLocalCopy": str(local_path),
        "copied": copied,
        "reportPath": str(output_path),
        "macosProofFacetCount": macos_summary.get("proofFacetCount"),
        "windowsProofFacetCount": windows_summary.get("proofFacetCount"),
        "windowsMissingProofFacetCount": windows_summary.get("missingProofFacetCount"),
        "audit": audit_payload,
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


def _local_artifact_file_metadata(path: Path) -> dict[str, object]:
    try:
        data = path.read_bytes()
    except OSError:
        return {}
    return {
        "artifactBytes": len(data),
        "artifactSha256": hashlib.sha256(data).hexdigest(),
    }


def _source_fingerprint_status(value: object, current_source: dict[str, object]) -> str:
    current = current_source.get("sourceFingerprint")
    if not isinstance(value, str) or not value.strip():
        return "missing"
    if isinstance(current, str) and value == current:
        return "current"
    return "stale"


def _mark_artifact_usability(item: dict[str, object]) -> None:
    source_status = item.get("sourceStatus")
    contract_status = item.get("contractStatus")
    exists = item.get("exists") is True
    ok = item.get("ok") is True
    contract_current = contract_status in (None, "current")
    item["usable"] = bool(exists and ok and source_status == "current" and contract_current)
    item["refreshRequired"] = source_status == "stale" or contract_status == "stale"


def _failed_artifact_checks(payload: dict[str, object]) -> list[str]:
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        mode = str(payload.get("mode") or "").strip()
        if payload.get("ok") is False and mode == "windows_live_proof_failed":
            return ["windows_live_proof_failed"]
        return ["artifact ok=false"] if payload.get("ok") is False else []
    failed: list[str] = []
    for name, value in checks.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            continue
        return_code = value.get("returnCode")
        if (
            value.get("ok") is False
            or value.get("verified") is False
            or (return_code not in (None, 0))
            or bool(value.get("error"))
            or bool(value.get("stderr"))
        ):
            failed.append(name)
    if not failed and payload.get("ok") is False:
        failed.append("artifact ok=false")
    return failed


def _artifact_source_fingerprint(payload: dict[str, object]) -> object:
    artifact_source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    nested = artifact_source.get("sourceFingerprint")
    if isinstance(nested, str) and nested.strip():
        return nested
    top_level = payload.get("sourceFingerprint")
    if isinstance(top_level, str) and top_level.strip():
        return top_level
    preflight = payload.get("preflight") if isinstance(payload.get("preflight"), dict) else {}
    preflight_value = preflight.get("sourceFingerprint")
    if isinstance(preflight_value, str) and preflight_value.strip():
        return preflight_value
    return top_level


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
            fingerprint = _artifact_source_fingerprint(payload)
            item.update({
                "ok": payload.get("ok") is True,
                "mode": payload.get("mode"),
                "parityRunId": payload.get("parityRunId"),
                "sourceFingerprint": fingerprint,
                "sourceStatus": _source_fingerprint_status(fingerprint, source),
                "generatedAt": payload.get("generatedAt"),
            })
            item.update(_local_artifact_file_metadata(path))
            error_text = str(payload.get("error") or "").strip()
            if error_text:
                item["error"] = error_text[:240]
            failed_stage = str(payload.get("failedStage") or "").strip()
            if failed_stage:
                item["failureStage"] = failed_stage
            next_steps = payload.get("nextSteps")
            if isinstance(next_steps, dict):
                item["failureNextSteps"] = next_steps
            partial_artifact = payload.get("partialArtifact")
            if isinstance(partial_artifact, dict):
                item["failurePartialArtifact"] = partial_artifact
            preflight = payload.get("preflight") if isinstance(payload.get("preflight"), dict) else {}
            os_preflight = preflight.get("os") if isinstance(preflight.get("os"), dict) else {}
            if os_preflight:
                item["preflight"] = {
                    "sessionName": os_preflight.get("sessionName"),
                    "isElevated": os_preflight.get("isElevated"),
                    "isWindows": os_preflight.get("isWindows"),
                }
            failed_checks = _failed_artifact_checks(payload)
            if failed_checks:
                item["failedChecks"] = failed_checks[:8]
        _mark_artifact_usability(item)
        artifacts[label] = item
    handoff, handoff_error = _load_json_file(handoff_path)
    handoff_item: dict[str, object] = {"path": str(handoff_path), "exists": handoff is not None}
    if handoff_error:
        handoff_item["status"] = handoff_error
    if isinstance(handoff, dict):
        handoff_source = handoff.get("source") if isinstance(handoff.get("source"), dict) else {}
        macos_artifact = handoff.get("macosArtifact") if isinstance(handoff.get("macosArtifact"), dict) else {}
        windows_proof = handoff.get("windowsProof") if isinstance(handoff.get("windowsProof"), dict) else {}
        windows_commands = windows_proof.get("commands") if isinstance(windows_proof.get("commands"), dict) else {}
        operator_checklist = windows_proof.get("operatorChecklist") if isinstance(windows_proof.get("operatorChecklist"), list) else []
        required_facets = (
            windows_proof.get("requiredProofFacets")
            if isinstance(windows_proof.get("requiredProofFacets"), list)
            else []
        )
        proof_facet_count = windows_proof.get("proofFacetCount")
        if not isinstance(proof_facet_count, int) or proof_facet_count < 0:
            proof_facet_count = len(required_facets)
        fingerprint = handoff_source.get("sourceFingerprint")
        handoff_source_status = _source_fingerprint_status(fingerprint, source)
        handoff_item.update({
            "ok": handoff.get("ok") is True,
            "kind": handoff.get("kind"),
            "parityRunId": macos_artifact.get("parityRunId"),
            "sourceFingerprint": fingerprint,
            "sourceStatus": handoff_source_status,
            "generatedAt": handoff.get("generatedAt"),
            "proofFacetCount": proof_facet_count,
        })
        if required_facets:
            handoff_item["requiredProofFacets"] = required_facets
        contract_findings = _windows_handoff_contract_findings(handoff)
        handoff_item["contractStatus"] = "stale" if contract_findings else "current"
        if contract_findings:
            handoff_item["contractFindings"] = contract_findings[:8]
        windows_item = artifacts.get("windowsLocal")
        if isinstance(windows_item, dict):
            windows_item["expectedProofFacetCount"] = proof_facet_count
            windows_item["expectedContractStatus"] = handoff_item["contractStatus"]
            if contract_findings:
                windows_item["contractStatus"] = "stale"
                windows_item["contractFindings"] = contract_findings[:8]
            if required_facets:
                windows_item["requiredProofFacets"] = required_facets
            if windows_item.get("exists") is not True:
                windows_item["sourceStatus"] = handoff_source_status
                windows_item["refreshRequired"] = handoff_source_status == "stale"
                windows_item["usable"] = False
            if isinstance(windows_proof.get("outputPath"), str):
                windows_item["copySourcePath"] = windows_proof["outputPath"]
            if isinstance(windows_proof.get("copyInstruction"), str):
                windows_item["copyInstruction"] = windows_proof["copyInstruction"]
            if isinstance(windows_commands.get("artifactValidate"), str):
                windows_item["validateOnWindowsCommand"] = windows_commands["artifactValidate"]
            if isinstance(windows_commands.get("liveProof"), str):
                windows_item["liveProofCommand"] = windows_commands["liveProof"]
            if operator_checklist:
                normalized_checklist = [dict(item) for item in operator_checklist if isinstance(item, dict)]
                accept_command = str(windows_commands.get("acceptWindowsArtifact") or "").strip()
                if not accept_command:
                    windows_source_path = str(windows_proof.get("outputPath") or DEFAULT_WINDOWS_PROOF_PATH)
                    accept_command = (
                        f"{local_cli_command('automation', 'accept-windows')} "
                        f"{shell_quote(str(windows_path))} "
                        f"--handoff {shell_quote(DEFAULT_WINDOWS_HANDOFF_PATH)} "
                        "--max-artifact-age-hours 24.0 "
                        f"--windows-source-path {shell_quote(windows_source_path)}"
                    )
                if (
                    not any(item.get("id") == "accept_windows_artifact" for item in normalized_checklist)
                    and accept_command
                ):
                    accept_item = {
                        "id": "accept_windows_artifact",
                        "host": "repo",
                        "command": accept_command,
                        "successCondition": "artifact validates, is installed at the local copy path, report is generated, and audit passes",
                    }
                    insert_at = next(
                        (idx for idx, item in enumerate(normalized_checklist) if item.get("id") in {"generate_report", "audit_gate"}),
                        len(normalized_checklist),
                    )
                    normalized_checklist.insert(insert_at, accept_item)
                windows_item["operatorChecklist"] = normalized_checklist
            if isinstance(macos_artifact.get("parityRunId"), str):
                windows_item["expectedParityRunId"] = macos_artifact["parityRunId"]
            if isinstance(fingerprint, str):
                windows_item["expectedSourceFingerprint"] = fingerprint
            _mark_artifact_usability(windows_item)
    _mark_artifact_usability(handoff_item)
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
            parts = [f"exists={exists}", f"status={status or 'missing'}"]
            source_status = item.get("sourceStatus")
            if isinstance(source_status, str) and source_status:
                parts.append(f"source={source_status}")
            contract_status = item.get("contractStatus") or item.get("expectedContractStatus")
            if isinstance(contract_status, str) and contract_status:
                parts.append(f"contract={contract_status}")
            if item.get("refreshRequired") is True:
                parts.append("refreshRequired=true")
            if label == "windowsLocal":
                copy_source = item.get("copySourcePath")
                expected_run = item.get("expectedParityRunId")
                expected_facets = item.get("expectedProofFacetCount")
                if isinstance(copy_source, str) and copy_source:
                    parts.append(f"copyFrom={copy_source}")
                if isinstance(expected_run, str) and expected_run:
                    parts.append(f"run={expected_run}")
                if isinstance(expected_facets, int) and expected_facets > 0:
                    parts.append(f"proofFacets={expected_facets}")
            lines.append(f"{label}: " + ", ".join(parts))
            continue
        parts = [
            f"exists={exists}",
            f"ok={bool_text(item.get('ok'))}",
            f"source={item.get('sourceStatus') or 'unknown'}",
            f"usable={bool_text(item.get('usable'))}",
        ]
        contract_status = item.get("contractStatus") or item.get("expectedContractStatus")
        if isinstance(contract_status, str) and contract_status:
            parts.append(f"contract={contract_status}")
        if item.get("refreshRequired") is True:
            parts.append("refreshRequired=true")
        contract_findings = item.get("contractFindings")
        if isinstance(contract_findings, list) and contract_findings:
            parts.append(f"contractFinding={str(contract_findings[0])[:120]}")
        failed_checks = item.get("failedChecks")
        if isinstance(failed_checks, list) and failed_checks:
            parts.append("failed=" + "|".join(str(check) for check in failed_checks[:3]))
        failure_stage = str(item.get("failureStage") or "").strip()
        if failure_stage:
            parts.append(f"failureStage={failure_stage}")
        error_text = str(item.get("error") or "").strip()
        if error_text:
            parts.append(f"error={error_text[:120]}")
        preflight = item.get("preflight")
        if isinstance(preflight, dict):
            session_name = preflight.get("sessionName")
            is_windows = preflight.get("isWindows")
            if session_name is not None:
                parts.append(f"session={session_name}")
            if is_windows is not None:
                parts.append(f"isWindows={bool_text(is_windows)}")
        run_id = item.get("parityRunId")
        if isinstance(run_id, str) and run_id:
            parts.append(f"run={run_id}")
        artifact_bytes = item.get("artifactBytes")
        if isinstance(artifact_bytes, int) and not isinstance(artifact_bytes, bool) and artifact_bytes > 0:
            parts.append(f"artifactBytes={artifact_bytes}")
        artifact_sha = item.get("artifactSha256")
        if isinstance(artifact_sha, str) and re.fullmatch(r"[0-9a-f]{64}", artifact_sha):
            parts.append(f"artifactSha256={artifact_sha[:12]}")
        proof_facets = item.get("proofFacetCount") or item.get("expectedProofFacetCount")
        if isinstance(proof_facets, int) and proof_facets > 0:
            parts.append(f"proofFacets={proof_facets}")
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
                if key in {"macosSourceValidate", "windowsSourceValidate"} and "automation source" in value and "--json" not in value:
                    value = f"{value} --json"
                if key in {"windowsArtifactValidateOnWindows", "windowsArtifactValidateLocal"} and "automation artifact" in value and "--json" not in value:
                    value = f"{value} --json"
                if key == "windowsProbe" and "automation windows-probe" in value:
                    if "--output" in value:
                        value = re.sub(
                            r"--output\s+(?:\"[^\"]*\"|'[^']*'|\S+)",
                            lambda _match: f"--output {DEFAULT_WINDOWS_PROBE_PATH}",
                            value,
                        )
                    else:
                        value = f"{value} --output {DEFAULT_WINDOWS_PROBE_PATH}"
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


def _reusable_handoff_parity_run_id(local_artifacts: object) -> str | None:
    if not isinstance(local_artifacts, dict):
        return None
    handoff = local_artifacts.get("handoff")
    if not isinstance(handoff, dict):
        return None
    run_id = handoff.get("parityRunId")
    if (
        isinstance(run_id, str)
        and run_id.strip()
        and handoff.get("usable") is True
        and handoff.get("sourceStatus") == "current"
    ):
        return run_id
    return None


def align_parity_commands_with_local_artifacts(commands: object, local_artifacts: object) -> dict[str, object]:
    if not isinstance(commands, dict):
        return {}
    normalized = dict(commands)
    reusable_run_id = _reusable_handoff_parity_run_id(local_artifacts)
    existing_run_id = normalized.get("parityRunId")
    if (
        isinstance(reusable_run_id, str)
        and isinstance(existing_run_id, str)
        and existing_run_id
        and existing_run_id != reusable_run_id
    ):
        for key, value in list(normalized.items()):
            if isinstance(value, str):
                normalized[key] = value.replace(existing_run_id, reusable_run_id)
        normalized["backendParityRunId"] = existing_run_id
        normalized["parityRunId"] = reusable_run_id
        normalized["parityRunIdSource"] = "local_handoff"
    return normalized


def build_windows_operator_checklist_from_commands(commands: object) -> list[dict[str, object]]:
    if not isinstance(commands, dict):
        return []
    checklist: list[dict[str, object]] = []

    def add_command(step_id: str, label: str, command_key: str) -> None:
        if any(item.get("id") == step_id for item in checklist):
            return
        command = commands.get(command_key)
        if isinstance(command, str) and command.strip():
            checklist.append({"id": step_id, "label": label, "command": command})

    add_command("source_validate", "Validate the checked-out source on Windows", "windowsSourceValidate")
    add_command("mcp_gateway_setup", "Prepare MCP external-write gateway settings on Windows", "mcpGatewaySetupJson")
    add_command("mcp_gateway_probe", "Probe MCP external-write gateway readiness on Windows", "mcpGatewayProbeJson")
    add_command("mcp_gateway_status", "Validate MCP connector catalog readiness on Windows", "mcpGatewayStatusJson")
    add_command("native_browser_desktop_smoke", "Run native Windows browser/desktop smoke diagnostics", "nativeBrowserDesktopSmoke")
    add_command("windows_raw_probe", "Run raw Windows HostBridge probe diagnostics", "windowsProbe")
    add_command("windows_live_proof", "Run the native Windows live proof", "windowsLiveProofRunner")
    add_command("artifact_validate_on_windows", "Validate the Windows proof artifact on Windows", "windowsArtifactValidateOnWindows")

    copy_hint = commands.get("windowsArtifactCopyHint")
    copy_item: dict[str, object] | None = None
    if isinstance(copy_hint, str) and copy_hint.strip():
        copy_item = {
            "id": "copy_to_repo_host",
            "label": "Copy the Windows proof artifact back to this repo host",
            "command": copy_hint,
        }
    else:
        windows_source = commands.get("windowsProofPath") or commands.get("windowsOutputPath") or DEFAULT_WINDOWS_PROOF_PATH
        local_copy = commands.get("windowsLocalCopyPath") or commands.get("windowsLocalPath") or DEFAULT_WINDOWS_LOCAL_COPY_PATH
        if isinstance(windows_source, str) and isinstance(local_copy, str):
            copy_item = {
                "id": "copy_to_repo_host",
                "label": "Copy the Windows proof artifact back to this repo host",
                "from": windows_source,
                "to": local_copy,
            }
    if copy_item is not None:
        checklist.append(copy_item)

    accept_windows = commands.get("acceptWindowsArtifact")
    if isinstance(accept_windows, str) and accept_windows.strip():
        checklist.append({"id": "accept_windows_artifact", "label": "Accept and install the copied Windows proof artifact", "command": accept_windows})
    report = commands.get("automationReport") or commands.get("report")
    if isinstance(report, str) and report.strip():
        checklist.append({"id": "generate_report", "label": "Generate the cross-OS parity report", "command": report})
    verify = commands.get("verify")
    if isinstance(verify, str) and verify.strip():
        checklist.append({"id": "audit_gate", "label": "Run the OpenClaw Windows audit gate", "command": verify})
    return checklist


def normalize_native_parity_matrix_payload(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    legacy_fallback = normalized.pop("windows" + "SubsystemFallback", None)
    if "windowsNativeHostOnly" not in normalized and legacy_fallback is False:
        normalized["windowsNativeHostOnly"] = True
    return normalized


def normalize_runtime_payload_for_cli(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    v2 = normalized.get("v2")
    if isinstance(v2, dict):
        normalized_v2 = dict(v2)
        native_runtime = normalized_v2.get("nativeRuntime")
        if isinstance(native_runtime, dict):
            normalized_v2["nativeRuntime"] = normalize_native_parity_matrix_payload(native_runtime)
        normalized["v2"] = normalized_v2
    return normalized


def normalize_parity_payload_for_cli(payload: object, *, current_source: dict[str, object] | None = None) -> object:
    if not isinstance(payload, dict):
        return payload
    source_summary = current_source if current_source is not None else current_source_summary()
    normalized = dict(payload)
    native_parity = normalize_native_parity_matrix_payload(normalized.get("nativeParityMatrix"))
    if isinstance(native_parity, dict):
        normalized["nativeParityMatrix"] = native_parity
    commands = normalize_parity_commands(normalized.get("commands"), current_source=source_summary)
    if commands:
        normalized["commands"] = commands
        operator_checklist = build_windows_operator_checklist_from_commands(commands)
        if operator_checklist:
            normalized["windowsProofOperatorChecklist"] = operator_checklist
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
    source = payload.get("currentSource") if isinstance(payload.get("currentSource"), dict) else payload.get("cliSource")
    if isinstance(source, dict):
        fingerprint = str(source.get("sourceFingerprint") or "")
        git_head = str(source.get("gitHead") or "")
        parts = []
        if fingerprint:
            parts.append(f"sourceFingerprint={fingerprint[:12]}")
        if source.get("sourceFileCount") is not None:
            parts.append(f"sourceFileCount={source.get('sourceFileCount')}")
        if git_head:
            parts.append(f"gitHead={git_head[:12]}")
        if parts:
            lines.append("HostBridge proof target: " + ", ".join(parts))
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
    checklist = payload.get("windowsProofOperatorChecklist")
    if not isinstance(checklist, list):
        checklist = build_windows_operator_checklist_from_commands(commands)
    for index, item in enumerate(checklist[:8], start=1):
        if not isinstance(item, dict):
            continue
        step_id = item.get("id") or f"step_{index}"
        command = item.get("command")
        if isinstance(command, str) and command.strip():
            lines.append(f"windowsProofChecklist[{index}].{step_id}={command}")
            continue
        source = item.get("from")
        target = item.get("to")
        if isinstance(source, str) and isinstance(target, str):
            lines.append(f"windowsProofChecklist[{index}].{step_id}=copy {source} -> {target}")
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


def run_winget_install(winget: str, package_id: str, display_name: str, *, dry_run: bool) -> None:
    command = [winget, "install", "--id", package_id, "--exact", "--accept-source-agreements", "--accept-package-agreements"]
    try:
        run_interactive(command, dry_run=dry_run)
        return
    except StepFailure as first_error:
        if dry_run:
            raise
        print_check(False, f"{display_name} winget install", f"retrying after source refresh; {str(first_error)[:180]}")
        try:
            run_interactive([winget, "source", "update"], dry_run=False)
        except StepFailure as source_error:
            print_check(False, "winget source update", f"{str(source_error)[:180]}; retrying install anyway")
        try:
            run_interactive(command, dry_run=False)
        except StepFailure as retry_error:
            raise StepFailure(
                f"{display_name} winget install failed after source refresh",
                next_step=(
                    f"First attempt: {str(first_error)[:400]}\n"
                    f"Retry: {str(retry_error)[:400]}\n"
                    f"Install {display_name} manually, then run .\\atrium.ps1 setup again."
                ),
            ) from retry_error


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
            run_winget_install(winget, package_id, f"{tool} for Windows", dry_run=dry_run)
        ensure_common_paths()
    if not command_path("python3"):
        if not winget:
            raise StepFailure(
                "Python 3 is missing",
                next_step="Install Python 3 for Windows, restart PowerShell, then run .\\atrium.ps1 setup again.",
            )
        run_winget_install(winget, "Python.Python.3.12", "Python 3", dry_run=dry_run)
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
        run_winget_install(winget, "Docker.DockerDesktop", "Docker Desktop", dry_run=dry_run)
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
            run_winget_install(winget, "Google.Chrome", "Google Chrome", dry_run=dry_run)
        except StepFailure as exc:
            print_check(False, "Chromium browser", f"winget install failed; install Chrome/Edge/Brave/Chromium manually, then run .\\atrium.ps1 automation status --commands; {str(exc)[:180]}")
    if not command_path("claude"):
        winget_error = ""
        if winget:
            try:
                run_winget_install(winget, "Anthropic.ClaudeCode", "Claude Code", dry_run=dry_run)
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


def launcher_mode() -> str:
    if windows_native():
        return "windows-native"
    system = platform.system()
    if system == "Darwin":
        return "macos-screen"
    if system == "Linux":
        return "linux-screen"
    return f"{system.lower() or 'unknown'}-screen"


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


def windows_process_identity(pid: int | None) -> dict[str, object] | None:
    if platform.system() != "Windows" or not pid or pid <= 0:
        return None
    powershell = powershell_command()
    if not powershell:
        return {"ok": False, "error": "PowerShell unavailable"}
    script = (
        "$p = Get-CimInstance Win32_Process -Filter "
        f"{ps_single_quote(f'ProcessId = {pid}')}"
        " -ErrorAction SilentlyContinue | Select-Object -First 1 "
        "ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine; "
        "if ($null -eq $p) { exit 1 }; "
        "$p | ConvertTo-Json -Compress"
    )
    result = run([powershell, "-NoProfile", "-Command", script], timeout=5)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "process not found").strip()[:240]
        return {"ok": False, "error": detail}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "Unable to parse process identity"}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "Unexpected process identity payload"}
    payload["ok"] = True
    redacted = redact_json_value(payload)
    if isinstance(redacted, dict):
        return redacted
    return {"ok": False, "error": "Unexpected process identity after redaction"}


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
    detail: dict[str, object] = {
        "label": label,
        "pid": pid,
        "running": running,
        "pidFile": str(pid_path),
        "pidFileExists": pid_path.exists(),
        "logPath": str(log_path),
        "logExists": log_path.exists(),
        "ownedByAtriumLauncher": pid is not None,
    }
    identity = windows_process_identity(pid)
    if identity is not None:
        detail["processIdentity"] = identity
    return detail


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


def collect_process_payload() -> dict[str, object]:
    return {
        "mode": launcher_mode(),
        "summary": windows_process_status() if windows_native() else screen_sessions().replace("\n", " | "),
        "details": windows_process_details() if windows_native() else None,
    }


def collect_windows_runtime_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "windowsNative": windows_native(),
        "platform": platform.platform(),
        "launcher": local_cli_command("status"),
        "sessionName": os.environ.get("SESSIONNAME"),
    }
    if not windows_native():
        return payload
    powershell = powershell_command()
    payload["powershell"] = {
        "command": powershell,
        "version": None,
    }
    if powershell:
        result = run([powershell, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"], timeout=5)
        payload["powershell"]["version"] = result.stdout.strip() if result.returncode == 0 else None
        payload["powershell"]["error"] = result.stderr.strip()[:240] if result.returncode != 0 else None
    payload["commands"] = {name: report_command_path(name) for name in report_tool_names()}
    payload["dockerDesktop"] = [
        {
            "path": str(candidate),
            "exists": candidate.exists(),
        }
        for candidate in (
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe",
            Path(os.environ.get("LocalAppData", "")) / "Docker" / "Docker Desktop.exe",
        )
    ]
    payload["process"] = collect_process_payload()
    return payload


def _source_file_metadata(path: Path) -> dict[str, object]:
    digest, size = file_sha256_and_size(path)
    payload: dict[str, object] = {
        "path": str(path),
        "relativePath": rel(path),
        "exists": path.exists(),
        "sha256": digest,
        "bytes": size,
    }
    try:
        payload["modifiedAt"] = int(path.stat().st_mtime)
    except OSError:
        payload["modifiedAt"] = None
    return payload


def _read_source_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def windows_openclaw_lifecycle_commands() -> dict[str, object]:
    windows_artifact = DEFAULT_WINDOWS_PROOF_PATH
    local_windows_artifact = DEFAULT_WINDOWS_LOCAL_COPY_PATH
    macos_artifact = DEFAULT_MACOS_PROOF_PATH
    handoff_artifact = DEFAULT_WINDOWS_HANDOFF_PATH
    native_setup = ".\\atrium.ps1 setup --yes"
    native_start = ".\\atrium.ps1 start"
    native_status = ".\\atrium.ps1 status --json"
    native_logs = ".\\atrium.ps1 logs --json"
    native_report = ".\\atrium.ps1 report --bundle"
    native_stop = ".\\atrium.ps1 stop"
    native_restart = ".\\atrium.ps1 restart --force"
    native_permissions_status = ".\\atrium.ps1 permissions status --json"
    native_permissions_set = ".\\atrium.ps1 permissions set full_auto --agent-full-access true"
    native_provider_status = ".\\atrium.ps1 provider status --probe --json"
    native_provider_reference = ".\\atrium.ps1 provider reference --json"
    native_provider_env = ".\\atrium.ps1 provider env --json"
    native_provider_login_chatgpt = ".\\atrium.ps1 provider login chatgpt"
    native_provider_login_claude = ".\\atrium.ps1 provider login claude-code"
    native_provider_disconnect_chatgpt = ".\\atrium.ps1 provider disconnect chatgpt"
    native_provider_disconnect_claude = ".\\atrium.ps1 provider disconnect claude-code"
    native_tools_status = ".\\atrium.ps1 tools status --json"
    native_tools_catalog = ".\\atrium.ps1 tools catalog --json"
    mcp_gateway_setup = ".\\atrium.ps1 tools mcp-gateway --json"
    mcp_gateway_status = ".\\atrium.ps1 tools status --json"
    mcp_gateway_probe = ".\\atrium.ps1 tools mcp-probe --json"
    native_browser_desktop_smoke = f".\\atrium.ps1 automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium --output {DEFAULT_WINDOWS_SMOKE_PATH}"
    windows_probe = f".\\atrium.ps1 automation windows-probe --full --browser-url http://127.0.0.1:5173 --browser-profile atrium --output {DEFAULT_WINDOWS_PROBE_PATH}"
    windows_source_validate = (
        ".\\atrium.ps1 automation source "
        "--expect-source-fingerprint <fingerprint> "
        "--expect-source-manifest-sha256 <manifest-sha256> "
        "--expect-source-file-count <count> --json"
    )
    windows_live_proof = (
        ".\\atrium.ps1 automation windows-live-proof "
        "--parity-run-id <parity-run-id> "
        "--source-fingerprint <fingerprint> "
        "--source-manifest-sha256 <manifest-sha256> "
        "--source-file-count <count> "
        "--max-artifact-age-hours 24.0 "
        f"--output {ps_single_quote(windows_artifact)}"
    )
    windows_artifact_validate = (
        ".\\atrium.ps1 automation artifact --label windows "
        "--expect-parity-run-id <parity-run-id> "
        "--expect-source-fingerprint <fingerprint> "
        "--expect-source-manifest-sha256 <manifest-sha256> "
        "--expect-source-file-count <count> "
        "--max-artifact-age-hours 24.0 --json "
        f"{ps_single_quote(windows_artifact)}"
    )
    report = (
        f"{local_cli_command('automation', 'report')} "
        f"--macos {shell_quote(macos_artifact)} "
        f"--windows {shell_quote(local_windows_artifact)} "
        "--max-artifact-age-hours 24.0 "
        f"--windows-source-path {shell_quote(windows_artifact)}"
    )
    accept_windows = (
        f"{local_cli_command('automation', 'accept-windows')} "
        f"{shell_quote(local_windows_artifact)} "
        f"--handoff {shell_quote(handoff_artifact)} "
        "--max-artifact-age-hours 24.0 "
        f"--windows-source-path {shell_quote(windows_artifact)}"
    )
    commands = {
        "nativeSetup": native_setup,
        "nativeStart": native_start,
        "nativeStatusJson": native_status,
        "nativeLogsJson": native_logs,
        "nativeReportBundle": native_report,
        "nativeStop": native_stop,
        "nativeRestart": native_restart,
        "nativePermissionsStatusJson": native_permissions_status,
        "nativePermissionsSetFullAuto": native_permissions_set,
        "nativeProviderStatusProbeJson": native_provider_status,
        "nativeProviderReferenceJson": native_provider_reference,
        "nativeProviderEnvJson": native_provider_env,
        "nativeProviderLoginChatGPT": native_provider_login_chatgpt,
        "nativeProviderLoginClaudeCode": native_provider_login_claude,
        "nativeProviderDisconnectChatGPT": native_provider_disconnect_chatgpt,
        "nativeProviderDisconnectClaudeCode": native_provider_disconnect_claude,
        "nativeToolsStatusJson": native_tools_status,
        "nativeToolsCatalogJson": native_tools_catalog,
        "mcpGatewaySetupJson": mcp_gateway_setup,
        "mcpGatewayStatusJson": mcp_gateway_status,
        "mcpGatewayProbeJson": mcp_gateway_probe,
        "nativeBrowserDesktopSmoke": native_browser_desktop_smoke,
        "windowsProbe": windows_probe,
        "sourceValidate": windows_source_validate,
        "handoff": (
            f"{local_cli_command('automation', 'handoff')} "
            f"--macos {shell_quote(macos_artifact)} "
            f"--output {shell_quote(handoff_artifact)} "
            f"--windows-output {shell_quote(windows_artifact)} "
            f"--windows-local-copy {shell_quote(local_windows_artifact)}"
        ),
        "windowsLiveProof": windows_live_proof,
        "windowsArtifactValidate": windows_artifact_validate,
        "windowsArtifactValidateOnWindows": windows_artifact_validate,
        "copyToRepoHost": f"Copy {windows_artifact} from Windows to {local_windows_artifact} on this repo host.",
        "acceptWindowsArtifact": accept_windows,
        "report": report,
        "audit": local_cli_command("automation", "audit"),
    }
    checklist = [
        {"id": "native_setup_start", "host": "windows", "command": f"{native_setup}; {native_start}; {native_status}"},
        {"id": "native_permissions", "host": "windows", "command": f"{native_permissions_status}; {native_permissions_set}; {native_permissions_status}"},
        {
            "id": "native_provider_ai_tools",
            "host": "windows",
            "command": f"{native_provider_status}; {native_provider_reference}; {native_provider_env}; {native_tools_status}; {native_tools_catalog}",
            "loginCommands": [native_provider_login_chatgpt, native_provider_login_claude],
            "accountSwitchCommands": [native_provider_disconnect_chatgpt, native_provider_disconnect_claude],
        },
        {"id": "native_logs_report", "host": "windows", "command": f"{native_logs}; {native_report}"},
        {"id": "native_stop_restart", "host": "windows", "command": f"{native_stop}; {native_restart}; {native_status}"},
        {"id": "mcp_gateway_setup", "host": "windows", "command": mcp_gateway_setup},
        {"id": "mcp_gateway_probe", "host": "windows", "command": mcp_gateway_probe},
        {"id": "mcp_gateway_status", "host": "windows", "command": mcp_gateway_status},
        {"id": "native_browser_desktop_smoke", "host": "windows", "command": native_browser_desktop_smoke},
        {"id": "windows_raw_probe", "host": "windows", "command": windows_probe},
        {"id": "source_validate", "host": "windows", "command": windows_source_validate},
        {"id": "windows_live_proof", "host": "windows", "command": windows_live_proof},
        {"id": "artifact_validate_on_windows", "host": "windows", "command": windows_artifact_validate},
        {"id": "copy_to_repo_host", "host": "transfer", "command": commands["copyToRepoHost"]},
        {"id": "accept_windows_artifact", "host": "repo", "command": accept_windows},
        {"id": "generate_report", "host": "repo", "command": report},
        {"id": "audit_gate", "host": "repo", "command": commands["audit"]},
    ]
    return {
        "ok": True,
        "commands": commands,
        "operatorChecklist": checklist,
        "requiredGate": "Run accept-windows with the copied Windows artifact; it must validate, write the report, and audit must pass before claiming OpenClaw-level Windows parity.",
    }


def macos_native_next_check_commands() -> list[dict[str, object]]:
    commands = [
        ("doctor_json", "./atrium doctor --json"),
        ("status_json", "./atrium status --json"),
        ("provider_status", "./atrium provider status --probe --json"),
        ("provider_reference", "./atrium provider reference --json"),
        ("provider_env", "./atrium provider env --json"),
        ("provider_login_chatgpt", "./atrium provider login chatgpt"),
        ("provider_login_claude_code", "./atrium provider login claude-code"),
        ("provider_disconnect_chatgpt", "./atrium provider disconnect chatgpt"),
        ("provider_disconnect_claude_code", "./atrium provider disconnect claude-code"),
        ("permissions_status", "./atrium permissions status --json"),
        ("permissions_full_auto", "./atrium permissions set full_auto --agent-full-access true"),
        ("tools_status", "./atrium tools status --json"),
        ("tools_mcp_gateway", "./atrium tools mcp-gateway --json"),
        ("tools_mcp_probe", "./atrium tools mcp-probe --json"),
        ("tools_catalog", "./atrium tools catalog --json"),
        ("automation_status", "./atrium automation status --commands"),
        (
            "automation_smoke",
            "./atrium automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium --output /tmp/atrium_host_bridge_macos_smoke.json",
        ),
        ("automation_probe", "uv --project system run python ops/macos_host_bridge_probe.py --full"),
        ("logs_json", "./atrium logs --json"),
        ("report_bundle", "./atrium report --bundle"),
    ]
    return [{"id": item_id, "host": "macos", "command": command} for item_id, command in commands]


def native_next_checks_payload() -> dict[str, object]:
    if windows_native():
        proof = windows_openclaw_lifecycle_commands()
        commands = proof.get("commands")
        checklist = proof.get("operatorChecklist")
        return {
            "host": "windows",
            "launcherMode": launcher_mode(),
            "commands": commands if isinstance(commands, dict) else {},
            "operatorChecklist": checklist if isinstance(checklist, list) else [],
            "requiredGate": proof.get("requiredGate"),
        }
    host = "macos" if platform.system() == "Darwin" else "non-windows"
    return {
        "host": host,
        "launcherMode": launcher_mode(),
        "operatorChecklist": macos_native_next_check_commands(),
    }


def native_parity_matrix_payload() -> dict[str, object]:
    windows_commands = windows_openclaw_lifecycle_commands().get("commands")
    windows_commands = windows_commands if isinstance(windows_commands, dict) else {}
    macos_commands = {item["id"]: item["command"] for item in macos_native_next_check_commands()}
    surfaces = [
        {
            "id": "install",
            "label": "Native install and setup",
            "macos": ["./atrium setup --yes", "ops/install_macos.sh"],
            "windows": [str(windows_commands.get("nativeSetup") or ".\\atrium.ps1 setup --yes"), "ops/install_windows_native.ps1"],
        },
        {
            "id": "lifecycle",
            "label": "Start, stop, restart, and process truth",
            "macos": ["./atrium start", "./atrium stop", "./atrium restart", str(macos_commands.get("status_json") or "./atrium status --json")],
            "windows": [
                str(windows_commands.get("nativeStart") or ".\\atrium.ps1 start"),
                str(windows_commands.get("nativeStop") or ".\\atrium.ps1 stop"),
                str(windows_commands.get("nativeRestart") or ".\\atrium.ps1 restart --force"),
                str(windows_commands.get("nativeStatusJson") or ".\\atrium.ps1 status --json"),
            ],
        },
        {
            "id": "runtime_status",
            "label": "Runtime and provider readiness status",
            "macos": [
                str(macos_commands.get("doctor_json") or "./atrium doctor --json"),
                str(macos_commands.get("status_json") or "./atrium status --json"),
                str(macos_commands.get("provider_status") or "./atrium provider status --probe --json"),
            ],
            "windows": [
                ".\\atrium.ps1 doctor --json",
                str(windows_commands.get("nativeStatusJson") or ".\\atrium.ps1 status --json"),
                str(windows_commands.get("nativeProviderStatusProbeJson") or ".\\atrium.ps1 provider status --probe --json"),
            ],
        },
        {
            "id": "provider_login",
            "label": "Provider login, account switch, and credential reference",
            "macos": [
                str(macos_commands.get("provider_reference") or "./atrium provider reference --json"),
                str(macos_commands.get("provider_env") or "./atrium provider env --json"),
                str(macos_commands.get("provider_login_chatgpt") or "./atrium provider login chatgpt"),
                str(macos_commands.get("provider_login_claude_code") or "./atrium provider login claude-code"),
                str(macos_commands.get("provider_disconnect_chatgpt") or "./atrium provider disconnect chatgpt"),
                str(macos_commands.get("provider_disconnect_claude_code") or "./atrium provider disconnect claude-code"),
            ],
            "windows": [
                str(windows_commands.get("nativeProviderReferenceJson") or ".\\atrium.ps1 provider reference --json"),
                str(windows_commands.get("nativeProviderEnvJson") or ".\\atrium.ps1 provider env --json"),
                str(windows_commands.get("nativeProviderLoginChatGPT") or ".\\atrium.ps1 provider login chatgpt"),
                str(windows_commands.get("nativeProviderLoginClaudeCode") or ".\\atrium.ps1 provider login claude-code"),
                str(windows_commands.get("nativeProviderDisconnectChatGPT") or ".\\atrium.ps1 provider disconnect chatgpt"),
                str(windows_commands.get("nativeProviderDisconnectClaudeCode") or ".\\atrium.ps1 provider disconnect claude-code"),
            ],
        },
        {
            "id": "ai_tools",
            "label": "AI tool registry, catalog, and MCP gateway",
            "macos": [
                str(macos_commands.get("tools_status") or "./atrium tools status --json"),
                str(macos_commands.get("tools_mcp_gateway") or "./atrium tools mcp-gateway --json"),
                str(macos_commands.get("tools_mcp_probe") or "./atrium tools mcp-probe --json"),
                str(macos_commands.get("tools_catalog") or "./atrium tools catalog --json"),
            ],
            "windows": [
                str(windows_commands.get("nativeToolsStatusJson") or ".\\atrium.ps1 tools status --json"),
                str(windows_commands.get("mcpGatewaySetupJson") or ".\\atrium.ps1 tools mcp-gateway --json"),
                str(windows_commands.get("mcpGatewayProbeJson") or ".\\atrium.ps1 tools mcp-probe --json"),
                str(windows_commands.get("nativeToolsCatalogJson") or ".\\atrium.ps1 tools catalog --json"),
            ],
        },
        {
            "id": "browser_desktop_tools",
            "label": "Browser and desktop automation tools",
            "macos": [
                str(macos_commands.get("automation_status") or "./atrium automation status --commands"),
                str(macos_commands.get("automation_smoke") or "./atrium automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium"),
                str(macos_commands.get("automation_probe") or "uv --project system run python ops/macos_host_bridge_probe.py --full"),
            ],
            "windows": [
                ".\\atrium.ps1 automation status --commands",
                str(windows_commands.get("nativeBrowserDesktopSmoke") or ".\\atrium.ps1 automation smoke --browser-url http://127.0.0.1:5173 --browser-profile atrium"),
                str(windows_commands.get("windowsLiveProof") or ".\\atrium.ps1 automation windows-live-proof"),
            ],
        },
        {
            "id": "logs_support_report",
            "label": "Logs and support report",
            "macos": [
                str(macos_commands.get("logs_json") or "./atrium logs --json"),
                str(macos_commands.get("report_bundle") or "./atrium report --bundle"),
            ],
            "windows": [
                str(windows_commands.get("nativeLogsJson") or ".\\atrium.ps1 logs --json"),
                str(windows_commands.get("nativeReportBundle") or ".\\atrium.ps1 report --bundle"),
            ],
        },
        {
            "id": "automation_permission",
            "label": "Owner automation permission and OpenClaw audit gate",
            "macos": [
                str(macos_commands.get("permissions_status") or "./atrium permissions status --json"),
                str(macos_commands.get("permissions_full_auto") or "./atrium permissions set full_auto --agent-full-access true"),
                "./atrium automation audit",
            ],
            "windows": [
                str(windows_commands.get("nativePermissionsStatusJson") or ".\\atrium.ps1 permissions status --json"),
                str(windows_commands.get("nativePermissionsSetFullAuto") or ".\\atrium.ps1 permissions set full_auto --agent-full-access true"),
                str(windows_commands.get("audit") or ".\\atrium.ps1 automation audit"),
            ],
        },
    ]
    return {
        "host": "windows" if windows_native() else ("macos" if platform.system() == "Darwin" else "non-windows"),
        "launcherMode": launcher_mode(),
        "nativeOnly": True,
        "windowsNativeHostOnly": True,
        "evidenceType": "command_surface_and_handoff; live Windows proof artifact is still required for final parity",
        "surfaces": surfaces,
    }


def summarize_native_parity_matrix(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["native.parity=unavailable"]
    surfaces = payload.get("surfaces")
    lines = [
        f"native.parity.host={payload.get('host') or 'unknown'}",
        f"native.parity.launcher={payload.get('launcherMode') or 'unknown'}",
        f"native.parity.nativeOnly={bool_text(payload.get('nativeOnly'))}",
        f"native.parity.windowsNativeHostOnly={bool_text(payload.get('windowsNativeHostOnly'))}",
    ]
    if isinstance(surfaces, list):
        lines.append(f"native.parity.surfaces={len(surfaces)}")
        for surface in surfaces:
            if not isinstance(surface, dict):
                continue
            surface_id = str(surface.get("id") or "unknown")
            macos_commands = surface.get("macos") if isinstance(surface.get("macos"), list) else []
            windows_commands = surface.get("windows") if isinstance(surface.get("windows"), list) else []
            lines.append(
                f"native.parity.{surface_id}=macos:{len(macos_commands)} commands; "
                f"windows:{len(windows_commands)} commands"
            )
    evidence_type = payload.get("evidenceType")
    if isinstance(evidence_type, str) and evidence_type.strip():
        lines.append(f"native.parity.evidence={evidence_type}")
    return lines


def collect_windows_entrypoints_payload() -> dict[str, object]:
    files = {
        "atriumPs1": ROOT / "atrium.ps1",
        "atriumCmd": ROOT / "atrium.cmd",
        "nativeInstaller": ROOT / "ops" / "install_windows_native.ps1",
        "liveProofRunner": ROOT / "ops" / "windows_host_bridge_live_proof.ps1",
        "atriumCli": ROOT / "ops" / "atrium_cli.py",
    }
    texts = {name: _read_source_text(path) for name, path in files.items()}
    checks = {
        "atriumPs1Runner": all(
            token in texts["atriumPs1"]
            for token in ("Add-PythonInstallPaths", "system\\.venv\\Scripts\\python.exe", "uv", "Python 3 is required")
        ),
        "atriumCmdForwarder": all(
            token.lower() in texts["atriumCmd"].lower()
            for token in ("powershell.exe", "pwsh.exe", "-ExecutionPolicy Bypass", "atrium.ps1", "%*", "missing_powershell")
        ),
        "standardPowerShellPathFallback": (
            "%systemroot%\\system32\\windowspowershell\\v1.0\\powershell.exe" in texts["atriumCmd"].lower()
            and "%systemroot%\\syswow64\\windowspowershell\\v1.0\\powershell.exe" in texts["atriumCmd"].lower()
            and "%programfiles%\\powershell\\7\\pwsh.exe" in texts["atriumCmd"].lower()
            and "%programfiles(x86)%\\powershell\\7\\pwsh.exe" in texts["atriumCmd"].lower()
            and "SystemRoot" in texts["atriumCli"]
        ),
        "postStartReadiness": all(
            token in texts["atriumCli"]
            for token in (
                "def print_post_start_readiness",
                "Post-start Readiness",
                "/api/runtime",
                "/api/provider-auth/status",
                "/api/permissions/mode",
                "/api/tools/catalog",
                "/api/connectors",
                "/api/host-bridge/parity",
            )
        ),
        "nativeInstallerSetupHandoff": all(
            token in texts["nativeInstaller"]
            for token in ("Resolve-PowerShell", "Invoke-Native", '".\\atrium.ps1"', '"setup"', '"--yes"')
        ),
        "nativeInstallerNextChecks": all(
            token in texts["nativeInstaller"]
            for token in (
                ".\\atrium.ps1 doctor --json",
                ".\\atrium.ps1 start",
                ".\\atrium.ps1 provider status --probe --json",
                ".\\atrium.ps1 provider reference --json",
                ".\\atrium.ps1 provider env --json",
                ".\\atrium.ps1 permissions status --json",
                ".\\atrium.ps1 permissions set full_auto --agent-full-access true",
                ".\\atrium.ps1 tools status --json",
                ".\\atrium.ps1 automation status --commands",
                ".\\atrium.ps1 automation source --json",
                ".\\atrium.ps1 automation windows-live-proof",
                "--source-fingerprint <fingerprint>",
                "--source-manifest-sha256 <manifest>",
                "--source-file-count <count>",
                ".\\atrium.ps1 automation smoke",
                ".\\atrium.ps1 automation artifact --label windows",
                "--max-artifact-age-hours 24.0",
                ".\\atrium.ps1 automation windows-probe --full",
                "raw diagnostic; automation smoke is the normal native smoke command",
                ".\\atrium.ps1 report --bundle",
                ".\\atrium.ps1 stop",
            )
        ),
        "liveProofRunner": all(
            token in texts["liveProofRunner"]
            for token in (
                "windows_host_bridge_probe.py",
                "host_bridge_artifact_summary.py",
                "MaxArtifactAgeHours",
                "--full",
                "Get-LiveProofPreflight",
                "Write-LiveProofFailureArtifact",
                "windows_live_proof_failed",
            )
        ),
        "openclawLifecycleProofPackage": all(
            token in texts["atriumCli"]
            for token in (
                "windows_openclaw_lifecycle_commands",
                "native_setup_start",
                "native_permissions",
                "native_provider_ai_tools",
                "native_logs_report",
                "native_stop_restart",
                '"nativeStart"',
                '"nativeStop"',
                '"nativeRestart"',
                '"nativeStatusJson"',
                '"nativeLogsJson"',
                '"nativeReportBundle"',
                '"nativePermissionsStatusJson"',
                '"nativePermissionsSetFullAuto"',
                '"nativeProviderStatusProbeJson"',
                '"nativeProviderReferenceJson"',
                '"nativeProviderEnvJson"',
                '"nativeProviderLoginChatGPT"',
                '"nativeProviderLoginClaudeCode"',
                '"nativeProviderDisconnectChatGPT"',
                '"nativeProviderDisconnectClaudeCode"',
                '"nativeToolsStatusJson"',
                '"nativeToolsCatalogJson"',
                '"mcpGatewayStatusJson"',
                '"mcpGatewayProbeJson"',
                '"nativeBrowserDesktopSmoke"',
                "source_validate",
                "mcp_gateway_setup",
                "mcp_gateway_probe",
                "mcp_gateway_status",
                "native_browser_desktop_smoke",
                "windows_raw_probe",
                "accountSwitchCommands",
                "windows_live_proof",
                "artifact_validate_on_windows",
                "copy_to_repo_host",
                "generate_report",
                "audit_gate",
                "requiredGate",
            )
        ),
    }
    return {
        "windowsNative": windows_native(),
        "launcher": local_cli_command("status"),
        "powershellCommand": powershell_command(),
        "files": {name: _source_file_metadata(path) for name, path in files.items()},
        "checks": checks,
        "openclawLifecycleProof": windows_openclaw_lifecycle_commands(),
        "ok": all(bool(value) for value in checks.values()) and all(path.exists() for path in files.values()),
    }


def summarize_windows_entrypoints_payload(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return ["windows.entrypoints=unavailable"]
    lines = [
        f"windows.entrypoints.ok={bool_text(payload.get('ok'))}",
        f"windows.entrypoints.launcher={payload.get('launcher') or 'unknown'}",
        f"windows.entrypoints.powershell={payload.get('powershellCommand') or 'missing'}",
    ]
    checks = payload.get("checks")
    if isinstance(checks, dict):
        for name, value in checks.items():
            lines.append(f"windows.entrypoint.{name}={bool_text(value)}")
    proof = payload.get("openclawLifecycleProof")
    if isinstance(proof, dict):
        lines.append(f"windows.entrypoints.openclawLifecycleProof.ok={bool_text(proof.get('ok'))}")
        commands = proof.get("commands")
        if isinstance(commands, dict):
            for name in (
                "nativeSetup",
                "nativeStart",
                "nativeStatusJson",
                "nativeLogsJson",
                "nativeReportBundle",
                "nativeStop",
                "nativeRestart",
                "nativePermissionsStatusJson",
                "nativePermissionsSetFullAuto",
                "nativeProviderStatusProbeJson",
                "nativeProviderReferenceJson",
                "nativeProviderEnvJson",
                "nativeProviderLoginChatGPT",
                "nativeProviderLoginClaudeCode",
                "nativeProviderDisconnectChatGPT",
                "nativeProviderDisconnectClaudeCode",
                "nativeToolsStatusJson",
                "nativeToolsCatalogJson",
                "mcpGatewaySetupJson",
                "mcpGatewayProbeJson",
                "mcpGatewayStatusJson",
                "nativeBrowserDesktopSmoke",
                "windowsProbe",
                "sourceValidate",
                "windowsLiveProof",
                "windowsArtifactValidate",
                "windowsArtifactValidateOnWindows",
                "acceptWindowsArtifact",
                "report",
                "audit",
            ):
                command = commands.get(name)
                if isinstance(command, str) and command.strip():
                    lines.append(f"windows.entrypoints.openclawLifecycleProof.{name}={command}")
        checklist = proof.get("operatorChecklist")
        if isinstance(checklist, list):
            ids = [str(item.get("id")) for item in checklist if isinstance(item, dict) and item.get("id")]
            if ids:
                lines.append("windows.entrypoints.openclawLifecycleProof.checklist=" + ", ".join(ids))
    files = payload.get("files")
    if isinstance(files, dict):
        for name, meta in files.items():
            if not isinstance(meta, dict):
                continue
            digest = str(meta.get("sha256") or "")
            digest_label = digest[:12] if digest else "missing"
            lines.append(
                "windows.entrypoint.file."
                f"{name}=exists={bool_text(meta.get('exists'))}; "
                f"path={meta.get('relativePath') or meta.get('path') or 'unknown'}; "
                f"bytes={meta.get('bytes') if meta.get('bytes') is not None else 'unknown'}; "
                f"sha256={digest_label}"
            )
    return lines


def windows_popen_command(command: Sequence[str]) -> list[str]:
    prepared = list(command)
    if not prepared or platform.system() != "Windows":
        return prepared
    suffix = Path(prepared[0]).suffix.lower()
    if suffix not in {".cmd", ".bat"}:
        return prepared
    cmd = command_path("cmd.exe") or os.environ.get("ComSpec") or "cmd.exe"
    return [cmd, "/D", "/S", "/C", subprocess.list2cmdline(prepared)]


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
            windows_popen_command(command),
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
        return ("git", "node", "pnpm", "uv", "python3", "winget", "powershell", "claude")
    return ("git", "brew", "node", "pnpm", "uv", "python3")


def report_tool_names() -> tuple[str, ...]:
    if windows_native():
        return ("git", "node", "pnpm", "uv", "python3", "winget", "powershell", "claude")
    return ("git", "brew", "node", "pnpm", "uv", "python3", "screen")


def command_provider(args: argparse.Namespace) -> int:
    ensure_repo_root()
    action = args.provider_action
    target = normalize_provider_auth_target(getattr(args, "provider", "chatgpt"))
    start_hint = local_cli_command("start")

    if action == "status":
        probe = bool(getattr(args, "probe", False))
        suffix = "?probe=true" if probe else ""
        ok, _raw, payload = backend_json(f"/api/provider-auth/status{suffix}", timeout=15.0 if probe else 5.0)
        if not ok:
            raise StepFailure(
                "ATRIUM backend is not reachable for provider status",
                next_step=f"Start ATRIUM first, then rerun provider status.\n{start_hint}",
            )
        if getattr(args, "json", False):
            print(json.dumps(redact_json_value(payload), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print_header("Provider Auth")
        for line in summarize_provider_auth_payload(payload):
            print_info(line)
        return 0

    if action == "reference":
        ok, raw, payload = backend_json("/api/provider-auth/reference", timeout=10.0)
        if not ok:
            raise StepFailure(
                "ATRIUM backend is not reachable for provider credential reference",
                next_step=f"Start ATRIUM first, then rerun provider reference.\n{start_hint}\nBackend response: {redact_text(raw)[:800]}",
            )
        if getattr(args, "json", False):
            print(json.dumps(redact_json_value(payload), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print_header("Provider Reference")
        for line in summarize_provider_reference_payload(payload):
            print_info(line)
        return 0

    if action == "env":
        ok, raw, payload = backend_json("/api/provider-auth/env", timeout=10.0)
        if not ok:
            raise StepFailure(
                "ATRIUM backend is not reachable for provider environment settings",
                next_step=f"Start ATRIUM first, then rerun provider env.\n{start_hint}\nBackend response: {redact_text(raw)[:800]}",
            )
        if getattr(args, "json", False):
            print(json.dumps(redact_json_value(payload), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print_header("Provider Environment")
        for line in summarize_provider_env_payload(payload):
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
                    f"{start_hint}"
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
    runtime_payload = normalize_runtime_payload_for_cli(runtime_payload) if ok_runtime else runtime_payload
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
    ok, raw, payload = backend_json("/api/host-bridge/parity", timeout=HOST_BRIDGE_PARITY_TIMEOUT_SECONDS)
    if not ok or not isinstance(payload, dict):
        return False, {"ok": False, "backendReachable": False, "error": raw[:400]}
    source_summary = current_source_summary()
    normalized = normalize_parity_payload_for_cli(payload, current_source=source_summary)
    normalized["backendReachable"] = True
    local_artifacts = collect_local_proof_artifacts(source_summary)
    normalized["localArtifacts"] = local_artifacts
    normalized["commands"] = align_parity_commands_with_local_artifacts(
        normalized.get("commands"),
        local_artifacts,
    )
    normalized["windowsProofOperatorChecklist"] = build_windows_operator_checklist_from_commands(
        normalized.get("commands"),
    )
    ok_permission, raw_permission, permission_payload = backend_json("/api/permissions/mode", timeout=5.0)
    normalized["permissionMode"] = permission_payload if ok_permission else {"ok": False, "error": raw_permission[:400]}
    return True, normalized


def collect_openclaw_windows_proof_readiness_payload() -> dict[str, object]:
    source_summary = current_source_summary()
    automation_ok, automation_payload = collect_automation_status_payload()
    if not automation_ok:
        automation_payload = {"ok": False, **automation_payload}
    commands = automation_payload.get("commands") if isinstance(automation_payload.get("commands"), dict) else {}
    checklist = automation_payload.get("windowsProofOperatorChecklist")
    if not isinstance(checklist, list):
        checklist = build_windows_operator_checklist_from_commands(commands)
    contract = automation_payload.get("contract") if isinstance(automation_payload.get("contract"), dict) else {}
    report = automation_payload.get("report") if isinstance(automation_payload.get("report"), dict) else {}
    local_artifacts = automation_payload.get("localArtifacts")
    if not isinstance(local_artifacts, dict):
        local_artifacts = collect_local_proof_artifacts(source_summary)
    ok_mcp_probe, raw_mcp_probe, mcp_probe_payload = backend_json("/api/tools/mcp-gateway?probe=true", timeout=15.0)
    mcp_probe = (
        mcp_probe_payload
        if ok_mcp_probe and isinstance(mcp_probe_payload, dict)
        else {"ok": False, "error": raw_mcp_probe[:400]}
    )
    proof_blocker = mcp_probe.get("proofBlocker") if isinstance(mcp_probe.get("proofBlocker"), dict) else None
    return {
        "ok": automation_payload.get("ok") is True,
        "status": automation_payload.get("status"),
        "summary": automation_payload.get("summary"),
        "source": {
            key: source_summary.get(key)
            for key in ("sourceFingerprint", "sourceManifestSha256", "sourceFileCount", "gitHead", "gitDirty")
            if isinstance(source_summary, dict) and key in source_summary
        },
        "contractStatus": contract.get("status"),
        "reportOk": report.get("ok"),
        "reportPath": report.get("path") or report.get("reportPath"),
        "mcpExternalWriteReady": mcp_probe.get("ready") is True,
        "mcpProofBlocker": proof_blocker,
        "localArtifacts": local_artifacts,
        "windowsProofOperatorChecklist": checklist,
        "commands": {
            key: commands.get(key)
            for key in (
                "windowsSourceValidate",
                "mcpGatewaySetupJson",
                "mcpGatewayProbeJson",
                "mcpGatewayStatusJson",
                "nativeBrowserDesktopSmoke",
                "windowsProbe",
                "windowsLiveProofRunner",
                "windowsArtifactValidateOnWindows",
                "windowsArtifactCopyHint",
                "acceptWindowsArtifact",
                "automationReport",
                "verify",
            )
            if isinstance(commands.get(key), str)
        },
        "remainingGaps": list(automation_payload.get("gaps") or [])[:24],
        "proofRequirement": "OpenClaw-level Windows parity requires this readiness payload, current macOS and Windows live artifacts, write-capable MCP gateway proof, accept-windows, and a passing automation audit.",
    }


def command_permissions(args: argparse.Namespace) -> int:
    ensure_repo_root()
    action = args.permissions_action
    start_hint = local_cli_command("start")
    if action == "status":
        ok, raw, payload = backend_json("/api/permissions/mode", timeout=5.0)
        if not ok:
            raise StepFailure(
                "ATRIUM backend is not reachable for permission mode",
                next_step=f"Start ATRIUM first, then rerun permissions status.\n{start_hint}\nBackend response: {redact_text(raw)[:800]}",
            )
        if getattr(args, "json", False):
            print(json.dumps(redact_json_value(payload), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print_header("Owner Permissions")
        for line in summarize_full_autonomy(payload):
            print_info(line)
        return 0

    if action == "set":
        payload: dict[str, object] = {
            "mode": args.mode,
            "updatedBy": getattr(args, "updated_by", None) or "atrium-cli",
        }
        if getattr(args, "agent_full_access", None) is not None:
            payload["agentFullAccess"] = args.agent_full_access
        for flag, key in (
            ("allowed_tools", "allowedTools"),
            ("denied_tools", "deniedTools"),
            ("allowed_risk_classes", "allowedRiskClasses"),
            ("denied_risk_classes", "deniedRiskClasses"),
            ("command_allowlist", "commandAllowlist"),
            ("command_denylist", "commandDenylist"),
        ):
            values = split_csv_values(getattr(args, flag, None))
            if values:
                payload[key] = values
        if getattr(args, "ask_fallback", None):
            payload["askFallback"] = args.ask_fallback
        if getattr(args, "strict_inline_eval", None) is not None:
            payload["strictInlineEval"] = args.strict_inline_eval
        ok, raw, result = backend_json_request("/api/permissions/mode", method="PATCH", payload=payload, timeout=15.0)
        if not ok:
            raise StepFailure(
                "Could not update permission mode through ATRIUM backend",
                next_step=f"Start ATRIUM first, then retry permissions set.\n{start_hint}\nBackend response: {redact_text(raw)[:800]}",
            )
        if getattr(args, "json", False):
            print(json.dumps(redact_json_value(result), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print_header("Owner Permissions")
        for line in summarize_full_autonomy(result):
            print_info(line)
        return 0

    raise StepFailure(f"Unknown permissions action: {action}")


def command_tools(args: argparse.Namespace) -> int:
    ensure_repo_root()
    action = args.tools_action
    start_hint = local_cli_command("start")
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
                    f"Start ATRIUM first, then rerun tools status.\n{start_hint}"
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

    if action == "mcp-gateway":
        if not ok_runtime or not tool_ok["connectors"]:
            raise StepFailure(
                "ATRIUM backend is not reachable for MCP gateway readiness",
                next_step=(
                    f"runtime: {redact_text(raw_runtime)[:400]}\n"
                    f"connectors: {redact_text(tool_raw['connectors'])[:400]}\n"
                    f"Start ATRIUM first, then rerun tools mcp-gateway.\n{start_hint}"
                ),
            )
        payload = mcp_gateway_setup_payload(runtime_payload, connectors_payload)
        if getattr(args, "json", False):
            print(json.dumps(redact_json_value(payload), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print_header("MCP External-Write Gateway")
        for line in summarize_mcp_gateway_setup_payload(payload):
            print_info(line)
        return 0

    if action == "mcp-probe":
        ok, raw, payload = backend_json("/api/tools/mcp-gateway?probe=true", timeout=15.0)
        if not ok or not isinstance(payload, dict):
            raise StepFailure(
                "ATRIUM backend is not reachable for MCP gateway probe",
                next_step=redact_text(raw)[:800] or "Start ATRIUM first, then rerun tools mcp-probe.",
            )
        if getattr(args, "json", False):
            print(json.dumps(redact_json_value(payload), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print_header("MCP External-Write Gateway Probe")
        for line in summarize_mcp_gateway_setup_payload(payload):
            print_info(line)
        return 0

    raise StepFailure(f"Unknown tools action: {action}")


def command_automation(args: argparse.Namespace) -> int:
    ensure_repo_root()
    action = args.automation_action
    start_hint = local_cli_command("start")

    if action == "status":
        backend_ok, payload = collect_automation_status_payload()
        if not backend_ok:
            raise StepFailure(
                "ATRIUM backend is not reachable for automation status",
                next_step=f"Start ATRIUM first, then rerun automation status.\n{start_hint}",
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
        ok, raw, payload = backend_json("/api/host-bridge/parity", timeout=HOST_BRIDGE_PARITY_TIMEOUT_SECONDS)
        if not ok or not isinstance(payload, dict):
            raise StepFailure(
                "ATRIUM backend is not reachable for OpenClaw Windows audit",
                next_step=raw or "Start ATRIUM first, then rerun automation audit.",
            )
        source_summary = current_source_summary()
        payload = normalize_parity_payload_for_cli(payload, current_source=source_summary)
        local_artifacts = collect_local_proof_artifacts(source_summary)
        payload["localArtifacts"] = local_artifacts
        payload["commands"] = align_parity_commands_with_local_artifacts(
            payload.get("commands"),
            local_artifacts,
        )
        payload["windowsProofOperatorChecklist"] = build_windows_operator_checklist_from_commands(
            payload.get("commands"),
        )
        if args.json:
            print(json.dumps(redact_json_value(payload), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print_header("OpenClaw Windows Audit")
            for line in summarize_parity_payload(payload):
                print_info(line)
            print_header("Local Proof Artifacts")
            for line in summarize_local_proof_artifacts(payload):
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
            commands = normalize_parity_commands(commands, current_source=source_summary)
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
        if getattr(args, "json", False):
            result = run(command, cwd=ROOT, timeout=15)
            output = result.stdout.strip()
            if output:
                print(output)
            elif result.stderr.strip():
                print(json.dumps({"ok": False, "findings": [result.stderr.strip()]}, ensure_ascii=False, indent=2, sort_keys=True))
            return result.returncode
        run_interactive(command, cwd=ROOT)
        return 0

    if action == "smoke":
        host_system = platform.system()
        if host_system == "Windows":
            script = "ops/windows_host_bridge_probe.py"
            output_path = args.output or DEFAULT_WINDOWS_SMOKE_PATH
        elif host_system == "Darwin":
            script = "ops/macos_host_bridge_probe.py"
            output_path = args.output or DEFAULT_MACOS_SMOKE_PATH
        else:
            raise StepFailure(
                "Browser/desktop automation smoke is supported only on native Windows or macOS hosts",
                next_step="Run this command from Windows PowerShell or macOS Terminal on a signed-in desktop session.",
            )
        command = uv_python_command(script)
        command.append("--full")
        append_optional_flag(command, args.simulate, "--simulate")
        append_optional_value(command, "--browser-url", args.browser_url)
        append_optional_value(command, "--browser-profile", args.browser_profile)
        append_optional_value(command, "--output", output_path)
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
        output_path = args.output or (DEFAULT_WINDOWS_PROBE_PATH if args.full else None)
        append_optional_value(command, "--output", output_path)
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
                    "--source-file-count <count> --max-artifact-age-hours 24.0"
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
        output_path = Path(args.output).expanduser()
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        payload = build_windows_handoff_payload(
            macos_artifact=args.macos,
            macos_summary=macos,
            source_summary=source,
            windows_output=args.windows_output,
            windows_local_copy=args.windows_local_copy,
            handoff_output=str(output_path),
        )
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
        append_optional_value(command, "--max-artifact-age-hours", args.max_artifact_age_hours)
        command.append(args.artifact)
        if getattr(args, "json", False):
            result = run(command, cwd=ROOT, timeout=30)
            output = result.stdout.strip()
            if output:
                print(output)
            elif result.stderr.strip():
                print(json.dumps({"ok": False, "findings": [result.stderr.strip()]}, ensure_ascii=False, indent=2, sort_keys=True))
            return result.returncode
        run_interactive(command, cwd=ROOT)
        return 0

    if action == "report":
        output_path = resolve_repo_output_path(args.output)
        backend_report_path = is_backend_parity_report_path(output_path)
        if args.skip_current_source_check and backend_report_path:
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
        append_optional_value(command, "--output", str(output_path))
        append_optional_value(command, "--max-artifact-age-hours", args.max_artifact_age_hours)
        append_optional_value(command, "--windows-source-path", args.windows_source_path)
        append_optional_flag(command, args.skip_current_source_check, "--skip-current-source-check")
        run_interactive(command, cwd=ROOT)
        destination = "backend default report path" if backend_report_path else "custom report path"
        print_check(True, "HostBridge parity report", f"verified and wrote {destination}: {rel(output_path)}")
        return 0

    if action == "accept-windows":
        payload = accept_windows_artifact(
            artifact=args.artifact,
            handoff_path=args.handoff,
            output=args.output,
            max_artifact_age_hours=args.max_artifact_age_hours,
            windows_source_path=args.windows_source_path,
        )
        redacted = redact_json_value(payload)
        if args.json:
            print(json.dumps(redacted, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print_header("Accept Windows Proof Artifact")
            action_label = "copied" if payload.get("copied") is True else "already at local path"
            print_check(True, "Windows artifact", f"{action_label}: {payload.get('windowsLocalCopy') or ''}")
            print_info("parityRunId", str(payload.get("parityRunId") or ""))
            print_info("sourceFingerprint", str(payload.get("sourceFingerprint") or ""))
            print_info("windowsProofFacets", str(payload.get("windowsProofFacetCount") or "unknown"))
            print_info("reportPath", str(payload.get("reportPath") or ""))
            if payload.get("ok") is True:
                print_check(True, "OpenClaw Windows audit", "cross_os_verified")
            else:
                print_check(False, "OpenClaw Windows audit", str(payload.get("status") or "not verified"))
        return 0 if payload.get("ok") is True else 2

    raise StepFailure(f"Unknown automation action: {action}")


def command_doctor(_args: argparse.Namespace) -> int:
    ensure_repo_root()
    if getattr(_args, "json", False):
        print(json.dumps(redact_json_value(collect_doctor_payload()), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
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
        found = report_command_path(tool)
        print_check(bool(found), tool, found or "missing")
    docker_payload = collect_docker_payload()
    docker_plan = docker_payload.get("plan") if isinstance(docker_payload.get("plan"), dict) else {}
    compose_cmd = docker_payload.get("compose")
    docker_required = bool(docker_plan.get("required"))
    if docker_required:
        print_check(bool(compose_cmd), "docker compose", " ".join(compose_cmd) if isinstance(compose_cmd, list) else "missing")
    else:
        detail = " ".join(compose_cmd) if isinstance(compose_cmd, list) else "missing; optional for native/manual services"
        print_info("docker compose", detail)
    print_check(browser_installed(), "Chromium browser", "Chrome/Edge/Brave/Chromium installed" if browser_installed() else "missing")

    print_header("Windows Native Entrypoints")
    for line in summarize_windows_entrypoints_payload(collect_windows_entrypoints_payload()):
        print_info(line)

    print_header("Native Parity Matrix")
    for line in summarize_native_parity_matrix(native_parity_matrix_payload()):
        print_info(line)

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
            payload = normalize_runtime_payload_for_cli(payload)
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
    ok, raw, provider_reference_payload = backend_json("/api/provider-auth/reference", timeout=10.0)
    if ok:
        for line in summarize_provider_reference_payload(provider_reference_payload):
            print_info(line)
    else:
        print_check(False, "provider reference", redact_text(raw)[:240] or "unavailable")
    ok, raw, provider_env_payload = backend_json("/api/provider-auth/env", timeout=10.0)
    if ok:
        for line in summarize_provider_env_payload(provider_env_payload):
            print_info(line)
    else:
        print_check(False, "provider env", redact_text(raw)[:240] or "unavailable")
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

    try:
        tools_payload, _tool_ok, _tool_raw = collect_tools_status_payload()
        print_header("MCP External Tools")
        for line in summarize_mcp_gateway_setup_payload(
            mcp_gateway_setup_payload(
                tools_payload.get("runtime"),
                tools_payload.get("connectors"),
            )
        ):
            print_info(line)
        ok, raw, mcp_probe_payload = backend_json("/api/tools/mcp-gateway?probe=true", timeout=15.0)
        if ok:
            for line in summarize_mcp_gateway_setup_payload(mcp_probe_payload):
                print_info(line)
        else:
            print_check(False, "MCP probe", redact_text(raw)[:240] or "unavailable")
    except Exception as exc:
        print_check(False, "MCP external tools", str(exc)[:240])

    ok, _raw, parity_payload = backend_json("/api/host-bridge/parity", timeout=HOST_BRIDGE_PARITY_TIMEOUT_SECONDS)
    if ok:
        parity_payload = normalize_parity_payload_for_cli(parity_payload)
        print_header("Automation Permission")
        for line in summarize_parity_payload(parity_payload):
            print_info(line)

    print_header("Local Proof Artifacts")
    for line in summarize_local_proof_artifacts(collect_local_proof_artifacts(current_source_summary())):
        print_info(line)
    return 0


def collect_doctor_payload() -> dict[str, object]:
    risky, path_detail = is_i_cloud_risky(ROOT)
    remote_ready, remote_detail = remote_ok()
    tools = {tool: report_command_path(tool) for tool in doctor_tool_names()}
    docker_payload = collect_docker_payload()
    runtime_http: dict[str, object] = {}
    runtime_payload: object | None = None
    provider_auth_payload: object | None = None
    permission_payload: object | None = None
    for path in ("/health", "/api/runtime", "/api/provider-auth/status", "/api/permissions/mode"):
        ok, raw, payload = backend_json(path, timeout=15.0 if path == "/api/runtime" else 3.0)
        if path == "/api/runtime" and ok:
            payload = normalize_runtime_payload_for_cli(payload)
            runtime_payload = payload
        if path == "/api/provider-auth/status" and ok:
            provider_auth_payload = payload
        if path == "/api/permissions/mode" and ok:
            permission_payload = payload
        runtime_http[path] = {
            "ok": ok,
            "summary": summarize_runtime_payload(payload) if ok and path == "/api/runtime" else raw[:180],
            "payload": payload if ok else None,
        }
    ok_connectors, raw_connectors, connectors_payload = backend_json("/api/connectors", timeout=8.0)
    ok_catalog, raw_catalog, catalog_payload = backend_json("/api/tools/catalog", timeout=8.0)
    ok_parity, raw_parity, parity_payload = backend_json("/api/host-bridge/parity", timeout=HOST_BRIDGE_PARITY_TIMEOUT_SECONDS)
    ok_provider_reference, raw_provider_reference, provider_reference_payload = backend_json("/api/provider-auth/reference", timeout=10.0)
    ok_provider_env, raw_provider_env, provider_env_payload = backend_json("/api/provider-auth/env", timeout=10.0)
    try:
        tools_status_payload, _tool_ok, _tool_raw = collect_tools_status_payload()
        mcp_gateway_payload: object = mcp_gateway_setup_payload(
            tools_status_payload.get("runtime"),
            tools_status_payload.get("connectors"),
        )
    except Exception as exc:
        tools_status_payload = {"ok": False, "error": str(exc)[:400]}
        mcp_gateway_payload = {"ok": False, "error": str(exc)[:400]}
    ok_mcp_probe, raw_mcp_probe, mcp_probe_payload = backend_json("/api/tools/mcp-gateway?probe=true", timeout=15.0)
    source = current_source_summary()
    parity_payload = normalize_parity_payload_for_cli(parity_payload, current_source=source) if ok_parity else {"ok": False, "error": raw_parity[:400]}
    if isinstance(parity_payload, dict):
        parity_payload = dict(parity_payload)
        parity_payload["localArtifacts"] = collect_local_proof_artifacts(source)
    return {
        "repo": str(ROOT),
        "platform": platform.platform(),
        "machine": platform.machine() or "unknown",
        "ram": memory_gb(),
        "launcherMode": launcher_mode(),
        "git": {
            "remoteOk": remote_ready,
            "remote": remote_detail,
            "status": git_status_summary(),
        },
        "installPath": {
            "ok": not risky,
            "detail": path_detail,
        },
        "tools": tools,
        "docker": {**docker_payload, "browserInstalled": browser_installed()},
        "ports": {
            label: {
                "port": port,
                "open": port_open(port),
                "owner": port_owner(port) if port_open(port) else "free",
            }
            for port, label in PORTS.items()
        },
        "env": {
            "system": {"path": str(SYSTEM_ENV), "exists": SYSTEM_ENV.exists(), "summary": render_env_status(SYSTEM_ENV, FULL_STACK_DEFAULTS.keys())},
            "ui": {"path": str(UI_ENV), "exists": UI_ENV.exists(), "summary": render_env_status(UI_ENV, UI_DEFAULTS.keys())},
        },
        "runtimeHttp": runtime_http,
        "runtime": runtime_payload,
        "providerAuth": provider_auth_payload,
        "providerReference": provider_reference_payload if ok_provider_reference else {"ok": False, "error": raw_provider_reference[:400]},
        "providerEnv": provider_env_payload if ok_provider_env else {"ok": False, "error": raw_provider_env[:400]},
        "permissionMode": permission_payload,
        "connectors": connectors_payload if ok_connectors else {"ok": False, "error": raw_connectors[:400]},
        "toolCatalog": catalog_payload if ok_catalog else {"ok": False, "error": raw_catalog[:400]},
        "toolsStatus": tools_status_payload,
        "mcpGateway": mcp_gateway_payload,
        "mcpProbe": mcp_probe_payload if ok_mcp_probe and isinstance(mcp_probe_payload, dict) else {"ok": False, "error": raw_mcp_probe[:400]},
        "automationPermission": parity_payload,
        "nativeNextChecks": native_next_checks_payload(),
        "nativeParityMatrix": native_parity_matrix_payload(),
        "windowsRuntime": collect_windows_runtime_payload(),
        "windowsEntryPoints": collect_windows_entrypoints_payload(),
    }


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
    plan = docker_stack_plan()
    services = [str(item) for item in plan.get("services", []) if str(item).strip()]
    required_services = [str(item) for item in plan.get("requiredServices", []) if str(item).strip()]
    satisfied = [str(item) for item in plan.get("satisfied", []) if str(item).strip()]
    if not services:
        detail = "; ".join(satisfied) if satisfied else "no Docker-backed services required by current env"
        print_info("Docker", f"not needed; {detail}")
        return

    docker = command_path("docker")
    compose_cmd = docker_compose_cmd() if docker else None
    if not docker or not compose_cmd:
        if required_services:
            raise StepFailure(
                "Docker Compose is unavailable for required local services",
                next_step=(
                    f"Missing services: {', '.join(required_services)}\n"
                    "Start native Postgres/Ollama, adjust system/.env to point at existing services, "
                    f"or install/update Docker Desktop and rerun {local_cli_command('start')}."
                ),
            )
        print_info("Docker Compose", f"missing; optional services not started: {', '.join(services)}")
        return
    if run([docker, "info"], timeout=10).returncode != 0:
        if required_services:
            if not wait_for_docker_ready(seconds=120, assume_yes=True, prompt=False):
                raise StepFailure(
                    "Docker is not running for required local services",
                    next_step=(
                        f"Missing services: {', '.join(required_services)}\n"
                        f"Open Docker Desktop, start native services, or update system/.env, then run {local_cli_command('start')} again."
                    ),
                )
        else:
            notes = plan.get("notes") if isinstance(plan.get("notes"), list) else []
            detail = "; ".join(str(item) for item in notes) or f"optional services not started: {', '.join(services)}"
            print_info("Docker", f"not running; {detail}")
            return
    compose(["up", "-d", *services], timeout=300)


def command_setup(args: argparse.Namespace) -> int:
    ensure_repo_root()
    print_header("ATRIUM Guided Setup")
    print_info("goal", f"install, start, verify, then open {FRONTEND_URL}")
    print_info("repo", str(ROOT))
    if args.dry_run:
        print_info("mode", "dry-run; no files, services, or installs will be changed")
    elif not args.yes:
        setup_scope = (
            "This setup can install winget packages, Docker Desktop, Python/Node dependencies, Claude Code, browser support, and start native Windows services."
            if windows_native()
            else "This setup can install Homebrew packages, Docker Desktop, Python/Node dependencies, and start local services."
        )
        prompt_enter(
            setup_scope,
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
        print_native_next_checks()
        return 0
    if args.dry_run:
        print(f"\n[DRY-RUN] {local_cli_command('start')}")
        print(f"[DRY-RUN] {local_cli_command('status')}")
        print_native_next_checks()
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
    print_native_next_checks()
    return 0


def print_native_next_checks() -> None:
    print_header("Next Native Checks")
    payload = native_next_checks_payload()
    checklist = payload.get("operatorChecklist") if isinstance(payload.get("operatorChecklist"), list) else []
    for item in checklist:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "check")
        command = item.get("command")
        if isinstance(command, str) and command.strip():
            print_info(item_id, command)
        login_commands = item.get("loginCommands")
        if isinstance(login_commands, list):
            for login in login_commands:
                if isinstance(login, str) and login.strip():
                    print_info(f"{item_id}.login", login)
        account_switch_commands = item.get("accountSwitchCommands")
        if isinstance(account_switch_commands, list):
            for command_text in account_switch_commands:
                if isinstance(command_text, str) and command_text.strip():
                    print_info(f"{item_id}.accountSwitch", command_text)
    required_gate = payload.get("requiredGate")
    if isinstance(required_gate, str) and required_gate.strip():
        print_info("requiredGate", required_gate)


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
    print_post_start_readiness()
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
    print_post_start_readiness()
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
    last_health = "not checked"
    last_frontend = False
    while time.time() < deadline:
        health, raw_health, _ = http_get_json(f"{BACKEND_URL}/health", timeout=1.5)
        last_health = raw_health[:180]
        frontend = port_open(5173)
        last_frontend = frontend
        if health and frontend:
            return
        time.sleep(1)
    backend_owner = port_owner(8787) if port_open(8787) else "free"
    frontend_owner = port_owner(5173) if last_frontend or port_open(5173) else "free"
    raise StepFailure(
        f"ATRIUM did not become ready within {seconds}s",
        next_step=(
            f"Backend /health: {redact_text(last_health) or 'not reachable'}\n"
            f"Backend :8787 listener: {backend_owner}\n"
            f"Frontend :5173 listener: {frontend_owner}\n"
            f"Logs: {rel(LOG_DIR / 'backend.log')} and {rel(LOG_DIR / 'ui.log')}\n"
            f"Run {local_cli_command('status --json')} and {local_cli_command('logs --json')} for redacted diagnostics."
        ),
    )


def print_post_start_readiness() -> None:
    print_header("Post-start Readiness")
    probes: tuple[tuple[str, str, float], ...] = (
        ("/api/runtime", "runtime", 15.0),
        ("/api/provider-auth/status", "provider auth", 5.0),
        ("/api/permissions/mode", "owner permissions", 5.0),
        ("/api/tools/catalog", "AI tool catalog", 8.0),
        ("/api/connectors", "connectors", 8.0),
        ("/api/host-bridge/parity", "automation permission", HOST_BRIDGE_PARITY_TIMEOUT_SECONDS),
    )
    for path, label, timeout in probes:
        ok, raw, payload = backend_json(path, timeout=timeout)
        if path == "/api/runtime" and ok:
            payload = normalize_runtime_payload_for_cli(payload)
            print_check(True, label, summarize_runtime_payload(payload))
        elif path == "/api/provider-auth/status" and ok:
            print_check(True, label, "; ".join(summarize_provider_auth_payload(payload)[:2]))
        elif path == "/api/permissions/mode" and ok:
            print_check(True, label, "; ".join(summarize_full_autonomy(payload)[:2]))
        elif path == "/api/tools/catalog" and ok:
            print_check(True, label, "; ".join(summarize_tool_catalog_payload(payload, limit=2)[:2]))
        elif path == "/api/connectors" and ok:
            print_check(True, label, "; ".join(summarize_connectors_payload(payload)[:3]))
        elif path == "/api/host-bridge/parity" and ok:
            normalized = normalize_parity_payload_for_cli(payload, current_source=current_source_summary())
            print_check(True, label, "; ".join(summarize_parity_payload(normalized)[:2]))
        else:
            print_check(False, label, redact_text(raw)[:240] or f"{path} unavailable")


def summarize_runtime_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return "non-json"
    parts = [f"ok={payload.get('ok', 'unknown')}"]
    if "running" in payload:
        parts.append(f"running={payload.get('running')}")
    v2 = payload.get("v2") if isinstance(payload.get("v2"), dict) else {}
    agent = v2.get("agentRuntime") if isinstance(v2.get("agentRuntime"), dict) else payload.get("agentRuntime")
    if isinstance(agent, dict):
        parts.append(f"agentRuntime.ok={agent.get('ok', 'unknown')}")
        parts.append(f"backend={agent.get('configuredBackend', agent.get('backend', 'unknown'))}")
    native_runtime = v2.get("nativeRuntime") if isinstance(v2.get("nativeRuntime"), dict) else {}
    if native_runtime:
        parts.append(f"native.launcher={native_runtime.get('launcher', 'unknown')}")
        parts.append(f"nativeOnly={bool_text(native_runtime.get('nativeOnly'))}")
        parts.append(f"browserTools={bool_text(native_runtime.get('browserAutomationReady'))}")
        parts.append(f"desktopTools={bool_text(native_runtime.get('desktopAutomationReady'))}")
    if "toolRegistryCount" in v2:
        parts.append(f"tools={v2.get('toolRegistryCount')}")
    memory = v2.get("memory") if isinstance(v2.get("memory"), dict) else payload.get("memory")
    if isinstance(memory, dict):
        parts.append(f"memory.ok={memory.get('ok', 'unknown')}")
    graph = payload.get("graph")
    if isinstance(graph, dict):
        parts.append(f"graph={graph.get('backend', graph.get('status', 'unknown'))}")
    return ", ".join(parts)


def collect_status_payload() -> dict[str, object]:
    process_payload = collect_process_payload()
    ports_payload = {
        label: {
            "port": port,
            "open": port_open(port),
            "owner": port_owner(port) if port_open(port) else "free",
        }
        for port, label in PORTS.items()
    }
    docker_payload = collect_docker_payload()
    http_payload: dict[str, object] = {}
    runtime_payload: object | None = None
    provider_auth_payload: object | None = None
    permission_payload: object | None = None
    for path in ("/health", "/api/runtime", "/api/provider-auth/status", "/api/permissions/mode"):
        ok, raw, payload = backend_json(path, timeout=15.0 if path == "/api/runtime" else 3.0)
        if path == "/api/runtime" and ok:
            payload = normalize_runtime_payload_for_cli(payload)
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
    ok_catalog, raw_catalog, catalog_payload = backend_json("/api/tools/catalog", timeout=8.0)
    ok_parity, raw_parity, parity_payload = backend_json("/api/host-bridge/parity", timeout=HOST_BRIDGE_PARITY_TIMEOUT_SECONDS)
    ok_provider_reference, raw_provider_reference, provider_reference_payload = backend_json("/api/provider-auth/reference", timeout=10.0)
    ok_provider_env, raw_provider_env, provider_env_payload = backend_json("/api/provider-auth/env", timeout=10.0)
    try:
        tools_status_payload, _tool_ok, _tool_raw = collect_tools_status_payload()
        mcp_gateway_payload: object = mcp_gateway_setup_payload(
            tools_status_payload.get("runtime"),
            tools_status_payload.get("connectors"),
        )
    except Exception as exc:
        tools_status_payload = {"ok": False, "error": str(exc)[:400]}
        mcp_gateway_payload = {"ok": False, "error": str(exc)[:400]}
    ok_mcp_probe, raw_mcp_probe, mcp_probe_payload = backend_json("/api/tools/mcp-gateway?probe=true", timeout=15.0)
    source_summary = current_source_summary()
    parity_payload = normalize_parity_payload_for_cli(parity_payload, current_source=source_summary) if ok_parity else None
    local_artifacts = collect_local_proof_artifacts(source_summary)
    automation_permission_payload: dict[str, object]
    if isinstance(parity_payload, dict):
        automation_permission_payload = dict(parity_payload)
        automation_permission_payload["localArtifacts"] = local_artifacts
    else:
        automation_permission_payload = {"ok": False, "error": raw_parity[:400], "localArtifacts": local_artifacts}
    return {
        "repo": str(ROOT),
        "platform": platform.platform(),
        "launcherMode": launcher_mode(),
        "process": process_payload,
        "windowsRuntime": collect_windows_runtime_payload(),
        "windowsEntryPoints": collect_windows_entrypoints_payload(),
        "ports": ports_payload,
        "docker": docker_payload,
        "http": http_payload,
        "providerAuth": provider_auth_payload,
        "providerReference": provider_reference_payload if ok_provider_reference else {"ok": False, "error": raw_provider_reference[:400]},
        "providerEnv": provider_env_payload if ok_provider_env else {"ok": False, "error": raw_provider_env[:400]},
        "permissionMode": permission_payload,
        "runtime": runtime_payload,
        "toolCatalog": catalog_payload if ok_catalog else {"ok": False, "error": raw_catalog[:400]},
        "toolsStatus": tools_status_payload,
        "mcpGateway": mcp_gateway_payload,
        "mcpProbe": mcp_probe_payload if ok_mcp_probe and isinstance(mcp_probe_payload, dict) else {"ok": False, "error": raw_mcp_probe[:400]},
        "connectors": connectors_payload if ok_connectors else {"ok": False, "error": raw_connectors[:400]},
        "hostBridgeSource": source_summary if isinstance(source_summary, dict) else {"ok": False, "error": "source summary unavailable"},
        "localProofArtifacts": local_artifacts,
        "automationPermission": automation_permission_payload,
        "nativeNextChecks": native_next_checks_payload(),
        "nativeParityMatrix": native_parity_matrix_payload(),
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

    print_header("Windows Native Entrypoints")
    for line in summarize_windows_entrypoints_payload(collect_windows_entrypoints_payload()):
        print_info(line)

    print_header("Native Parity Matrix")
    for line in summarize_native_parity_matrix(native_parity_matrix_payload()):
        print_info(line)

    print_header("Docker")
    docker_payload = collect_docker_payload()
    docker = docker_payload.get("cli")
    docker_plan = docker_payload.get("plan") if isinstance(docker_payload.get("plan"), dict) else {}
    compose_cmd = docker_payload.get("compose")
    docker_required = bool(docker_plan.get("required"))
    if docker and isinstance(compose_cmd, list):
        if docker_payload.get("running") is True:
            result = run([*compose_cmd, "ps"], timeout=30)
            if result.stdout.strip():
                print(redact_text(result.stdout.strip()))
            else:
                print_info("Docker", "running; no compose services listed")
        elif docker_required:
            print_check(False, "Docker", str(docker_payload.get("error") or "not running"))
        else:
            print_info("Docker", f"not running; optional ({docker_payload.get('error') or 'docker info failed'})")
    else:
        if docker_required:
            print_check(False, "Docker Compose", "missing")
        else:
            satisfied = docker_plan.get("satisfied") if isinstance(docker_plan.get("satisfied"), list) else []
            detail = "; ".join(str(item) for item in satisfied) if satisfied else "optional for Docker-backed full stack"
            print_info("Docker Compose", f"missing; {detail}")

    print_header("HTTP")
    runtime_payload: object | None = None
    provider_auth_payload: object | None = None
    permission_payload: object | None = None
    for path in ("/health", "/api/runtime", "/api/provider-auth/status", "/api/permissions/mode"):
        ok, raw, payload = backend_json(path, timeout=15.0 if path == "/api/runtime" else 3.0)
        if path == "/api/runtime" and ok:
            payload = normalize_runtime_payload_for_cli(payload)
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
    ok, raw, provider_reference_payload = backend_json("/api/provider-auth/reference", timeout=10.0)
    if ok:
        for line in summarize_provider_reference_payload(provider_reference_payload):
            print_info(line)
    else:
        print_check(False, "provider reference", redact_text(raw)[:240] or "unavailable")
    ok, raw, provider_env_payload = backend_json("/api/provider-auth/env", timeout=10.0)
    if ok:
        for line in summarize_provider_env_payload(provider_env_payload):
            print_info(line)
    else:
        print_check(False, "provider env", redact_text(raw)[:240] or "unavailable")

    print_header("Owner Permissions")
    for line in summarize_full_autonomy(permission_payload):
        print_info(line)

    if runtime_payload is not None:
        print_header("AI Tools")
        for line in summarize_runtime_ai_tools(runtime_payload):
            print_info(line)

    ok, _raw, tool_catalog_payload = backend_json("/api/tools/catalog", timeout=8.0)
    if ok:
        print_header("AI Tool Catalog")
        for line in summarize_tool_catalog_payload(tool_catalog_payload):
            print_info(line)

    ok, _raw, connectors_payload = backend_json("/api/connectors", timeout=8.0)
    if ok:
        print_header("Connectors")
        for line in summarize_connectors_payload(connectors_payload):
            print_info(line)

    try:
        tools_payload, _tool_ok, _tool_raw = collect_tools_status_payload()
        print_header("MCP External Tools")
        for line in summarize_mcp_gateway_setup_payload(
            mcp_gateway_setup_payload(
                tools_payload.get("runtime"),
                tools_payload.get("connectors"),
            )
        ):
            print_info(line)
        ok, raw, mcp_probe_payload = backend_json("/api/tools/mcp-gateway?probe=true", timeout=15.0)
        if ok:
            for line in summarize_mcp_gateway_setup_payload(mcp_probe_payload):
                print_info(line)
        else:
            print_check(False, "MCP probe", redact_text(raw)[:240] or "unavailable")
    except Exception as exc:
        print_check(False, "MCP external tools", str(exc)[:240])

    ok, _raw, parity_payload = backend_json("/api/host-bridge/parity", timeout=HOST_BRIDGE_PARITY_TIMEOUT_SECONDS)
    if ok:
        parity_payload = normalize_parity_payload_for_cli(parity_payload)
        print_header("Automation Permission")
        for line in summarize_parity_payload(parity_payload):
            print_info(line)

    print_header("Local Proof Artifacts")
    for line in summarize_local_proof_artifacts(collect_local_proof_artifacts(current_source_summary())):
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


def _sqlite_backup_source_path() -> Path:
    database_url = configured_database_url()
    if database_url.startswith("sqlite"):
        parsed = urllib.parse.urlparse(database_url)
        if parsed.path:
            return Path(urllib.parse.unquote(parsed.path)).expanduser()
    return configured_data_dir() / "atrium.db"


def _copy_offsite(files: Sequence[Path], offsite_dir: str | None) -> list[str]:
    if not offsite_dir:
        return []
    target = Path(offsite_dir).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for path in files:
        dest = target / path.name
        shutil.copy2(path, dest)
        copied.append(str(dest))
    return copied


def _write_key_value_manifest(path: Path, values: dict[str, object]) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def command_backup(args: argparse.Namespace) -> int:
    ensure_repo_root()
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else configured_backup_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    offsite_dir = args.offsite_dir or os.environ.get("ATRIUM_BACKUP_OFFSITE_DIR") or ""
    require_offsite = bool(args.require_offsite)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    if uses_postgres_database():
        result = run_postgres_backup(
            output_dir,
            stamp=stamp,
            schema=args.schema,
            offsite_dir=offsite_dir,
            require_offsite=require_offsite,
            timeout=args.timeout,
        )
    else:
        result = run_sqlite_backup(
            output_dir,
            stamp=stamp,
            offsite_dir=offsite_dir,
            require_offsite=require_offsite,
        )

    if args.json:
        print(json.dumps(redact_json_value(result), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print_header("Backup")
    print_check(bool(result.get("ok")), "backup", str(result.get("backend") or "unknown"))
    for key in ("backupFile", "manifest", "sha256File", "backupDir"):
        value = result.get(key)
        if value:
            print_info(key, str(value))
    copied = result.get("offsiteFiles")
    if isinstance(copied, list) and copied:
        print_info("offsite", "; ".join(str(item) for item in copied))
    if result.get("skipped"):
        print_info("skipped", str(result.get("message") or "nothing to back up yet"))
    return 0


def run_sqlite_backup(
    output_dir: Path,
    *,
    stamp: str,
    offsite_dir: str,
    require_offsite: bool,
) -> dict[str, object]:
    db_path = _sqlite_backup_source_path()
    if not db_path.exists():
        if require_offsite and not offsite_dir:
            raise StepFailure(
                "SQLite backup cannot satisfy required offsite copy",
                next_step="Set ATRIUM_BACKUP_OFFSITE_DIR or pass --offsite-dir, then rerun backup.",
            )
        return {
            "ok": True,
            "backend": "sqlite",
            "skipped": True,
            "message": "SQLite database file does not exist yet",
            "backupDir": str(output_dir),
        }
    dest = output_dir / f"atrium-sqlite-{stamp}.db"
    shutil.copy2(db_path, dest)
    files = [dest]
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            sidecar_dest = Path(str(dest) + suffix)
            shutil.copy2(sidecar, sidecar_dest)
            files.append(sidecar_dest)
    sha, size = file_sha256_and_size(dest)
    manifest = output_dir / f"atrium-sqlite-{stamp}.manifest.json"
    manifest_payload = {
        "format": "atrium-sqlite-backup-manifest-v1",
        "createdAt": stamp,
        "source": str(db_path),
        "files": [str(path) for path in files],
        "sha256": sha,
        "sizeBytes": size,
    }
    manifest.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    files.append(manifest)
    offsite_files = _copy_offsite(files, offsite_dir)
    if require_offsite and not offsite_files:
        raise StepFailure(
            "SQLite backup completed locally but offsite copy is required",
            next_step="Set ATRIUM_BACKUP_OFFSITE_DIR or pass --offsite-dir, then rerun backup.",
        )
    return {
        "ok": True,
        "backend": "sqlite",
        "backupDir": str(output_dir),
        "backupFile": str(dest),
        "manifest": str(manifest),
        "sha256": sha,
        "sizeBytes": size,
        "offsiteFiles": offsite_files,
    }


def pgvector_available(pg_url: str) -> bool:
    psql = command_path("psql")
    if not psql:
        return False
    result = run(
        [psql, "-Atq", "--dbname", pg_url, "-c", "SELECT to_regtype('vector') IS NOT NULL"],
        timeout=10,
    )
    return result.returncode == 0 and result.stdout.strip().lower() in {"t", "true", "1"}


def run_postgres_backup(
    output_dir: Path,
    *,
    stamp: str,
    schema: str,
    offsite_dir: str,
    require_offsite: bool,
    timeout: int,
) -> dict[str, object]:
    pg_url = normalize_pg_tool_url(configured_database_url())
    pg_dump = command_path("pg_dump")
    if not pg_dump:
        script = ROOT / "ops" / "scripts" / "backup_postgres.sh"
        bash = command_path("bash") or "/bin/bash"
        if platform.system() != "Windows" and script.exists() and Path(bash).exists():
            env = os.environ.copy()
            env["ATRIUM_DATABASE_URL"] = pg_url
            env["ATRIUM_BACKUP_DIR"] = str(output_dir)
            env["ATRIUM_BACKUP_SCHEMA"] = schema
            if offsite_dir:
                env["ATRIUM_BACKUP_OFFSITE_DIR"] = offsite_dir
            if require_offsite:
                env["ATRIUM_BACKUP_REQUIRE_OFFSITE"] = "true"
            result = run([bash, str(script)], env=env, timeout=timeout)
            if result.returncode != 0:
                raise StepFailure("Postgres backup failed", next_step=redact_text(result.stderr or result.stdout)[:1200])
            return {
                "ok": True,
                "backend": "postgres",
                "backupDir": str(output_dir),
                "stdout": redact_text(result.stdout[-2000:]),
            }
        raise StepFailure(
            "pg_dump is missing",
            next_step="Install PostgreSQL client tools and make pg_dump available on PATH, then rerun backup.",
        )

    out = output_dir / f"atrium-{stamp}.sql.gz"
    sha_file = Path(str(out) + ".sha256")
    manifest = Path(str(out) + ".manifest")
    dump_cmd = [pg_dump, "--dbname", pg_url]
    if schema and schema != "all":
        dump_cmd.append(f"--schema={schema}")
    result = run(dump_cmd, timeout=timeout)
    if result.returncode != 0:
        raise StepFailure("Postgres backup failed", next_step=redact_text(result.stderr or result.stdout)[:1200])

    prelude = ""
    if schema and schema != "all" and pgvector_available(pg_url):
        prelude = "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;\n"
    with gzip.open(out, "wb", compresslevel=9) as handle:
        if prelude:
            handle.write(prelude.encode("utf-8"))
        handle.write(result.stdout.encode("utf-8"))
    sha, size = file_sha256_and_size(out)
    sha_file.write_text(f"{sha}  {out.name}\n", encoding="utf-8")
    manifest_values = {
        "format": "atrium-backup-manifest-v1",
        "created_utc": stamp,
        "backup_file": out.name,
        "schema": schema,
        "postgres_database": "configured-url",
        "sha256": sha or "",
        "size_bytes": size or 0,
        "gzip_ok": "true",
        "offsite_copied": "false",
        "offsite_dir": "",
    }
    files = [out, sha_file, manifest]
    offsite_files: list[str] = []
    if offsite_dir:
        offsite_files = _copy_offsite([out, sha_file], offsite_dir)
        manifest_values["offsite_copied"] = "true"
        manifest_values["offsite_dir"] = offsite_dir
    elif require_offsite:
        raise StepFailure(
            "Postgres backup completed locally but offsite copy is required",
            next_step="Set ATRIUM_BACKUP_OFFSITE_DIR or pass --offsite-dir, then rerun backup.",
        )
    _write_key_value_manifest(manifest, manifest_values)
    if offsite_dir:
        _copy_offsite([manifest], offsite_dir)
        offsite_files.append(str(Path(offsite_dir).expanduser() / manifest.name))
    return {
        "ok": True,
        "backend": "postgres",
        "backupDir": str(output_dir),
        "backupFile": str(out),
        "sha256File": str(sha_file),
        "manifest": str(manifest),
        "sha256": sha,
        "sizeBytes": size,
        "offsiteFiles": offsite_files,
    }


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
        payloads["diagnostics/doctor.json"] = collect_doctor_payload()
    except Exception as exc:
        payloads["diagnostics/doctor.json"] = {"ok": False, "error": str(exc)[:400]}
    try:
        payloads["diagnostics/status.json"] = collect_status_payload()
    except Exception as exc:
        payloads["diagnostics/status.json"] = {"ok": False, "error": str(exc)[:400]}
    try:
        payloads["diagnostics/process.json"] = collect_process_payload()
    except Exception as exc:
        payloads["diagnostics/process.json"] = {"ok": False, "error": str(exc)[:400]}
    try:
        payloads["diagnostics/windows-runtime.json"] = collect_windows_runtime_payload()
    except Exception as exc:
        payloads["diagnostics/windows-runtime.json"] = {"ok": False, "error": str(exc)[:400]}
    try:
        payloads["diagnostics/windows-entrypoints.json"] = collect_windows_entrypoints_payload()
    except Exception as exc:
        payloads["diagnostics/windows-entrypoints.json"] = {"ok": False, "error": str(exc)[:400]}
    try:
        payloads["diagnostics/native-next-checks.json"] = native_next_checks_payload()
    except Exception as exc:
        payloads["diagnostics/native-next-checks.json"] = {"ok": False, "error": str(exc)[:400]}
    try:
        payloads["diagnostics/native-parity-matrix.json"] = native_parity_matrix_payload()
    except Exception as exc:
        payloads["diagnostics/native-parity-matrix.json"] = {"ok": False, "error": str(exc)[:400]}
    try:
        payloads["diagnostics/docker.json"] = collect_docker_payload()
    except Exception as exc:
        payloads["diagnostics/docker.json"] = {"ok": False, "error": str(exc)[:400]}
    try:
        source_summary = current_source_summary()
        payloads["diagnostics/host-bridge-source.json"] = source_summary if isinstance(source_summary, dict) else {"ok": False, "error": "source summary unavailable"}
    except Exception as exc:
        payloads["diagnostics/host-bridge-source.json"] = {"ok": False, "error": str(exc)[:400]}
    try:
        source_for_artifacts = payloads.get("diagnostics/host-bridge-source.json")
        payloads["diagnostics/local-proof-artifacts.json"] = collect_local_proof_artifacts(
            source_for_artifacts if isinstance(source_for_artifacts, dict) else None
        )
    except Exception as exc:
        payloads["diagnostics/local-proof-artifacts.json"] = {"ok": False, "error": str(exc)[:400]}
    try:
        handoff_payload, handoff_error = _load_json_file(Path(DEFAULT_WINDOWS_HANDOFF_PATH))
        payloads["diagnostics/windows-proof-handoff.json"] = (
            handoff_payload
            if isinstance(handoff_payload, dict)
            else {"ok": False, "path": DEFAULT_WINDOWS_HANDOFF_PATH, "error": handoff_error or "handoff packet is missing"}
        )
    except Exception as exc:
        payloads["diagnostics/windows-proof-handoff.json"] = {"ok": False, "path": DEFAULT_WINDOWS_HANDOFF_PATH, "error": str(exc)[:400]}
    try:
        payloads["diagnostics/logs.json"] = collect_logs_payload("all", 200)
    except Exception as exc:
        payloads["diagnostics/logs.json"] = {"ok": False, "error": str(exc)[:400]}
    ok_runtime, raw_runtime, runtime_payload = backend_json("/api/runtime", timeout=15.0)
    runtime_payload = normalize_runtime_payload_for_cli(runtime_payload) if ok_runtime else runtime_payload
    payloads["diagnostics/runtime.json"] = runtime_payload if ok_runtime else {"ok": False, "error": raw_runtime[:400]}
    ok_connectors, raw_connectors, connectors_payload = backend_json("/api/connectors", timeout=8.0)
    payloads["diagnostics/connectors.json"] = connectors_payload if ok_connectors else {"ok": False, "error": raw_connectors[:400]}
    ok_catalog, raw_catalog, catalog_payload = backend_json("/api/tools/catalog", timeout=8.0)
    payloads["diagnostics/tools-catalog.json"] = catalog_payload if ok_catalog else {"ok": False, "error": raw_catalog[:400]}
    ok_parity, raw_parity, parity_payload = backend_json("/api/host-bridge/parity", timeout=HOST_BRIDGE_PARITY_TIMEOUT_SECONDS)
    payloads["diagnostics/host-bridge-parity.json"] = (
        normalize_parity_payload_for_cli(parity_payload) if ok_parity else {"ok": False, "error": raw_parity[:400]}
    )
    ok_permission, raw_permission, permission_payload = backend_json("/api/permissions/mode", timeout=5.0)
    payloads["diagnostics/permission-mode.json"] = permission_payload if ok_permission else {"ok": False, "error": raw_permission[:400]}
    ok_provider, raw_provider, provider_payload = backend_json("/api/provider-auth/status?probe=true", timeout=15.0)
    payloads["diagnostics/provider-status.json"] = provider_payload if ok_provider else {"ok": False, "error": raw_provider[:400]}
    ok_provider_reference, raw_provider_reference, provider_reference_payload = backend_json("/api/provider-auth/reference", timeout=10.0)
    payloads["diagnostics/provider-reference.json"] = (
        provider_reference_payload if ok_provider_reference else {"ok": False, "error": raw_provider_reference[:400]}
    )
    ok_provider_env, raw_provider_env, provider_env_payload = backend_json("/api/provider-auth/env", timeout=10.0)
    payloads["diagnostics/provider-env.json"] = (
        provider_env_payload if ok_provider_env else {"ok": False, "error": raw_provider_env[:400]}
    )
    try:
        tools_payload, _tool_ok, _tool_raw = collect_tools_status_payload()
        payloads["diagnostics/tools-status.json"] = tools_payload
        payloads["diagnostics/tools-mcp-gateway.json"] = mcp_gateway_setup_payload(
            tools_payload.get("runtime"),
            tools_payload.get("connectors"),
        )
    except Exception as exc:
        payloads["diagnostics/tools-status.json"] = {"ok": False, "error": str(exc)[:400]}
        payloads["diagnostics/tools-mcp-gateway.json"] = {"ok": False, "error": str(exc)[:400]}
    ok_mcp_probe, raw_mcp_probe, mcp_probe_payload = backend_json("/api/tools/mcp-gateway?probe=true", timeout=15.0)
    payloads["diagnostics/tools-mcp-probe.json"] = (
        mcp_probe_payload if ok_mcp_probe and isinstance(mcp_probe_payload, dict) else {"ok": False, "error": raw_mcp_probe[:400]}
    )
    try:
        _automation_ok, automation_payload = collect_automation_status_payload()
        payloads["diagnostics/automation-status.json"] = automation_payload
    except Exception as exc:
        payloads["diagnostics/automation-status.json"] = {"ok": False, "error": str(exc)[:400]}
    try:
        payloads["diagnostics/openclaw-windows-proof-readiness.json"] = collect_openclaw_windows_proof_readiness_payload()
    except Exception as exc:
        payloads["diagnostics/openclaw-windows-proof-readiness.json"] = {"ok": False, "error": str(exc)[:400]}
    return payloads


def report_lines() -> list[str]:
    lines = [
        "# ATRIUM support report",
        f"repo={ROOT}",
        f"platform={platform.platform()}",
        f"machine={platform.machine()}",
        f"ram={memory_gb()}",
        f"launcher_mode={launcher_mode()}",
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
        windows_runtime = collect_windows_runtime_payload()
        powershell_payload = windows_runtime.get("powershell") if isinstance(windows_runtime.get("powershell"), dict) else {}
        lines.append(f"windows.session={windows_runtime.get('sessionName') or 'unknown'}")
        lines.append(f"windows.powershell={powershell_payload.get('command') or 'missing'}")
        lines.append(f"windows.powershell.version={powershell_payload.get('version') or 'unknown'}")
    else:
        lines.append(f"process.screen={screen_sessions().replace(chr(10), ' | ')}")
    entrypoints = collect_windows_entrypoints_payload()
    checks = entrypoints.get("checks") if isinstance(entrypoints.get("checks"), dict) else {}
    lines.append(f"windows.entrypoints.ok={bool_text(entrypoints.get('ok'))}")
    for name, value in checks.items():
        lines.append(f"windows.entrypoint.{name}={bool_text(value)}")
    proof = entrypoints.get("openclawLifecycleProof")
    if isinstance(proof, dict):
        lines.append(f"windows.entrypoints.openclawLifecycleProof.ok={bool_text(proof.get('ok'))}")
        commands = proof.get("commands")
        if isinstance(commands, dict):
            for name in (
                "nativeSetup",
                "nativeStart",
                "nativeStatusJson",
                "nativeLogsJson",
                "nativeReportBundle",
                "nativeStop",
                "nativeRestart",
                "nativePermissionsStatusJson",
                "nativePermissionsSetFullAuto",
                "nativeProviderStatusProbeJson",
                "nativeProviderReferenceJson",
                "nativeProviderEnvJson",
                "nativeProviderLoginChatGPT",
                "nativeProviderLoginClaudeCode",
                "nativeProviderDisconnectChatGPT",
                "nativeProviderDisconnectClaudeCode",
                "nativeToolsStatusJson",
                "nativeToolsCatalogJson",
                "mcpGatewaySetupJson",
                "mcpGatewayProbeJson",
                "mcpGatewayStatusJson",
                "nativeBrowserDesktopSmoke",
                "windowsProbe",
                "sourceValidate",
                "windowsLiveProof",
                "windowsArtifactValidate",
                "windowsArtifactValidateOnWindows",
                "acceptWindowsArtifact",
                "report",
                "audit",
            ):
                command = commands.get(name)
                if isinstance(command, str) and command.strip():
                    lines.append(f"windows.entrypoints.openclawLifecycleProof.{name}={command}")
        checklist = proof.get("operatorChecklist")
        if isinstance(checklist, list):
            ids = [str(item.get("id")) for item in checklist if isinstance(item, dict) and item.get("id")]
            if ids:
                lines.append("windows.entrypoints.openclawLifecycleProof.checklist=" + ", ".join(ids))
    lines.extend(summarize_native_parity_matrix(native_parity_matrix_payload()))
    files = entrypoints.get("files") if isinstance(entrypoints.get("files"), dict) else {}
    for name, meta in files.items():
        if not isinstance(meta, dict):
            continue
        digest = str(meta.get("sha256") or "")
        lines.append(
            "windows.entrypoint.file."
            f"{name}=exists={bool_text(meta.get('exists'))};"
            f"path={meta.get('relativePath') or meta.get('path') or 'unknown'};"
            f"bytes={meta.get('bytes') if meta.get('bytes') is not None else 'unknown'};"
            f"sha256={digest[:12] if digest else 'missing'}"
        )
    for tool in report_tool_names():
        lines.append(f"tool.{tool}={'present' if report_command_path(tool) else 'missing'}")
    lines.extend(docker_report_lines())
    for port, label in PORTS.items():
        lines.append(f"port.{label}.{port}={port_owner(port) if port_open(port) else 'free'}")
    runtime_payload: object | None = None
    provider_auth_payload: object | None = None
    permission_payload: object | None = None
    for path in ("/health", "/api/runtime", "/api/provider-auth/status", "/api/permissions/mode"):
        ok, raw, payload = backend_json(path, timeout=15.0 if path == "/api/runtime" else 3.0)
        if path == "/api/runtime" and ok:
            payload = normalize_runtime_payload_for_cli(payload)
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
    ok, _raw, provider_reference_payload = backend_json("/api/provider-auth/reference", timeout=10.0)
    lines.append("provider_reference:")
    lines.extend(summarize_provider_reference_payload(provider_reference_payload if ok else None))
    ok, _raw, provider_env_payload = backend_json("/api/provider-auth/env", timeout=10.0)
    lines.append("provider_env:")
    lines.extend(summarize_provider_env_payload(provider_env_payload if ok else None))
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
    try:
        tools_payload, _tool_ok, _tool_raw = collect_tools_status_payload()
        lines.append("mcp_external_tools:")
        lines.extend(
            summarize_mcp_gateway_setup_payload(
                mcp_gateway_setup_payload(
                    tools_payload.get("runtime"),
                    tools_payload.get("connectors"),
                )
            )
        )
    except Exception as exc:
        lines.append("mcp_external_tools:")
        lines.append(f"MCP gateway setup=unavailable: {str(exc)[:240]}")
    ok, _raw, mcp_probe_payload = backend_json("/api/tools/mcp-gateway?probe=true", timeout=15.0)
    lines.append("mcp_external_probe:")
    lines.extend(summarize_mcp_gateway_setup_payload(mcp_probe_payload if ok else None))
    ok, _raw, parity_payload = backend_json("/api/host-bridge/parity", timeout=HOST_BRIDGE_PARITY_TIMEOUT_SECONDS)
    parity_payload = normalize_parity_payload_for_cli(parity_payload) if ok else None
    local_artifacts = collect_local_proof_artifacts(current_source_summary())
    if isinstance(parity_payload, dict):
        parity_payload = dict(parity_payload)
        parity_payload["localArtifacts"] = local_artifacts
    lines.append("automation_permission:")
    lines.extend(summarize_parity_payload(parity_payload))
    lines.append("automation_permission.local_artifacts:")
    lines.extend(summarize_local_proof_artifacts(local_artifacts))
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
                "launcherMode": launcher_mode(),
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
            manifest["included"].append("manifest.json")
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        print(f"wrote bundle {bundle}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ATRIUM local full-stack setup shortcut")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check local setup without changing files")
    doctor.add_argument("--json", action="store_true", help="print redacted machine-readable preflight diagnostics")
    doctor.set_defaults(func=command_doctor)

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

    provider_reference = provider_sub.add_parser("reference", help="show provider credential meanings and configured route readiness")
    provider_reference.add_argument("--json", action="store_true", help="print redacted provider credential reference JSON")
    provider_reference.set_defaults(func=command_provider)

    provider_env = provider_sub.add_parser("env", help="show provider environment settings without printing secret values")
    provider_env.add_argument("--json", action="store_true", help="print redacted provider environment settings JSON")
    provider_env.set_defaults(func=command_provider)

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

    permissions = sub.add_parser("permissions", help="inspect or update owner automation permission mode")
    permissions_sub = permissions.add_subparsers(dest="permissions_action", required=True)
    permissions_status = permissions_sub.add_parser("status", help="show owner automation permission mode")
    permissions_status.add_argument("--json", action="store_true", help="print redacted permission mode JSON")
    permissions_status.set_defaults(func=command_permissions)
    permissions_set = permissions_sub.add_parser("set", help="set owner automation permission mode from the native terminal")
    permissions_set.add_argument("mode", choices=PERMISSION_MODE_CHOICES, help="permission mode to apply")
    permissions_set.add_argument("--agent-full-access", type=parse_bool_option, help="set whether the local agent has full filesystem/terminal/browser access")
    permissions_set.add_argument("--allow-tool", dest="allowed_tools", action="append", help="allowed tool id or comma-separated ids; repeatable")
    permissions_set.add_argument("--deny-tool", dest="denied_tools", action="append", help="denied tool id or comma-separated ids; repeatable")
    permissions_set.add_argument("--allow-risk", dest="allowed_risk_classes", action="append", help="allowed risk class or comma-separated classes; repeatable")
    permissions_set.add_argument("--deny-risk", dest="denied_risk_classes", action="append", help="denied risk class or comma-separated classes; repeatable")
    permissions_set.add_argument("--allow-command", dest="command_allowlist", action="append", help="allowed command pattern or comma-separated patterns; repeatable")
    permissions_set.add_argument("--deny-command", dest="command_denylist", action="append", help="denied command pattern or comma-separated patterns; repeatable")
    permissions_set.add_argument("--ask-fallback", choices=("ask", "deny"), help="fallback behavior for ask-gated actions")
    permissions_set.add_argument("--strict-inline-eval", type=parse_bool_option, help="set strict inline evaluation true/false")
    permissions_set.add_argument("--updated-by", default="atrium-cli", help="audit actor label for the permission change")
    permissions_set.add_argument("--json", action="store_true", help="print redacted updated permission mode JSON")
    permissions_set.set_defaults(func=command_permissions)

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
    tools_mcp_gateway = tools_sub.add_parser("mcp-gateway", help="show MCP external-write gateway setup and readiness")
    tools_mcp_gateway.add_argument("--json", action="store_true", help="print redacted MCP gateway setup JSON")
    tools_mcp_gateway.set_defaults(func=command_tools)
    tools_mcp_probe = tools_sub.add_parser("mcp-probe", help="probe MCP external-write gateway health")
    tools_mcp_probe.add_argument("--json", action="store_true", help="print redacted MCP gateway probe JSON")
    tools_mcp_probe.set_defaults(func=command_tools)

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
    automation_source.add_argument("--json", action="store_true", help="print pure source summary JSON without command trace")
    automation_source.set_defaults(func=command_automation)

    automation_smoke = automation_sub.add_parser("smoke", help="run native browser/desktop smoke diagnostics for the current OS")
    automation_smoke.add_argument("--browser-url", default=FRONTEND_URL, help="URL to open through browser.open during smoke diagnostics")
    automation_smoke.add_argument("--browser-profile", default="atrium", help="browser profile for browser probes")
    automation_smoke.add_argument("--output", help="write the stamped smoke artifact JSON")
    automation_smoke.add_argument("--simulate", action="store_true", help="simulate the current host probe where the underlying probe supports it")
    automation_smoke.set_defaults(func=command_automation)

    windows_probe = automation_sub.add_parser("windows-probe", help="run the Windows HostBridge probe through uv")
    windows_probe.add_argument("--simulate", action="store_true", help="simulate Windows branch coverage")
    windows_probe.add_argument("--full", action="store_true", help="run the full live Windows parity probe")
    windows_probe.add_argument("--screenshot", action="store_true", help="capture a screenshot probe")
    windows_probe.add_argument("--notification", action="store_true", help="send a notification probe")
    windows_probe.add_argument("--interactive", action="store_true", help="run interactive Notepad desktop control checks")
    windows_probe.add_argument("--browser-url", help="open a URL through browser.open")
    windows_probe.add_argument("--browser-profile", default="atrium", help="browser profile for browser probes")
    windows_probe.add_argument("--output", help=f"write the stamped probe artifact JSON; --full defaults to {DEFAULT_WINDOWS_PROBE_PATH}")
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
    artifact.add_argument("--max-artifact-age-hours", type=float, default=24.0, help="reject artifacts older than this many hours")
    artifact.add_argument("--json", action="store_true", help="print pure artifact summary JSON without command trace")
    artifact.set_defaults(func=command_automation)

    parity_report = automation_sub.add_parser("report", help="verify macOS/Windows live artifacts and install the backend parity report")
    parity_report.add_argument("--macos", required=True, help="local path to macOS full live proof artifact JSON")
    parity_report.add_argument("--windows", required=True, help="local path to copied Windows full live proof artifact JSON")
    parity_report.add_argument("--output", default=str(HOST_BRIDGE_PARITY_REPORT), help="report path read by the ATRIUM backend")
    parity_report.add_argument("--max-artifact-age-hours", type=float, default=24.0)
    parity_report.add_argument("--windows-source-path", default="C:\\Temp\\atrium_host_bridge_windows_live.json", help="original Windows-host artifact path for transfer hints")
    parity_report.add_argument("--skip-current-source-check", action="store_true", help="allow offline historical audits; not valid for claiming current OpenClaw-level parity")
    parity_report.set_defaults(func=command_automation)

    accept_windows = automation_sub.add_parser(
        "accept-windows",
        help="repo-side gate: validate, import, report, and audit a copied Windows proof artifact",
    )
    accept_windows.add_argument("artifact", help="path to the Windows full live proof artifact copied from the Windows host")
    accept_windows.add_argument("--handoff", default=DEFAULT_WINDOWS_HANDOFF_PATH, help="current Windows proof handoff packet")
    accept_windows.add_argument("--output", default=str(HOST_BRIDGE_PARITY_REPORT), help="backend parity report path to write")
    accept_windows.add_argument("--max-artifact-age-hours", type=float, default=24.0, help="reject artifacts older than this many hours")
    accept_windows.add_argument("--windows-source-path", help="original Windows-host artifact path for transfer hints")
    accept_windows.add_argument("--json", action="store_true", help="print accept/import/audit result JSON")
    accept_windows.set_defaults(func=command_automation)

    stop = sub.add_parser("stop", help="stop ATRIUM-owned local sessions")
    stop.add_argument("--launchd", action="store_true", help="also uninstall the ATRIUM LaunchAgent")
    stop.set_defaults(func=command_stop)

    logs = sub.add_parser("logs", help="show recent backend/UI logs")
    logs.add_argument("service", nargs="?", choices=("backend", "ui", "all"), default="all")
    logs.add_argument("-n", "--lines", type=int, default=80)
    logs.add_argument("--json", action="store_true", help="print redacted log payload as JSON")
    logs.set_defaults(func=command_logs)

    backup = sub.add_parser("backup", help="run a local ATRIUM database backup now")
    backup.add_argument("--output-dir", help="directory for backup files; defaults to ATRIUM_BACKUP_DIR or system/data/backups")
    backup.add_argument("--offsite-dir", help="optional directory to copy backup artifacts after local verification")
    backup.add_argument("--require-offsite", action="store_true", help="fail unless --offsite-dir or ATRIUM_BACKUP_OFFSITE_DIR is set")
    backup.add_argument("--schema", default="atrium", help="Postgres schema to dump; use 'all' for a full database dump")
    backup.add_argument("--timeout", type=int, default=300, help="Postgres pg_dump timeout in seconds")
    backup.add_argument("--json", action="store_true", help="print redacted backup result JSON")
    backup.set_defaults(func=command_backup)

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
